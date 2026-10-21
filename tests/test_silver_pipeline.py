"""End-to-end Silver on generated data: every table builds, DQ is recorded, and entity
resolution is measured against the answer key (offline evaluation, never in the pipeline)."""

from itertools import combinations

import pandas as pd
import pytest

from generator import answer_key
from generator.config import load_config
from generator.world import build_world
from isbank.silver import pipeline
from isbank.silver.finance import BUREAU_LAYOUT
from tests.local_platform import build_silver

pytestmark = pytest.mark.spark


@pytest.fixture(scope="module")
def world():
    return build_world(load_config(profile="dev"))


@pytest.fixture(scope="module")
def silver(spark, world, tmp_path_factory):
    return build_silver(spark, world, tmp_path_factory.mktemp("silver") / "landing")


def test_every_table_builds(silver):
    run, written = silver
    failed = [(o.table, o.message) for o in run.outcomes if o.status != "SUCCEEDED"]
    assert failed == []
    assert set(written) == {t.name for t in pipeline.TABLES} | {"party_xref"}
    assert all(o.rows > 0 for o in run.outcomes)


def test_dq_results_cover_every_rule(silver):
    run, _ = silver
    expected = {(t.name, r.name) for t in pipeline.TABLES for r in t.rules}
    assert {(r["table_name"], r["rule_name"]) for r in run.dq_results} == expected


def test_events_deduplicated_and_late_flagged(silver):
    _, w = silver
    cards = w["card_authorisation"].toPandas()
    assert cards["event_id"].is_unique
    assert cards["is_late"].any()
    apps = w["app_event"].toPandas()
    assert apps["event_id"].is_unique
    assert apps.loc[apps["app_major_version"] < 3, "biometric_used"].isna().all()


def test_loan_applications_resolve_to_one_row_with_income(silver):
    _, w = silver
    apps = w["loan_application"].toPandas()
    assert apps["application_ref"].is_unique
    assert (apps["status"] != "PENDING").mean() > 0.95
    assert apps["monthly_income"].notna().all()


def test_gl_unpivots_every_line(silver, world):
    _, w = silver
    gl = w["gl_pnl"].toPandas()
    months = 36
    assert len(gl) == months * len(world.config.branches) * 7
    assert gl["cost_centre"].str.fullmatch(r"CC-\d{3}").all()


def test_bureau_layout_matches_the_published_spec(silver):
    from generator.projectors.credit_bureau import LAYOUT

    assert tuple(LAYOUT) == BUREAU_LAYOUT
    scores = silver[1]["bureau_score"].toPandas()
    assert scores["score"].between(300, 850).all()
    assert scores["ghana_card"].notna().all()


def _pairs(groups: pd.Series) -> set[tuple[str, str]]:
    out = set()
    for members in groups:
        out |= set(combinations(sorted(members), 2))
    return out


def test_entity_resolution_precision_and_recall(silver, world):
    _, w = silver
    xref = w["party_xref"].toPandas()
    xref["node"] = xref["source_system"] + "|" + xref["source_id"]
    key = answer_key.build(world)
    people = key["person_crosswalk"]

    truth = {}
    for _, p in people.iterrows():
        truth[f"core|{p['cif_number']}"] = p["person_id"]
        if pd.notna(p["dup_cif_number"]):
            truth[f"core|{p['dup_cif_number']}"] = p["person_id"]
        truth[f"bureau|{p['bureau_ref']}"] = p["person_id"]
    by_card = people.set_index("ghana_card")["person_id"]
    apps = w["loan_application"].toPandas()
    for _, a in apps.dropna(subset=["ghana_card"]).iterrows():
        truth[f"los|{a['application_ref']}"] = by_card.get(a["ghana_card"])

    scored = xref[xref["node"].map(truth).notna()].copy()
    scored["person"] = scored["node"].map(truth)
    predicted = _pairs(scored.groupby("party_id")["node"].apply(list))
    actual = _pairs(scored.groupby("person")["node"].apply(list))
    precision = len(predicted & actual) / len(predicted)
    recall = len(predicted & actual) / len(actual)
    print(f"ER pairwise precision={precision:.4f} recall={recall:.4f} pairs={len(actual)}")
    assert precision >= 0.99, precision
    assert recall >= 0.95, recall

    # The planted duplicate CIFs specifically are found.
    dups = people.dropna(subset=["dup_cif_number"])
    party = xref.set_index("node")["party_id"]
    found = [
        party.get(f"core|{d['cif_number']}") == party.get(f"core|{d['dup_cif_number']}")
        for _, d in dups.iterrows()
        if f"core|{d['dup_cif_number']}" in party.index
    ]
    assert found
    assert sum(found) / len(found) >= 0.9
