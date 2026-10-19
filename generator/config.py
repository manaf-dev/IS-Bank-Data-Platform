"""Load, profile-merge, and validate `generator/config.yaml`.

The simulator trusts its config completely, so every consistency rule lives here and a
broken config fails at load time instead of silently skewing the generated world.
"""

import copy
import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
_TOLERANCE = 1e-9


class ConfigError(ValueError):
    """Raised when the generator config is inconsistent. Lists every problem found."""


@dataclass(frozen=True)
class Branch:
    """One IS Bank branch and the characteristics of its catchment."""

    branch_code: str
    name: str
    city: str
    region: str
    gps_district: str
    opened_on: dt.date
    customer_share: float
    income_index: float
    digital_index: float
    segment_mix: dict[str, float] | None


@dataclass(frozen=True)
class Archetype:
    """Behavioural parameters of one customer segment."""

    name: str
    customer_type: str
    income_median_ghs: float
    income_sigma: float
    income_regularity: float
    cash_intensity: float


@dataclass(frozen=True)
class Product:
    """A product and its take-up probability for each eligible segment."""

    product_code: str
    name: str
    family: str
    take_up: dict[str, float]


@dataclass(frozen=True)
class NameParams:
    """Rates that shape how names are sampled."""

    popularity_exponent: float
    akan_day_name_rate: float
    day_name_first_share: float
    middle_name_rate: float
    common_given_share: dict[str, float]
    business_family_firm_share: float


@dataclass(frozen=True)
class CustomerParams:
    """Rates and distributions used to generate the customer population."""

    count: int
    pre_window_share: float
    earliest_join_date: dt.date
    age_mean: float
    age_sd: float
    age_min: int
    age_max: int
    female_share: float
    email_share: dict[str, float]
    email_digit_suffix_share: float
    email_domains: dict[str, float]
    mobile_network_share: dict[str, float]
    names: NameParams
    ethnicity_by_region: dict[str, dict[str, float]]


@dataclass(frozen=True)
class WorldConfig:
    """The fully resolved, validated generator config for one profile."""

    profile: str
    seed: int
    start_date: dt.date
    today: dt.date
    currency: str
    branches: tuple[Branch, ...]
    segment_mix: dict[str, float]
    archetypes: dict[str, Archetype]
    products: tuple[Product, ...]
    services: dict[str, dict[str, float]]
    customers: CustomerParams


