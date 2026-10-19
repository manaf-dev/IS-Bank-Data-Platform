"""Shared building blocks for projectors: stable randomness, system IDs, and mess.

Projectors run once for the backfill snapshot and again for every daily tick. To keep the
output idempotent, every per-record decision (is this customer duplicated? is this event
late? which typo?) is a pure function of (seed, salt, record id), never a fresh draw from a
stateful generator. The same record therefore gets the same mess in every run.
"""

import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_MASK64 = np.uint64(0xFFFFFFFFFFFFFFFF)


@dataclass
class SourceBatch:
    """One delivery from a source system: named tables plus how they should be written.

    `tables` map a table (or file stem) name to its rows; `formats` say how an emitter writes
    each one (e.g. "sql", "jsonl", "csv", "xlsx", "fixed_width").
    """

    system: str
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    formats: dict[str, str] = field(default_factory=dict)

    def add(self, name: str, frame: pd.DataFrame, fmt: str) -> None:
        """Register a table and the format an emitter should write it in."""
        self.tables[name] = frame.reset_index(drop=True)
        self.formats[name] = fmt


def stable_uniform(seed: int, salt: str, ids: np.ndarray | pd.Series) -> np.ndarray:
    """Uniform [0, 1) values that depend only on (seed, salt, id): splitmix64 hashing."""
    x = np.asarray(ids, dtype="int64").astype("uint64")
    key = np.uint64((seed * 0x9E3779B1 ^ zlib.crc32(salt.encode())) & 0xFFFFFFFFFFFFFFFF)
    with np.errstate(over="ignore"):
        z = (x ^ key) + np.uint64(0x9E3779B97F4A7C15)
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        z = (z ^ (z >> np.uint64(31))) & _MASK64
    return (z >> np.uint64(11)).astype("float64") / float(1 << 53)


def stable_choice(seed: int, salt: str, ids, options: list, weights: list[float] | None = None):
    """Pick one option per id, stably. Weights default to uniform."""
    u = stable_uniform(seed, salt, ids)
    w = np.ones(len(options)) if weights is None else np.asarray(weights, dtype=float)
    edges = np.cumsum(w / w.sum())
    idx = np.minimum(np.searchsorted(edges, u, side="right"), len(options) - 1)
    return np.asarray(options, dtype=object)[idx]


def stable_int(seed: int, salt: str, ids, low: int, high: int) -> np.ndarray:
    """Integers in [low, high], stably."""
    u = stable_uniform(seed, salt, ids)
    return (low + np.floor(u * (high - low + 1))).astype("int64")


def system_id(seed: int, salt: str, ids, modulus: int) -> np.ndarray:
    """A keyed bijection of ids onto [0, modulus): stable, unique, and not the raw id.

    Uses an affine permutation x -> (a*x + b) mod m with gcd(a, m) = 1.
    """
    rng = np.random.default_rng([seed, zlib.crc32(salt.encode())])
    while True:
        a = int(rng.integers(modulus // 3, modulus - 1))
        if np.gcd(a, modulus) == 1:
            break
    b = int(rng.integers(0, modulus))
    x = np.asarray(ids, dtype=object)
    return np.array([(a * int(v) + b) % modulus for v in x], dtype="int64")


# ---------------------------------------------------------------------- mess


def typo(names: pd.Series, seed: int, salt: str, ids) -> pd.Series:
    """Introduce one realistic typo per name: swap, double, or drop a letter."""
    u = stable_uniform(seed, salt + ":kind", ids)
    pos_u = stable_uniform(seed, salt + ":pos", ids)
    out = []
    for name, kind, p in zip(names.astype(str), u, pos_u, strict=True):
        if len(name) < 4:
            out.append(name)
            continue
        i = 1 + int(p * (len(name) - 2))
        if kind < 0.4:
            out.append(name[:i] + name[i + 1] + name[i] + name[i + 2 :])
        elif kind < 0.7:
            out.append(name[: i + 1] + name[i] + name[i + 1 :])
        else:
            out.append(name[:i] + name[i + 1 :])
    return pd.Series(out, index=names.index, dtype="string")


def ghana_card_variants(cards: pd.Series, seed: int, salt: str, ids, weights: dict[str, float]):
    """Re-format canonical GHA-XXXXXXXXX-X numbers the way a given system stores them."""
    style = stable_choice(seed, salt, ids, list(weights), list(weights.values()))
    digits = cards.str.replace("GHA-", "", regex=False)
    body, check = digits.str.slice(0, 9), digits.str.slice(10, 11)
    formatted = {
        "canonical": cards,
        "no_dashes": "GHA" + body + check,
        "lowercase": cards.str.lower(),
        "digits_only": body + check,
        "spaced": "GHA " + body + " " + check,
    }
    out = cards.copy()
    for key, values in formatted.items():
        mask = style == key
        out[mask] = values[mask]
    return out


def phone_variants(phones: pd.Series, seed: int, salt: str, ids, weights: dict[str, float]):
    """Re-format national 0XXXXXXXXX numbers: international, spaced, or as-is."""
    style = stable_choice(seed, salt, ids, list(weights), list(weights.values()))
    national = phones.str.slice(1)
    formatted = {
        "national": phones,
        "international": "+233" + national,
        "spaced": "233 "
        + national.str.slice(0, 2)
        + " "
        + national.str.slice(2, 5)
        + " "
        + national.str.slice(5),
    }
    out = phones.copy()
    for key, values in formatted.items():
        mask = style == key
        out[mask] = values[mask]
    return out


def null_out(values: pd.Series, seed: int, salt: str, ids, share: float) -> pd.Series:
    """Blank a stable share of values (a missing field in the source)."""
    mask = stable_uniform(seed, salt, ids) < share
    out = values.copy()
    out[mask] = pd.NA
    return out


def ghs(pesewas) -> np.ndarray:
    """Pesewas to cedis as float (for sources that store decimal amounts)."""
    return np.asarray(pesewas, dtype="float64") / 100


def ghs_string(pesewas) -> np.ndarray:
    """Pesewas to a human-formatted string such as '3,500.00' (CSV/Excel sources)."""
    return np.array([f"{v / 100:,.2f}" for v in np.asarray(pesewas, dtype="int64")], dtype=object)
