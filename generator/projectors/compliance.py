"""Compliance watchlists: sanctions and PEP lists as monthly CSV snapshots.

Lists are synthetic (never real sanctioned people or real lists). A small share of IS Bank
customers are genuine PEPs, listed under a name variant; other entries deliberately share a
common customer name, so naive name matching produces false hits that screening has to
resolve with date of birth and ID.
"""

import datetime as dt

import numpy as np
import pandas as pd

from generator.projectors.common import (
    SourceBatch,
    stable_choice,
    stable_int,
    stable_uniform,
    system_id,
)
from generator.world import World
from generator.world.names import load_name_lists

SYSTEM = "compliance"
_NATIONALITIES = ["GH", "NG", "TG", "BF", "CI", "LB", "GB", "US"]
# Surnames of prominent Ghanaian political families: never used on synthetic watchlists, so
# no entry can read as a reference to a real politically exposed person.
_EXCLUDED_SURNAMES = {"Mahama", "Akufo-Addo", "Bawumia", "Kufuor", "Rawlings", "Nkrumah", "Mills"}


def monthly_lists(world: World, since: dt.date | None, until: dt.date) -> SourceBatch:
    """A full list snapshot for each month start in (since, until]."""
    entries = list_entries(world)
    lo = (
        pd.Timestamp(world.config.start_date) - pd.Timedelta(days=1)
        if since is None
        else pd.Timestamp(since)
    )
    month_starts = pd.date_range(lo + pd.Timedelta(days=1), pd.Timestamp(until), freq="MS")
    snaps = [
        entries[entries["listed_on"] <= m].drop(columns=["person_id"]).assign(snapshot_date=m)
        for m in month_starts
    ]
    frame = (
        pd.concat(snaps, ignore_index=True)
        if snaps
        else entries.head(0).drop(columns=["person_id"])
    )
    frame["listed_on"] = pd.to_datetime(frame["listed_on"]).dt.strftime("%Y-%m-%d")
    batch = SourceBatch(SYSTEM)
    batch.add("watchlist", frame, "csv", partition_by="snapshot_date")
    return batch


def list_entries(world: World) -> pd.DataFrame:
    """Every list entry with the date it was listed (person_id only for customer PEPs)."""
    p = world.config.projectors[SYSTEM]
    seed = world.config.seed
    names = load_name_lists()
    start, end = pd.Timestamp(world.config.start_date), pd.Timestamp(world.config.end_date)
    customers = world.customers[
        (world.customers["customer_type"] == "individual")
        & ~world.customers["last_name"].isin(_EXCLUDED_SURNAMES)
    ]

    # Customer PEPs, listed under a name variant (initial for the first name).
    pep_people = customers[
        stable_uniform(seed, "cmp:pep", customers["person_id"]) < p["pep_customer_share"]
    ]
    pep_rows = pd.DataFrame(
        {
            "person_id": pep_people["person_id"].to_numpy(),
            "list_type": "PEP",
            "full_name": (pep_people["first_name"].str.slice(0, 1) + ". " + pep_people["last_name"])
            .str.upper()
            .to_numpy(),
            "date_of_birth": pep_people["date_of_birth"].dt.strftime("%Y-%m-%d").to_numpy(),
            "nationality": "GH",
            "id_number": pep_people["ghana_card"].to_numpy(),
        }
    )

    # Synthetic entries; some share a common customer name (false-hit bait).
    n_other = p["sanctions_entries"] + p["pep_entries"]
    ids = np.arange(1, n_other + 1)
    surname_pool = {
        g: [s for s in pool if s not in _EXCLUDED_SURNAMES] for g, pool in names.surnames.items()
    }
    groups = list(surname_pool)
    group = stable_choice(seed, "cmp:group", ids, groups)
    surnames = np.array(
        [
            surname_pool[g][stable_int(seed, "cmp:sur", [i], 0, len(surname_pool[g]) - 1)[0]]
            for g, i in zip(group, ids, strict=True)
        ]
    )
    gender = stable_choice(seed, "cmp:gender", ids, ["male", "female"])
    given = np.array(
        [
            names.common_given["M" if g == "male" else "F"][
                stable_int(seed, "cmp:given", [i], 0, 19)[0]
            ]
            for g, i in zip(gender, ids, strict=True)
        ]
    )
    near = stable_uniform(seed, "cmp:near", ids) < p["near_miss_share"]
    if near.any() and len(customers):
        pick = stable_int(seed, "cmp:near:pick", ids[near], 0, len(customers) - 1)
        surnames[near] = customers["last_name"].to_numpy()[pick]
        given[near] = customers["first_name"].to_numpy()[pick]
    birth = pd.Timestamp("1950-01-01") + pd.to_timedelta(
        stable_int(seed, "cmp:dob", ids, 0, 50 * 365), unit="D"
    )
    other = pd.DataFrame(
        {
            "person_id": pd.NA,
            "list_type": np.where(ids <= p["sanctions_entries"], "SANCTION", "PEP"),
            "full_name": np.char.upper(
                (given.astype(str) + " " + surnames.astype(str)).astype(str)
            ),
            "date_of_birth": birth.strftime("%Y-%m-%d"),
            "nationality": stable_choice(seed, "cmp:nat", ids, _NATIONALITIES),
            "id_number": None,
        }
    )
    entries = pd.concat([pep_rows, other], ignore_index=True)
    k = np.arange(1, len(entries) + 1)
    # Most entries predate the window; a few are added each month.
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    new = stable_uniform(seed, "cmp:new", k) < p["new_entries_per_month"] * months / len(entries)
    offset = stable_int(seed, "cmp:when", k, 0, (end - start).days)
    entries["listed_on"] = np.where(
        new,
        start + pd.to_timedelta(offset, unit="D"),
        start - pd.to_timedelta(stable_int(seed, "cmp:old", k, 30, 3000), unit="D"),
    )
    entries.insert(0, "list_entry_id", [f"WL-{v:06d}" for v in system_id(seed, "cmp:id", k, 10**6)])
    entries["source"] = np.where(
        entries["list_type"] == "SANCTION", "SYNTHETIC-SANCTIONS", "SYNTHETIC-PEP"
    )
    return entries
