import datetime as dt

from isbank.audit import StepOutcome, audit_rows


def test_audit_rows_carry_duration_and_status():
    t0 = dt.datetime(2026, 1, 1, 10, 0, tzinfo=dt.UTC)
    rows = audit_rows(
        "run-1",
        "nb_bronze_ingest",
        "bronze",
        [
            StepOutcome("core_customers", 2, 100, t0, t0 + dt.timedelta(seconds=30)),
            StepOutcome("los_applications", 0, 0, t0, t0, status="FAILED", message="x" * 5000),
        ],
    )
    assert rows[0]["duration_s"] == 30
    assert rows[0]["status"] == "SUCCEEDED"
    assert rows[1]["status"] == "FAILED"
    assert len(rows[1]["message"]) == 2000
