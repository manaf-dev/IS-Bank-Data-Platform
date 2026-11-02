"""Gold star schema: conformed dimensions and facts built from Silver (PySpark).

Design choices:

- Surrogate keys are deterministic hashes of business keys (`xxhash64`), so they are stable
  across full rebuilds and need no sequence table.
- One customer per golden party (from entity resolution); Gold exposes only what reports
  need: masked names and account numbers, no Ghana Card, phone, or email (CLAUDE.md s9).
- Card events and mobile app users carry no core customer id. Cards link through the core
  card register by masked PAN (ambiguous PANs stay unlinked rather than guessed); app users
  link behaviourally: an app transfer and the core posting it caused share the exact
  timestamp and amount.
- A GL-vs-core reconciliation table surfaces fee-income breaks for the DQ dashboard.
"""

import datetime as dt

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

UNKNOWN_KEY = -1
NIGHT_HOURS = (0, 5)  # [start, end) hours treated as night-time for card activity
DEFAULT_LOANS_MISSED = 3  # missed instalments that classify a loan as in default


def key(*cols) -> F.Column:
    """Deterministic surrogate key from business key columns."""
    return F.xxhash64(*[F.coalesce(F.col(c).cast("string"), F.lit("")) for c in cols])


def date_key(col) -> F.Column:
    """Integer date key (yyyymmdd) for dim_date."""
    return F.date_format(col, "yyyyMMdd").cast("int")


def month_key(col) -> F.Column:
    """Integer month key (yyyymm) for monthly grains."""
    return F.date_format(col, "yyyyMM").cast("int")


# ---------------------------------------------------------------------- dimensions


def dim_date(spark, start: dt.date, end: dt.date) -> DataFrame:
    """One row per calendar day, with month-end and salary-season flags for the reports.

    Weeks follow ISO 8601 (Monday start); a week belongs to the year of its Thursday, so
    29 Dec 2025 is in 2026-W01. Report slicers use week_label, which sorts as text.
    """
    days = spark.sql(
        f"select explode(sequence(to_date('{start}'), to_date('{end}'), interval 1 day)) as d"
    )
    week_start = F.to_date(F.date_trunc("week", "d"))
    iso_year = F.year(F.date_add(week_start, 3))
    week = F.weekofyear("d")
    return days.select(
        date_key(F.col("d")).alias("date_key"),
        F.col("d").alias("date"),
        F.year("d").alias("year"),
        F.quarter("d").alias("quarter"),
        F.concat(F.lit("Q"), F.quarter("d")).alias("quarter_name"),
        week.alias("week_of_year"),
        week_start.alias("week_start"),
        F.concat(iso_year, F.lit("-W"), F.lpad(week.cast("string"), 2, "0")).alias("week_label"),
        F.month("d").alias("month"),
        F.date_format("d", "MMMM").alias("month_name"),
        month_key(F.col("d")).alias("month_key"),
        F.dayofmonth("d").alias("day_of_month"),
        F.date_format("d", "EEEE").alias("day_name"),
        (F.dayofweek("d").isin(1, 7)).alias("is_weekend"),
        (F.col("d") == F.last_day("d")).alias("is_month_end"),
    )


def dim_branch(branch: DataFrame) -> DataFrame:
    """Branches with their GL cost centre (CC-<branch number>)."""
    return branch.select(
        key("branch_code").alias("branch_key"),
        "branch_code",
        "branch_name",
        "city",
        "region",
        "opened_date",
        F.concat(F.lit("CC-"), F.substring("branch_code", 3, 3)).alias("cost_centre"),
    )


def dim_product(product: DataFrame) -> DataFrame:
    """Products and families."""
    return product.select(
        key("product_code").alias("product_key"), "product_code", "product_name", "product_family"
    )


def dim_channel(transaction: DataFrame) -> DataFrame:
    """Channels seen in the core ledger."""
    return (
        transaction.select("channel")
        .distinct()
        .select(key("channel").alias("channel_key"), "channel")
    )


def cif_party(party_xref: DataFrame) -> DataFrame:
    """CIF -> golden party."""
    return party_xref.where(F.col("source_system") == "core").select(
        F.col("source_id").alias("cif_number"), "party_id"
    )


