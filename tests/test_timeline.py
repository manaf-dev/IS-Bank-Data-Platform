"""Snapshots and deltas must compose exactly, so incremental loads never duplicate or drop."""

import datetime as dt

import pandas as pd
import pytest

from generator.config import load_config
from generator.timeline import customers_as_of, delta, snapshot
from generator.world import World, build_world

DAY = dt.timedelta(days=1)


@pytest.fixture(scope="module")
def world() -> World:
    return build_world(load_config(profile="dev"))


def test_snapshot_hides_the_future(world: World):
    today = world.config.today
    s = snapshot(world, today)
    end_of_today = pd.Timestamp(today) + pd.Timedelta(days=1)
    assert s.postings["posted_at"].max() < end_of_today
    assert s.customers["joined_on"].max() <= pd.Timestamp(today)
    assert s.accounts["opened_on"].max() <= pd.Timestamp(today)
    assert (s.accounts["closed_on"].dropna() <= pd.Timestamp(today)).all()
    assert (s.complaints["resolved_on"].dropna() <= pd.Timestamp(today)).all()
    # The world itself goes beyond today.
    assert world.ledger.postings["posted_at"].max() > end_of_today


def test_snapshot_plus_deltas_equals_later_snapshot(world: World):
    t0 = world.config.today
    t1 = t0 + 10 * DAY
    before, after = snapshot(world, t0), snapshot(world, t1)
    d = delta(world, t0, t1)
    combined = set(before.postings["posting_id"]) | set(d.postings["posting_id"])
    assert combined == set(after.postings["posting_id"])
    assert not set(before.postings["posting_id"]) & set(d.postings["posting_id"])
    assert set(before.customers["person_id"]) | set(d.new_customers["person_id"]) == set(
        after.customers["person_id"]
    )
    assert set(before.accounts["account_id"]) | set(d.opened_accounts["account_id"]) == set(
        after.accounts["account_id"]
    )


def test_daily_ticks_compose_into_the_range_delta(world: World):
    t0 = world.config.today
    whole = delta(world, t0, t0 + 7 * DAY)
    daily = [delta(world, t0 + i * DAY, t0 + (i + 1) * DAY) for i in range(7)]
    for field in ("postings", "customer_changes", "opened_accounts", "complaints_opened"):
        joined = pd.concat([getattr(d, field) for d in daily], ignore_index=True)
        assert len(joined) == len(getattr(whole, field)), field
    ids = pd.concat([d.postings for d in daily])["posting_id"]
    assert ids.is_unique
    assert set(ids) == set(whole.postings["posting_id"])


def test_ticks_are_idempotent(world: World):
    t0 = world.config.today
    first = delta(world, t0, t0 + DAY)
    again = delta(world, t0, t0 + DAY)
    pd.testing.assert_frame_equal(first.postings, again.postings)
    rebuilt = delta(build_world(world.config), t0, t0 + DAY)
    pd.testing.assert_frame_equal(first.postings, rebuilt.postings)


def test_customer_details_follow_their_change_history(world: World):
    change = world.customer_changes.iloc[len(world.customer_changes) // 2]
    person, field, when = change["person_id"], change["field"], change["changed_on"].date()
    before = customers_as_of(world, when - DAY).set_index("person_id")
    after = customers_as_of(world, when).set_index("person_id")
    if person in before.index:
        assert before.at[person, field] == change["old_value"]
    later = world.customer_changes[
        (world.customer_changes["person_id"] == person)
        & (world.customer_changes["field"] == field)
        & (world.customer_changes["changed_on"].dt.date == when)
    ]
    assert after.at[person, field] == later.iloc[-1]["new_value"]


def test_change_history_chains(world: World):
    for _, g in world.customer_changes.groupby(["person_id", "field"]):
        g = g.sort_values("change_id")
        assert (g["old_value"].iloc[1:].to_numpy() == g["new_value"].iloc[:-1].to_numpy()).all()


def test_closures_are_consistent(world: World):
    closures = world.account_closures
    assert closures["account_id"].is_unique
    acc = world.holdings.accounts.set_index("account_id")
    assert (
        closures["closed_on"].to_numpy() >= acc.loc[closures["account_id"], "opened_on"].to_numpy()
    ).all()
    churners = set(world.scenarios.churn["person_id"])
    churn_closed = closures[closures["reason"] == "CHURN"]
    assert set(acc.loc[churn_closed["account_id"], "person_id"]) <= churners
    # Loans in arrears at maturity stay open.
    shocked = world.scenarios.loan_outcomes.dropna(subset=["shock_date"])["account_id"]
    repaid = set(closures.loc[closures["reason"] == "REPAID", "account_id"])
    assert len(set(shocked) - repaid) > 0


def test_dates_outside_the_simulation_are_rejected(world: World):
    with pytest.raises(ValueError, match="outside the simulated range"):
        snapshot(world, world.config.end_date + DAY)
    with pytest.raises(ValueError, match="until must be after since"):
        delta(world, world.config.today, world.config.today)
