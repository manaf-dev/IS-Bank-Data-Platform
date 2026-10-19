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
class ProductTerms:
    """Contract terms drawn when a product is opened."""

    amount_median_income_multiple: float
    amount_sigma: float
    rate_min_pct: float
    rate_max_pct: float
    term_months: tuple[int, ...] | None
    term_weights: tuple[float, ...] | None


@dataclass(frozen=True)
class Product:
    """A product, its take-up per eligible segment, dependencies, and contract terms."""

    product_code: str
    name: str
    family: str
    take_up: dict[str, float]
    requires_any: tuple[str, ...] = ()
    terms: ProductTerms | None = None


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
class MerchantCategory:
    """A merchant category (MCC) and how often card spend goes to it."""

    name: str
    mcc: str
    weight: float
    channel: str  # "pos", "online", or "both"
    label: str


@dataclass(frozen=True)
class MerchantParams:
    """How the merchant population is generated and chosen for purchases."""

    per_1000_customers: float
    local_share: float
    categories: dict[str, MerchantCategory]


@dataclass(frozen=True)
class FlowMix:
    """Poisson mean of monthly events and the channel mix for one segment."""

    events: float
    channels: dict[str, float]


@dataclass(frozen=True)
class CreditCardParams:
    """Credit card usage and repayment behaviour."""

    purchase_share: float
    repayment_day: int
    pay_full_share: float
    min_payment_pct: float


@dataclass(frozen=True)
class BehaviourParams:
    """Rates that drive monthly money flows (see `behaviour:` in config.yaml)."""

    activity_median: float
    activity_sigma: float
    dormant_share: float
    dormant_multiplier: float
    opening_balance_median: float
    opening_balance_sigma: float
    spend_ratio_mean: float
    spend_ratio_sd: float
    spend_ratio_min: float
    spend_ratio_max: float
    income_noise_sd: float
    salary_day: dict[str, tuple[int, int]]
    irregular_inflows: dict[str, FlowMix]
    spending: dict[str, FlowMix]
    operating_account_priority: tuple[str, ...]
    savings_sweep_share: float
    seasonality: dict[int, float]
    hour_weights: tuple[float, ...]
    branch_open_hour: int
    branch_close_hour: int
    credit_card: CreditCardParams
    monthly_fee_ghs: dict[str, float]
    counterparty_banks: tuple[str, ...]
    billers: dict[str, float]


@dataclass(frozen=True)
class Range:
    """An inclusive numeric range drawn from uniformly."""

    min: float
    max: float


@dataclass(frozen=True)
class DefaultParams:
    """Logistic model for a loan's true probability of default, and the shock it causes."""

    intercept: float
    dti: float
    stress: float
    log_income: float
    segment_effect: dict[str, float]
    noise_sd: float
    shock_income_share: Range
    shock_spend_share: float
    shock_balance_drain_share: float
    shock_min_months: int
    shock_max_term_share: float


@dataclass(frozen=True)
class ComplaintParams:
    """How often customers complain, about what, and how complaints are resolved."""

    per_customer_per_year: float
    categories: dict[str, float]
    resolved_share: float
    resolution_days: Range
    fraud_report_share: float


@dataclass(frozen=True)
class ChurnParams:
    """Monthly churn hazard and the observable run-down before a customer leaves."""

    base_monthly_hazard: dict[str, float]
    unresolved_complaint: float
    low_activity: float
    low_activity_threshold: float
    noise_sd: float
    ramp_months: Range
    ramp_floor: float
    closing_transfer_share: float
    earliest_month: int


@dataclass(frozen=True)
class CardTestingParams:
    """Card testing: many tiny online probes, then a cash-out purchase."""

    monthly_victims_per_1000_cards: float
    probes: Range
    probe_amount_ghs: Range
    probe_window_minutes: int
    cashout_income_multiple: Range


@dataclass(frozen=True)
class GeoJumpParams:
    """Night-time card purchases in a city far from the cardholder's home."""

    monthly_victims_per_1000_cards: float
    purchases: Range
    hours: tuple[int, ...]
    amount_income_multiple: Range


