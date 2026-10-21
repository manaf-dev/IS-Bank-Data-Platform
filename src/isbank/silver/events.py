"""Silver transforms for streaming-style sources: card authorisations and mobile app events.

Bronze keeps each JSON line as received. Silver parses it with an explicit schema (so drift
shows up as NULLs, not failures), removes duplicate deliveries by event id, and flags late
arrivals: events filed in a later delivery day than the day they happened.
"""

from pyspark.sql import functions as F

from isbank.dq import Rule
from isbank.silver.standardize import latest_per_key

CARD_SCHEMA = (
    "event_id string, mti string, processing_code string, pan_masked string, card_token string, "
    "stan string, rrn string, amount_minor long, currency_code string, txn_datetime string, "
    "mcc string, merchant_id string, merchant_name string, merchant_city string, "
    "terminal_id string, entry_mode string, response_code string"
)
APP_SCHEMA = (
    "event_id string, event_type string, event_ts string, user_id string, device_id string, "
    "device_os string, app_version string, ip_address string, geo_city string, status string, "
    "amount_minor long, beneficiary_account string, beneficiary_bank string, "
    "biometric_used boolean"
)


def _delivery_date():
    """The delivery day a line was filed under (yyyy/mm/dd in its landing path)."""
    parts = F.regexp_extract(F.col("_source_file"), r"/(\d{4})/(\d{2})/(\d{2})/[^/]+$", 0)
    return F.to_date(F.regexp_replace(F.substring(parts, 2, 10), "/", "-"))


def _parse(bronze, schema: str):
    parsed = bronze.select(
        F.from_json("raw", schema).alias("e"),
        F.col("raw"),
        _delivery_date().alias("delivery_date"),
        F.col("_ingested_at"),
        F.col("_source_file"),
    )
    return parsed.select("e.*", "raw", "delivery_date", "_ingested_at", "_source_file")


def card_authorisations(bronze):
    """One row per card authorisation event, first delivery wins."""
    e = _parse(bronze, CARD_SCHEMA)
    typed = e.select(
        "event_id",
        F.to_timestamp("txn_datetime").alias("event_ts"),
        "mti",
        F.when(F.col("processing_code") == "010000", "CASH_WITHDRAWAL")
        .otherwise("PURCHASE")
        .alias("txn_kind"),
        "pan_masked",
        "card_token",
        "stan",
        "rrn",
        (F.col("amount_minor") / 100).cast("decimal(18,2)").alias("amount"),
        "currency_code",
        "mcc",
        "merchant_id",
        "merchant_name",
        "merchant_city",
        "terminal_id",
        F.when(F.col("entry_mode") == "81", "ECOMMERCE")
        .when(F.col("entry_mode") == "07", "CONTACTLESS")
        .when(F.col("entry_mode") == "05", "CHIP")
        .otherwise("OTHER")
        .alias("entry_mode"),
        "response_code",
        (F.col("response_code") == "00").alias("is_approved"),
        "delivery_date",
        "_ingested_at",
        "_source_file",
        "raw",
    )
    first = latest_per_key(typed, ["event_id"], [F.col("_ingested_at"), F.col("_source_file")])
    return first.withColumn("is_late", F.col("delivery_date") > F.to_date("event_ts")).drop(
        "_ingested_at", "_source_file"
    )


CARD_RULES = [
    Rule(
        "event_parsed", "event_id IS NOT NULL AND event_ts IS NOT NULL", description="JSON parsed."
    ),
    Rule("amount_positive", "amount > 0", description="Authorisation amount is positive."),
    Rule(
        "known_response",
        "response_code IN ('00', '05', '51')",
        severity="warn",
        description="Response code is one the platform understands.",
    ),
]


def app_events(bronze):
    """One row per mobile banking event, first delivery wins; app 3.0 adds biometric_used."""
    e = _parse(bronze, APP_SCHEMA)
    typed = e.select(
        "event_id",
        "event_type",
        F.to_timestamp("event_ts").alias("event_ts"),
        "user_id",
        "device_id",
        "device_os",
        "app_version",
        F.split("app_version", r"\.").getItem(0).cast("int").alias("app_major_version"),
        "ip_address",
        "geo_city",
        "status",
        (F.col("amount_minor") / 100).cast("decimal(18,2)").alias("amount"),
        "beneficiary_account",
        "beneficiary_bank",
        "biometric_used",
        "delivery_date",
        "_ingested_at",
        "_source_file",
        "raw",
    )
    first = latest_per_key(typed, ["event_id"], [F.col("_ingested_at"), F.col("_source_file")])
    return first.withColumn("is_late", F.col("delivery_date") > F.to_date("event_ts")).drop(
        "_ingested_at", "_source_file"
    )


APP_RULES = [
    Rule(
        "event_parsed", "event_id IS NOT NULL AND event_ts IS NOT NULL", description="JSON parsed."
    ),
    Rule(
        "biometric_only_from_v3",
        "biometric_used IS NULL OR app_major_version >= 3",
        severity="warn",
        description="biometric_used only exists from app 3.0 (schema drift check).",
    ),
]
