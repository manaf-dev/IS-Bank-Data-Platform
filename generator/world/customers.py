"""Generate IS Bank's ground-truth customer population.

One row per real person (or SME and its owner). `person_id` is the simulator's internal
identity: it is never shipped to any source system, and projectors derive each system's
own IDs from it. All values are random; nothing is derived from real people.
"""

import numpy as np
import pandas as pd

from generator.config import WorldConfig
from generator.world.names import (
    NameLists,
    load_name_lists,
    sample_business_names,
    sample_person_names,
)
from generator.world.sampling import uniform_dates
from isbank.reference.ghana import MOBILE_PREFIXES

_DAYS_PER_YEAR = 365.25
_ONE_DAY = np.timedelta64(1, "D")


def generate_customers(
    config: WorldConfig,
    rng: np.random.Generator,
    names: NameLists | None = None,
) -> pd.DataFrame:
    """Return the ground-truth customer table for `config`.

    Columns: person_id, customer_type, segment, home_branch_code, ethnicity, gender,
    first_name, middle_name, last_name, business_name, date_of_birth, joined_on,
    ghana_card, phone_number, mobile_network, email, digital_address,
    monthly_income_pesewas (monthly turnover for SMEs).
    """
    names = names or load_name_lists()
    n = config.customers.count
    branches = config.branches

    branch_idx = rng.choice(len(branches), size=n, p=[b.customer_share for b in branches])
    segment = _sample_segments(config, rng, branch_idx)
    customer_type = np.array([config.archetypes[s].customer_type for s in segment], dtype=object)

    joined_on = _sample_join_dates(config, rng, branch_idx)
    date_of_birth = _sample_birth_dates(config, rng, joined_on)
    gender = np.where(rng.random(n) < config.customers.female_share, "F", "M")

    region = np.array([branches[i].region for i in branch_idx], dtype=object)
    ethnicity = _sample_ethnicity(config, rng, region)
    # Sunday = 0, matching the Akan day-name lists. 1970-01-01 was a Thursday (= 4).
    birth_weekday = (date_of_birth.astype("int64") + 4) % 7
    person = sample_person_names(
        rng, ethnicity, gender, birth_weekday, config.customers.names, names
    )

    business_name = np.full(n, None, dtype=object)
    is_business = customer_type == "business"
    business_name[is_business] = sample_business_names(
        rng, person.last[is_business], config.customers.names, names
    )

    phone_number, mobile_network = _sample_phones(config, rng, n)

    frame = pd.DataFrame(
        {
            "person_id": np.arange(1, n + 1, dtype="int64"),
            "customer_type": customer_type,
            "segment": segment,
            "home_branch_code": np.array([branches[i].branch_code for i in branch_idx]),
            "ethnicity": ethnicity,
            "gender": gender,
            "first_name": person.first,
            "middle_name": person.middle,
            "last_name": person.last,
            "business_name": business_name,
            "date_of_birth": date_of_birth.astype("datetime64[s]"),
            "joined_on": joined_on.astype("datetime64[s]"),
            "ghana_card": _sample_ghana_cards(rng, n),
            "phone_number": phone_number,
            "mobile_network": mobile_network,
            "email": _sample_emails(
                config, rng, person.first, person.last, business_name, customer_type
            ),
            "digital_address": _sample_digital_addresses(config, rng, branch_idx),
            "monthly_income_pesewas": _sample_income(config, rng, segment, branch_idx),
        }
    )
    string_columns = [c for c in frame.columns if frame[c].dtype == object]
    return frame.astype(dict.fromkeys(string_columns, "string"))


def _sample_segments(
    config: WorldConfig, rng: np.random.Generator, branch_idx: np.ndarray
) -> np.ndarray:
    segments = np.array(list(config.archetypes), dtype=object)
    out = np.empty(len(branch_idx), dtype=object)
    for i, branch in enumerate(config.branches):
        idx = np.flatnonzero(branch_idx == i)
        mix = branch.segment_mix or config.segment_mix
        out[idx] = rng.choice(segments, size=idx.size, p=[mix[s] for s in segments])
    return out


def _sample_join_dates(
    config: WorldConfig, rng: np.random.Generator, branch_idx: np.ndarray
) -> np.ndarray:
    """Join dates respect each branch's opening date; most customers predate the window."""
    start = np.datetime64(config.start_date, "D")
    end = np.datetime64(config.end_date, "D")
    earliest = np.datetime64(config.customers.earliest_join_date, "D")
    opened = np.array([np.datetime64(b.opened_on, "D") for b in config.branches])[branch_idx]

    pre_window = (opened < start) & (
        rng.random(len(branch_idx)) < config.customers.pre_window_share
    )
    low = np.where(pre_window, np.maximum(opened, earliest), np.maximum(opened, start))
    high = np.where(pre_window, start - _ONE_DAY, end)
    return uniform_dates(rng, low, high)


