"""Ledger invariants for the transaction simulation (dev profile, fixed seed).

These are the properties downstream pipelines and DQ checks will rely on: balances
reconcile with postings, floors are never breached, events stay inside customer and
account lifetimes, and two-leg transfers succeed or fail as a unit.
"""

import numpy as np
import pandas as pd
import pytest

from generator.config import WorldConfig, load_config
from generator.world.customers import generate_customers
from generator.world.holdings import generate_holdings
from generator.world.merchants import generate_merchants
from generator.world.transactions import Ledger, simulate_ledger


def build(config: WorldConfig):
    rng = np.random.default_rng(config.seed)
    customers = generate_customers(config, rng)
    holdings = generate_holdings(config, customers, rng)
    merchants = generate_merchants(config, rng)
    return (
        customers,
        holdings,
        merchants,
        simulate_ledger(config, customers, holdings, merchants, rng),
    )


@pytest.fixture(scope="module")
def config() -> WorldConfig:
    return load_config(profile="dev")


@pytest.fixture(scope="module")
def world(config: WorldConfig):
    return build(config)


@pytest.fixture(scope="module")
def customers(world) -> pd.DataFrame:
    return world[0]


@pytest.fixture(scope="module")
def accounts(world) -> pd.DataFrame:
    return world[1].accounts


@pytest.fixture(scope="module")
def merchants(world) -> pd.DataFrame:
    return world[2]


@pytest.fixture(scope="module")
def ledger(world) -> Ledger:
    return world[3]


@pytest.fixture(scope="module")
def postings(ledger: Ledger) -> pd.DataFrame:
    return ledger.postings


@pytest.fixture(scope="module")
def posted(postings: pd.DataFrame) -> pd.DataFrame:
    return postings[postings["status"] == "POSTED"]


def test_deterministic_for_same_seed(config: WorldConfig, ledger: Ledger):
    again = build(config)[3]
    pd.testing.assert_frame_equal(ledger.postings, again.postings)


def test_ids_and_ordering(postings: pd.DataFrame):
    assert postings["posting_id"].tolist() == list(range(1, len(postings) + 1))
    assert postings["posted_at"].is_monotonic_increasing


def test_postings_inside_window(config: WorldConfig, postings: pd.DataFrame):
    assert postings["posted_at"].min() >= pd.Timestamp(config.start_date)
    assert postings["posted_at"].max() < pd.Timestamp(config.today) + pd.Timedelta(days=1)


def test_balance_after_reconciles_with_opening_and_postings(ledger: Ledger, posted: pd.DataFrame):
    opening = ledger.opening_balances.set_index("account_id")["balance_pesewas"]
    ordered = posted.sort_values("posting_id")
    expected = (
        ordered["account_id"].map(opening)
        + ordered.groupby("account_id")["amount_pesewas"].cumsum()
    )
    assert (ordered["balance_after_pesewas"] == expected).all()


def test_declined_rows_have_no_balance(postings: pd.DataFrame):
    declined = postings["status"] == "DECLINED"
    assert postings.loc[declined, "balance_after_pesewas"].isna().all()
    assert postings.loc[~declined, "balance_after_pesewas"].notna().all()
    assert (postings.loc[declined, "amount_pesewas"] < 0).any()


def test_balances_never_breach_floors(accounts: pd.DataFrame, posted: pd.DataFrame):
    info = accounts.set_index("account_id")
    merged = posted.join(info[["product_code", "contract_amount_pesewas"]], on="account_id")
    deposits = merged["product_code"].isin(["CA_PER", "CA_BIZ", "SA_REG", "SA_SUSU"])
    assert (merged.loc[deposits, "balance_after_pesewas"] >= 0).all()
    cards = merged["product_code"] == "CC_CLASSIC"
    assert (
        merged.loc[cards, "balance_after_pesewas"] >= -merged.loc[cards, "contract_amount_pesewas"]
    ).all()


def test_postings_respect_customer_and_account_lifetimes(
    customers: pd.DataFrame, accounts: pd.DataFrame, postings: pd.DataFrame
):
    merged = postings.merge(accounts[["account_id", "person_id", "opened_on"]], on="account_id")
    assert (merged["person_id_x"] == merged["person_id_y"]).all()
    assert (merged["posted_at"].dt.normalize() >= merged["opened_on"]).all()
    joined = postings["person_id"].map(customers.set_index("person_id")["joined_on"])
    assert (postings["posted_at"].dt.normalize() >= joined).all()


def test_two_leg_events_share_status(postings: pd.DataFrame):
    legs = postings.groupby("event_id")["status"].nunique()
    assert (legs == 1).all()


def test_internal_transfers_net_to_zero(posted: pd.DataFrame):
    for txn in ("SAVINGS_SWEEP", "CARD_REPAYMENT", "LOAN_DISBURSEMENT"):
        net = posted[posted["txn_type"] == txn].groupby("event_id")["amount_pesewas"].sum()
        assert len(net) > 0, txn
        assert (net == 0).all(), txn


