"""Core banking, card switch, and mobile banking projectors (dev profile).

Projectors must never leak ground truth, must compose (backfill + ticks = later backfill)
without duplicates or gaps, must be idempotent, and must carry their planted flaws.
"""

import datetime as dt

import pandas as pd
import pytest

from generator.config import load_config
from generator.projectors import card_switch, core_banking, mobile_banking
from generator.timeline import delta, snapshot
from generator.world import World, build_world

GROUND_TRUTH_COLUMNS = {
    "person_id",
    "account_id",
    "posting_id",
    "event_id_world",
    "fraud_type",
    "fraud_scenario_id",
    "segment",
    "ethnicity",
    "dormant",
    "activity",
    "p_default",
    "will_default",
}
DAY = pd.Timedelta(days=1)


@pytest.fixture(scope="module")
def world() -> World:
    return build_world(load_config(profile="dev"))


@pytest.fixture(scope="module")
def today(world: World) -> pd.Timestamp:
    return pd.Timestamp(world.config.today)


@pytest.fixture(scope="module")
def core(world: World):
    return core_banking.snapshot_batch(world, snapshot(world, world.config.today))


@pytest.fixture(scope="module")
def cards(world: World, today):
    return card_switch.events_delivered(world, None, today).tables["card_authorisations"]


@pytest.fixture(scope="module")
def app(world: World, today):
    return mobile_banking.events_delivered(world, None, today).tables["app_events"]


def test_no_ground_truth_leaks(core, cards, app):
    for name, table in [*core.tables.items(), ("cards", cards), ("app", app)]:
        assert not GROUND_TRUTH_COLUMNS & set(table.columns), name


# ---------------------------------------------------------------- core banking


def test_core_ids_are_unique_and_well_formed(core):
    customers = core.tables["core_customers"]
    accounts = core.tables["core_accounts"]
    assert customers["cif_number"].is_unique
    assert customers["cif_number"].str.fullmatch(r"CIF\d{8}").all()
    assert accounts["account_number"].is_unique
    assert accounts["account_number"].str.fullmatch(r"\d{13}").all()
    assert set(accounts["cif_number"]) <= set(customers["cif_number"])


def test_core_has_duplicate_customers_at_other_branches(world: World, core):
    customers = core.tables["core_customers"]
    n_people = len(snapshot(world, world.config.today).customers)
    duplicates = len(customers) - n_people
    share = world.config.projectors["core_banking"]["duplicate_customer_share"]
    assert duplicates > 0
    assert duplicates / n_people < 3 * share
    # Duplicates share a date of birth and Ghana Card with their twin but not the branch.
    twins = customers.dropna(subset=["ghana_card_no"]).groupby("ghana_card_no")
    pairs = twins.filter(lambda g: len(g) == 2)
    assert len(pairs) > 0
    assert (pairs.groupby("ghana_card_no")["branch_code"].nunique() == 2).all()


def test_core_only_keeps_posted_transactions_and_reconciles(world: World, core):
    txns = core.tables["core_transactions"]
    postings = snapshot(world, world.config.today).postings
    posted = postings[postings["status"] == "POSTED"]
    assert len(txns) >= len(posted)
    doubled = len(txns) - len(posted)
    assert 0 < doubled < 0.005 * len(posted)
    signed = txns["amount"].where(txns["dr_cr"] == "CR", -txns["amount"])
    unique = txns.drop_duplicates(["account_number", "booking_ts", "amount", "dr_cr", "txn_code"])
    unique_signed = unique["amount"].where(unique["dr_cr"] == "CR", -unique["amount"])
    assert unique_signed.sum() == pytest.approx(posted["amount_pesewas"].sum() / 100, abs=1)
    assert signed.sum() != pytest.approx(unique_signed.sum(), abs=0.001)


def test_core_has_planted_kyc_gaps(core):
    customers = core.tables["core_customers"]
    assert customers["ghana_card_no"].isna().any()
    assert (pd.to_datetime(customers["date_of_birth"]) == pd.Timestamp("1900-01-01")).any()


def test_core_delta_upserts_changed_customers(world: World):
    since = world.config.today
    until = since + dt.timedelta(days=30)
    d = core_banking.delta_batch(world, delta(world, since, until))
    upserts = d.tables["core_customers"]
    assert len(upserts) > 0
    assert (
        pd.to_datetime(upserts[["created_at", "updated_at"]].max(axis=1)) > pd.Timestamp(since)
    ).all()


