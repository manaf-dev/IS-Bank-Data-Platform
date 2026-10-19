"""Vectorised sampling of Ghanaian personal and business names.

Name lists live in `data/names.yaml`; every rate lives in `config.yaml`. The world holds
each person's *true* name; spelling variants and formatting quirks are added later by the
projectors, not here.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from generator.config import NameParams

NAMES_PATH = Path(__file__).with_name("data") / "names.yaml"
GENDERS = ("F", "M")
_GENDER_KEY = {"F": "female", "M": "male"}


@dataclass(frozen=True)
class NameLists:
    """Name lists loaded from `names.yaml`, as arrays ready for sampling."""

    akan_day_names: dict[str, np.ndarray]  # gender -> 7 names, Sunday first
    common_given: dict[str, np.ndarray]  # gender -> names
    group_given: dict[str, dict[str, np.ndarray]]  # group -> gender -> names (no Akan)
    surnames: dict[str, np.ndarray]  # group -> names
    business_prefixes: np.ndarray
    business_trades: np.ndarray
    business_suffixes: np.ndarray


@dataclass(frozen=True)
class PersonNames:
    """Sampled names, aligned with the input arrays. Missing middle names are None."""

    first: np.ndarray
    middle: np.ndarray
    last: np.ndarray


def load_name_lists(path: Path = NAMES_PATH) -> NameLists:
    """Load the name lists from YAML."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    def by_gender(entry: dict) -> dict[str, np.ndarray]:
        return {g: np.array(entry[_GENDER_KEY[g]], dtype=object) for g in GENDERS}

    groups = raw["groups"]
    return NameLists(
        akan_day_names=by_gender(raw["akan_day_names"]),
        common_given=by_gender(raw["common_given"]),
        group_given={g: by_gender(v["given"]) for g, v in groups.items() if "given" in v},
        surnames={g: np.array(v["surnames"], dtype=object) for g, v in groups.items()},
        business_prefixes=np.array(raw["business"]["prefixes"], dtype=object),
        business_trades=np.array(raw["business"]["trades"], dtype=object),
        business_suffixes=np.array(raw["business"]["suffixes"], dtype=object),
    )


def rank_weights(n: int, exponent: float) -> np.ndarray:
    """Zipf-like sampling weights: the k-th listed name has weight 1 / k**exponent."""
    weights = 1.0 / np.arange(1, n + 1, dtype=float) ** exponent
    return weights / weights.sum()


def sample_person_names(
    rng: np.random.Generator,
    ethnicity: np.ndarray,
    gender: np.ndarray,
    birth_weekday: np.ndarray,
    params: NameParams,
    lists: NameLists,
) -> PersonNames:
    """Sample first, middle, and last names for each person.

    Akan customers usually carry the day name of their birth weekday (Sunday = 0), as
    either first or middle name, so the planted cultural signal is real but imperfect.
    Other groups mix their own given names with common Christian/English ones.
    """
    n = len(ethnicity)
    first = np.empty(n, dtype=object)
    middle = np.full(n, None, dtype=object)
    last = np.empty(n, dtype=object)

    def pick(pool: np.ndarray, size: int) -> np.ndarray:
        return rng.choice(pool, size=size, p=rank_weights(len(pool), params.popularity_exponent))

    for group in np.unique(ethnicity):
        in_group = ethnicity == group
        last[in_group] = pick(lists.surnames[group], int(in_group.sum()))

        for g in GENDERS:
            idx = np.flatnonzero(in_group & (gender == g))
            if idx.size == 0:
                continue
            common = lists.common_given[g]

            if group == "akan":
                first[idx] = pick(common, idx.size)
                has_day_name = rng.random(idx.size) < params.akan_day_name_rate
                day_names = lists.akan_day_names[g][birth_weekday[idx]]
                day_first = has_day_name & (rng.random(idx.size) < params.day_name_first_share)
                day_middle = has_day_name & ~day_first
                first[idx[day_first]] = day_names[day_first]
                middle[idx[day_middle]] = day_names[day_middle]
                extra_middle = ~has_day_name & (rng.random(idx.size) < params.middle_name_rate)
                middle[idx[extra_middle]] = pick(common, int(extra_middle.sum()))
                continue

            own = lists.group_given[group][g]
            uses_common = rng.random(idx.size) < params.common_given_share[group]
            first[idx] = np.where(uses_common, pick(common, idx.size), pick(own, idx.size))
            has_middle = rng.random(idx.size) < params.middle_name_rate
            # A middle name comes from the other pool, e.g. "Emmanuel Selorm Agbeko".
            other = np.where(uses_common, pick(own, idx.size), pick(common, idx.size))
            middle[idx[has_middle]] = other[has_middle]

    return PersonNames(first=first, middle=middle, last=last)


def sample_business_names(
    rng: np.random.Generator,
    owner_surnames: np.ndarray,
    params: NameParams,
    lists: NameLists,
) -> np.ndarray:
    """Compose SME trading names such as "Adom Hardware Ventures" or "Owusu & Sons Ltd"."""
    n = len(owner_surnames)
    suffix = rng.choice(lists.business_suffixes, size=n)
    prefix = rng.choice(lists.business_prefixes, size=n)
    trade = rng.choice(lists.business_trades, size=n)
    family_firm = rng.random(n) < params.business_family_firm_share

    names = prefix + " " + trade + " " + suffix
    names[family_firm] = owner_surnames[family_firm] + " & Sons " + suffix[family_firm]
    return names