def dim_customer(customer_history: DataFrame, party_xref: DataFrame, as_of: dt.date) -> DataFrame:
    """One row per golden party, described by its earliest-created CIF (current version)."""
    current = customer_history.where("is_current").join(cif_party(party_xref), "cif_number")
    window = Window.partitionBy("party_id").orderBy("created_at", "cif_number")
    ranked = current.withColumn("rn", F.row_number().over(window)).withColumn(
        "cif_count", F.count("*").over(Window.partitionBy("party_id"))
    )
    primary = ranked.where("rn = 1")
    age = F.floor(F.months_between(F.lit(as_of), F.col("date_of_birth")) / 12)
    return primary.select(
        key("party_id").alias("customer_key"),
        "party_id",
        "customer_type",
        F.when(F.col("customer_type") == "CORPORATE", F.col("surname"))
        .otherwise(F.concat(F.substring("first_name", 1, 1), F.lit(". "), F.initcap("surname")))
        .alias("display_name"),
        "gender",
        F.when(age.isNull(), "Unknown")
        .when(age < 25, "18-24")
        .when(age < 35, "25-34")
        .when(age < 45, "35-44")
        .when(age < 55, "45-54")
        .otherwise("55+")
        .alias("age_band"),
        F.col("branch_code").alias("home_branch_code"),
        F.to_date("created_at").alias("customer_since"),
        "cif_count",
        (F.col("cif_count") > 1).alias("has_duplicate_cif"),
        (F.col("ghana_card").isNull() & (F.col("customer_type") != "CORPORATE")).alias(
            "has_kyc_gap"
        ),
    )


def dim_account(account: DataFrame, party_xref: DataFrame) -> DataFrame:
    """Accounts with masked numbers and their customer, product, and branch keys."""
    acc = account.join(cif_party(party_xref), "cif_number", "left")
    return acc.select(
        key("account_number").alias("account_key"),
        "account_number",
        F.concat(F.lit("*********"), F.substring("account_number", 10, 4)).alias(
            "account_number_masked"
        ),
        F.coalesce(key("party_id"), F.lit(UNKNOWN_KEY)).alias("customer_key"),
        key("product_code").alias("product_key"),
        key("branch_code").alias("branch_key"),
        "product_code",
        "branch_code",
        "status",
        "opened_date",
        "closed_date",
        "contract_amount",
        "interest_rate_pct",
        "term_months",
        "maturity_date",
    )


def dim_merchant(card_authorisation: DataFrame) -> DataFrame:
    """Merchants seen by the card switch (ATMs grouped as one merchant)."""
    merchants = card_authorisation.select(
        F.coalesce("merchant_id", F.lit("ATM")).alias("merchant_id"),
        "merchant_name",
        "mcc",
        "merchant_city",
    )
    window = Window.partitionBy("merchant_id").orderBy(F.col("merchant_name").desc_nulls_last())
    one = merchants.withColumn("rn", F.row_number().over(window)).where("rn = 1").drop("rn")
    return one.select(
        key("merchant_id").alias("merchant_key"),
        "merchant_id",
        F.coalesce("merchant_name", F.lit("IS BANK ATM")).alias("merchant_name"),
        "mcc",
        F.when(F.col("merchant_id") == "ATM", None)
        .otherwise(F.col("merchant_city"))
        .alias("merchant_city"),
    )


# ---------------------------------------------------------------------- linkage


def card_accounts(card: DataFrame) -> DataFrame:
    """Masked PAN -> account, only where the masked PAN identifies a single card."""
    counts = card.groupBy("pan_masked").agg(
        F.count("*").alias("n"), F.first("account_number").alias("account_number")
    )
    return counts.where("n = 1").select("pan_masked", "account_number")


def app_user_customers(
    app_event: DataFrame, transaction: DataFrame, account: DataFrame, party_xref: DataFrame
):
    """App user -> customer, by matching app actions to the core postings they caused.

    An action and its posting share the exact timestamp and amount. Each user is assigned
    the customer most of their matched actions point to.
    """
    actions = app_event.where(
        F.col("event_type").isin("TRANSFER", "MOMO_SEND", "BILL_PAYMENT", "MOMO_PULL")
        & (F.col("status") == "SUCCESS")
    ).select("user_id", F.col("event_ts").alias("ts"), "amount")
    postings = transaction.where(F.col("channel") == "MOBILE").select(
        "account_number", F.col("booking_ts").alias("ts"), "amount"
    )
    owners = account.join(cif_party(party_xref), "cif_number").select("account_number", "party_id")
    matched = actions.join(postings, ["ts", "amount"]).join(owners, "account_number")
    votes = matched.groupBy("user_id", "party_id").agg(F.count("*").alias("matches"))
    window = Window.partitionBy("user_id").orderBy(F.col("matches").desc(), "party_id")
    best = votes.withColumn("rn", F.row_number().over(window)).where("rn = 1")
    return best.select("user_id", key("party_id").alias("customer_key"), "matches")


