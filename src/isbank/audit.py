"""Run audit: one row per table per notebook run (what ran, row counts, status, duration).

Every pipeline step records its outcome here so runs can be checked and compared without
reading notebook logs. Rows are built as plain dicts; the notebook appends them to the
`run_audit` Delta table.
"""

import datetime as dt
from dataclasses import dataclass

AUDIT_TABLE = "run_audit"
AUDIT_SCHEMA = (
    "run_id string, notebook string, step string, table_name string, status string, "
    "input_files long, rows_written long, started_at timestamp, ended_at timestamp, "
    "duration_s double, message string"
)


@dataclass(frozen=True)
class StepOutcome:
    """The result of one step of a run (e.g. one Bronze source)."""

    table: str
    files: int
    rows: int
    started_at: dt.datetime
    ended_at: dt.datetime
    status: str = "SUCCEEDED"
    message: str = ""


def audit_rows(run_id: str, notebook: str, step: str, outcomes: list[StepOutcome]) -> list[dict]:
    """Audit rows for a run, ready for `spark.createDataFrame(rows, AUDIT_SCHEMA)`."""
    return [
        {
            "run_id": run_id,
            "notebook": notebook,
            "step": step,
            "table_name": o.table,
            "status": o.status,
            "input_files": o.files,
            "rows_written": o.rows,
            "started_at": o.started_at,
            "ended_at": o.ended_at,
            "duration_s": (o.ended_at - o.started_at).total_seconds(),
            "message": o.message[:2000],
        }
        for o in outcomes
    ]


ROWS_PER_FILE = 2_000_000


def output_files(rows: int, rows_per_file: int = ROWS_PER_FILE) -> int:
    """How many files to write for `rows` rows.

    Spark otherwise writes one file per shuffle partition (200 by default), so even a 15-row
    table becomes 200 tiny files, which slows every reader (the small-file problem).
    """
    return max(1, -(-rows // rows_per_file))
