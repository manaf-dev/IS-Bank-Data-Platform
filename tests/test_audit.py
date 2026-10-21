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


def test_output_files_sizes_by_rows():
    from isbank.audit import output_files

    assert output_files(0) == 1
    assert output_files(15) == 1
    assert output_files(2_000_000) == 1
    assert output_files(2_000_001) == 2
    assert output_files(7_000_000) == 4