# ---------------------------------------------------------------------- facts


def fact_transaction(transaction: DataFrame, dim_acc: DataFrame) -> DataFrame:
    """One row per posted core transaction."""
    acc = dim_acc.select(
        "account_number", "account_key", "customer_key", "product_key", "branch_key"
    )
    t = transaction.join(acc, "account_number", "left")
    return t.select(
        "txn_ref",
        date_key(F.col("value_date")).alias("date_key"),
        F.coalesce("account_key", F.lit(UNKNOWN_KEY)).alias("account_key"),
        F.coalesce("customer_key", F.lit(UNKNOWN_KEY)).alias("customer_key"),
        F.coalesce("product_key", F.lit(UNKNOWN_KEY)).alias("product_key"),
        F.coalesce("branch_key", F.lit(UNKNOWN_KEY)).alias("branch_key"),
        key("channel").alias("channel_key"),
        "txn_code",
        "dr_cr",
        "booking_ts",
        "amount",
        "signed_amount",
    )


def fact_account_balance_monthly(transaction: DataFrame, dim_acc: DataFrame) -> DataFrame:
    """Month-end balance per account for months with activity (last running balance)."""
    window = Window.partitionBy("account_number", "mk").orderBy(
        F.col("booking_ts").desc(), F.col("txn_ref").desc()
    )
    last = (
        transaction.withColumn("mk", month_key(F.col("value_date")))
        .withColumn("rn", F.row_number().over(window))
        .where("rn = 1")
    )
    acc = dim_acc.select(
        "account_number", "account_key", "customer_key", "product_key", "branch_key"
    )
    return last.join(acc, "account_number").select(
        F.col("mk").alias("month_key"),
        "account_key",
        "customer_key",
        "product_key",
        "branch_key",
        F.col("running_balance").alias("balance"),
    )


def fact_loan(
    dim_acc: DataFrame,
    loan_repayment: DataFrame,
    loan_application: DataFrame,
    bureau_score: DataFrame,
    party_xref: DataFrame,
) -> DataFrame:
    """One row per loan: terms, repayment behaviour, applicant income, and bureau score."""
    loans = dim_acc.where(F.col("product_code").startswith("LN_"))
    behaviour = loan_repayment.groupBy(F.col("loan_account_number").alias("account_number")).agg(
        F.count("*").alias("instalments_due"),
        F.sum(F.col("is_missed").cast("int")).alias("instalments_missed"),
        F.sum("amount_paid").alias("amount_repaid"),
        F.max(F.when(F.col("is_missed"), F.col("due_date"))).alias("last_missed_date"),
    )
    application = loan_application.where(F.col("status") == "APPROVED").select(
        F.col("loan_account_number").alias("account_number"),
        F.col("monthly_income").alias("applicant_monthly_income"),
        "application_date",
        "employer_type",
    )
    bureau_party = party_xref.where(F.col("source_system") == "bureau").select(
        F.col("source_id").alias("bureau_ref"), "party_id"
    )
    latest_score = (
        bureau_score.join(bureau_party, "bureau_ref")
        .groupBy("party_id")
        .agg(F.max_by("score", "report_date").alias("bureau_score"))
        .select(key("party_id").alias("customer_key"), "bureau_score")
    )
    missed = F.coalesce(F.col("instalments_missed"), F.lit(0))
    status = (
        F.when(missed >= DEFAULT_LOANS_MISSED, "DEFAULT")
        .when(missed > 0, "ARREARS")
        .when(F.col("status") == "CLOSED", "REPAID")
        .otherwise("PERFORMING")
    )
    return (
        loans.join(behaviour, "account_number", "left")
        .join(application, "account_number", "left")
        .join(latest_score, "customer_key", "left")
        .select(
            "account_key",
            "customer_key",
            "product_key",
            "branch_key",
            date_key(F.col("opened_date")).alias("disbursed_date_key"),
            F.col("contract_amount").alias("principal"),
            "interest_rate_pct",
            "term_months",
            F.coalesce("instalments_due", F.lit(0)).alias("instalments_due"),
            missed.alias("instalments_missed"),
            F.coalesce("amount_repaid", F.lit(0)).cast("decimal(18,2)").alias("amount_repaid"),
            "last_missed_date",
            "applicant_monthly_income",
            "employer_type",
            "bureau_score",
            status.alias("loan_status"),
        )
    )