@dataclass(frozen=True)
class TakeoverParams:
    """Account-takeover rings sharing beneficiary accounts and devices."""

    rings_per_year_per_10k_customers: float
    victims: Range
    beneficiaries: Range
    devices: Range
    campaign_days: int
    transfers_per_victim: Range
    transfer_income_multiple: Range
    night_share: float


@dataclass(frozen=True)
class SignalParams:
    """Planted signals: default, complaints, churn, fraud, and the noise around them."""

    macro_stress_by_quarter: dict[str, float]
    pre_window_macro_stress: float
    default: DefaultParams
    complaints: ComplaintParams
    churn: ChurnParams
    beneficiaries_per_customer: Range
    new_beneficiary_share: float
    device_count_weights: dict[int, float]
    legit_night_purchase_share: float
    card_testing: CardTestingParams
    geo_jump: GeoJumpParams
    takeover: TakeoverParams


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
    momo_same_number_share: float
    contract_amount_rounding_ghs: int
    dependent_open_lookback_months: int
    merchants: MerchantParams
    behaviour: BehaviourParams
    signals: SignalParams


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
    by_code = {p.product_code: p for p in config.products}
    for p in config.products:
        _check_product_structure(problems, p, by_code)
    # Every customer is guaranteed an account they can open on day one.
    deposit_segments = {
        s
        for p in config.products
        if p.family == "deposit" and not p.requires_any
        for s in p.take_up
    }
    if deposit_segments != segments:
        problems.append(
            f"segments without a standalone deposit product: {sorted(segments - deposit_segments)}"
        )
    for service, take_up in config.services.items():
        _check_probabilities(problems, f"services.{service}", take_up, segments)

    if not 0 <= config.momo_same_number_share <= 1:
        problems.append("holdings.momo_same_number_share must be in [0, 1]")
    if config.contract_amount_rounding_ghs <= 0:
        problems.append("holdings.contract_amount_rounding_ghs must be positive")
    if config.dependent_open_lookback_months < 0:
        problems.append("holdings.dependent_open_lookback_months must be non-negative")
    _check_merchants(problems, config.merchants)
    _check_behaviour(problems, config, segments)
    _check_signals(problems, config, segments)
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
                requires_any=tuple(p.get("requires_any", ())),
                terms=_build_terms(p["terms"]) if "terms" in p else None,
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
        momo_same_number_share=float(raw["holdings"]["momo_same_number_share"]),
        contract_amount_rounding_ghs=int(raw["holdings"]["contract_amount_rounding_ghs"]),
        dependent_open_lookback_months=int(raw["holdings"]["dependent_open_lookback_months"]),
        merchants=_build_merchants(raw["merchants"]),
        behaviour=_build_behaviour(raw["behaviour"]),
        signals=_build_signals(raw["signals"]),
    )


def _range(raw: dict[str, Any]) -> Range:
    return Range(min=float(raw["min"]), max=float(raw["max"]))


