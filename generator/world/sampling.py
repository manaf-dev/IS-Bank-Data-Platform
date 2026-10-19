"""Small vectorised sampling helpers shared by the world simulator."""

import numpy as np


def uniform_dates(rng: np.random.Generator, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    """Draw one date uniformly from each inclusive [low, high] range (datetime64[D])."""
    span = (high - low).astype("int64") + 1
    return low + (rng.random(len(low)) * span).astype("timedelta64[D]")


def add_months(dates: np.ndarray, months: np.ndarray) -> np.ndarray:
    """Add whole calendar months, clamping to month end (31 Jan + 1 month = 28/29 Feb)."""
    month_start = dates.astype("datetime64[M]")
    day_offset = dates - month_start.astype("datetime64[D]")
    target_month = month_start + months.astype("timedelta64[M]")
    first_day = target_month.astype("datetime64[D]")
    next_first_day = (target_month + np.timedelta64(1, "M")).astype("datetime64[D]")
    last_day_offset = next_first_day - first_day - np.timedelta64(1, "D")
    return first_day + np.minimum(day_offset, last_day_offset)
