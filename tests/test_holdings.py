"""Tests for product holdings and service enrolment (dev profile, fixed seed)."""

import numpy as np
import pandas as pd
import pytest

from generator.config import WorldConfig, load_config
from generator.world.customers import generate_customers
from generator.world.holdings import Holdings, generate_holdings
from isbank.reference.ghana import mobile_network


@pytest.fixture(scope="module")
def config() -> WorldConfig:
    return load_config(profile="dev")


@pytest.fixture(scope="module")
def world(config: WorldConfig) -> tuple[pd.DataFrame, Holdings]:
    rng = np.random.default_rng(config.seed)
    customers = generate_customers(config, rng)
    return customers, generate_holdings(config, customers, rng)


@pytest.fixture(scope="module")
def customers(world) -> pd.DataFrame:
    return world[0]


@pytest.fixture(scope="module")
def accounts(world) -> pd.DataFrame:
    return world[1].accounts


@pytest.fixture(scope="module")
def services(world) -> pd.DataFrame:
    return world[1].services


@pytest.fixture(scope="module")
def products(config: WorldConfig) -> dict:
    return {p.product_code: p for p in config.products}


def test_deterministic_for_same_seed(config: WorldConfig, world):
    rng = np.random.default_rng(config.seed)
    customers = generate_customers(config, rng)
    again = generate_holdings(config, customers, rng)
    pd.testing.assert_frame_equal(world[1].accounts, again.accounts)
    pd.testing.assert_frame_equal(world[1].services, again.services)


def test_account_ids_are_sequential_in_opening_order(accounts: pd.DataFrame):
    assert accounts["account_id"].tolist() == list(range(1, len(accounts) + 1))
    assert accounts["opened_on"].is_monotonic_increasing


def test_at_most_one_contract_per_product_per_person(accounts: pd.DataFrame):
    assert not accounts.duplicated(["person_id", "product_code"]).any()


def test_every_customer_opened_a_standalone_deposit_on_joining(
    customers: pd.DataFrame, accounts: pd.DataFrame, products: dict
):
    standalone = [c for c, p in products.items() if p.family == "deposit" and not p.requires_any]
    first_deposit = (
        accounts[accounts["product_code"].isin(standalone)].groupby("person_id")["opened_on"].min()
    )
    joined = customers.set_index("person_id")["joined_on"]
    assert first_deposit.index.sort_values().equals(joined.index.sort_values())
    assert (first_deposit == joined.loc[first_deposit.index]).all()


def test_products_only_held_by_eligible_segments(
    customers: pd.DataFrame, accounts: pd.DataFrame, products: dict
):
    merged = accounts.merge(customers[["person_id", "segment"]], on="person_id")
    for code, group in merged.groupby("product_code"):
        assert set(group["segment"]) <= set(products[code].take_up), code


def test_dependent_products_link_to_an_earlier_required_account(
    accounts: pd.DataFrame, products: dict
):
    dependent = accounts[accounts["product_code"].map(lambda c: bool(products[c].requires_any))]
    assert dependent["linked_account_id"].notna().all()
    linked = dependent.merge(
        accounts[["account_id", "person_id", "product_code", "opened_on"]],
        left_on="linked_account_id",
        right_on="account_id",
        suffixes=("", "_link"),
    )
    assert len(linked) == len(dependent)
    assert (linked["person_id"] == linked["person_id_link"]).all()
    assert all(
        link in products[code].requires_any
        for code, link in zip(linked["product_code"], linked["product_code_link"], strict=True)
    )
    assert (linked["opened_on"] >= linked["opened_on_link"]).all()


def test_standalone_accounts_have_no_link(accounts: pd.DataFrame, products: dict):
    standalone = accounts[accounts["product_code"].map(lambda c: not products[c].requires_any)]
    assert standalone["linked_account_id"].isna().all()


def test_opening_dates_within_customer_life_and_window(
    config: WorldConfig, customers: pd.DataFrame, accounts: pd.DataFrame, products: dict
):
    merged = accounts.merge(customers[["person_id", "joined_on"]], on="person_id")
    assert (merged["opened_on"] >= merged["joined_on"]).all()
    assert (merged["opened_on"] <= pd.Timestamp(config.end_date)).all()
    horizon = pd.Timestamp(config.start_date) - pd.DateOffset(
        months=config.dependent_open_lookback_months
    )
    is_dependent = merged["product_code"].map(lambda c: bool(products[c].requires_any))
    assert (merged.loc[is_dependent, "opened_on"] >= horizon).all()


def test_contract_terms_follow_product_config(accounts: pd.DataFrame, products: dict, config):
    step = config.contract_amount_rounding_ghs * 100
    for code, group in accounts.groupby("product_code"):
        terms = products[code].terms
        if terms is None:
            assert (
                group[["contract_amount_pesewas", "annual_rate_bps", "term_months"]]
                .isna()
                .all()
                .all()
            )
            assert group["maturity_date"].isna().all()
            continue
        assert (group["contract_amount_pesewas"] > 0).all(), code
        assert (group["contract_amount_pesewas"] % step == 0).all(), code
        assert (
            group["annual_rate_bps"]
            .between(terms.rate_min_pct * 100, terms.rate_max_pct * 100)
            .all()
        )
        if terms.term_months is None:
            assert group["maturity_date"].isna().all(), code
            continue
        assert set(group["term_months"]) <= set(terms.term_months), code
        expected = [
            o + pd.DateOffset(months=int(m))
            for o, m in zip(group["opened_on"], group["term_months"], strict=True)
        ]
        assert (group["maturity_date"] == pd.Series(expected, index=group.index)).all(), code


def test_take_up_of_standalone_products_matches_config(
    customers: pd.DataFrame, accounts: pd.DataFrame, products: dict
):
    # Susu savers almost all hold a susu account; the forced fallback only adds to this.
    susu_savers = customers.loc[customers["segment"] == "susu_saver", "person_id"]
    holders = accounts.loc[accounts["product_code"] == "SA_SUSU", "person_id"]
    rate = susu_savers.isin(holders).mean()
    assert rate == pytest.approx(products["SA_SUSU"].take_up["susu_saver"], abs=0.04)


def test_service_uptake_scales_with_digital_index(
    config: WorldConfig, customers: pd.DataFrame, services: pd.DataFrame
):
    enrolled = services.loc[services["service"] == "mobile_banking", "person_id"]
    corp = customers[customers["segment"] == "corporate_employee"]
    rate = (
        corp.assign(enrolled=corp["person_id"].isin(enrolled))
        .groupby("home_branch_code")["enrolled"]
        .mean()
    )
    # Airport City (digital index 1.5) caps at full uptake; Bolgatanga (0.65) is far lower.
    assert rate["BR005"] > rate["BR015"] + 0.2


def test_momo_wallets_are_valid_and_mostly_own_number(
    config: WorldConfig, customers: pd.DataFrame, services: pd.DataFrame
):
    momo = services[services["service"] == "momo_wallet_link"].merge(
        customers[["person_id", "phone_number", "joined_on"]], on="person_id"
    )
    assert momo["wallet_number"].map(mobile_network).notna().all()
    same = (momo["wallet_number"] == momo["phone_number"]).mean()
    assert same == pytest.approx(config.momo_same_number_share, abs=0.04)
    assert (momo["enrolled_on"] >= momo["joined_on"]).all()
    other_banking = services.loc[services["service"] != "momo_wallet_link", "wallet_number"]
    assert other_banking.isna().all()