def _build_signals(raw: dict[str, Any]) -> SignalParams:
    d, c, ch, f = raw["default"], raw["complaints"], raw["churn"], raw["fraud"]
    ct, gj, ato = f["card_testing"], f["night_geo_jump"], f["account_takeover"]
    return SignalParams(
        macro_stress_by_quarter={
            str(k): float(v) for k, v in raw["macro_stress_by_quarter"].items()
        },
        pre_window_macro_stress=float(raw["pre_window_macro_stress"]),
        default=DefaultParams(
            intercept=float(d["intercept"]),
            dti=float(d["dti"]),
            stress=float(d["stress"]),
            log_income=float(d["log_income"]),
            segment_effect={k: float(v) for k, v in d["segment_effect"].items()},
            noise_sd=float(d["noise_sd"]),
            shock_income_share=_range(d["shock_income_share"]),
            shock_spend_share=float(d["shock_spend_share"]),
            shock_balance_drain_share=float(d["shock_balance_drain_share"]),
            shock_min_months=int(d["shock_min_months"]),
            shock_max_term_share=float(d["shock_max_term_share"]),
        ),
        complaints=ComplaintParams(
            per_customer_per_year=float(c["per_customer_per_year"]),
            categories={k: float(v) for k, v in c["categories"].items()},
            resolved_share=float(c["resolved_share"]),
            resolution_days=_range(c["resolution_days"]),
            fraud_report_share=float(c["fraud_report_share"]),
        ),
        churn=ChurnParams(
            base_monthly_hazard={k: float(v) for k, v in ch["base_monthly_hazard"].items()},
            unresolved_complaint=float(ch["unresolved_complaint"]),
            low_activity=float(ch["low_activity"]),
            low_activity_threshold=float(ch["low_activity_threshold"]),
            noise_sd=float(ch["noise_sd"]),
            ramp_months=_range(ch["ramp_months"]),
            ramp_floor=float(ch["ramp_floor"]),
            closing_transfer_share=float(ch["closing_transfer_share"]),
            earliest_month=int(ch["earliest_month"]),
        ),
        beneficiaries_per_customer=_range(raw["beneficiaries"]["per_customer"]),
        new_beneficiary_share=float(raw["beneficiaries"]["new_beneficiary_share"]),
        device_count_weights={
            int(k): float(v) for k, v in raw["devices"]["per_customer_weights"].items()
        },
        legit_night_purchase_share=float(raw["legit_night_purchase_share"]),
        card_testing=CardTestingParams(
            monthly_victims_per_1000_cards=float(ct["monthly_victims_per_1000_cards"]),
            probes=_range(ct["probes"]),
            probe_amount_ghs=_range(ct["probe_amount_ghs"]),
            probe_window_minutes=int(ct["probe_window_minutes"]),
            cashout_income_multiple=_range(ct["cashout_income_multiple"]),
        ),
        geo_jump=GeoJumpParams(
            monthly_victims_per_1000_cards=float(gj["monthly_victims_per_1000_cards"]),
            purchases=_range(gj["purchases"]),
            hours=tuple(int(h) for h in gj["hours"]),
            amount_income_multiple=_range(gj["amount_income_multiple"]),
        ),
        takeover=TakeoverParams(
            rings_per_year_per_10k_customers=float(ato["rings_per_year_per_10k_customers"]),
            victims=_range(ato["victims"]),
            beneficiaries=_range(ato["beneficiaries"]),
            devices=_range(ato["devices"]),
            campaign_days=int(ato["campaign_days"]),
            transfers_per_victim=_range(ato["transfers_per_victim"]),
            transfer_income_multiple=_range(ato["transfer_income_multiple"]),
            night_share=float(ato["night_share"]),
        ),
    )


def _build_merchants(raw: dict[str, Any]) -> MerchantParams:
    return MerchantParams(
        per_1000_customers=float(raw["per_1000_customers"]),
        local_share=float(raw["local_share"]),
        categories={
            name: MerchantCategory(
                name=name,
                mcc=str(c["mcc"]),
                weight=float(c["weight"]),
                channel=c["channel"],
                label=c["label"],
            )
            for name, c in raw["categories"].items()
        },
    )


