"""LOS, CRM, GL, credit bureau, compliance, and reference-data projectors (dev profile)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from generator.config import load_config
from generator.projectors import (
    compliance,
    credit_bureau,
    crm,
    general_ledger,
    loan_origination,
    reference_data,
)
from generator.projectors.core_banking import _Core
from generator.world import World, build_world

LEAKS = {
    "person_id",
    "account_id",
    "posting_id",
    "fraud_type",
    "segment",
    "p_default",
    "will_default",
}


@pytest.fixture(scope="module")
def world() -> World:
    return build_world(load_config(profile="dev"))


@pytest.fixture(scope="module")
def today(world: World) -> dt.date:
    return world.config.today


@pytest.fixture(scope="module")
def batches(world: World, today):
    return {
        "los": loan_origination.extracts(world, None, today),
        "crm": crm.exports(world, None, today),
        "gl": general_ledger.monthly_statements(world, None, today),
        "bureau": credit_bureau.weekly_reports(world, None, today),
        "compliance": compliance.monthly_lists(world, None, today),
        "fx": reference_data.fx_rates(world, None, today),
    }


def test_no_ground_truth_leaks(batches):
    for name, batch in batches.items():
        for table, frame in batch.tables.items():
            assert not LEAKS & set(frame.columns), f"{name}.{table}"
            assert "partition_by" in batch.meta[table], f"{name}.{table}"


@pytest.mark.parametrize(
    "fn",
    [
        loan_origination.extracts,
        crm.exports,
        general_ledger.monthly_statements,
        credit_bureau.weekly_reports,
        compliance.monthly_lists,
        reference_data.fx_rates,
    ],
)
def test_backfill_plus_ticks_equals_later_backfill(world: World, today, fn):
    later = today + dt.timedelta(days=40)
    before, tick, after = fn(world, None, today), fn(world, today, later), fn(world, None, later)
    for table in after.tables:
        combined = len(before.tables.get(table, [])) + len(tick.tables.get(table, []))
        assert combined == len(after.tables[table]), table


# ---------------------------------------------------------------- LOS


def test_los_identity_and_formats(batches):
    apps = batches["los"].tables["los_applications"]
    assert apps["applicant_ghana_card"].str.contains("-").any()
    assert (~apps["applicant_ghana_card"].str.contains("-")).any()
    assert apps["monthly_income"].str.fullmatch(r"\d{1,3}(,\d{3})*\.\d{2}").all()
    declined = apps[apps["status"] == "DECLINED"]
    assert len(declined) > 0
    assert declined["loan_account_number"].isna().all()
    assert declined["decline_reason"].notna().all()


def test_los_links_to_core_loans(world: World, batches):
    apps = batches["los"].tables["los_applications"]
    core_numbers = set(_Core(world).account_numbers["account_number"])
    approved = apps[apps["status"] == "APPROVED"]
    assert set(approved["loan_account_number"]) <= core_numbers


def test_los_schema_drift_and_resends(world: World, batches):
    v2 = batches["los"].tables["los_applications_v2"]
    assert "net_monthly_income" in v2.columns
    assert "monthly_income" not in v2.columns
    rename_from = pd.Timestamp(
        world.config.projectors["loan_origination"]["rename_income_column_from"]
    )
    assert (v2["extract_date"] >= rename_from).all()
    assert batches["los"].meta["los_applications_v2"]["file_stem"] == "los_applications"
    assert len(batches["los"].meta["los_repayments"]["resend_partitions"]) > 0


def test_los_pending_rows_precede_decisions(batches):
    apps = batches["los"].tables["los_applications"]
    twice = apps[apps.duplicated("application_ref", keep=False)]
    assert len(twice) > 0
    for _, g in twice.groupby("application_ref"):
        assert g.sort_values("extract_date")["status"].iloc[0] == "PENDING"


def test_los_repayments_include_misses(batches):
    repay = batches["los"].tables["los_repayments"]
    assert set(repay["payment_status"]) == {"PAID", "MISSED"}
    missed = repay[repay["payment_status"] == "MISSED"]
    assert (missed["amount_paid"] == "0.00").all()


# ---------------------------------------------------------------- CRM


def test_crm_has_no_strong_identifiers(batches):
    contacts = batches["crm"].tables["crm_contacts"]
    assert not {"cif_number", "ghana_card", "ghana_card_no"} & set(contacts.columns)
    assert contacts["email"].isna().mean() > 0.2
    assert contacts["phone"].str.startswith("+233").any()
    assert contacts["phone"].str.startswith("0").any()


def test_crm_schema_drift(world: World, batches):
    old, new = batches["crm"].tables["crm_contacts"], batches["crm"].tables["crm_contacts_v2"]
    assert "preferred_channel" not in old.columns
    assert "preferred_channel" in new.columns
    drift_from = pd.Timestamp(world.config.projectors["crm"]["add_preferred_channel_from"])
    assert (new["export_date"] >= drift_from).all()
    assert (old["export_date"] < drift_from).all()


def test_crm_cases_open_then_resolve(batches):
    cases = batches["crm"].tables["crm_cases"]
    resolved = cases[cases["status"] == "RESOLVED"]
    assert len(resolved) > 0
    assert set(resolved["case_id"]) <= set(cases.loc[cases["status"] == "OPEN", "case_id"])


def test_nps_is_lower_before_churn(world: World, batches):
    nps = batches["crm"].tables["crm_nps_responses"]
    contact = crm._Crm(world).contact_id
    churners = set(contact.loc[world.scenarios.churn["person_id"]])
    leaving = nps["contact_id"].isin(churners)
    assert nps.loc[leaving, "score"].mean() < nps.loc[~leaving, "score"].mean()


# ---------------------------------------------------------------- GL


def test_gl_reconciles_with_core_except_planted_breaks(world: World):
    frame, breaks = general_ledger.pnl(world)
    assert len(breaks) > 0
    fees_gl = frame[frame["gl_account"] == "4100"].set_index(["period", "cost_centre"])[
        "amount_ghs"
    ]
    posted = world.ledger.postings
    posted = posted[(posted["status"] == "POSTED") & (posted["txn_type"] == "MONTHLY_FEE")]
    branch = posted["account_id"].map(
        world.holdings.accounts.set_index("account_id")["branch_code"]
    )
    core = (
        -posted.groupby(
            [posted["posted_at"].dt.to_period("M").astype(str), "CC-" + branch.str.slice(2)]
        )["amount_pesewas"].sum()
        / 100
    )
    diff = (fees_gl - core.reindex(fees_gl.index).fillna(0)).round(2)
    planted = breaks.assign(cost_centre="CC-" + breaks["branch_code"].str.slice(2)).set_index(
        ["period", "cost_centre"]
    )["difference_ghs"]
    assert (diff[diff != 0].sort_index() == planted.sort_index()).all()
    assert set(diff[diff != 0].index) == set(planted.index)


def test_gl_branch_profitability_varies(world: World):
    frame, _ = general_ledger.pnl(world)
    year = frame[frame["period"].str.startswith("2025")]
    net = year.groupby("cost_centre")["amount_ghs"].sum()
    assert (net > 0).any()
    assert (net < 0).any()


# ---------------------------------------------------------------- bureau, compliance, FX


def test_bureau_records_fit_the_fixed_width_layout(batches):
    batch = batches["bureau"]
    frame = batch.tables["bureau_report"]
    for column, width in batch.meta["bureau_report"]["layout"]:
        assert frame[column].astype(str).str.len().max() <= width, column
    days = pd.to_datetime(frame["report_date"], format="%Y%m%d")
    assert (days.dt.dayofweek == 0).all()


def test_bureau_score_tracks_true_risk(world: World, batches):
    frame = batches["bureau"].tables["bureau_report"]
    latest = frame[frame["report_date"] == frame["report_date"].max()]
    card = world.customers.assign(
        national_id=world.customers["ghana_card"].str.replace(r"[^0-9]", "", regex=True)
    )
    person = latest["national_id"].map(card.set_index("national_id")["person_id"])
    risk = world.scenarios.loan_outcomes.groupby("person_id")["p_default"].max()
    has = person.isin(risk.index)
    corr = np.corrcoef(latest.loc[has, "score"], person[has].map(risk))[0, 1]
    assert corr < -0.3


def test_watchlists_have_customer_peps_and_false_hits(world: World):
    entries = compliance.list_entries(world)
    peps = entries.dropna(subset=["person_id"])
    assert len(peps) > 0 or len(world.customers) < 1000
    names = set((world.customers["first_name"] + " " + world.customers["last_name"]).str.upper())
    synthetic = entries[entries["person_id"].isna()]
    assert synthetic["full_name"].isin(names).any()
    assert not entries["full_name"].str.contains("MAHAMA").any()


def test_fx_rates_are_positive_and_daily(batches, today):
    fx = batches["fx"].tables["fx_rates"]
    assert (fx["mid_rate"] > 0).all()
    assert set(fx["base_currency"]) == {"USD", "EUR", "GBP"}
    assert fx.groupby("base_currency")["rate_date"].nunique().nunique() == 1
