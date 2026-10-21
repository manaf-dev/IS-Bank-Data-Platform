"""Silver transforms for finance, bureau, and reference sources.

- GL: turns the human-formatted P&L workbook (kept as raw cells in Bronze) into one row per
  period, cost centre, and GL account
- Credit bureau: parses the fixed-width weekly report by its published layout
- Reference: FX rates and the current compliance watchlist
"""

from pyspark.sql import functions as F

from isbank.dq import Rule
from isbank.silver.standardize import ghana_card, latest_per_key, name_key

# The bureau's published record layout (field, width), in file order.
BUREAU_LAYOUT = (
    ("record_type", 2),
    ("bureau_ref", 12),
    ("national_id", 10),
    ("surname", 25),
    ("first_name", 20),
    ("date_of_birth", 8),
    ("score", 3),
    ("external_facilities", 2),
    ("external_balance_ghs", 12),
    ("worst_dpd_24m", 3),
    ("enquiries_6m", 2),
    ("report_date", 8),
)


def gl_pnl(cells):
    """Unpivot P&L workbooks: header row names the cost centres; GL lines are 4-digit codes."""
    keys = ["_source_file", "sheet"]
    header_row = cells.where(
        (F.col("column_number") == 1) & (F.col("value") == "GL account")
    ).select(*keys, F.col("row_number").alias("header_row"))
    centres = (
        cells.join(header_row, keys)
        .where(
            (F.col("row_number") == F.col("header_row"))
            & (F.col("column_number") >= 3)
            & (F.col("value") != "TOTAL")
        )
        .select(*keys, "column_number", F.col("value").alias("cost_centre"))
    )
    lines = cells.where((F.col("column_number") == 1) & F.col("value").rlike(r"^\d{4}$")).select(
        *keys, "row_number", F.col("value").alias("gl_account")
    )
    labels = cells.where(F.col("column_number") == 2).select(
        *keys, "row_number", F.col("value").alias("gl_description")
    )
    amounts = cells.where(F.col("column_number") >= 3).select(
        *keys, "row_number", "column_number", F.col("value").alias("amount_text"), "_ingested_at"
    )
    rows = (
        amounts.join(lines, [*keys, "row_number"])
        .join(labels, [*keys, "row_number"], "left")
        .join(centres, [*keys, "column_number"])
        .select(
            F.regexp_extract("sheet", r"(\d{4}-\d{2})", 1).alias("period"),
            "cost_centre",
            "gl_account",
            "gl_description",
            F.col("amount_text").cast("decimal(18,2)").alias("amount_ghs"),
            "_ingested_at",
        )
    )
    latest = latest_per_key(
        rows, ["period", "cost_centre", "gl_account"], [F.col("_ingested_at").desc()]
    )
    return latest.withColumn("period_start", F.to_date(F.concat("period", F.lit("-01")))).drop(
        "_ingested_at"
    )


GL_RULES = [
    Rule("amount_parsed", "amount_ghs IS NOT NULL", description="Workbook amount parsed."),
    Rule(
        "period_parsed", "period_start IS NOT NULL", description="Period read from the sheet name."
    ),
]


def bureau_scores(lines):
    """Parse bureau records (type 01); header and trailer lines are dropped."""
    start = 1
    fields = []
    for name, width in BUREAU_LAYOUT:
        fields.append(F.trim(F.substring("raw", start, width)).alias(name))
        start += width
    parsed = lines.where(F.col("raw").startswith("01")).select(*fields, "_ingested_at")
    typed = parsed.select(
        "bureau_ref",
        ghana_card(F.col("national_id")).alias("ghana_card"),
        "surname",
        "first_name",
        name_key(F.concat_ws(" ", "first_name", "surname")).alias("name_key"),
        F.to_date("date_of_birth", "yyyyMMdd").alias("date_of_birth"),
        F.col("score").cast("int").alias("score"),
        F.col("external_facilities").cast("int").alias("external_facilities"),
        F.col("external_balance_ghs").cast("decimal(18,2)").alias("external_balance_ghs"),
        F.col("worst_dpd_24m").cast("int").alias("worst_dpd_24m"),
        F.col("enquiries_6m").cast("int").alias("enquiries_6m"),
        F.to_date("report_date", "yyyyMMdd").alias("report_date"),
        "_ingested_at",
    )
    latest = latest_per_key(typed, ["bureau_ref", "report_date"], [F.col("_ingested_at").desc()])
    return latest.drop("_ingested_at")


BUREAU_RULES = [
    Rule(
        "score_in_range",
        "score BETWEEN 300 AND 850",
        description="Score within the bureau's scale.",
    ),
    Rule("report_date_parsed", "report_date IS NOT NULL", description="Report date parsed."),
]


def fx_rates(bronze):
    """One GHS mid rate per currency per day."""
    typed = bronze.select(
        F.to_date("rate_date").alias("rate_date"),
        "base_currency",
        "quote_currency",
        F.col("mid_rate").cast("decimal(18,6)").alias("mid_rate"),
        "_ingested_at",
    )
    latest = latest_per_key(typed, ["rate_date", "base_currency"], [F.col("_ingested_at").desc()])
    return latest.drop("_ingested_at")


FX_RULES = [Rule("rate_positive", "mid_rate > 0", description="Exchange rates are positive.")]


def watchlist(bronze):
    """The current watchlist: entries in the latest monthly snapshot."""
    latest_snapshot = bronze.agg(F.max(F.to_date("snapshot_date")).alias("d"))
    current = bronze.where(F.to_date("snapshot_date") == latest_snapshot.first()["d"])
    return latest_per_key(
        current.select(
            "list_entry_id",
            "list_type",
            "full_name",
            name_key(F.col("full_name")).alias("name_key"),
            F.to_date("date_of_birth").alias("date_of_birth"),
            "nationality",
            ghana_card(F.col("id_number")).alias("ghana_card"),
            F.to_date("listed_on").alias("listed_on"),
            "source",
            F.to_date("snapshot_date").alias("snapshot_date"),
            "_ingested_at",
        ),
        ["list_entry_id"],
        [F.col("_ingested_at").desc()],
    ).drop("_ingested_at")


WATCHLIST_RULES = [Rule("has_name", "full_name IS NOT NULL", description="Every entry has a name.")]