def _build_behaviour(raw: dict[str, Any]) -> BehaviourParams:
    def flows(section: dict[str, Any]) -> dict[str, FlowMix]:
        return {
            seg: FlowMix(events=float(v["events"]), channels=v["channels"])
            for seg, v in section.items()
        }

    act = raw["activity"]
    bal = raw["opening_balance_income_multiple"]
    spend = raw["spend_ratio"]
    cc = raw["credit_card"]
    return BehaviourParams(
        activity_median=float(act["median"]),
        activity_sigma=float(act["sigma"]),
        dormant_share=float(act["dormant_share"]),
        dormant_multiplier=float(act["dormant_multiplier"]),
        opening_balance_median=float(bal["median"]),
        opening_balance_sigma=float(bal["sigma"]),
        spend_ratio_mean=float(spend["mean"]),
        spend_ratio_sd=float(spend["sd"]),
        spend_ratio_min=float(spend["min"]),
        spend_ratio_max=float(spend["max"]),
        income_noise_sd=float(raw["income_noise_sd"]),
        salary_day={s: (int(d["min"]), int(d["max"])) for s, d in raw["salary_day"].items()},
        irregular_inflows=flows(raw["irregular_inflows"]),
        spending=flows(raw["spending"]),
        operating_account_priority=tuple(raw["operating_account_priority"]),
        savings_sweep_share=float(raw["savings_sweep_share"]),
        seasonality={int(m): float(v) for m, v in raw["seasonality"].items()},
        hour_weights=tuple(float(w) for w in raw["hour_weights"]),
        branch_open_hour=int(raw["branch_hours"]["open"]),
        branch_close_hour=int(raw["branch_hours"]["close"]),
        credit_card=CreditCardParams(
            purchase_share=float(cc["purchase_share"]),
            repayment_day=int(cc["repayment_day"]),
            pay_full_share=float(cc["pay_full_share"]),
            min_payment_pct=float(cc["min_payment_pct"]),
        ),
        monthly_fee_ghs={k: float(v) for k, v in raw["monthly_fee_ghs"].items()},
        counterparty_banks=tuple(raw["counterparty_banks"]),
        billers=raw["billers"],
    )


