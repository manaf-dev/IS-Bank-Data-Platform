"""Gold star schema on locally built Silver: integrity, privacy, and planted signals."""

import datetime as dt

import pandas as pd
import pytest

from generator import answer_key
from generator.config import load_config
from generator.world import build_world
from isbank.gold import model
from tests.local_platform import TODAY, build_silver

pytestmark = pytest.mark.spark

PII_COLUMNS = {
    "ghana_card",
    "mobile_number",
    "email",
    "digital_address",
    "first_name",
    "surname",
    "full_name_std",
}


@pytest.fixture(scope="module")
def world():
    return build_world(load_config(profile="dev"))


@pytest.fixture(scope="module")
def gold(spark, world, tmp_path_factory):
    _, silver = build_silver(spark, world, tmp_path_factory.mktemp("gold") / "landing")
    tables = model.build(spark, silver, dt.date(2005, 1, 1), dt.date(2026, 12, 31), TODAY)
    out = {name: df.toPandas() for name, df in tables.items()}
    out["silver_party_xref"] = silver["party_xref"].toPandas()
    return out


@pytest.fixture(scope="module")
def key(world):
    return answer_key.build(world)


def test_every_table_is_populated(gold):
    empty = [name for name, df in gold.items() if df.empty and not name.startswith("silver_")]
    assert empty == []


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("dim_date", "date_key"),
        ("dim_branch", "branch_key"),
        ("dim_product", "product_key"),
        ("dim_channel", "channel_key"),
        ("dim_customer", "customer_key"),
        ("dim_account", "account_key"),
        ("dim_merchant", "merchant_key"),
    ],
)
def test_dimension_keys_are_unique(gold, table, column):
    assert gold[table][column].is_unique


@pytest.mark.parametrize(
    ("fact", "column", "dim", "dim_column"),
    [
        ("fact_transaction", "account_key", "dim_account", "account_key"),
        ("fact_transaction", "customer_key", "dim_customer", "customer_key"),
        ("fact_transaction", "branch_key", "dim_branch", "branch_key"),
        ("fact_transaction", "channel_key", "dim_channel", "channel_key"),
        ("fact_transaction", "date_key", "dim_date", "date_key"),
        ("fact_loan", "account_key", "dim_account", "account_key"),
        ("fact_loan_payment", "branch_key", "dim_branch", "branch_key"),
        ("fact_loan_payment", "customer_key", "dim_customer", "customer_key"),
        ("fact_card_transaction", "merchant_key", "dim_merchant", "merchant_key"),
        ("fact_card_transaction", "date_key", "dim_date", "date_key"),
        ("fact_digital_event", "customer_key", "dim_customer", "customer_key"),
        ("fact_gl_entry", "branch_key", "dim_branch", "branch_key"),
        ("dim_account", "customer_key", "dim_customer", "customer_key"),
    ],
)
def test_facts_reference_dimensions(gold, fact, column, dim, dim_column):
    values = set(gold[fact][column]) - {model.UNKNOWN_KEY}
    assert values <= set(gold[dim][dim_column])


def test_gold_carries_no_raw_pii(gold):
    for name, df in gold.items():
        if name.startswith("silver_"):
            continue
        assert not PII_COLUMNS & set(df.columns), name
    assert gold["dim_account"]["account_number_masked"].str.startswith("*********").all()


def test_one_customer_per_party_and_duplicates_flagged(gold, key):
    customers = gold["dim_customer"]
    assert customers["party_id"].is_unique
    planted = key["dq_duplicate_customers"]
    assert 0 < customers["has_duplicate_cif"].sum() <= len(planted)


