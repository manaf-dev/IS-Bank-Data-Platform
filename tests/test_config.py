"""Consistency checks for generator/config.yaml.

The world simulator trusts this config, so mistakes here (weights that don't sum to 1,
a product offered to an unknown segment) would silently skew every downstream dataset.
"""

import datetime as dt
from pathlib import Path

import pytest
import yaml

TOLERANCE = 1e-9


@pytest.fixture(scope="module")
def config(repo_root: Path) -> dict:
    return yaml.safe_load((repo_root / "generator" / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def segment_names(config: dict) -> set[str]:
    return set(config["segments"]["archetypes"])


def test_simulation_window_is_three_full_years(config: dict):
    sim = config["simulation"]
    assert sim["start_date"] == dt.date(2023, 1, 1)
    assert sim["today"] == dt.date(2025, 12, 31)
    assert sim["currency"] == "GHS"


def test_branches_have_unique_codes_and_names(config: dict):
    branches = config["branches"]
    assert len(branches) == 15
    assert len({b["branch_code"] for b in branches}) == len(branches)
    assert len({b["name"] for b in branches}) == len(branches)


def test_branch_customer_shares_sum_to_one(config: dict):
    total = sum(b["customer_share"] for b in config["branches"])
    assert total == pytest.approx(1.0, abs=TOLERANCE)


def test_branches_cover_all_planned_cities(config: dict):
    planned = {
        "Accra", "Tema", "Kumasi", "Takoradi", "Tamale",
        "Cape Coast", "Ho", "Sunyani", "Koforidua", "Bolgatanga",
    }  # fmt: skip
    assert {b["city"] for b in config["branches"]} == planned


def test_branch_opening_dates_precede_simulated_today(config: dict):
    today = config["simulation"]["today"]
    for branch in config["branches"]:
        assert isinstance(branch["opened_on"], dt.date)
        assert branch["opened_on"] <= today, branch["branch_code"]


def test_segment_mixes_sum_to_one_over_known_segments(config: dict, segment_names: set[str]):
    mixes = {"bank-wide": config["segments"]["mix"]}
    mixes |= {b["branch_code"]: b["segment_mix"] for b in config["branches"] if "segment_mix" in b}
    for owner, mix in mixes.items():
        assert set(mix) == segment_names, owner
        assert sum(mix.values()) == pytest.approx(1.0, abs=TOLERANCE), owner


def test_archetype_parameters_are_in_range(config: dict):
    for name, archetype in config["segments"]["archetypes"].items():
        assert archetype["customer_type"] in {"individual", "business"}, name
        assert archetype["monthly_income_ghs"]["median"] > 0, name
        assert archetype["monthly_income_ghs"]["sigma"] > 0, name
        assert 0 <= archetype["income_regularity"] <= 1, name
        assert 0 <= archetype["cash_intensity"] <= 1, name


def test_products_have_unique_codes_and_valid_take_up(config: dict, segment_names: set[str]):
    products = config["products"]
    assert len({p["product_code"] for p in products}) == len(products)
    for product in products:
        assert product["family"] in {"deposit", "card", "loan"}, product["product_code"]
        assert set(product["take_up"]) <= segment_names, product["product_code"]
        assert all(0 < p <= 1 for p in product["take_up"].values()), product["product_code"]


def test_every_segment_can_hold_a_deposit_product(config: dict, segment_names: set[str]):
    covered = {
        segment
        for product in config["products"]
        if product["family"] == "deposit"
        for segment in product["take_up"]
    }
    assert covered == segment_names


def test_services_reference_known_segments(config: dict, segment_names: set[str]):
    for service, take_up in config["services"].items():
        assert set(take_up) <= segment_names, service
        assert all(0 < p <= 1 for p in take_up.values()), service