def test_core_backfill_plus_delta_transactions_compose(world: World):
    t0 = world.config.today
    t1 = t0 + dt.timedelta(days=5)
    before = core_banking.snapshot_batch(world, snapshot(world, t0)).tables["core_transactions"]
    d = core_banking.delta_batch(world, delta(world, t0, t1)).tables["core_transactions"]
    after = core_banking.snapshot_batch(world, snapshot(world, t1)).tables["core_transactions"]
    assert not set(before["txn_ref"]) & set(d["txn_ref"])
    assert set(before["txn_ref"]) | set(d["txn_ref"]) == set(after["txn_ref"])


def test_core_is_idempotent(world: World, core):
    again = core_banking.snapshot_batch(world, snapshot(world, world.config.today))
    for name, table in core.tables.items():
        pd.testing.assert_frame_equal(table, again.tables[name], obj=name)


# ---------------------------------------------------------------- card switch


def test_card_events_cover_every_card_posting(world: World, cards, today):
    postings = snapshot(world, world.config.today).postings
    card_postings = postings[
        postings["channel"].isin(["POS", "ONLINE", "ATM"])
        & postings["txn_type"].isin(["CARD_PURCHASE", "CASH_WITHDRAWAL"])
    ]
    unique = cards.drop_duplicates("event_id")
    # Late events of the last days are still in flight; everything else has arrived.
    settled = card_postings[card_postings["posted_at"] < today - 3 * DAY]
    assert len(unique) >= len(settled)
    assert set(cards["response_code"]) == {"00", "51"}
    declined = (card_postings["status"] == "DECLINED").sum()
    assert (unique["response_code"] == "51").sum() <= declined


def test_card_events_have_late_and_duplicate_deliveries(cards):
    lag = (cards["delivered_at"] - cards["txn_datetime"]).dt.total_seconds()
    assert (lag > 300).any()
    assert cards["event_id"].duplicated().any()
    assert cards["pan_masked"].str.fullmatch(r"99000[12]\*{6}\d{4}").all()


def test_card_ticks_compose_with_backfill(world: World, cards, today):
    ticks = [
        card_switch.events_delivered(world, today + i * DAY, today + (i + 1) * DAY).tables[
            "card_authorisations"
        ]
        for i in range(3)
    ]
    later = card_switch.events_delivered(world, None, today + 3 * DAY).tables["card_authorisations"]
    combined = pd.concat([cards, *ticks], ignore_index=True)
    key = ["event_id", "delivered_at"]
    assert not combined.duplicated(key).any()
    assert len(combined) == len(later)
    assert set(map(tuple, combined[key].astype(str).to_numpy())) == set(
        map(tuple, later[key].astype(str).to_numpy())
    )


# ---------------------------------------------------------------- mobile banking


def test_app_events_schema_drift(world: World, app):
    v3_from = pd.Timestamp(world.config.projectors["mobile_banking"]["app_v3_from"])
    old, new = app[app["event_ts"] < v3_from], app[app["event_ts"] >= v3_from]
    assert old["biometric_used"].isna().all()
    assert new["biometric_used"].notna().all()


def test_takeover_sessions_look_like_takeovers(world: World, app):
    ring_devices = {d for ds in world.scenarios.fraud_rings["devices"] for d in ds}
    ring = app[app["device_id"].isin(ring_devices)]
    if ring.empty:
        pytest.skip("no takeover delivered before today in this world")
    assert (ring["event_type"] == "LOGIN_FAILED").any()
    assert ring["ip_address"].str.startswith("192.0.2.").all()
    genuine = app[~app["device_id"].isin(ring_devices)]
    assert genuine["ip_address"].str.startswith("203.0.113.").all()
    # Genuine users fail logins and add payees too, so neither is a perfect fraud flag.
    assert (genuine["event_type"] == "LOGIN_FAILED").any()
    assert (genuine["event_type"] == "BENEFICIARY_ADDED").any()


def test_payee_added_before_first_transfer(app):
    added = app[app["event_type"] == "BENEFICIARY_ADDED"]
    transfers = app[app["event_type"] == "TRANSFER"]
    first = transfers.sort_values("event_ts").drop_duplicates(["user_id", "beneficiary_account"])
    merged = first.merge(added, on=["user_id", "beneficiary_account"], suffixes=("", "_add"))
    assert len(merged) > 0
    assert (merged["event_ts_add"] < merged["event_ts"]).all()


def test_app_ticks_compose_with_backfill(world: World, app, today):
    tick = mobile_banking.events_delivered(world, today, today + DAY).tables["app_events"]
    later = mobile_banking.events_delivered(world, None, today + DAY).tables["app_events"]
    assert not set(app["event_id"]) & set(tick["event_id"])
    assert set(app["event_id"]) | set(tick["event_id"]) == set(later["event_id"])
