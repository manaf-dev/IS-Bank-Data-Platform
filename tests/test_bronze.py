"""Bronze ingestion: pure rules, plus a local Spark run over real generated landing files."""

import datetime as dt
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from generator.config import load_config
from generator.emit.local import LandingWriter
from generator.projectors import (
    card_switch,
    core_banking,
    credit_bureau,
    general_ledger,
    loan_origination,
)
from generator.timeline import snapshot
from generator.world import build_world
from isbank.bronze.files import LandedFile, batch_id, group_by_header, new_files, workbook_cells
from isbank.bronze.ingest import METADATA_COLUMNS, ingest_source, relative_key
from isbank.bronze.sources import SOURCES, enabled_sources

# ---------------------------------------------------------------- pure rules


def test_new_files_skips_ingested_and_sorts():
    files = [LandedFile("b", 1, 1), LandedFile("a", 1, 1), LandedFile("c", 1, 1)]
    assert [f.path for f in new_files(files, {"c"})] == ["a", "b"]


def test_new_files_compares_by_key():
    files = [LandedFile("abfss://x/Files/landing/a.csv", 1, 1)]
    assert new_files(files, {"a.csv"}, key=relative_key) == []
    assert len(new_files(files, {"a.csv"})) == 1  # full path differs from the logged key


def test_batch_id_is_stable_and_content_sensitive():
    a = [LandedFile("x", 1, 1), LandedFile("y", 2, 2)]
    assert batch_id("t", a) == batch_id("t", list(reversed(a)))
    assert batch_id("t", a) != batch_id("t", [LandedFile("x", 1, 1), LandedFile("y", 3, 2)])
    assert batch_id("t", a) != batch_id("u", a)


def test_group_by_header_separates_schema_versions():
    headers = {"a.csv": "id,income\n", "b.csv": "id,net_income\n", "c.csv": "id,income\n"}
    groups = group_by_header(headers, headers.__getitem__)
    assert groups == {"id,income": ["a.csv", "c.csv"], "id,net_income": ["b.csv"]}


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/landing/crm/x.csv", "crm/x.csv"),
        ("file:/C:/tmp/out/landing/core_banking/full_1/a.parquet", "core_banking/full_1/a.parquet"),
        ("C:\\tmp\\landing\\gl\\b.xlsx", "gl/b.xlsx"),
    ],
)
def test_relative_key(path, key):
    assert relative_key(path) == key


def test_relative_key_requires_landing_folder():
    with pytest.raises(ValueError, match="landing"):
        relative_key("/tmp/elsewhere/a.csv")


def test_registry_is_consistent():
    tables = [s.table for s in SOURCES]
    assert len(tables) == len(set(tables))
    assert all(not s.table.startswith("crm_") for s in enabled_sources())


# ---------------------------------------------------------------- local Spark run


class MemoryStore:
    """In-memory BronzeStore: rows per table, replaced by source file like the Delta store."""

    def __init__(self) -> None:
        self.tables: dict[str, pd.DataFrame] = {}
        self.file_log: list[dict] = []
        self.fail_next_log = False

    def ingested_keys(self, table: str) -> set[str]:
        return {r["relative_path"] for r in self.file_log if r["table_name"] == table}

    def write(self, df, table: str, source_files: list[str]) -> None:
        new = df.toPandas()
        old = self.tables.get(table)
        if old is not None:
            old = old[~old["_source_file"].isin(source_files)]
            new = pd.concat([old, new], ignore_index=True)
        self.tables[table] = new

    def log(self, rows: list[dict]) -> None:
        if self.fail_next_log:
            self.fail_next_log = False
            raise RuntimeError("simulated crash after the data write")
        self.file_log.extend(rows)


def _local_first_line(path: str) -> str:
    parsed = urlparse(path)
    local = unquote(parsed.path) if parsed.scheme == "file" else path
    if local.startswith("/") and len(local) > 2 and local[2] == ":":
        local = local[1:]
    with open(local, encoding="utf-8") as f:
        return f.readline()


@pytest.fixture(scope="module")
def landing(tmp_path_factory) -> Path:
    world = build_world(load_config(profile="dev"))
    out = tmp_path_factory.mktemp("bronze") / "landing"
    writer = LandingWriter(root=out)
    writer.write(
        core_banking.snapshot_batch(world, snapshot(world, dt.date(2023, 1, 31))), "full_20230131"
    )
    writer.write(loan_origination.extracts(world, dt.date(2024, 5, 31), dt.date(2024, 7, 31)), "")
    writer.write(
        card_switch.events_delivered(world, pd.Timestamp("2025-03-01"), pd.Timestamp("2025-03-03")),
        "",
    )
    writer.write(
        general_ledger.monthly_statements(world, dt.date(2024, 11, 30), dt.date(2024, 12, 31)), ""
    )
    writer.write(credit_bureau.weekly_reports(world, dt.date(2025, 3, 2), dt.date(2025, 3, 9)), "")
    return out


