"""Tests for the ground-truth customer generator (dev profile, fixed seed).

Distribution checks use tolerances of roughly three standard errors at n = 2000, so they
catch wiring mistakes (wrong weights, wrong column) without being flaky.
"""

import numpy as np
import pandas as pd
import pytest

from generator.config import WorldConfig, load_config
from generator.world.customers import generate_customers
from generator.world.names import load_name_lists
from isbank.reference.ghana import (
    is_valid_digital_address,
    is_valid_ghana_card,
    mobile_network,
)


@pytest.fixture(scope="module")
def config() -> WorldConfig:
    return load_config(profile="dev")


@pytest.fixture(scope="module")
def customers(config: WorldConfig) -> pd.DataFrame:
    return generate_customers(config, np.random.default_rng(config.seed))


def test_row_count_and_unique_ids(customers: pd.DataFrame, config: WorldConfig):
    assert len(customers) == config.customers.count
    assert customers["person_id"].is_unique
    assert customers["ghana_card"].is_unique
    assert customers["phone_number"].is_unique
    assert customers["email"].dropna().is_unique


def test_same_seed_is_deterministic(config: WorldConfig, customers: pd.DataFrame):
    again = generate_customers(config, np.random.default_rng(config.seed))
    pd.testing.assert_frame_equal(customers, again)


def test_different_seed_gives_different_world(config: WorldConfig, customers: pd.DataFrame):
    other = generate_customers(config, np.random.default_rng(config.seed + 1))
    assert not customers["ghana_card"].equals(other["ghana_card"])


def test_identifier_formats(customers: pd.DataFrame):
    assert customers["ghana_card"].map(is_valid_ghana_card).all()
    assert customers["digital_address"].map(is_valid_digital_address).all()
    assert (customers["phone_number"].map(mobile_network) == customers["mobile_network"]).all()
    assert customers["email"].dropna().str.match(r"^[a-z.0-9]+@example\.(com|org|net)$").all()


def test_branch_shares_match_config(customers: pd.DataFrame, config: WorldConfig):
    observed = customers["home_branch_code"].value_counts(normalize=True)
    for branch in config.branches:
        assert observed[branch.branch_code] == pytest.approx(branch.customer_share, abs=0.025)


def test_segment_mix_matches_branch_weighted_expectation(
    customers: pd.DataFrame, config: WorldConfig
):
    expected = dict.fromkeys(config.archetypes, 0.0)
    for branch in config.branches:
        mix = branch.segment_mix or config.segment_mix
        for segment, weight in mix.items():
            expected[segment] += branch.customer_share * weight
    observed = customers["segment"].value_counts(normalize=True)
    for segment, share in expected.items():
        assert observed[segment] == pytest.approx(share, abs=0.03)


def test_only_smes_are_businesses_with_names(customers: pd.DataFrame):
    is_sme = customers["segment"] == "sme"
    assert (customers.loc[is_sme, "customer_type"] == "business").all()
    assert (customers.loc[~is_sme, "customer_type"] == "individual").all()
    assert customers.loc[is_sme, "business_name"].notna().all()
    assert customers.loc[~is_sme, "business_name"].isna().all()


def test_join_dates_respect_window_and_branch_opening(customers: pd.DataFrame, config: WorldConfig):
    opened = {b.branch_code: pd.Timestamp(b.opened_on) for b in config.branches}
    assert (customers["joined_on"] >= customers["home_branch_code"].map(opened)).all()
    assert (customers["joined_on"] >= pd.Timestamp(config.customers.earliest_join_date)).all()
    assert (customers["joined_on"] <= pd.Timestamp(config.today)).all()


def test_branch_opened_in_window_has_no_earlier_customers(
    customers: pd.DataFrame, config: WorldConfig
):
    madina = next(b for b in config.branches if b.opened_on > config.start_date)
    joined = customers.loc[customers["home_branch_code"] == madina.branch_code, "joined_on"]
    assert len(joined) > 0
    assert joined.min() >= pd.Timestamp(madina.opened_on)


def test_pre_window_share_for_established_branches(customers: pd.DataFrame, config: WorldConfig):
    established = {b.branch_code for b in config.branches if b.opened_on < config.start_date}
    subset = customers[customers["home_branch_code"].isin(established)]
    pre_window = (subset["joined_on"] < pd.Timestamp(config.start_date)).mean()
    assert pre_window == pytest.approx(config.customers.pre_window_share, abs=0.04)


def test_everyone_is_an_adult_when_joining(customers: pd.DataFrame, config: WorldConfig):
    age_at_join = (customers["joined_on"] - customers["date_of_birth"]).dt.days / 365.25
    assert age_at_join.min() >= config.customers.age_min
    assert age_at_join.max() < config.customers.age_max


def test_akan_day_names_match_birth_weekday_at_configured_rate(
    customers: pd.DataFrame, config: WorldConfig
):
    day_names = load_name_lists().akan_day_names
    akan = customers[customers["ethnicity"] == "akan"]
    # pandas: Monday = 0; Akan lists start on Sunday.
    weekday = (akan["date_of_birth"].dt.dayofweek + 1) % 7
    expected = [day_names[g][w] for g, w in zip(akan["gender"], weekday, strict=True)]
    has_day_name = (akan["first_name"] == expected) | (akan["middle_name"] == expected)
    assert has_day_name.mean() == pytest.approx(config.customers.names.akan_day_name_rate, abs=0.05)


def test_ethnicity_follows_region(customers: pd.DataFrame, config: WorldConfig):
    region = customers["home_branch_code"].map({b.branch_code: b.region for b in config.branches})
    assert (customers.loc[region == "Volta", "ethnicity"] == "ewe").mean() > 0.6
    assert (customers.loc[region == "Upper East", "ethnicity"] == "northern").mean() > 0.7


def test_income_is_positive_pesewas_and_scales_with_branch(customers: pd.DataFrame):
    income = customers["monthly_income_pesewas"]
    assert income.dtype == "int64"
    assert (income > 0).all()
    salaried = customers[customers["segment"] == "corporate_employee"]
    by_branch = salaried.groupby("home_branch_code")["monthly_income_pesewas"].median()
    # Airport City (income index 1.6) should out-earn Bolgatanga (0.6) for the same segment.
    assert by_branch["BR005"] > by_branch["BR015"]
