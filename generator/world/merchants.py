"""Generate the merchants IS Bank customers buy from.

Merchants are spread across the branch cities in proportion to the customer base, so most
card spend is local. Names are composed from the business-name lists and never refer to
real businesses.
"""

import numpy as np
import pandas as pd

from generator.config import WorldConfig
from generator.world.names import NameLists, load_name_lists


def generate_merchants(
    config: WorldConfig, rng: np.random.Generator, names: NameLists | None = None
) -> pd.DataFrame:
    """Return merchants: merchant_id, merchant_name, category, mcc, channel, city."""
    names = names or load_name_lists()
    params = config.merchants
    n = max(1, round(params.per_1000_customers * config.customers.count / 1000))

    categories = list(params.categories.values())
    cat_idx = rng.choice(len(categories), size=n, p=[c.weight for c in categories])

    city_share: dict[str, float] = {}
    for b in config.branches:
        city_share[b.city] = city_share.get(b.city, 0.0) + b.customer_share
    cities = np.array(list(city_share), dtype=object)
    city = rng.choice(cities, size=n, p=list(city_share.values()))

    prefix = rng.choice(names.business_prefixes, size=n)
    label = np.array([categories[i].label for i in cat_idx], dtype=object)
    frame = pd.DataFrame(
        {
            "merchant_id": np.arange(1, n + 1, dtype="int64"),
            "merchant_name": prefix + " " + label,
            "category": [categories[i].name for i in cat_idx],
            "mcc": [categories[i].mcc for i in cat_idx],
            "channel": [categories[i].channel for i in cat_idx],
            "city": city,
        }
    )
    # Online-only merchants are not tied to a city.
    frame.loc[frame["channel"] == "online", "city"] = None
    return frame.astype(
        {c: "string" for c in ("merchant_name", "category", "mcc", "channel", "city")}
    )