def _build_terms(raw: dict[str, Any]) -> ProductTerms:
    amount = raw["amount_income_multiple"]
    rate = raw["annual_rate_pct"]
    tenor = raw.get("term_months")
    return ProductTerms(
        amount_median_income_multiple=float(amount["median"]),
        amount_sigma=float(amount["sigma"]),
        rate_min_pct=float(rate["min"]),
        rate_max_pct=float(rate["max"]),
        term_months=tuple(int(v) for v in tenor["values"]) if tenor else None,
        term_weights=tuple(float(w) for w in tenor["weights"]) if tenor else None,
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


def _check_product_structure(problems: list[str], p: Product, by_code: dict[str, Product]) -> None:
    """Dependencies must point at deposit accounts; term products need complete terms."""
    code = p.product_code
    for required in p.requires_any:
        if required not in by_code:
            problems.append(f"{code}: requires unknown product {required!r}")
        elif by_code[required].family != "deposit" or by_code[required].requires_any:
            problems.append(f"{code}: may only require standalone deposits, not {required!r}")
    if p.family in {"card", "loan"} and not p.requires_any:
        problems.append(f"{code}: cards and loans must require an account to link to")
    needs_tenor = p.family == "loan" or code.startswith("FD_")
    terms = p.terms
    if terms is None:
        if p.family == "loan" or needs_tenor:
            problems.append(f"{code}: missing terms")
        return
    if terms.amount_median_income_multiple <= 0 or terms.amount_sigma <= 0:
        problems.append(f"{code}: amount median and sigma must be positive")
    if not 0 < terms.rate_min_pct <= terms.rate_max_pct:
        problems.append(f"{code}: need 0 < annual_rate_pct.min <= max")
    if needs_tenor and terms.term_months is None:
        problems.append(f"{code}: missing term_months")
    if terms.term_months is not None:
        weights = terms.term_weights or ()
        if len(weights) != len(terms.term_months) or any(m <= 0 for m in terms.term_months):
            problems.append(f"{code}: term_months values and weights must align and be positive")
        else:
            tenor_weights = dict(zip(map(str, terms.term_months), weights, strict=True))
            _check_weights(problems, f"{code}.term_months", tenor_weights)


INFLOW_CHANNELS = frozenset({"BRANCH_CASH_DEPOSIT", "MOMO_TO_BANK", "TRANSFER_IN"})
SPEND_CHANNELS = frozenset(
    {
        "ATM_WITHDRAWAL",
        "BRANCH_WITHDRAWAL",
        "POS_PURCHASE",
        "ONLINE_PURCHASE",
        "BANK_TO_MOMO",
        "TRANSFER_OUT",
        "BILL_PAYMENT",
    }
)


def _check_merchants(problems: list[str], m: MerchantParams) -> None:
    if m.per_1000_customers <= 0:
        problems.append("merchants.per_1000_customers must be positive")
    if not 0 <= m.local_share <= 1:
        problems.append("merchants.local_share must be in [0, 1]")
    weights = {k: c.weight for k, c in m.categories.items()}
    _check_weights(problems, "merchants.categories", weights)
    for c in m.categories.values():
        if c.channel not in {"pos", "online", "both"}:
            problems.append(f"merchants.{c.name}: channel must be pos, online, or both")
    channels = {c.channel for c in m.categories.values()}
    if not {"online", "both"} & channels or not {"pos", "both"} & channels:
        problems.append("merchants: need categories for both POS and online purchases")


def _check_behaviour(problems: list[str], config: WorldConfig, segments: set[str]) -> None:
    b = config.behaviour
    salaried = set(b.salary_day)
    irregular = set(b.irregular_inflows)
    if salaried & irregular or salaried | irregular != segments:
        problems.append(
            "behaviour: each segment needs exactly one of salary_day or irregular_inflows"
        )
    if set(b.spending) != segments:
        problems.append("behaviour.spending must cover every segment")
    for seg, (lo, hi) in b.salary_day.items():
        if not 1 <= lo <= hi <= 31:
            problems.append(f"behaviour.salary_day.{seg}: need 1 <= min <= max <= 31")
    for label, mixes, allowed in (
        ("irregular_inflows", b.irregular_inflows, INFLOW_CHANNELS),
        ("spending", b.spending, SPEND_CHANNELS),
    ):
        for seg, mix in mixes.items():
            if mix.events < 0:
                problems.append(f"behaviour.{label}.{seg}.events must be non-negative")
            if unknown := set(mix.channels) - allowed:
                problems.append(f"behaviour.{label}.{seg}: unknown channels {sorted(unknown)}")
            _check_weights(problems, f"behaviour.{label}.{seg}.channels", mix.channels)
    standalone = {p.product_code for p in config.products if not p.requires_any}
    if not set(b.operating_account_priority) <= standalone:
        problems.append("behaviour.operating_account_priority must list standalone deposits")
    if set(b.seasonality) != set(range(1, 13)) or any(v <= 0 for v in b.seasonality.values()):
        problems.append("behaviour.seasonality needs a positive multiplier for months 1-12")
    if len(b.hour_weights) != 24 or any(w < 0 for w in b.hour_weights):
        problems.append("behaviour.hour_weights needs 24 non-negative weights")
    if not 0 <= b.branch_open_hour < b.branch_close_hour <= 24:
        problems.append("behaviour.branch_hours: need 0 <= open < close <= 24")
    if not b.spend_ratio_min <= b.spend_ratio_mean <= b.spend_ratio_max or b.spend_ratio_sd <= 0:
        problems.append("behaviour.spend_ratio: need min <= mean <= max and sd > 0")
    for field in ("dormant_share", "savings_sweep_share"):
        if not 0 <= getattr(b, field) <= 1:
            problems.append(f"behaviour.{field} must be in [0, 1]")
    cc = b.credit_card
    if not 0 <= cc.purchase_share <= 1 or not 0 <= cc.pay_full_share <= 1:
        problems.append("behaviour.credit_card shares must be in [0, 1]")
    if not 1 <= cc.repayment_day <= 28 or not 0 < cc.min_payment_pct <= 100:
        problems.append("behaviour.credit_card: repayment_day 1-28, min_payment_pct in (0, 100]")
    if not set(b.monthly_fee_ghs) <= standalone:
        problems.append("behaviour.monthly_fee_ghs keys must be standalone deposit products")
    if not b.counterparty_banks:
        problems.append("behaviour.counterparty_banks must not be empty")
    _check_weights(problems, "behaviour.billers", b.billers)


def _check_signals(problems: list[str], config: WorldConfig, segments: set[str]) -> None:
    sig = config.signals
    expected_quarters = _quarters(config.start_date, config.today)
    if set(sig.macro_stress_by_quarter) != expected_quarters:
        problems.append(f"signals.macro_stress_by_quarter must cover {sorted(expected_quarters)}")
    ranges = {
        "default.shock_income_share": sig.default.shock_income_share,
        "complaints.resolution_days": sig.complaints.resolution_days,
        "churn.ramp_months": sig.churn.ramp_months,
        "beneficiaries.per_customer": sig.beneficiaries_per_customer,
        "fraud.card_testing.probes": sig.card_testing.probes,
        "fraud.card_testing.probe_amount_ghs": sig.card_testing.probe_amount_ghs,
        "fraud.card_testing.cashout_income_multiple": sig.card_testing.cashout_income_multiple,
        "fraud.night_geo_jump.purchases": sig.geo_jump.purchases,
        "fraud.night_geo_jump.amount_income_multiple": sig.geo_jump.amount_income_multiple,
        "fraud.account_takeover.victims": sig.takeover.victims,
        "fraud.account_takeover.beneficiaries": sig.takeover.beneficiaries,
        "fraud.account_takeover.devices": sig.takeover.devices,
        "fraud.account_takeover.transfers_per_victim": sig.takeover.transfers_per_victim,
        "fraud.account_takeover.transfer_income_multiple": sig.takeover.transfer_income_multiple,
    }
    for label, r in ranges.items():
        if not 0 <= r.min <= r.max:
            problems.append(f"signals.{label}: need 0 <= min <= max")
    for label, mapping in (
        ("default.segment_effect", sig.default.segment_effect),
        ("churn.base_monthly_hazard", sig.churn.base_monthly_hazard),
    ):
        if set(mapping) != segments:
            problems.append(f"signals.{label} must cover every segment")
    if any(not 0 <= h < 1 for h in sig.churn.base_monthly_hazard.values()):
        problems.append("signals.churn.base_monthly_hazard values must be in [0, 1)")
    shares = {
        "default.shock_spend_share": sig.default.shock_spend_share,
        "default.shock_max_term_share": sig.default.shock_max_term_share,
        "default.shock_balance_drain_share": sig.default.shock_balance_drain_share,
        "complaints.resolved_share": sig.complaints.resolved_share,
        "complaints.fraud_report_share": sig.complaints.fraud_report_share,
        "churn.ramp_floor": sig.churn.ramp_floor,
        "churn.closing_transfer_share": sig.churn.closing_transfer_share,
        "legit_night_purchase_share": sig.legit_night_purchase_share,
        "beneficiaries.new_beneficiary_share": sig.new_beneficiary_share,
        "fraud.account_takeover.night_share": sig.takeover.night_share,
    }
    for label, value in shares.items():
        if not 0 <= value <= 1:
            problems.append(f"signals.{label} must be in [0, 1]")
    _check_weights(problems, "signals.complaints.categories", sig.complaints.categories)
    _check_weights(
        problems,
        "signals.devices.per_customer_weights",
        {str(k): v for k, v in sig.device_count_weights.items()},
    )
    if any(not 0 <= h <= 23 for h in sig.geo_jump.hours) or not sig.geo_jump.hours:
        problems.append("signals.fraud.night_geo_jump.hours must be hours 0-23")
    if sig.churn.earliest_month < 0 or sig.default.shock_min_months < 1:
        problems.append("signals: churn.earliest_month >= 0 and default.shock_min_months >= 1")


def _quarters(start: dt.date, end: dt.date) -> set[str]:
    quarters = set()
    year, quarter = start.year, (start.month - 1) // 3 + 1
    while (year, quarter) <= (end.year, (end.month - 1) // 3 + 1):
        quarters.add(f"{year}Q{quarter}")
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return quarters
