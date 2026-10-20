"""Data quality rules: check, quarantine, and record, never silently drop.

A rule is a Spark SQL boolean expression that is TRUE for a valid row. A NULL result counts
as a failure, so a rule can't pass by accident on missing data. Each rule has a severity:

- `quarantine`: failing rows are removed from the clean output and written to quarantine
  with the rule name and the full record as JSON
- `warn`: failing rows stay in the clean output; the failure is only counted

Every rule's checked/failed counts are returned as result rows for the `dq_results` table.
"""

import datetime as dt
from dataclasses import dataclass

QUARANTINE_SCHEMA = (
    "run_id string, table_name string, rule_name string, record_key string, record string, "
    "quarantined_at timestamp"
)
RESULTS_SCHEMA = (
    "run_id string, table_name string, rule_name string, severity string, description string, "
    "rows_checked long, rows_failed long, checked_at timestamp"
)
SEVERITIES = frozenset({"quarantine", "warn"})


@dataclass(frozen=True)
class Rule:
    """One data quality rule for one table."""

    name: str
    condition: str
    severity: str = "quarantine"
    description: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.name}: severity must be one of {sorted(SEVERITIES)}")


@dataclass(frozen=True)
class DqOutcome:
    """Clean rows, quarantined rows, and per-rule result rows."""

    clean: object  # DataFrame
    quarantine: object  # DataFrame (QUARANTINE_SCHEMA)
    results: list[dict]


def apply_rules(
    df,
    table: str,
    rules: list[Rule],
    key_columns: list[str],
    run_id: str,
    now: dt.datetime | None = None,
) -> DqOutcome:
    """Evaluate `rules` on `df` in one pass and split it into clean and quarantined rows."""
    from pyspark.sql import functions as F

    now = now or dt.datetime.now(dt.UTC)
    flags = [
        F.coalesce(F.expr(r.condition), F.lit(False)).alias(f"__ok_{i}")
        for i, r in enumerate(rules)
    ]
    data_columns = df.columns
    checked = df.select("*", *flags).cache()

    totals = checked.agg(
        F.count(F.lit(1)).alias("n"),
        *[
            F.sum(F.when(~F.col(f"__ok_{i}"), 1).otherwise(0)).alias(f"__fail_{i}")
            for i in range(len(rules))
        ],
    ).first()
    results = [
        {
            "run_id": run_id,
            "table_name": table,
            "rule_name": r.name,
            "severity": r.severity,
            "description": r.description,
            "rows_checked": int(totals["n"]),
            "rows_failed": int(totals[f"__fail_{i}"] or 0),
            "checked_at": now,
        }
        for i, r in enumerate(rules)
    ]

    blocking = [i for i, r in enumerate(rules) if r.severity == "quarantine"]
    keep = F.lit(True)
    for i in blocking:
        keep = keep & F.col(f"__ok_{i}")
    clean = checked.where(keep).select(*data_columns)

    key = F.concat_ws("|", *[F.col(c).cast("string") for c in key_columns])
    record = F.to_json(F.struct(*[F.col(c) for c in data_columns]))
    parts = [
        checked.where(~F.col(f"__ok_{i}")).select(
            F.lit(run_id).alias("run_id"),
            F.lit(table).alias("table_name"),
            F.lit(rules[i].name).alias("rule_name"),
            key.alias("record_key"),
            record.alias("record"),
            F.lit(now).cast("timestamp").alias("quarantined_at"),
        )
        for i in blocking
    ]
    quarantine = (
        parts[0]
        if parts
        else checked.limit(0).select(
            F.lit(run_id).alias("run_id"),
            F.lit(table).alias("table_name"),
            F.lit("").alias("rule_name"),
            key.alias("record_key"),
            record.alias("record"),
            F.lit(now).cast("timestamp").alias("quarantined_at"),
        )
    )
    for part in parts[1:]:
        quarantine = quarantine.unionByName(part)
    return DqOutcome(clean=clean, quarantine=quarantine, results=results)