def _sample_birth_dates(
    config: WorldConfig, rng: np.random.Generator, joined_on: np.ndarray
) -> np.ndarray:
    """Age at joining is a truncated normal, so every customer is an adult when they join."""
    c = config.customers
    ages = rng.normal(c.age_mean, c.age_sd, size=len(joined_on))
    while (bad := (ages < c.age_min) | (ages >= c.age_max)).any():
        ages[bad] = rng.normal(c.age_mean, c.age_sd, size=int(bad.sum()))
    return joined_on - np.ceil(ages * _DAYS_PER_YEAR).astype("timedelta64[D]")


def _sample_ethnicity(
    config: WorldConfig, rng: np.random.Generator, region: np.ndarray
) -> np.ndarray:
    out = np.empty(len(region), dtype=object)
    for name, weights in config.customers.ethnicity_by_region.items():
        idx = np.flatnonzero(region == name)
        groups = np.array(list(weights), dtype=object)
        out[idx] = rng.choice(groups, size=idx.size, p=list(weights.values()))
    return out


def _sample_ghana_cards(rng: np.random.Generator, n: int) -> np.ndarray:
    """Unique `GHA-XXXXXXXXX-X` numbers; the check digit is random (no real algorithm)."""
    body = rng.choice(10**9, size=n, replace=False)
    check = rng.integers(0, 10, size=n)
    return np.array([f"GHA-{b:09d}-{c}" for b, c in zip(body, check, strict=True)], dtype=object)


def _sample_phones(
    config: WorldConfig, rng: np.random.Generator, n: int
) -> tuple[np.ndarray, np.ndarray]:
    """Unique national-format mobile numbers, with the network implied by the prefix."""
    networks = np.array(list(config.customers.mobile_network_share), dtype=object)
    network = rng.choice(networks, size=n, p=list(config.customers.mobile_network_share.values()))
    prefix = np.empty(n, dtype=object)
    for name in networks:
        idx = np.flatnonzero(network == name)
        prefix[idx] = rng.choice(np.array(MOBILE_PREFIXES[name], dtype=object), size=idx.size)

    subscriber = rng.integers(0, 10**7, size=n)
    numbers = prefix + pd.Series(subscriber).astype(str).str.zfill(7).to_numpy(dtype=object)
    while (dup := pd.Series(numbers).duplicated().to_numpy()).any():
        subscriber[dup] = rng.integers(0, 10**7, size=int(dup.sum()))
        numbers[dup] = prefix[dup] + pd.Series(subscriber[dup]).astype(str).str.zfill(7).to_numpy(
            dtype=object
        )
    return numbers, network


def _sample_emails(
    config: WorldConfig,
    rng: np.random.Generator,
    first: np.ndarray,
    last: np.ndarray,
    business_name: np.ndarray,
    customer_type: np.ndarray,
) -> np.ndarray:
    """Unique addresses on RFC 2606 reserved domains, for a configured share of customers."""
    n = len(first)
    share = np.array([config.customers.email_share[t] for t in customer_type])
    has_email = rng.random(n) < share

    personal = pd.Series(first).str.split().str[-1] + "." + pd.Series(last)
    local = pd.Series(
        np.where(customer_type == "business", business_name, personal), dtype="string"
    )
    local = local.str.lower().str.replace(r"[^a-z.]", "", regex=True)
    with_digits = rng.random(n) < config.customers.email_digit_suffix_share
    local = local.where(~with_digits, local + pd.Series(rng.integers(1, 1000, size=n)).astype(str))

    domains = rng.choice(
        np.array(list(config.customers.email_domains), dtype=object),
        size=n,
        p=list(config.customers.email_domains.values()),
    )
    email = (local + "@" + domains).to_numpy(dtype=object)
    email[~has_email] = None
    # Two people may share a name; make their addresses distinct as a real provider would.
    taken = pd.Series(email).duplicated(keep="first").to_numpy() & has_email
    email[taken] = [
        e.replace("@", f"{i}@") for e, i in zip(email[taken], np.flatnonzero(taken), strict=True)
    ]
    return email


def _sample_digital_addresses(
    config: WorldConfig, rng: np.random.Generator, branch_idx: np.ndarray
) -> np.ndarray:
    """Ghana Post GPS-style addresses in the home branch's district."""
    district = np.array([b.gps_district for b in config.branches], dtype=object)[branch_idx]
    area = rng.integers(100, 10_000, size=len(branch_idx))
    unit = rng.integers(0, 10_000, size=len(branch_idx))
    return np.array(
        [f"{d}-{a}-{u:04d}" for d, a, u in zip(district, area, unit, strict=True)], dtype=object
    )


def _sample_income(
    config: WorldConfig,
    rng: np.random.Generator,
    segment: np.ndarray,
    branch_idx: np.ndarray,
) -> np.ndarray:
    """Lognormal monthly income in integer pesewas, scaled by the branch income index."""
    median = np.array([config.archetypes[s].income_median_ghs for s in segment])
    sigma = np.array([config.archetypes[s].income_sigma for s in segment])
    index = np.array([b.income_index for b in config.branches])[branch_idx]
    ghs = np.exp(np.log(median * index) + sigma * rng.standard_normal(len(segment)))
    return np.round(ghs * 100).astype("int64")
