"""Emitters and the answer key: formats, idempotency, duplicates, and separation."""

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import load_workbook

from generator import answer_key
from generator.config import load_config
from generator.emit.local import LandingWriter
from generator.emit.run import State, backfill, new_state, tick
from generator.projectors.credit_bureau import LAYOUT
from generator.world import World, build_world

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def world() -> World:
    return build_world(load_config(profile="dev"))


@pytest.fixture(scope="module")
def landing(world: World, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("out")
    writer = LandingWriter(root=out / "landing", redirect={"crm": out / "drive"})
    backfill(world, writer, out / "answer_key")
    new_state(world.config).save(out)
    return out


def files(root: Path, pattern: str) -> list[Path]:
    return sorted(root.rglob(pattern))


def test_every_source_lands(landing: Path):
    systems = {p.name for p in (landing / "landing").iterdir()}
    assert systems == {
        "core_banking",
        "card_switch",
        "mobile_banking",
        "loan_origination",
        "general_ledger",
        "credit_bureau",
        "compliance",
        "reference_data",
    }
    assert files(landing / "drive", "crm_contacts_*.csv"), "CRM is redirected to the drive folder"


def test_jsonl_omits_null_fields(landing: Path):
    lines = [
        json.loads(line)
        for f in files(landing / "landing" / "mobile_banking", "*.jsonl")[:50]
        for line in f.read_text(encoding="utf-8").splitlines()
    ]
    assert lines
    assert all(v is not None for record in lines for v in record.values())
    assert any("biometric_used" not in r for r in lines)


def test_fixed_width_records_have_constant_length(landing: Path):
    width = sum(w for _, w in LAYOUT)
    report = files(landing / "landing" / "credit_bureau", "*.txt")[-1]
    lines = report.read_text(encoding="ascii").splitlines()
    assert lines[0].startswith("HDR")
    assert lines[-1].startswith("TRL")
    assert all(len(line) == width for line in lines[1:-1])
    assert int(lines[-1][3:]) == len(lines) - 2


def test_gl_workbook_is_human_formatted(landing: Path):
    book = files(landing / "landing" / "general_ledger", "*.xlsx")[-1]
    ws = load_workbook(book).active
    assert "IS BANK" in str(ws["A1"].value)
    assert ws["A3"].value == "Amounts in GHS"
    labels = [ws.cell(row=r, column=2).value for r in range(1, ws.max_row + 1)]
    assert "Net profit / (loss)" in labels


def test_resent_files_appear_twice(landing: Path):
    names = [p.name for p in files(landing / "landing" / "loan_origination", "*.csv")]
    assert pd.Series(names).duplicated().any()


def test_routing_columns_are_not_written(landing: Path):
    sample = files(landing / "landing" / "loan_origination", "los_applications_*.csv")[0]
    assert "extract_date" not in pd.read_csv(sample, nrows=1).columns


def test_ticks_are_idempotent(world: World, landing: Path):
    writer = LandingWriter(root=landing / "landing", redirect={"crm": landing / "drive"})
    since = world.config.today
    tick(world, writer, since)
    folder = f"cdc_{since + dt.timedelta(days=1):%Y%m%d}"
    core_file = landing / "landing" / "core_banking" / folder / "core_transactions.parquet"
    first = core_file.read_bytes()
    tick(world, writer, since)
    assert core_file.read_bytes() == first


def test_state_round_trips(world: World, landing: Path):
    state = State.load(landing)
    assert state.cursor == world.config.today
    assert state.profile == "dev"


def test_tick_refuses_to_pass_the_horizon(world: World, landing: Path):
    writer = LandingWriter(root=landing / "landing")
    with pytest.raises(ValueError, match="horizon"):
        tick(world, writer, world.config.end_date)


def test_answer_key_contents(world: World, landing: Path):
    key = answer_key.build(world)
    assert set(key["fraud_postings"]["fraud_type"].dropna()) == {
        "card_testing",
        "night_geo_jump",
        "account_takeover",
    }
    assert key["person_crosswalk"]["cif_number"].is_unique
    assert key["dq_duplicate_customers"]["dup_cif_number"].notna().all()
    assert len(key["dq_gl_breaks"]) > 0
    assert (landing / "answer_key" / "person_crosswalk.parquet").exists()


def test_platform_code_never_touches_the_answer_key():
    for path in (REPO / "src" / "isbank").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "answer_key" not in text, path
        assert "import generator" not in text, path
        assert "from generator" not in text, path
