"""Tests for the generator config loader and its validation rules.

The shipped config must load cleanly, and each class of mistake (weights that don't sum
to 1, unknown segments, impossible dates) must be rejected at load time.
"""

import copy
import datetime as dt
from pathlib import Path

import pytest
import yaml

from generator.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, parse_config


@pytest.fixture(scope="module")
def raw() -> dict:
    return yaml.safe_load(Path(DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))


def broken(raw: dict, mutate) -> dict:
    copied = copy.deepcopy(raw)
    mutate(copied)
    return copied


def test_shipped_config_loads_for_every_profile(raw: dict):
    for profile in raw["profiles"]:
        assert load_config(profile=profile).profile == profile


def test_profile_overrides_customer_count():
    assert load_config(profile="dev").customers.count == 2000
    assert load_config(profile="full").customers.count == 50000


def test_simulation_window_is_three_full_years():
    config = load_config()
    assert config.start_date == dt.date(2023, 1, 1)
    assert config.today == dt.date(2025, 12, 31)
    assert config.currency == "GHS"


def test_branches_cover_all_planned_cities():
    planned = {
        "Accra", "Tema", "Kumasi", "Takoradi", "Tamale",
        "Cape Coast", "Ho", "Sunyani", "Koforidua", "Bolgatanga",
    }  # fmt: skip
    branches = load_config().branches
    assert len(branches) == 15
    assert {b.city for b in branches} == planned


def test_unknown_profile_is_rejected(raw: dict):
    with pytest.raises(ConfigError, match="unknown profile"):
        parse_config(raw, profile="prod")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["branches"][0].update(customer_share=0.5), "customer_share"),
        (lambda c: c["branches"][1].update(branch_code="BR001"), "branch codes must be unique"),
        (lambda c: c["branches"][0].update(opened_on=dt.date(2026, 6, 1)), "opened_on"),
        (lambda c: c["branches"][0]["segment_mix"].update(sme=0.5), "BR001.segment_mix"),
        (lambda c: c["segments"]["mix"].pop("sme"), "segments.mix"),
        (lambda c: c["products"][0]["take_up"].update(pensioner=0.5), "unknown segments"),
        (lambda c: c["products"][1].update(product_code="CA_PER"), "product codes must be unique"),
        (lambda c: c["products"][0].update(family="insurance"), "unknown family"),
        (lambda c: c["simulation"].update(today=dt.date(2022, 1, 1)), "start_date must be before"),
        (lambda c: c["customers"].update(female_share=1.5), "female_share"),
        (lambda c: c["customers"]["mobile_network_share"].update(MTN=0.9), "mobile_network_share"),
        (lambda c: c["customers"]["ethnicity_by_region"].pop("Volta"), "no ethnicity weights"),
        (lambda c: c["customers"]["age_at_join"].update(min=90), "age_at_join"),
        (lambda c: c["products"][5].update(requires_any=["XX"]), "requires unknown product"),
        (lambda c: c["products"][7].update(requires_any=["DC_DEBIT"]), "standalone deposits"),
        (lambda c: c["products"][7].pop("requires_any"), "must require an account"),
        (lambda c: c["products"][7].pop("terms"), "missing terms"),
        (
            lambda c: c["products"][7]["terms"]["term_months"].update(weights=[1.0]),
            "must align",
        ),
        (lambda c: c["products"][7]["terms"]["annual_rate_pct"].update(min=50), "annual_rate_pct"),
        (lambda c: c["holdings"].update(momo_same_number_share=2), "momo_same_number_share"),
    ],
)
def test_inconsistent_config_is_rejected(raw: dict, mutate, message: str):
    with pytest.raises(ConfigError, match=message):
        parse_config(broken(raw, mutate))


def test_missing_section_is_reported_as_config_error(raw: dict):
    with pytest.raises(ConfigError, match="malformed config"):
        parse_config(broken(raw, lambda c: c.pop("customers")))