def test_loan_repayments_split_into_principal_and_interest(
    accounts: pd.DataFrame, posted: pd.DataFrame
):
    repay = posted[posted["txn_type"] == "LOAN_REPAYMENT"]
    legs = repay.pivot_table(
        index="event_id",
        columns=repay["amount_pesewas"] > 0,
        values="amount_pesewas",
        aggfunc="sum",
        observed=True,
    )
    debit, principal = -legs[False], legs[True]
    assert ((principal > 0) & (principal <= debit)).all()
    loans = accounts.loc[accounts["family"] == "loan", ["account_id", "term_months"]]
    attempts = repay[repay["amount_pesewas"] > 0].groupby("account_id").size()
    assert (attempts <= attempts.index.map(loans.set_index("account_id")["term_months"])).all()


def test_loan_balances_never_turn_positive(accounts: pd.DataFrame, posted: pd.DataFrame):
    loan_ids = accounts.loc[accounts["family"] == "loan", "account_id"]
    balances = posted.loc[posted["account_id"].isin(loan_ids), "balance_after_pesewas"]
    # Rounding of principal parts may leave a few pesewas.
    assert balances.max() <= 100


def test_salaries_only_for_salaried_and_not_dormant(
    customers: pd.DataFrame, ledger: Ledger, posted: pd.DataFrame
):
    salary = posted[posted["txn_type"] == "SALARY_CREDIT"]
    profile = ledger.profiles.set_index("person_id")
    assert profile.loc[salary["person_id"].unique(), "salary_day"].notna().all()
    assert not profile.loc[salary["person_id"].unique(), "dormant"].any()
    pay_day = salary["person_id"].map(profile["salary_day"]).astype(int)
    month_end = salary["posted_at"].dt.days_in_month
    assert (salary["posted_at"].dt.day == np.minimum(pay_day, month_end)).all()


def test_dormant_customers_receive_no_income(ledger: Ledger, posted: pd.DataFrame):
    dormant = ledger.profiles.loc[ledger.profiles["dormant"], "person_id"]
    inflows = posted[
        posted["txn_type"].isin(["SALARY_CREDIT", "CASH_DEPOSIT", "MOMO_TO_BANK", "TRANSFER_IN"])
    ]
    assert len(dormant) > 0
    assert not inflows["person_id"].isin(dormant).any()


def test_branch_postings_respect_opening_hours(config: WorldConfig, postings: pd.DataFrame):
    branch = postings[postings["channel"] == "BRANCH"]
    hours = config.behaviour
    assert (branch["posted_at"].dt.dayofweek != 6).mean() > 0.995  # Sunday only at month edges
    assert (
        branch["posted_at"]
        .dt.hour.between(hours.branch_open_hour, hours.branch_close_hour - 1)
        .all()
    )


def test_card_purchases_have_valid_merchants_and_cards(
    accounts: pd.DataFrame, merchants: pd.DataFrame, postings: pd.DataFrame
):
    cards = postings[postings["txn_type"] == "CARD_PURCHASE"]
    assert cards["merchant_id"].notna().all()
    assert postings.loc[postings["txn_type"] != "CARD_PURCHASE", "merchant_id"].isna().all()
    channel = cards["merchant_id"].map(merchants.set_index("merchant_id")["channel"])
    assert (channel[cards["channel"] == "POS"] != "online").all()
    assert (channel[cards["channel"] == "ONLINE"] != "pos").all()

    debit_from = accounts[accounts["product_code"] == "DC_DEBIT"].set_index("person_id")[
        "opened_on"
    ]
    on_debit = cards[
        cards["account_id"].map(accounts.set_index("account_id")["product_code"]) != "CC_CLASSIC"
    ]
    assert (on_debit["posted_at"].dt.normalize() >= on_debit["person_id"].map(debit_from)).all()


def test_momo_flows_use_the_customers_wallet(world, postings: pd.DataFrame):
    services = world[1].services
    wallet = services[services["service"] == "momo_wallet_link"].set_index("person_id")[
        "wallet_number"
    ]
    momo = postings[postings["txn_type"].isin(["MOMO_TO_BANK", "BANK_TO_MOMO"])]
    assert len(momo) > 0
    assert (momo["counterparty"] == momo["person_id"].map(wallet)).all()


def test_december_spending_exceeds_february(posted: pd.DataFrame):
    spend = posted[posted["txn_type"].isin(["CASH_WITHDRAWAL", "CARD_PURCHASE", "BANK_TO_MOMO"])]
    by_month = -spend.groupby(spend["posted_at"].dt.month)["amount_pesewas"].sum()
    assert by_month[12] > by_month[2] * 1.15


def test_accounts_opened_in_window_start_empty(
    config: WorldConfig, accounts: pd.DataFrame, ledger: Ledger
):
    opening = ledger.opening_balances.merge(accounts[["account_id", "opened_on"]], on="account_id")
    in_window = opening["opened_on"] >= pd.Timestamp(config.start_date)
    assert (opening.loc[in_window, "balance_pesewas"] == 0).all()
    assert (opening.loc[~in_window, "balance_pesewas"] != 0).any()