def load_config(path: Path = DEFAULT_CONFIG_PATH, profile: str = "dev") -> WorldConfig:
    """Load the YAML config, apply `profile` over the base values, and validate it."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_config(raw, profile)


def parse_config(raw: dict[str, Any], profile: str = "dev") -> WorldConfig:
    """Build a validated `WorldConfig` from an already-parsed YAML mapping.

    Raises:
        ConfigError: if the profile is unknown or any consistency rule fails.
    """
    profiles = raw.get("profiles", {})
    if profile not in profiles:
        raise ConfigError(f"unknown profile {profile!r}; available: {sorted(profiles)}")
    merged = _deep_merge(raw, profiles[profile])

    try:
        config = _build(merged, profile)
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"malformed config: {exc!r}") from exc

    problems = validate(config)
    if problems:
        raise ConfigError("invalid generator config:\n- " + "\n- ".join(problems))
    return config


def validate(config: WorldConfig) -> list[str]:
    """Return every consistency problem in `config` (empty list means valid)."""
    problems: list[str] = []
    segments = set(config.archetypes)
    cust = config.customers

    if config.start_date >= config.today:
        problems.append("simulation.start_date must be before simulation.today")

    codes = [b.branch_code for b in config.branches]
    if len(set(codes)) != len(codes):
        problems.append("branch codes must be unique")
    if len({b.name for b in config.branches}) != len(codes):
        problems.append("branch names must be unique")
    _check_weights(
        problems,
        "branch customer_share",
        {b.branch_code: b.customer_share for b in config.branches},
    )

    _check_mix(problems, "segments.mix", config.segment_mix, segments)
    for b in config.branches:
        if b.opened_on > config.today:
            problems.append(f"{b.branch_code}: opened_on is after simulation.today")
        if b.region not in cust.ethnicity_by_region:
            problems.append(f"{b.branch_code}: no ethnicity weights for region {b.region!r}")
        if b.segment_mix is not None:
            _check_mix(problems, f"{b.branch_code}.segment_mix", b.segment_mix, segments)

    for a in config.archetypes.values():
        if a.customer_type not in {"individual", "business"}:
            problems.append(f"{a.name}: customer_type must be 'individual' or 'business'")
        if a.income_median_ghs <= 0 or a.income_sigma <= 0:
            problems.append(f"{a.name}: income median and sigma must be positive")
        for field in ("income_regularity", "cash_intensity"):
            if not 0 <= getattr(a, field) <= 1:
                problems.append(f"{a.name}: {field} must be in [0, 1]")

    product_codes = [p.product_code for p in config.products]
    if len(set(product_codes)) != len(product_codes):
        problems.append("product codes must be unique")
    for p in config.products:
        if p.family not in {"deposit", "card", "loan"}:
            problems.append(f"{p.product_code}: unknown family {p.family!r}")
        _check_probabilities(problems, p.product_code, p.take_up, segments)
    deposit_segments = {s for p in config.products if p.family == "deposit" for s in p.take_up}
    if deposit_segments != segments:
        problems.append(
            f"segments without a deposit product: {sorted(segments - deposit_segments)}"
        )
    for service, take_up in config.services.items():
        _check_probabilities(problems, f"services.{service}", take_up, segments)

    if cust.count <= 0:
        problems.append("customers.count must be positive")
    for field in ("pre_window_share", "female_share", "email_digit_suffix_share"):
        if not 0 <= getattr(cust, field) <= 1:
            problems.append(f"customers.{field} must be in [0, 1]")
    if cust.earliest_join_date >= config.start_date:
        problems.append("customers.earliest_join_date must be before simulation.start_date")
    if not cust.age_min < cust.age_max or cust.age_sd <= 0:
        problems.append("customers.age_at_join: need min < max and sd > 0")
    if not cust.age_min <= cust.age_mean <= cust.age_max:
        problems.append("customers.age_at_join.mean must lie within [min, max]")
    customer_types = {a.customer_type for a in config.archetypes.values()}
    if set(cust.email_share) != customer_types:
        problems.append(f"customers.email_share must cover customer types {sorted(customer_types)}")
    _check_weights(problems, "customers.email_domains", cust.email_domains)
    _check_weights(problems, "customers.mobile_network_share", cust.mobile_network_share)
    for region, weights in cust.ethnicity_by_region.items():
        _check_weights(problems, f"ethnicity_by_region.{region}", weights)
    names = cust.names
    for field in (
        "akan_day_name_rate",
        "day_name_first_share",
        "middle_name_rate",
        "business_family_firm_share",
    ):
        if not 0 <= getattr(names, field) <= 1:
            problems.append(f"customers.names.{field} must be in [0, 1]")
    ethnic_groups = {g for weights in cust.ethnicity_by_region.values() for g in weights}
    if set(names.common_given_share) != ethnic_groups - {"akan"}:
        problems.append("customers.names.common_given_share must cover every non-Akan group")
    if any(not 0 <= s <= 1 for s in names.common_given_share.values()):
        problems.append("customers.names.common_given_share values must be in [0, 1]")
    if names.popularity_exponent < 0:
        problems.append("customers.names.popularity_exponent must be non-negative")
    return problems


def _build(raw: dict[str, Any], profile: str) -> WorldConfig:
    sim = raw["simulation"]
    seg = raw["segments"]
    cust = raw["customers"]
    age = cust["age_at_join"]
    names = cust["names"]
    return WorldConfig(
        profile=profile,
        seed=int(raw["seed"]),
        start_date=sim["start_date"],
        today=sim["today"],
        currency=sim["currency"],
        branches=tuple(
            Branch(
                branch_code=b["branch_code"],
                name=b["name"],
                city=b["city"],
                region=b["region"],
                gps_district=b["gps_district"],
                opened_on=b["opened_on"],
                customer_share=float(b["customer_share"]),
                income_index=float(b["income_index"]),
                digital_index=float(b["digital_index"]),
                segment_mix=b.get("segment_mix"),
            )
            for b in raw["branches"]
        ),
        segment_mix=seg["mix"],
        archetypes={
            name: Archetype(
                name=name,
                customer_type=a["customer_type"],
                income_median_ghs=float(a["monthly_income_ghs"]["median"]),
                income_sigma=float(a["monthly_income_ghs"]["sigma"]),
                income_regularity=float(a["income_regularity"]),
                cash_intensity=float(a["cash_intensity"]),
            )
            for name, a in seg["archetypes"].items()
        },
        products=tuple(
            Product(
                product_code=p["product_code"],
                name=p["name"],
                family=p["family"],
                take_up=p["take_up"],
            )
            for p in raw["products"]
        ),
        services=raw["services"],
        customers=CustomerParams(
            count=int(cust["count"]),
            pre_window_share=float(cust["pre_window_share"]),
            earliest_join_date=cust["earliest_join_date"],
            age_mean=float(age["mean"]),
            age_sd=float(age["sd"]),
            age_min=int(age["min"]),
            age_max=int(age["max"]),
            female_share=float(cust["female_share"]),
            email_share=cust["email_share"],
            email_digit_suffix_share=float(cust["email_digit_suffix_share"]),
            email_domains=cust["email_domains"],
            mobile_network_share=cust["mobile_network_share"],
            names=NameParams(
                popularity_exponent=float(names["popularity_exponent"]),
                akan_day_name_rate=float(names["akan_day_name_rate"]),
                day_name_first_share=float(names["day_name_first_share"]),
                middle_name_rate=float(names["middle_name_rate"]),
                common_given_share=names["common_given_share"],
                business_family_firm_share=float(names["business_family_firm_share"]),
            ),
            ethnicity_by_region=cust["ethnicity_by_region"],
        ),
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _check_weights(problems: list[str], label: str, weights: dict[str, float]) -> None:
    if any(w < 0 for w in weights.values()):
        problems.append(f"{label}: weights must be non-negative")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=_TOLERANCE):
        problems.append(f"{label}: weights sum to {sum(weights.values()):.6f}, expected 1")


def _check_mix(problems: list[str], label: str, mix: dict[str, float], segments: set[str]) -> None:
    if set(mix) != segments:
        problems.append(f"{label}: must list exactly the segments {sorted(segments)}")
    _check_weights(problems, label, mix)


def _check_probabilities(
    problems: list[str], label: str, take_up: dict[str, float], segments: set[str]
) -> None:
    if unknown := set(take_up) - segments:
        problems.append(f"{label}: unknown segments {sorted(unknown)}")
    if any(not 0 < p <= 1 for p in take_up.values()):
        problems.append(f"{label}: take-up rates must be in (0, 1]")
