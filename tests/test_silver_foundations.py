"""DQ framework and standardisers (local Spark)."""

import datetime as dt

import pytest

from isbank.dq import Rule, apply_rules

pytestmark = pytest.mark.spark


def test_ghana_card_variants_normalise(spark):
    from pyspark.sql import functions as F

    from isbank.silver.standardize import ghana_card

    values = [
        "GHA-123456789-0",
        "GHA1234567890",
        "gha-123456789-0",
        "1234567890",
        "GHA 123456789 0",
        "GHA-12345-0",
        None,
    ]
    df = spark.createDataFrame([(v,) for v in values], "v string")
    out = [r.c for r in df.select(ghana_card(F.col("v")).alias("c")).collect()]
    assert out == ["GHA-123456789-0"] * 5 + [None, None]


def test_mobile_numbers_normalise(spark):
    from pyspark.sql import functions as F

    from isbank.silver.standardize import mobile_number

    values = ["0244123456", "+233244123456", "233 24 412 3456", "024412345", None]
    df = spark.createDataFrame([(v,) for v in values], "v string")
    out = [r.c for r in df.select(mobile_number(F.col("v")).alias("c")).collect()]
    assert out == ["0244123456"] * 3 + [None, None]


def test_amount_strings_parse(spark):
    from decimal import Decimal

    from pyspark.sql import functions as F

    from isbank.silver.standardize import amount

    df = spark.createDataFrame([("3,500.00",), (" 1 200.5 ",), ("abc",), (None,)], "v string")
    out = [r.a for r in df.select(amount(F.col("v")).alias("a")).collect()]
    assert out == [Decimal("3500.00"), Decimal("1200.50"), None, None]


def test_person_names_compare_across_formats(spark):
    from pyspark.sql import functions as F

    from isbank.silver.standardize import person_name

    df = spark.createDataFrame(
        [("MENSAH, Kwame",), ("Kwame  Mensah",), ("Naa Dedei O'Quaye",)], "v string"
    )
    out = [r.n for r in df.select(person_name(F.col("v")).alias("n")).collect()]
    assert out == ["KWAME MENSAH", "KWAME MENSAH", "NAA DEDEI OQUAYE"]


def test_latest_per_key(spark):
    from pyspark.sql import functions as F

    from isbank.silver.standardize import latest_per_key

    df = spark.createDataFrame([("a", 1), ("a", 3), ("b", 2)], "k string, v int")
    out = sorted((r.k, r.v) for r in latest_per_key(df, ["k"], [F.col("v").desc()]).collect())
    assert out == [("a", 3), ("b", 2)]


def test_dq_quarantines_and_counts(spark):
    df = spark.createDataFrame(
        [(1, "1990-01-01", 10.0), (2, "1900-01-01", 5.0), (3, None, -1.0)],
        "id int, dob string, amount double",
    )
    rules = [
        Rule("dob_not_placeholder", "dob <> '1900-01-01'", description="placeholder DOB"),
        Rule("amount_non_negative", "amount >= 0", severity="warn"),
    ]
    out = apply_rules(df, "t", rules, ["id"], "run-1", now=dt.datetime(2026, 1, 1))
    assert sorted(r.id for r in out.clean.collect()) == [
        1
    ]  # row 2: placeholder DOB; row 3: NULL DOB counts as a failure
    q = out.quarantine.collect()
    assert sorted((r.rule_name, r.record_key) for r in q) == [
        ("dob_not_placeholder", "2"),
        ("dob_not_placeholder", "3"),
    ]
    results = {r["rule_name"]: (r["rows_checked"], r["rows_failed"]) for r in out.results}
    assert results == {"dob_not_placeholder": (3, 2), "amount_non_negative": (3, 1)}


def test_rule_rejects_unknown_severity():
    with pytest.raises(ValueError, match="severity"):
        Rule("x", "true", severity="drop")
