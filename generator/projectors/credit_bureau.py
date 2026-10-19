"""External credit bureau: weekly fixed-width report files.

Every Monday the bureau returns a record for each IS Bank customer who has ever applied for
credit. Records are keyed by national ID (digits only, the bureau's own format), surname,
first name, and date of birth. The score is driven by the customer's true default risk
plus noise, so it is predictive but imperfect, which is what a real bureau score is.
"""

import datetime as dt

import numpy as np
import pandas as pd

from generator.projectors.common import SourceBatch, stable_int, stable_uniform, system_id
from generator.projectors.loan_origination import _Los
from generator.world import World

SYSTEM = "credit_bureau"
# Field name, width. Numeric fields are right-aligned and zero-padded.
LAYOUT = [
    ("record_type", 2),
    ("bureau_ref", 12),
    ("national_id", 10),
    ("surname", 25),
    ("first_name", 20),
    ("date_of_birth", 8),
    ("score", 3),
    ("external_facilities", 2),
    ("external_balance_ghs", 12),
    ("worst_dpd_24m", 3),
    ("enquiries_6m", 2),
    ("report_date", 8),
]


def weekly_reports(world: World, since: dt.date | None, until: dt.date) -> SourceBatch:
    """Report records for each Monday in (since, until]."""
    p = world.config.projectors[SYSTEM]
    seed = world.config.seed
    lo = (
        pd.Timestamp(world.config.start_date)
        if since is None
        else pd.Timestamp(since) + pd.Timedelta(days=1)
    )
    mondays = pd.date_range(lo, pd.Timestamp(until), freq="W-MON")
    applicants = _first_application(world)
    risk = _true_risk(world)
    rows = []
    for monday in mondays:
        people = applicants[applicants["first_applied"] <= monday]
        if people.empty:
            continue
        rows.append(_records(world, people, risk, monday, p, seed))
    frame = (
        pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=[f for f, _ in LAYOUT])
    )
    batch = SourceBatch(SYSTEM)
    batch.add("bureau_report", frame, "fixed_width", partition_by="report_date", layout=LAYOUT)
    return batch


def _first_application(world: World) -> pd.DataFrame:
    apps = _Los(world).application_rows()
    lookup = world.customers.set_index("ghana_card")["person_id"]
    digits = apps["applicant_ghana_card"].str.upper().str.replace(r"[^0-9]", "", regex=True)
    canonical = "GHA-" + digits.str.slice(0, 9) + "-" + digits.str.slice(9, 10)
    apps = apps.assign(
        person_id=canonical.map(lookup), applied=pd.to_datetime(apps["application_date"])
    )
    first = apps.groupby("person_id")["applied"].min().rename("first_applied").reset_index()
    return first.astype({"person_id": "int64"})


def _true_risk(world: World) -> pd.Series:
    """Each person's highest true probability of default across their loans (else a prior)."""
    lo = world.scenarios.loan_outcomes
    risk = lo.groupby("person_id")["p_default"].max()
    prior = float(lo["p_default"].median()) if len(lo) else 0.1
    return risk.reindex(world.customers["person_id"]).fillna(prior)


def _records(world: World, people: pd.DataFrame, risk: pd.Series, monday, p: dict, seed: int):
    c = world.customers.set_index("person_id").loc[people["person_id"]]
    pid = people["person_id"].to_numpy()
    week = int(monday.strftime("%G%V"))
    key = pid * 1_000_000 + week
    lo, hi = p["score_range"]["min"], p["score_range"]["max"]
    noise = p["score_noise_sd"] * (stable_uniform(seed, "bureau:noise", key) * 2 - 1) * 1.7
    score = np.clip(
        np.round(hi - (hi - lo) * risk.loc[pid].to_numpy() ** 0.6 + noise), lo, hi
    ).astype(int)
    facilities = np.minimum(
        stable_int(seed, "bureau:fac", pid, 0, int(3 * p["external_facilities_mean"])), 99
    )
    balance = facilities * stable_int(seed, "bureau:bal", pid, 500, 20_000)
    dpd = np.where(risk.loc[pid].to_numpy() > 0.4, stable_int(seed, "bureau:dpd", key, 0, 120), 0)
    enquiries = stable_int(seed, "bureau:enq", key, 0, int(3 * p["enquiries_mean"]))
    national = c["ghana_card"].str.replace(r"[^0-9]", "", regex=True)
    is_business = (c["customer_type"] == "business").to_numpy()
    frame = pd.DataFrame(
        {
            "record_type": "01",
            "bureau_ref": [f"XB{v:010d}" for v in system_id(seed, "bureau:ref", pid, 10**10)],
            "national_id": national.to_numpy(),
            "surname": np.where(
                is_business, c["business_name"].str.upper(), c["last_name"].str.upper()
            ),
            "first_name": np.where(is_business, "", c["first_name"].str.upper()),
            "date_of_birth": c["date_of_birth"].dt.strftime("%Y%m%d").to_numpy(),
            "score": score,
            "external_facilities": facilities,
            "external_balance_ghs": balance,
            "worst_dpd_24m": dpd,
            "enquiries_6m": enquiries,
            "report_date": monday.strftime("%Y%m%d"),
        }
    )
    # Fixed-width text fields are truncated to their width, as the bureau's system does.
    for column, width in LAYOUT:
        if not pd.api.types.is_numeric_dtype(frame[column]):
            frame[column] = frame[column].astype(str).str.slice(0, width)
    return frame
