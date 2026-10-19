"""Tests for the planted signals (dev profile, fixed seed).

Each signal must be present, observable in the ledger, and noisy enough that no single
feature separates it perfectly.
"""

import numpy as np
import pandas as pd
import pytest

from generator.config import load_config
from generator.world import World, build_world


@pytest.fixture(scope="module")
def world() -> World:
    return build_world(load_config(profile="dev"))


@pytest.fixture(scope="module")
def postings(world: World) -> pd.DataFrame:
    return world.ledger.postings


@pytest.fixture(scope="module")
def home_city(world: World) -> pd.Series:
    city = {b.branch_code: b.city for b in world.config.branches}
    return world.customers.set_index("person_id")["home_branch_code"].map(city)


# ---------------------------------------------------------------- credit default


def test_default_probabilities_are_valid_and_driven_by_dti(world: World):
    lo = world.scenarios.loan_outcomes
    assert lo["p_default"].between(0, 1).all()
    assert 0.05 < lo["will_default"].mean() < 0.35
    assert np.corrcoef(lo["dti"], lo["p_default"])[0, 1] > 0.2
    # Noise: plenty of risky loans still don't default, and some safe ones do.
    risky = lo["p_default"] > lo["p_default"].median()
    assert lo.loc[risky, "will_default"].mean() < 0.9
    assert lo.loc[~risky, "will_default"].sum() > 0


def test_shocked_loans_miss_far_more_repayments(world: World, postings: pd.DataFrame):
    lo = world.scenarios.loan_outcomes.set_index("account_id")
    repay = postings[postings["txn_type"] == "LOAN_REPAYMENT"]
    status = repay.groupby("event_id")["status"].first()
    loan_of_event = (
        repay[repay["account_id"].isin(lo.index)].groupby("event_id")["account_id"].first()
    )
    missed = (
        (status.loc[loan_of_event.index] == "DECLINED").groupby(loan_of_event.to_numpy()).mean()
    )
    shocked = lo.loc[missed.index, "shock_date"].notna().to_numpy()
    assert shocked.sum() > 0
    assert missed[shocked].mean() > 5 * missed[~shocked].mean()


def test_shock_date_inside_loan_life(world: World):
    lo = world.scenarios.loan_outcomes.dropna(subset=["shock_date"])
    accounts = world.holdings.accounts.set_index("account_id")
    assert lo["will_default"].all()
    assert (lo["shock_date"] > accounts.loc[lo["account_id"], "opened_on"].to_numpy()).all()
    assert (lo["shock_date"] <= accounts.loc[lo["account_id"], "maturity_date"].to_numpy()).all()


# ---------------------------------------------------------------- complaints and churn


def test_complaints_are_well_formed(world: World):
    c = world.scenarios.complaints
    assert c["complaint_id"].is_unique
    resolved = c.dropna(subset=["resolved_on"])
    assert (resolved["resolved_on"] >= resolved["opened_on"]).all()
    assert c["opened_on"].max() <= pd.Timestamp(world.config.today)
    assert 0.5 < c["resolved_on"].notna().mean() < 0.95


def test_most_fraud_victims_file_a_report(world: World):
    victims = set(world.scenarios.fraud_events["person_id"])
    reporters = set(world.scenarios.complaints.query("category == 'fraud_report'")["person_id"])
    assert len(victims & reporters) / len(victims) > 0.4


def test_churners_stop_transacting_after_leaving(world: World, postings: pd.DataFrame):
    churn = world.scenarios.churn.set_index("person_id")
    assert len(churn) > 0
    first_allowed = pd.Timestamp(world.config.start_date) + pd.DateOffset(
        months=world.config.signals.churn.earliest_month
    )
    assert (churn["churn_date"] >= first_allowed).all()
    merged = postings.merge(churn[["churn_date"]], left_on="person_id", right_index=True)
    assert (merged["posted_at"].dt.normalize() <= merged["churn_date"]).all()


def test_churners_hold_no_live_credit(world: World):
    churn = world.scenarios.churn
    acc = world.holdings.accounts
    credit = acc[(acc["family"] == "loan") | acc["product_code"].isin(["CC_CLASSIC", "FD_TERM"])]
    merged = credit.merge(churn, on="person_id")
    assert (merged["product_code"] != "CC_CLASSIC").all()
    assert (merged["maturity_date"] < merged["churn_date"]).all()


def test_churners_make_a_large_closing_transfer(world: World, postings: pd.DataFrame):
    churn = world.scenarios.churn
    out = postings[(postings["txn_type"] == "TRANSFER_OUT") & postings["fraud_type"].isna()]
    merged = out.merge(churn, on="person_id")
    closing = merged[merged["posted_at"].dt.normalize() == merged["churn_date"]]
    assert closing["person_id"].nunique() > 0.7 * len(churn)
    typical = -out["amount_pesewas"].median()
    assert -closing["amount_pesewas"].median() > 3 * typical