def _files(root: Path) -> list[LandedFile]:
    return [
        LandedFile(
            path=p.as_uri(), size=p.stat().st_size, modified_ms=int(p.stat().st_mtime * 1000)
        )
        for p in root.rglob("*")
        if p.is_file()
    ]


SPECS = {s.table: s for s in SOURCES}
TABLES = [
    "core_customers",
    "core_transactions",
    "los_applications",
    "card_authorisations",
    "gl_pnl_cells",
    "bureau_report_lines",
]


@pytest.fixture(scope="module")
def ingested(spark, landing):
    store = MemoryStore()
    results = {
        t: ingest_source(
            spark, SPECS[t], str(landing), store, _local_first_line, files=_files(landing)
        )
        for t in TABLES
    }
    return store, results


@pytest.mark.spark
def test_every_table_lands_with_metadata(ingested, landing):
    store, results = ingested
    for table in TABLES:
        frame = store.tables[table]
        assert results[table].files > 0, table
        assert len(frame) == results[table].rows > 0, table
        assert list(frame.columns[-4:]) == list(METADATA_COLUMNS), table
        assert frame["_batch_id"].str.startswith(table).all()
    logged = {r["relative_path"] for r in store.file_log}
    on_disk = {relative_key(f.path) for f in _files(landing)}
    assert {
        k
        for k in on_disk
        if not k.startswith(("loan_origination", "card", "core", "general", "credit"))
    } == set()
    assert logged <= on_disk


@pytest.mark.spark
def test_csv_schema_drift_keeps_both_columns(ingested):
    apps = ingested[0].tables["los_applications"]
    assert {"monthly_income", "net_monthly_income"} <= set(apps.columns)
    before = apps[apps["_source_file"].str.contains("2024/06/")]
    after = apps[apps["_source_file"].str.contains("2024/07/")]
    assert before["monthly_income"].notna().all()
    assert before["net_monthly_income"].isna().all()
    assert after["net_monthly_income"].notna().all()
    assert after["monthly_income"].isna().all()


@pytest.mark.spark
def test_raw_lines_are_kept_exactly(ingested, landing):
    cards = ingested[0].tables["card_authorisations"]
    lines = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in landing.rglob("*.jsonl"))
    assert len(cards) == lines
    assert cards["raw"].str.startswith('{"event_id"').all()
    bureau = ingested[0].tables["bureau_report_lines"]
    assert bureau["raw"].str.startswith("HDR").any()
    assert bureau["raw"].str.startswith("TRL").any()


@pytest.mark.spark
def test_workbook_is_kept_as_raw_cells(ingested):
    cells = ingested[0].tables["gl_pnl_cells"]
    assert (cells["value"] == "IS BANK - Management P&L").any()
    assert {"sheet", "row_number", "column_number", "value"} <= set(cells.columns)


@pytest.mark.spark
def test_rerun_ingests_nothing_new(spark, ingested, landing):
    store = ingested[0]
    before = {t: len(store.tables[t]) for t in TABLES}
    result = ingest_source(
        spark,
        SPECS["los_applications"],
        str(landing),
        store,
        _local_first_line,
        files=_files(landing),
    )
    assert result.files == 0
    assert {t: len(store.tables[t]) for t in TABLES} == before


@pytest.mark.spark
def test_crash_between_write_and_log_does_not_duplicate(spark, landing):
    store = MemoryStore()
    spec = SPECS["los_repayments"]
    store.fail_next_log = True
    with pytest.raises(RuntimeError, match="simulated crash"):
        ingest_source(spark, spec, str(landing), store, _local_first_line, files=_files(landing))
    rows_after_crash = len(store.tables[spec.table])
    ingest_source(
        spark, spec, str(landing), store, _local_first_line, files=_files(landing), chunk=2
    )
    assert len(store.tables[spec.table]) == rows_after_crash
    assert store.tables[spec.table]["_source_file"].nunique() == len(store.file_log)


def test_workbook_cells_reads_generated_workbook(landing):
    book = next(landing.rglob("*.xlsx"))
    cells = workbook_cells(book.read_bytes())
    assert "Amounts in GHS" in {v for *_, v in cells}