def test_gl_fee_breaks_match_the_planted_ones(gold, key):
    recon = gold["recon_gl_fees"]
    breaks = recon[recon["is_break"]]
    planted = key["dq_gl_breaks"]
    planted = planted[planted["period"] <= "2025-12"]
    found = set(zip(breaks["month_key"].astype(str), breaks["branch_code"], strict=True))
    expected = {
        (p.replace("-", ""), b)
        for p, b in zip(planted["period"], planted["branch_code"], strict=True)
    }
    assert found == expected
    merged = breaks.assign(period=breaks["month_key"].astype(str)).merge(
        planted.assign(period=planted["period"].str.replace("-", "")), on=["period", "branch_code"]
    )
    assert (merged["difference"].astype(float).round(2) == merged["difference_ghs"].round(2)).all()


def test_app_users_link_to_the_right_customer(gold, key):
    links = gold["app_user_link"].merge(
        gold["dim_customer"][["customer_key", "party_id"]], on="customer_key"
    )
    people = key["person_crosswalk"]
    xref = gold["silver_party_xref"]
    party_of_cif = xref[xref["source_system"] == "core"].set_index("source_id")["party_id"]
    expected = people.set_index("mobile_user_id")["cif_number"].map(party_of_cif)
    assert len(links) > 0
    correct = (links["party_id"] == links["user_id"].map(expected)).mean()
    assert correct >= 0.99, correct


def test_card_events_mostly_link_to_accounts(gold):
    cards = gold["fact_card_transaction"]
    debit = cards[cards["account_key"] != model.UNKNOWN_KEY]
    assert len(debit) / len(cards) > 0.6
    assert cards["is_night"].any()


def test_defaulters_show_up_as_default_loans(gold, key):
    loans = gold["fact_loan"]
    accounts = gold["dim_account"].set_index("account_key")["account_number"]
    outcome = key["loan_outcomes"].set_index("loan_account_number")["shock_date"]
    loans = loans.assign(number=loans["account_key"].map(accounts))
    loans = loans.assign(shocked=loans["number"].map(outcome).notna())
    default_rate = loans.groupby("shocked")["loan_status"].apply(lambda s: (s == "DEFAULT").mean())
    assert default_rate.get(True, 0) > 5 * default_rate.get(False, 0)


def test_dates_cover_the_history(gold):
    dates = gold["dim_date"]["date"]
    assert pd.Timestamp(dates.min()) <= pd.Timestamp("2005-01-01")
    assert pd.Timestamp(dates.max()) >= pd.Timestamp("2026-12-31")


@pytest.mark.parametrize(
    ("day", "label", "week_start", "quarter"),
    [
        ("2025-12-29", "2026-W01", "2025-12-29", "Q4"),  # ISO week belongs to next year
        ("2021-01-01", "2020-W53", "2020-12-28", "Q1"),  # and to the previous year
        ("2025-06-15", "2025-W24", "2025-06-09", "Q2"),  # Sunday ends the week
    ],
)
def test_dates_carry_iso_weeks_and_quarter_names(gold, day, label, week_start, quarter):
    dates = gold["dim_date"].assign(date=lambda d: pd.to_datetime(d["date"]))
    row = dates[dates["date"] == pd.Timestamp(day)].iloc[0]
    assert row["week_label"] == label
    assert pd.Timestamp(row["week_start"]) == pd.Timestamp(week_start)
    assert row["quarter_name"] == quarter


def test_every_branch_has_a_manager_and_grants_are_added(spark, gold):
    access = gold["sec_user_branch"]
    branches = set(gold["dim_branch"]["branch_code"])
    managers = access[access["user_upn"].str.startswith("manager.")]
    assert set(managers["branch_code"]) == branches
    assert managers.groupby("user_upn")["branch_code"].nunique().eq(1).all()

    dim_br = spark.createDataFrame(gold["dim_branch"][["branch_key", "branch_code"]])
    granted = model.sec_user_branch(
        spark, dim_br, {" Demo.User@Example.com ": ["BR001", "BR002", "BR999"]}
    ).toPandas()
    demo = granted[granted["user_upn"] == "demo.user@example.com"]
    assert sorted(demo["branch_code"]) == ["BR001", "BR002"]  # unknown codes are dropped
    assert demo["branch_key"].notna().all()