def test_churners_activity_runs_down_before_leaving(world: World, postings: pd.DataFrame):
    churn = world.scenarios.churn
    churn = churn[
        churn["churn_date"] >= pd.Timestamp(world.config.start_date) + pd.DateOffset(months=12)
    ]
    spend = postings[
        postings["txn_type"].isin(
            ["CASH_WITHDRAWAL", "CARD_PURCHASE", "BANK_TO_MOMO", "BILL_PAYMENT"]
        )
    ].merge(churn, on="person_id")
    days_before = (spend["churn_date"] - spend["posted_at"]).dt.days
    late = (days_before.between(0, 60)).sum()
    early = (days_before.between(300, 360)).sum()
    assert late < early


def test_unresolved_complaints_raise_churn(world: World):
    c = world.scenarios.complaints
    unresolved = set(c.loc[c["resolved_on"].isna(), "person_id"])
    churned = set(world.scenarios.churn["person_id"])
    everyone = set(world.customers["person_id"])
    rate_with = len(churned & unresolved) / max(len(unresolved), 1)
    rate_without = len(churned - unresolved) / len(everyone - unresolved)
    assert rate_with > rate_without


# ---------------------------------------------------------------- fraud


def test_every_fraud_typology_is_present(world: World, postings: pd.DataFrame):
    labelled = postings.dropna(subset=["fraud_type"])
    assert set(labelled["fraud_type"].unique()) == {
        "card_testing",
        "night_geo_jump",
        "account_takeover",
    }
    assert labelled["fraud_scenario_id"].notna().all()
    assert postings.loc[postings["fraud_type"].isna(), "fraud_scenario_id"].isna().all()


def test_card_testing_is_small_probes_then_a_large_purchase(world: World):
    fe = world.scenarios.fraud_events.query("fraud_type == 'card_testing'")
    params = world.config.signals.card_testing
    for _, g in fe.groupby("scenario_id"):
        g = g.sort_values("ts")
        probes, cashout = g.iloc[:-1], g.iloc[-1]
        assert len(probes) >= params.probes.min
        assert (-probes["amount_pesewas"] <= params.probe_amount_ghs.max * 100).all()
        assert -cashout["amount_pesewas"] > -probes["amount_pesewas"].max()
        assert probes["merchant_id"].nunique() == 1


def test_geo_jumps_happen_at_night_away_from_home(world: World, home_city: pd.Series):
    fe = world.scenarios.fraud_events.query("fraud_type == 'night_geo_jump'")
    merchant_city = fe["merchant_id"].map(world.merchants.set_index("merchant_id")["city"])
    assert (merchant_city.to_numpy() != fe["person_id"].map(home_city).to_numpy()).all()
    first = fe.sort_values("ts").groupby("scenario_id").head(1)
    assert first["ts"].dt.hour.isin(world.config.signals.geo_jump.hours).all()


def test_takeover_rings_share_payees_and_devices(world: World):
    fe = world.scenarios.fraud_events.query("fraud_type == 'account_takeover'")
    rings = world.scenarios.fraud_rings.set_index("scenario_id")
    assert fe["scenario_id"].nunique() >= 1
    legit_payees = set(world.scenarios.beneficiaries["beneficiary_account"])
    for sid, g in fe.groupby("scenario_id"):
        assert g["person_id"].nunique() > 1
        assert set(g["counterparty_account"]) <= set(rings.loc[sid, "beneficiary_accounts"])
        assert set(g["device_id"]) <= set(rings.loc[sid, "devices"])
        assert not set(g["counterparty_account"]) & legit_payees
    customer_devices = set(world.scenarios.devices["device_id"])
    assert not set(fe["device_id"]) & customer_devices


def test_fraud_signals_are_not_trivially_separable(world: World, postings: pd.DataFrame):
    cards = postings[postings["txn_type"] == "CARD_PURCHASE"]
    night = cards["posted_at"].dt.hour < 5
    # Genuine customers also shop at night, so night alone is not a fraud label.
    assert (night & cards["fraud_type"].isna()).sum() > (night & cards["fraud_type"].notna()).sum()
    # And genuine transfers sometimes go to a never-seen payee.
    legit_out = postings[(postings["txn_type"] == "TRANSFER_OUT") & postings["fraud_type"].isna()]
    regular = legit_out["counterparty_account"].isin(
        world.scenarios.beneficiaries["beneficiary_account"]
    )
    assert 0.5 < regular.mean() < 1.0


def test_mobile_postings_carry_customer_devices(world: World, postings: pd.DataFrame):
    devices = world.scenarios.devices.groupby("person_id")["device_id"].apply(set)
    mobile = postings[
        (postings["channel"] == "MOBILE")
        & postings["device_id"].notna()
        & postings["fraud_type"].isna()
    ]
    assert len(mobile) > 0
    owned = [
        d in devices.get(p, set())
        for p, d in zip(mobile["person_id"], mobile["device_id"], strict=True)
    ]
    assert all(owned)
