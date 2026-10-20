"""Customer attribute changes and account closures over time.

`customer_changes` is the ground-truth history of customer details (the source for SCD2 in
Silver); `account_closures` says when and why each account closed. Both are derived after
the ledger, so they never alter money flows.
"""

import numpy as np
import pandas as pd

from generator.config import WorldConfig
from generator.world.holdings import Holdings
from generator.world.names import NameLists, load_name_lists
from generator.world.scenarios import Scenarios
from generator.world.transactions import Ledger
from isbank.reference.ghana import MOBILE_PREFIXES

_REPAID_TOLERANCE_PESEWAS = 100
_DAY = np.timedelta64(1, "D")


def generate_customer_changes(
    config: WorldConfig,
    customers: pd.DataFrame,
    scenarios: Scenarios,
    rng: np.random.Generator,
    names: NameLists | None = None,
) -> pd.DataFrame:
    """Return change_id, person_id, changed_on, field, old_value, new_value (time-ordered).

    Changes stop at a customer's churn date. Values are applied in date order, so each
    change's old_value is the previous change's new_value.
    """
    names = names or load_name_lists()
    ch = config.customer_changes
    end = np.datetime64(config.end_date, "D")
    joined = customers["joined_on"].to_numpy().astype("datetime64[D]")
    churn = scenarios.churn_date
    stop = np.where(np.isnat(churn), end, churn)
    years = np.maximum((stop - joined).astype(int), 0) / 365.25
    n = len(customers)

    events: list[pd.DataFrame] = []
    for field, rate in ch.per_customer_per_year.items():
        counts = rng.poisson(rate * years)
        person = np.repeat(np.arange(n), counts)
        span = (stop[person] - joined[person]).astype(int)
        when = joined[person] + (rng.random(person.size) * span).astype("timedelta64[D]") + _DAY
        events.append(pd.DataFrame({"pos": person, "changed_on": when, "field": field}))

    # Marriage: women in the eligible age band, at most once.
    dob = customers["date_of_birth"].to_numpy().astype("datetime64[D]")
    lo = np.maximum(joined, dob + np.timedelta64(int(ch.marriage_age.min * 365.25), "D"))
    hi = np.minimum(stop, dob + np.timedelta64(int(ch.marriage_age.max * 365.25), "D"))
    eligible = (
        (customers["gender"].to_numpy() == "F")
        & (customers["customer_type"].to_numpy() == "individual")
        & (hi > lo)
    )
    years_eligible = np.where(eligible, (hi - lo).astype(int) / 365.25, 0)
    marries = rng.random(n) < 1 - np.exp(-ch.marriage_surname_per_year * years_eligible)
    pos = np.flatnonzero(marries)
    span = (hi[pos] - lo[pos]).astype(int)
    when = lo[pos] + (rng.random(pos.size) * span).astype("timedelta64[D]")
    events.append(pd.DataFrame({"pos": pos, "changed_on": when, "field": "last_name"}))

    ev = pd.concat(events, ignore_index=True)
    ev = ev[ev["changed_on"] <= end].sort_values(["changed_on", "pos", "field"], kind="stable")

    current = {
        f: customers[f].to_numpy(dtype=object, na_value=None).copy()
        for f in ("phone_number", "digital_address", "email", "last_name")
    }
    district = customers["digital_address"].str.slice(0, 2).to_numpy(dtype=object)
    districts = np.array(sorted({b.gps_district for b in config.branches}), dtype=object)
    used_phones = set(customers["phone_number"])
    prefixes = np.array([p for ps in MOBILE_PREFIXES.values() for p in ps], dtype=object)
    surnames = np.concatenate(list(names.surnames.values()))

    old_values, new_values = [], []
    for p, field in zip(ev["pos"].to_numpy(), ev["field"].to_numpy(), strict=True):
        old = current[field][p]
        if field == "phone_number":
            while (new := f"{rng.choice(prefixes)}{rng.integers(0, 10**7):07d}") in used_phones:
                pass
            used_phones.add(new)
        elif field == "digital_address":
            local = rng.random() < ch.local_move_share
            code = district[p] if local else rng.choice(districts)
            new = f"{code}-{rng.integers(100, 10_000)}-{rng.integers(0, 10_000):04d}"
        elif field == "email":
            first = str(customers["first_name"].iat[p]).split()[-1].lower()
            last = str(current["last_name"][p]).lower()
            local = f"{first}.{last}{rng.integers(1, 10_000)}".replace(" ", "").replace("'", "")
            new = f"{local}@example.com"
        else:
            new = rng.choice(surnames[surnames != old])
        current[field][p] = new
        old_values.append(old)
        new_values.append(new)

    frame = pd.DataFrame(
        {
            "person_id": ev["pos"].to_numpy() + 1,
            "changed_on": ev["changed_on"].to_numpy().astype("datetime64[s]"),
            "field": ev["field"].to_numpy(),
            "old_value": pd.array(old_values, dtype="string"),
            "new_value": pd.array(new_values, dtype="string"),
        }
    )
    frame.insert(0, "change_id", np.arange(1, len(frame) + 1, dtype="int64"))
    return frame.astype({"field": "string"})


def account_closures(
    config: WorldConfig, holdings: Holdings, scenarios: Scenarios, ledger: Ledger
) -> pd.DataFrame:
    """Return account_id, closed_on, reason for every account that closes by end_date.

    CHURN: a leaving customer's deposits and debit card close on their churn date.
    MATURED: fixed deposits close at maturity.
    REPAID: loans close at maturity only if fully repaid; loans in arrears stay open.
    """
    acc = holdings.accounts
    end = pd.Timestamp(config.end_date)
    parts = []

    churn = scenarios.churn[["person_id", "churn_date"]]
    leaving = acc[acc["product_code"].isin(["CA_PER", "CA_BIZ", "SA_REG", "SA_SUSU", "DC_DEBIT"])]
    leaving = leaving.merge(churn, on="person_id")
    parts.append(
        pd.DataFrame(
            {
                "account_id": leaving["account_id"],
                "closed_on": leaving["churn_date"],
                "reason": "CHURN",
            }
        )
    )

    fd = acc[(acc["product_code"] == "FD_TERM") & (acc["maturity_date"] <= end)]
    parts.append(
        pd.DataFrame(
            {"account_id": fd["account_id"], "closed_on": fd["maturity_date"], "reason": "MATURED"}
        )
    )

    loans = acc[(acc["family"] == "loan") & (acc["maturity_date"] <= end)]
    posted = ledger.postings
    posted = posted[(posted["status"] == "POSTED") & posted["account_id"].isin(loans["account_id"])]
    last_balance = posted.groupby("account_id")["balance_after_pesewas"].last()
    opening = ledger.opening_balances.set_index("account_id")["balance_pesewas"]
    balance = opening.loc[loans["account_id"]].copy()
    balance.loc[last_balance.index] = last_balance.to_numpy()
    repaid = loans[balance.to_numpy() >= -_REPAID_TOLERANCE_PESEWAS]
    parts.append(
        pd.DataFrame(
            {
                "account_id": repaid["account_id"],
                "closed_on": repaid["maturity_date"],
                "reason": "REPAID",
            }
        )
    )

    closures = pd.concat(parts, ignore_index=True).sort_values(["closed_on", "account_id"])
    return closures.reset_index(drop=True).astype({"reason": "string"})
