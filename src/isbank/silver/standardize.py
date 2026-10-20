"""Column standardisers shared by Silver transforms (Spark DataFrame API, no UDFs).

Each source formats the same thing its own way; these bring values to one canonical form so
records can be compared and joined.
"""

from pyspark.sql import Column, Window
from pyspark.sql import functions as F


def ghana_card(col: Column) -> Column:
    """Canonical `GHA-XXXXXXXXX-X` from any variant (no dashes, lower case, spaced, digits only).

    Returns NULL unless exactly ten digits remain, so malformed IDs never match anything.
    """
    digits = F.regexp_replace(F.upper(F.coalesce(col, F.lit(""))), r"[^0-9]", "")
    canonical = F.concat(
        F.lit("GHA-"), F.substring(digits, 1, 9), F.lit("-"), F.substring(digits, 10, 1)
    )
    return F.when(F.length(digits) == 10, canonical)


def mobile_number(col: Column) -> Column:
    """National `0XXXXXXXXX` from national, +233, or spaced 233 formats (else NULL)."""
    digits = F.regexp_replace(F.coalesce(col, F.lit("")), r"[^0-9]", "")
    international = (F.length(digits) == 12) & digits.startswith("233")
    national = (F.length(digits) == 10) & digits.startswith("0")
    return F.when(international, F.concat(F.lit("0"), F.substring(digits, 4, 9))).when(
        national, digits
    )


def amount(col: Column, scale: int = 2) -> Column:
    """Decimal from text amounts such as '3,500.00' or ' 1 200.5 ' (else NULL)."""
    cleaned = F.regexp_replace(F.trim(col.cast("string")), r"[,\s]", "")
    return F.when(cleaned.rlike(r"^-?\d+(\.\d+)?$"), cleaned.cast(f"decimal(18,{scale})"))


def person_name(col: Column) -> Column:
    """Upper-case name with punctuation removed and spaces collapsed.

    `SURNAME, First Middle` becomes `FIRST MIDDLE SURNAME`, so the order matches the
    `First Surname` style used elsewhere.
    """
    upper = F.upper(F.trim(col))
    swapped = F.when(
        upper.contains(","),
        F.concat_ws(
            " ", F.trim(F.split(upper, ",").getItem(1)), F.trim(F.split(upper, ",").getItem(0))
        ),
    ).otherwise(upper)
    letters = F.regexp_replace(swapped, r"[^A-Z ]", "")
    return F.trim(F.regexp_replace(letters, r"\s+", " "))


def latest_per_key(df, keys: list[str], order_by: list[Column]):
    """Keep one row per key: the first row by `order_by` (pass descending columns for latest)."""
    window = Window.partitionBy(*keys).orderBy(*order_by)
    return df.withColumn("__rn", F.row_number().over(window)).where("__rn = 1").drop("__rn")
