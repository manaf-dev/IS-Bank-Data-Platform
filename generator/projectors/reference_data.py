"""Reference data: daily synthetic GHS exchange rates.

Ghana public holidays and macro indicators come from real public APIs (Nager.Date and the
World Bank); the emitter fetches those when it runs. Only the synthetic part lives here, so
generation never needs the network.
"""

import datetime as dt

import numpy as np
import pandas as pd

from generator.projectors.common import SourceBatch
from generator.world import World

SYSTEM = "reference_data"


def fx_rates(world: World, since: dt.date | None, until: dt.date) -> SourceBatch:
    """GHS per unit of USD/EUR/GBP for each day in (since, until]: a seeded random walk."""
    frame = fx_history(world)
    lo = pd.Timestamp.min if since is None else pd.Timestamp(since)
    frame = frame[(frame["rate_date"] > lo) & (frame["rate_date"] <= pd.Timestamp(until))]
    batch = SourceBatch(SYSTEM)
    batch.add("fx_rates", frame, "csv", partition_by="rate_date")
    return batch


def fx_history(world: World) -> pd.DataFrame:
    """The whole simulated FX history (deterministic for a given seed)."""
    days = pd.date_range(world.config.start_date, world.config.end_date, freq="D")
    rows = []
    for i, (currency, params) in enumerate(world.config.projectors[SYSTEM]["fx"].items()):
        rng = np.random.default_rng([world.config.seed, 7919, i])
        steps = params["drift_per_day"] + params["vol_per_day"] * rng.standard_normal(len(days))
        rate = params["start"] * np.exp(np.cumsum(steps))
        rows.append(
            pd.DataFrame(
                {
                    "rate_date": days,
                    "base_currency": currency,
                    "quote_currency": "GHS",
                    "mid_rate": np.round(rate, 4),
                }
            )
        )
    return pd.concat(rows, ignore_index=True).sort_values(
        ["rate_date", "base_currency"], ignore_index=True
    )
