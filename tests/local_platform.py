"""Build Bronze and Silver locally from a generated world (shared by Silver and Gold tests)."""

import datetime as dt
from fnmatch import fnmatch
from pathlib import Path

import pandas as pd

from generator.emit.local import LandingWriter
from generator.projectors import (
    card_switch,
    compliance,
    core_banking,
    credit_bureau,
    general_ledger,
    loan_origination,
    mobile_banking,
    reference_data,
)
from generator.timeline import snapshot
from isbank.bronze.ingest import read_files, with_metadata
from isbank.bronze.sources import enabled_sources
from isbank.silver import pipeline

TODAY = dt.date(2025, 12, 31)
EVENTS_FROM = pd.Timestamp("2025-12-25")


def build_silver(spark, world, root: Path):
    """Land a realistic slice of every source, read it as Bronze, and run the Silver pipeline."""
    w = LandingWriter(root=root)
    w.write(core_banking.snapshot_batch(world, snapshot(world, TODAY)), f"full_{TODAY:%Y%m%d}")
    w.write(loan_origination.extracts(world, None, TODAY), "")
    w.write(credit_bureau.weekly_reports(world, None, TODAY), "")
    w.write(general_ledger.monthly_statements(world, None, TODAY), "")
    w.write(compliance.monthly_lists(world, dt.date(2025, 10, 31), TODAY), "")
    w.write(reference_data.fx_rates(world, dt.date(2025, 12, 1), TODAY), "")
    w.write(card_switch.events_delivered(world, EVENTS_FROM, pd.Timestamp(TODAY)), "")
    w.write(mobile_banking.events_delivered(world, EVENTS_FROM, pd.Timestamp(TODAY)), "")

    files = [p for p in root.rglob("*") if p.is_file()]

    def first_line(uri: str) -> str:
        path = Path(uri.replace("file:///", "").replace("file:/", ""))
        with open(path, encoding="utf-8") as f:
            return f.readline()

    bronze = {}
    for spec in enabled_sources():
        paths = [p.as_uri() for p in files if fnmatch(p.relative_to(root).as_posix(), spec.pattern)]
        df = read_files(spark, spec, paths, first_line)
        bronze[spec.table] = with_metadata(df, spec.system, "b", dt.datetime(2026, 1, 1)).cache()

    written = {}
    run = pipeline.run(spark, bronze.__getitem__, written.__setitem__, "run-1")
    return run, written
