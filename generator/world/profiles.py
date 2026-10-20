"""Per-customer behavioural parameters shared by the planted signals and the ledger."""

import numpy as np
import pandas as pd

from generator.config import WorldConfig
from generator.world.holdings import Holdings


def generate_profiles(
    config: WorldConfig,
    customers: pd.DataFrame,
    holdings: Holdings,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Return person_id, activity, dormant, spend_ratio, and salary_day (0 = not salaried).

    Borrowers are never dormant, so dormancy cannot create defaults by accident; defaults
    come only from the planted credit-risk signal.
    """
    b = config.behaviour
    n = len(customers)
    segment = customers["segment"].to_numpy(dtype=object)
    accounts = holdings.accounts

    activity = np.exp(np.log(b.activity_median) + b.activity_sigma * rng.standard_normal(n))
    credit = accounts[(accounts["family"] == "loan") | (accounts["product_code"] == "CC_CLASSIC")]
    borrower = customers["person_id"].isin(credit["person_id"]).to_numpy()
    dormant = (rng.random(n) < b.dormant_share) & ~borrower
    activity[dormant] *= b.dormant_multiplier

    ratio = rng.normal(b.spend_ratio_mean, b.spend_ratio_sd, size=n)
    salary_day = np.zeros(n, dtype="int64")
    for seg, (lo, hi) in b.salary_day.items():
        idx = np.flatnonzero(segment == seg)
        salary_day[idx] = rng.integers(lo, hi + 1, size=idx.size)

    return pd.DataFrame(
        {
            "person_id": customers["person_id"].to_numpy(),
            "activity": activity,
            "dormant": dormant,
            "spend_ratio": np.clip(ratio, b.spend_ratio_min, b.spend_ratio_max),
            "salary_day": salary_day,
        }
    )
