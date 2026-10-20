"""Core banking Silver transforms on generated data, checked against the answer key."""

import datetime as dt
from pathlib import Path

import pytest

from generator import answer_key
from generator.config import load_config
from generator.emit.local import LandingWriter
from generator.projectors import core_banking
from generator.timeline import delta, snapshot
from generator.world import build_world
from isbank.bronze.ingest import read_files, with_metadata
from isbank.bronze.sources import SOURCES
from isbank.dq import apply_rules
from isbank.silver import core

pytestmark = pytest.mark.spark
SPECS = {s.table: s for s in SOURCES}
FULL = dt.date(2025, 12, 31)


@pytest.fixture(scope="module")
def world():
    return build_world(load_config(profile="dev"))


@pytest.fixture(scope="module")
def bronze(spark, world, tmp_path_factory):
    """Bronze-shaped DataFrames from a full load plus 20 days of CDC."""
    root = tmp_path_factory.mktemp("core") / "landing"
    writer = LandingWriter(root=root)
    writer.write(core_banking.snapshot_batch(world, snapshot(world, FULL)), f"full_{FULL:%Y%m%d}")
    for i in range(20):
        since = FULL + dt.timedelta(days=i)
        until = since + dt.timedelta(days=1)
        writer.write(
            core_banking.delta_batch(world, delta(world, since, until)), f"cdc_{until:%Y%m%d}"
        )
    frames = {}
    for table in ("core_customers", "core_accounts", "core_transactions"):
        paths = [p.as_uri() for p in sorted(Path(root).rglob(f"{table}.parquet"))]
        df = read_files(spark, SPECS[table], paths, first_line=lambda _: "")
        frames[table] = with_metadata(df, "core_banking", "b", dt.datetime(2026, 1, 21)).cache()
    return frames


def test_customer_history_is_scd2(bronze):
    history = core.customer_history(bronze["core_customers"]).toPandas()
    current = history[history["is_current"]]
    assert current["cif_number"].is_unique
    # Every non-current version is closed by the next version of the same CIF.
    closed = history[~history["is_current"]].sort_values(["cif_number", "valid_from"])
    assert (closed["valid_to"] > closed["valid_from"]).all()
    assert len(history) > len(current)


def test_customer_history_tracks_detail_changes(world, bronze):
    history = core.customer_history(bronze["core_customers"]).toPandas()
    cif = answer_key.build(world)["person_crosswalk"].set_index("person_id")["cif_number"]
    changes = world.customer_changes
    window = changes[
        (changes["changed_on"] > dt.datetime(2025, 12, 31))
        & (changes["changed_on"] <= dt.datetime(2026, 1, 20))
        & (changes["field"] == "phone_number")
    ]
    assert len(window) > 0
    for _, change in window.iterrows():
        versions = history[history["cif_number"] == cif[change["person_id"]]]
        assert change["new_value"] in set(versions["mobile_number"])


def test_placeholder_dob_is_flagged_not_trusted(world, bronze):
    history = core.customer_history(bronze["core_customers"])
    out = apply_rules(history, "customer", core.CUSTOMER_RULES, ["cif_number"], "r1")
    current = out.clean.where("is_current").toPandas()
    planted = set(answer_key.build(world)["dq_placeholder_dob"]["cif_number"])
    flagged = set(current.loc[current["dob_is_placeholder"], "cif_number"])
    assert planted & set(current["cif_number"]) <= flagged
    assert current.loc[current["dob_is_placeholder"], "date_of_birth"].isna().all()
    failed = {r["rule_name"]: r["rows_failed"] for r in out.results}
    assert failed["dob_not_placeholder"] > 0
    assert failed["cif_present"] == 0


def test_double_postings_are_quarantined_exactly(world, bronze):
    txns = core.transactions(bronze["core_transactions"])
    out = apply_rules(txns, "transaction", core.TRANSACTION_RULES, ["txn_ref"], "r1")
    key = answer_key.build(world)
    planted = key["dq_double_postings"]
    in_window = planted[planted["posted_at"] < dt.datetime(2026, 1, 21)]
    q = out.quarantine.where("rule_name = 'not_double_posted'").count()
    assert q == len(in_window)
    clean = out.clean.toPandas()
    assert clean["txn_ref"].is_unique
    assert (clean["signed_amount"] != 0).all()


def test_accounts_take_the_latest_state(world, bronze):
    accounts = core.accounts(bronze["core_accounts"]).toPandas()
    assert accounts["account_number"].is_unique
    closures = answer_key.build(world)["account_closures"]
    closed_in_window = closures[
        (closures["closed_on"] > dt.datetime(2025, 12, 31))
        & (closures["closed_on"] <= dt.datetime(2026, 1, 20))
        & closures["account_number"].isin(accounts["account_number"])
    ]
    assert len(closed_in_window) > 0
    status = accounts.set_index("account_number")["status"]
    assert (status.loc[closed_in_window["account_number"]] == "CLOSED").all()
