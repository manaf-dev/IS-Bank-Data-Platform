"""Silver transforms for the loan origination system (LOS): applications and repayments.

LOS extracts store amounts as formatted text, renamed `monthly_income` to
`net_monthly_income` mid-history, file an application twice when the decision comes days
later, and are sometimes delivered twice. Silver resolves all of that to one typed row per
application and per instalment.
"""

from pyspark.sql import functions as F

from isbank.dq import Rule
from isbank.silver.standardize import amount, ghana_card, latest_per_key, mobile_number, name_key

_EXTRACT_DATE = r"_(\d{8})\.csv$"


def _extract_date():
    return F.to_date(F.regexp_extract(F.col("_source_file"), _EXTRACT_DATE, 1), "yyyyMMdd")


def applications(bronze):
    """Latest state of each application: a decision outranks PENDING, then the latest extract."""
    income = F.coalesce(
        *[F.col(c) for c in ("net_monthly_income", "monthly_income") if c in bronze.columns]
    )
    typed = bronze.select(
        "application_ref",
        F.col("applicant_name"),
        name_key(F.col("applicant_name")).alias("applicant_name_key"),
        ghana_card(F.col("applicant_ghana_card")).alias("ghana_card"),
        F.to_date("date_of_birth", "dd/MM/yyyy").alias("date_of_birth"),
        mobile_number(F.col("mobile_no")).alias("mobile_number"),
        "employer_type",
        amount(income).alias("monthly_income"),
        "product",
        amount(F.col("requested_amount")).alias("requested_amount"),
        amount(F.col("approved_amount")).alias("approved_amount"),
        F.col("tenor_months").cast("int").alias("tenor_months"),
        F.col("interest_rate_pct").cast("decimal(9,4)").alias("interest_rate_pct"),
        "branch_code",
        "collateral_type",
        F.to_date("application_date").alias("application_date"),
        F.to_date("decision_date").alias("decision_date"),
        "status",
        "decline_reason",
        "loan_account_number",
        _extract_date().alias("extract_date"),
        F.col("_ingested_at"),
    )
    decided_first = F.when(F.col("status") == "PENDING", 1).otherwise(0)
    latest = latest_per_key(
        typed,
        ["application_ref"],
        [decided_first, F.col("extract_date").desc(), F.col("_ingested_at").desc()],
    )
    return latest.drop("_ingested_at")


APPLICATION_RULES = [
    Rule(
        "income_parsed", "monthly_income IS NOT NULL", description="Income text parsed to a number."
    ),
    Rule(
        "ghana_card_valid",
        "ghana_card IS NOT NULL",
        severity="warn",
        description="Applicant Ghana Card normalises to GHA-XXXXXXXXX-X.",
    ),
    Rule(
        "approved_has_loan_account",
        "status <> 'APPROVED' OR loan_account_number IS NOT NULL",
        description="An approved application links to a core loan account.",
    ),
]


def repayments(bronze):
    """One row per loan instalment (resent files collapse), with paid/missed flags."""
    typed = bronze.select(
        "loan_account_number",
        F.col("installment_no").cast("int").alias("installment_no"),
        F.to_date("due_date").alias("due_date"),
        amount(F.col("amount_due")).alias("amount_due"),
        amount(F.col("amount_paid")).alias("amount_paid"),
        "payment_status",
        (F.col("payment_status") == "MISSED").alias("is_missed"),
        F.col("_ingested_at"),
    )
    latest = latest_per_key(
        typed, ["loan_account_number", "installment_no"], [F.col("_ingested_at").desc()]
    )
    return latest.drop("_ingested_at")


REPAYMENT_RULES = [
    Rule(
        "amounts_parsed",
        "amount_due IS NOT NULL AND amount_paid IS NOT NULL",
        description="Amounts parse.",
    ),
    Rule(
        "paid_not_above_due",
        "amount_paid <= amount_due",
        severity="warn",
        description="An instalment is never overpaid.",
    ),
]
