"""Silver transforms for core banking: customer history (SCD2), accounts, transactions.

Inputs are the Bronze tables as loaded (full snapshot plus daily CDC files, so the same key
can appear many times). Outputs are typed, standardised, and deduplicated.
"""

from pyspark.sql import Window
from pyspark.sql import functions as F

from isbank.dq import Rule
from isbank.silver.standardize import ghana_card, latest_per_key, mobile_number, person_name

PLACEHOLDER_DOB = "1900-01-01"


def _load_date():
    """The load a row came from: the date in full_YYYYMMDD or cdc_YYYYMMDD in its path."""
    return F.regexp_extract(F.col("_source_file"), r"/(full|cdc)_(\d{8})/", 2)


def customer_history(bronze):
    """SCD2 history per CIF: one row per distinct version, with valid_from/valid_to/is_current.

    A version is identified by `updated_at`; the same version delivered in several loads is
    kept once. `valid_to` is the next version's `valid_from` (NULL for the current one).
    """
    typed = bronze.select(
        F.col("cif_number"),
        F.col("customer_type"),
        F.col("first_name"),
        F.col("middle_name"),
        F.col("surname"),
        person_name(
            F.concat_ws(" ", F.col("first_name"), F.col("middle_name"), F.col("surname"))
        ).alias("full_name_std"),
        F.col("gender"),
        F.to_date("date_of_birth").alias("date_of_birth_raw"),
        ghana_card(F.col("ghana_card_no")).alias("ghana_card"),
        mobile_number(F.col("mobile_no")).alias("mobile_number"),
        F.lower(F.trim("email")).alias("email"),
        F.col("digital_address"),
        F.col("branch_code"),
        F.to_timestamp("created_at").alias("created_at"),
        F.to_timestamp("updated_at").alias("valid_from"),
        F.col("_ingested_at"),
    )
    versions = latest_per_key(typed, ["cif_number", "valid_from"], [F.col("_ingested_at").desc()])
    window = Window.partitionBy("cif_number").orderBy("valid_from")
    placeholder = F.col("date_of_birth_raw") == F.lit(PLACEHOLDER_DOB).cast("date")
    return (
        versions.withColumn("valid_to", F.lead("valid_from").over(window))
        .withColumn("is_current", F.col("valid_to").isNull())
        .withColumn("dob_is_placeholder", F.coalesce(placeholder, F.lit(False)))
        .withColumn("date_of_birth", F.when(~placeholder, F.col("date_of_birth_raw")))
        .drop("date_of_birth_raw", "_ingested_at")
    )


CUSTOMER_RULES = [
    Rule("cif_present", "cif_number IS NOT NULL", description="Every customer row has a CIF."),
    Rule(
        "dob_not_placeholder",
        "NOT dob_is_placeholder",
        severity="warn",
        description="Date of birth is a 1900-01-01 placeholder (kept, DOB set to NULL).",
    ),
    Rule(
        "ghana_card_present",
        "customer_type = 'CORPORATE' OR ghana_card IS NOT NULL",
        severity="warn",
        description="Individual without a valid Ghana Card (KYC gap).",
    ),
]


def accounts(bronze):
    """Latest state of each account: the row from the most recent load wins."""
    typed = bronze.select(
        "account_number",
        "cif_number",
        "product_code",
        "branch_code",
        "currency",
        F.to_date("opened_date").alias("opened_date"),
        "status",
        F.to_date("closed_date").alias("closed_date"),
        "linked_account_number",
        F.col("contract_amount").cast("decimal(18,2)").alias("contract_amount"),
        F.col("interest_rate_pct").cast("decimal(9,4)").alias("interest_rate_pct"),
        F.col("term_months").cast("int").alias("term_months"),
        F.to_date("maturity_date").alias("maturity_date"),
        _load_date().alias("_load_date"),
        F.col("_ingested_at"),
    )
    latest = latest_per_key(
        typed, ["account_number"], [F.col("_load_date").desc(), F.col("_ingested_at").desc()]
    )
    return latest.drop("_load_date", "_ingested_at")


ACCOUNT_RULES = [
    Rule("account_has_customer", "cif_number IS NOT NULL", description="Account has an owner CIF."),
    Rule(
        "closed_after_opened",
        "closed_date IS NULL OR closed_date >= opened_date",
        description="An account cannot close before it opens.",
    ),
]


def transactions(bronze):
    """Typed transactions with signed amounts and a duplicate rank per business key.

    Double postings share account, timestamp, code, amount, direction, and narration but have
    different references. `duplicate_rank` is 1 for the first copy; the DQ rule quarantines
    the rest.
    """
    typed = bronze.select(
        "txn_ref",
        "account_number",
        F.to_date("value_date").alias("value_date"),
        F.to_timestamp("booking_ts").alias("booking_ts"),
        "txn_code",
        "dr_cr",
        F.col("amount").cast("decimal(18,2)").alias("amount"),
        F.when(F.col("dr_cr") == "DR", -F.col("amount"))
        .otherwise(F.col("amount"))
        .cast("decimal(18,2)")
        .alias("signed_amount"),
        F.col("running_balance").cast("decimal(18,2)").alias("running_balance"),
        "channel",
        "narration",
        "counterparty_account",
        F.col("_ingested_at"),
    )
    once = latest_per_key(typed, ["txn_ref"], [F.col("_ingested_at").desc()])
    business_key = ["account_number", "booking_ts", "txn_code", "dr_cr", "amount", "narration"]
    window = Window.partitionBy(*business_key).orderBy("txn_ref")
    return once.withColumn("duplicate_rank", F.row_number().over(window)).drop("_ingested_at")


TRANSACTION_RULES = [
    Rule(
        "not_double_posted",
        "duplicate_rank = 1",
        description="Same account, time, code, amount, and narration as an earlier posting.",
    ),
    Rule("amount_positive", "amount > 0", description="Posted amounts are positive with DR/CR."),
    Rule("known_direction", "dr_cr IN ('DR', 'CR')", description="Direction is debit or credit."),
]