def fact_loan_payment(loan_repayment: DataFrame, dim_acc: DataFrame) -> DataFrame:
    """One row per loan instalment.

    Customer, product, and branch keys are carried from the account so the semantic model can
    filter instalments directly, without a second (ambiguous) path through dim_account.
    """
    acc = dim_acc.select("account_key", "customer_key", "product_key", "branch_key")
    return (
        loan_repayment.select(
            key("loan_account_number").alias("account_key"),
            "installment_no",
            date_key(F.col("due_date")).alias("due_date_key"),
            "amount_due",
            "amount_paid",
            "is_missed",
        )
        .join(acc, "account_key", "left")
        .select(
            "account_key",
            F.coalesce("customer_key", F.lit(UNKNOWN_KEY)).alias("customer_key"),
            F.coalesce("product_key", F.lit(UNKNOWN_KEY)).alias("product_key"),
            F.coalesce("branch_key", F.lit(UNKNOWN_KEY)).alias("branch_key"),
            "installment_no",
            "due_date_key",
            "amount_due",
            "amount_paid",
            "is_missed",
        )
    )


def fact_card_transaction(
    card_authorisation: DataFrame, card: DataFrame, dim_acc: DataFrame
) -> DataFrame:
    """One row per card authorisation (approved or declined), linked where the PAN is unique."""
    linked = card_authorisation.join(card_accounts(card), "pan_masked", "left").join(
        dim_acc.select("account_number", "account_key", "customer_key", "branch_key"),
        "account_number",
        "left",
    )
    hour = F.hour("event_ts")
    return linked.select(
        "event_id",
        date_key(F.col("event_ts")).alias("date_key"),
        "event_ts",
        hour.alias("hour_of_day"),
        ((hour >= NIGHT_HOURS[0]) & (hour < NIGHT_HOURS[1])).alias("is_night"),
        # Same hash as dim_merchant's key("merchant_id"), where ATMs are merchant "ATM".
        F.xxhash64(F.coalesce(F.col("merchant_id").cast("string"), F.lit("ATM"))).alias(
            "merchant_key"
        ),
        F.coalesce("account_key", F.lit(UNKNOWN_KEY)).alias("account_key"),
        F.coalesce("customer_key", F.lit(UNKNOWN_KEY)).alias("customer_key"),
        F.coalesce("branch_key", F.lit(UNKNOWN_KEY)).alias("branch_key"),
        "txn_kind",
        "entry_mode",
        "merchant_city",
        "amount",
        "response_code",
        "is_approved",
        "is_late",
    )


def fact_digital_event(app_event: DataFrame, users: DataFrame) -> DataFrame:
    """One row per mobile banking event, with the customer where the user is linked."""
    return app_event.join(users.select("user_id", "customer_key"), "user_id", "left").select(
        "event_id",
        date_key(F.col("event_ts")).alias("date_key"),
        "event_ts",
        "event_type",
        F.coalesce("customer_key", F.lit(UNKNOWN_KEY)).alias("customer_key"),
        "user_id",
        "device_os",
        "app_major_version",
        "biometric_used",
        "status",
        "amount",
        "is_late",
    )


def fact_gl_entry(gl_pnl: DataFrame) -> DataFrame:
    """Monthly P&L lines per branch cost centre (income positive, expense negative)."""
    return gl_pnl.select(
        month_key(F.col("period_start")).alias("month_key"),
        F.xxhash64(F.concat(F.lit("BR"), F.substring("cost_centre", 4, 3))).alias("branch_key"),
        "cost_centre",
        "gl_account",
        "gl_description",
        F.when(F.col("gl_account").startswith("4"), "INCOME")
        .otherwise("EXPENSE")
        .alias("line_type"),
        F.col("amount_ghs").alias("amount"),
    )


