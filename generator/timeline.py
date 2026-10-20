"""Time-slice the world: what the bank's systems know as of a date, and what changed.

The world is simulated once up to `config.end_date`. The backfill exposes everything up to
`config.today`; each daily tick then reveals one more day. Because the future already exists
deterministically, tick N gives the same answer however many times it is run, and whether
or not earlier ticks were run first. That is what makes source loads idempotent.

Projectors build each source system's view from a `Snapshot` (initial load) and from
`Delta`s (incremental loads). Ground-truth-only fields (labels, scenario ids) are still
present here; projectors are responsible for dropping them.
"""

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from generator.world import World

CHANGE_FIELDS = ("phone_number", "digital_address", "email", "last_name")


@dataclass(frozen=True)
class Snapshot:
    """Everything visible at the end of `as_of`."""

    as_of: dt.date
    customers: pd.DataFrame  # attributes as of `as_of`
    accounts: pd.DataFrame  # with closed_on / close_reason if closed by `as_of`
    services: pd.DataFrame
    devices: pd.DataFrame
    postings: pd.DataFrame
    complaints: pd.DataFrame  # resolved_on hidden if after `as_of`
    merchants: pd.DataFrame


@dataclass(frozen=True)
class Delta:
    """Everything that happened in the half-open day range (since, until]."""

    since: dt.date
    until: dt.date
    new_customers: pd.DataFrame
    customer_changes: pd.DataFrame
    opened_accounts: pd.DataFrame
    closed_accounts: pd.DataFrame
    services: pd.DataFrame
    devices: pd.DataFrame
    postings: pd.DataFrame
    complaints_opened: pd.DataFrame
    complaints_resolved: pd.DataFrame


def snapshot(world: World, as_of: dt.date) -> Snapshot:
    """Return the world as the bank's systems would see it at the end of `as_of`."""
    _check_date(world, as_of)
    cutoff = _end_of(as_of)
    day = pd.Timestamp(as_of)

    customers = customers_as_of(world, as_of)
    accounts = world.holdings.accounts
    accounts = accounts[accounts["opened_on"] <= day].merge(
        world.account_closures.rename(columns={"reason": "close_reason"}),
        on="account_id",
        how="left",
    )
    closed_later = accounts["closed_on"] > day
    accounts.loc[closed_later, ["closed_on", "close_reason"]] = [pd.NaT, pd.NA]

    services = world.holdings.services
    devices = world.scenarios.devices
    postings = world.ledger.postings
    complaints = world.scenarios.complaints
    complaints = complaints[complaints["opened_on"] <= day].copy()
    complaints.loc[complaints["resolved_on"] > day, "resolved_on"] = pd.NaT

    return Snapshot(
        as_of=as_of,
        customers=customers.reset_index(drop=True),
        accounts=accounts.reset_index(drop=True),
        services=services[services["enrolled_on"] <= day].reset_index(drop=True),
        devices=devices[devices["first_seen"] <= day].reset_index(drop=True),
        postings=postings[postings["posted_at"] < cutoff].reset_index(drop=True),
        complaints=complaints.reset_index(drop=True),
        merchants=world.merchants,
    )


def delta(world: World, since: dt.date, until: dt.date) -> Delta:
    """Return what happened after the end of `since`, up to the end of `until`."""
    _check_date(world, since)
    _check_date(world, until)
    if until <= since:
        raise ValueError("until must be after since")
    lo, hi = pd.Timestamp(since), pd.Timestamp(until)

    def in_range(series: pd.Series) -> pd.Series:
        return (series > lo) & (series <= hi)

    customers = customers_as_of(world, until)
    changes = world.customer_changes
    accounts = world.holdings.accounts
    closures = world.account_closures
    postings = world.ledger.postings
    complaints = world.scenarios.complaints
    services = world.holdings.services
    devices = world.scenarios.devices
    posted_day = postings["posted_at"].dt.normalize()

    return Delta(
        since=since,
        until=until,
        new_customers=customers[in_range(customers["joined_on"])].reset_index(drop=True),
        customer_changes=changes[in_range(changes["changed_on"])].reset_index(drop=True),
        opened_accounts=accounts[in_range(accounts["opened_on"])].reset_index(drop=True),
        closed_accounts=closures[in_range(closures["closed_on"])].reset_index(drop=True),
        services=services[in_range(services["enrolled_on"])].reset_index(drop=True),
        devices=devices[in_range(devices["first_seen"])].reset_index(drop=True),
        postings=postings[in_range(posted_day)].reset_index(drop=True),
        complaints_opened=complaints[in_range(complaints["opened_on"])].reset_index(drop=True),
        complaints_resolved=complaints[in_range(complaints["resolved_on"])].reset_index(drop=True),
    )


def customers_as_of(world: World, as_of: dt.date) -> pd.DataFrame:
    """Customers who have joined by `as_of`, with every detail change up to it applied."""
    day = pd.Timestamp(as_of)
    customers = world.customers[world.customers["joined_on"] <= day].copy()
    changes = world.customer_changes
    latest = (
        changes[changes["changed_on"] <= day]
        .sort_values("change_id")
        .groupby(["person_id", "field"], observed=True)["new_value"]
        .last()
        .unstack("field")
    )
    if latest.empty:
        return customers
    indexed = customers.set_index("person_id")
    for field in CHANGE_FIELDS:
        if field in latest.columns:
            values = latest[field].dropna()
            values = values[values.index.isin(indexed.index)]
            indexed.loc[values.index, field] = values.to_numpy()
    return indexed.reset_index()[customers.columns]


def _end_of(day: dt.date) -> pd.Timestamp:
    return pd.Timestamp(day) + pd.Timedelta(days=1)


def _check_date(world: World, day: dt.date) -> None:
    cfg = world.config
    if not cfg.start_date <= day <= cfg.end_date:
        raise ValueError(f"{day} is outside the simulated range {cfg.start_date}..{cfg.end_date}")
