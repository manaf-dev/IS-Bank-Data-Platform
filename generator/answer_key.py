"""The answer key: ground truth for offline evaluation only.

Platform code (`src/isbank`, Fabric items) must never read it. It exists to measure entity
resolution, DQ detection, and model quality, in offline evaluation notebooks. It is written
to its own directory, separate from the landing zone.
"""

from pathlib import Path

import pandas as pd

from generator.projectors import compliance, crm, general_ledger
from generator.projectors.common import stable_uniform, system_id
from generator.projectors.core_banking import _Core
from generator.projectors.credit_bureau import _first_application
from generator.world import World


def build(world: World) -> dict[str, pd.DataFrame]:
    """Assemble every answer-key table for the whole simulated horizon."""
    seed = world.config.seed
    core = _Core(world)
    customers = world.customers
    pid = customers["person_id"]

    people = core.cif.merge(customers[["person_id", "ghana_card", "segment"]], on="person_id")
    people["crm_contact_id"] = pid.map(crm._Crm(world).contact_id).to_numpy()
    people["mobile_user_id"] = [f"MB{v:07d}" for v in system_id(seed, "mb:user", pid, 10**7)]
    people["bureau_ref"] = [f"XB{v:010d}" for v in system_id(seed, "bureau:ref", pid, 10**10)]

    accounts = core.account_numbers.merge(
        world.holdings.accounts[["account_id", "person_id", "product_code"]], on="account_id"
    )
    numbers = accounts.set_index("account_id")["account_number"]

    postings = world.ledger.postings
    fraud = postings[postings["fraud_type"].notna()]
    fraud_core = fraud[fraud["status"] == "POSTED"].assign(
        txn_ref=lambda d: [
            f"FT{v:011d}" for v in system_id(seed, "core:txn", d["posting_id"], 10**11)
        ]
    )
    card_ids = postings[postings["channel"].isin(["POS", "ONLINE", "ATM"])]
    card_labels = pd.DataFrame(
        {
            "event_id": [
                f"evt_{v:016x}" for v in system_id(seed, "card:evt", card_ids["posting_id"], 2**62)
            ],
            "fraud_type": card_ids["fraud_type"].astype(object).to_numpy(),
            "fraud_scenario_id": card_ids["fraud_scenario_id"].to_numpy(),
        }
    )

    loans = world.scenarios.loan_outcomes.assign(
        loan_account_number=lambda d: d["account_id"].map(numbers)
    )
    churn = world.scenarios.churn.merge(core.cif[["person_id", "cif_number"]], on="person_id")
    _, gl_breaks = general_ledger.pnl(world)
    p_core = world.config.projectors["core_banking"]
    double = postings[
        (postings["status"] == "POSTED")
        & (
            stable_uniform(seed, "core:double", postings["posting_id"])
            < p_core["double_posting_share"]
        )
    ]
    placeholder_dob = customers[
        stable_uniform(seed, "core:dob", pid) < p_core["placeholder_dob_share"]
    ].merge(core.cif[["person_id", "cif_number"]], on="person_id")

    return {
        "person_crosswalk": people,
        "account_crosswalk": accounts,
        "fraud_postings": fraud[
            [
                "posting_id",
                "person_id",
                "account_id",
                "posted_at",
                "amount_pesewas",
                "txn_type",
                "channel",
                "status",
                "fraud_type",
                "fraud_scenario_id",
            ]
        ],
        "fraud_core_txn_refs": fraud_core[["txn_ref", "fraud_type", "fraud_scenario_id"]],
        "card_event_labels": card_labels,
        "fraud_rings": world.scenarios.fraud_rings.astype(
            {"victims": str, "beneficiary_accounts": str, "devices": str}
        ),
        "loan_outcomes": loans,
        "churn": churn,
        "complaints": world.scenarios.complaints,
        "customer_changes": world.customer_changes,
        "account_closures": world.account_closures.assign(
            account_number=lambda d: d["account_id"].map(numbers)
        ),
        "dq_duplicate_customers": core.cif.dropna(subset=["dup_cif_number"]),
        "dq_double_postings": double[["posting_id", "account_id", "posted_at", "amount_pesewas"]],
        "dq_placeholder_dob": placeholder_dob[["person_id", "cif_number", "date_of_birth"]],
        "dq_gl_breaks": gl_breaks,
        "watchlist_truth": compliance.list_entries(world),
        "loan_applicants": _first_application(world),
        "profiles": world.ledger.profiles,
    }


def write(world: World, directory: Path) -> list[Path]:
    """Write the answer key as Parquet files under `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, frame in build(world).items():
        path = directory / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        written.append(path)
    return written