def recon_gl_fees(gl_pnl: DataFrame, transaction: DataFrame, account: DataFrame) -> DataFrame:
    """Fee income per branch and month: GL vs the core ledger, with the difference."""
    gl = gl_pnl.where(F.col("gl_account") == "4100").select(
        month_key(F.col("period_start")).alias("month_key"),
        F.concat(F.lit("BR"), F.substring("cost_centre", 4, 3)).alias("branch_code"),
        F.col("amount_ghs").alias("gl_fee_income"),
    )
    core = (
        transaction.where(F.col("txn_code") == "FEE")
        .join(account.select("account_number", "branch_code"), "account_number")
        .groupBy(month_key(F.col("value_date")).alias("month_key"), "branch_code")
        .agg((-F.sum("signed_amount")).cast("decimal(18,2)").alias("core_fee_income"))
    )
    joined = gl.join(core, ["month_key", "branch_code"], "full")
    diff = F.coalesce("gl_fee_income", F.lit(0)) - F.coalesce("core_fee_income", F.lit(0))
    return joined.select(
        "month_key",
        "branch_code",
        key("branch_code").alias("branch_key"),
        F.coalesce("gl_fee_income", F.lit(0)).cast("decimal(18,2)").alias("gl_fee_income"),
        F.coalesce("core_fee_income", F.lit(0)).cast("decimal(18,2)").alias("core_fee_income"),
        diff.cast("decimal(18,2)").alias("difference"),
        (F.abs(diff) > 0.005).alias("is_break"),
    )


# ---------------------------------------------------------------------- build


def sec_user_branch(
    spark, dim_br: DataFrame, grants: dict[str, list[str]] | None = None
) -> DataFrame:
    """Which user may see which branch, for dynamic row-level security in the semantic model.

    Every branch gets a fictional manager (manager.<branch code>@isbank.example). Extra
    grants (user principal name -> branch codes) come from a run parameter, so real
    identities used for demos never live in the repository. Names are lower-cased because
    the RLS rule compares them with LOWER(USERPRINCIPALNAME()).
    """
    managers = dim_br.select(
        F.concat(F.lit("manager."), F.lower("branch_code"), F.lit("@isbank.example")).alias(
            "user_upn"
        ),
        "branch_code",
    )
    extra = [(upn.strip().lower(), code) for upn, codes in (grants or {}).items() for code in codes]
    if extra:
        import pandas as pd

        managers = managers.unionByName(
            spark.createDataFrame(pd.DataFrame(extra, columns=["user_upn", "branch_code"]))
        )
    return (
        managers.join(dim_br.select("branch_code", "branch_key"), "branch_code")
        .select("user_upn", "branch_code", "branch_key")
        .dropDuplicates(["user_upn", "branch_code"])
    )


def build(
    spark,
    silver: dict[str, DataFrame],
    start: dt.date,
    end: dt.date,
    as_of: dt.date,
    grants: dict[str, list[str]] | None = None,
) -> dict[str, DataFrame]:
    """Every Gold table from the Silver tables; `grants` adds RLS branch access per user."""
    s = silver
    branches = dim_branch(s["branch"])
    accounts = dim_account(s["account"], s["party_xref"])
    users = app_user_customers(s["app_event"], s["transaction"], s["account"], s["party_xref"])
    return {
        "dim_date": dim_date(spark, start, end),
        "dim_branch": branches,
        "dim_product": dim_product(s["product"]),
        "dim_channel": dim_channel(s["transaction"]),
        "dim_customer": dim_customer(s["customer_history"], s["party_xref"], as_of),
        "dim_account": accounts,
        "dim_merchant": dim_merchant(s["card_authorisation"]),
        "fact_transaction": fact_transaction(s["transaction"], accounts),
        "fact_account_balance_monthly": fact_account_balance_monthly(s["transaction"], accounts),
        "fact_loan": fact_loan(
            accounts, s["loan_repayment"], s["loan_application"], s["bureau_score"], s["party_xref"]
        ),
        "fact_loan_payment": fact_loan_payment(s["loan_repayment"], accounts),
        "fact_card_transaction": fact_card_transaction(
            s["card_authorisation"], s["card"], accounts
        ),
        "fact_digital_event": fact_digital_event(s["app_event"], users),
        "fact_gl_entry": fact_gl_entry(s["gl_pnl"]),
        "recon_gl_fees": recon_gl_fees(s["gl_pnl"], s["transaction"], s["account"]),
        "app_user_link": users,
        "sec_user_branch": sec_user_branch(spark, branches, grants),
    }
