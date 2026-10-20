"""Core banking system: customers (CIF), accounts, cards, and posted transactions.

The core is a relational system (landing in a Fabric SQL database, ADR-0004). It knows
people by CIF number, stores names in upper case, keeps only *posted* transactions with a
running balance, and has the flaws real cores accumulate:

- the same person registered twice (a second CIF at another branch, often with a typo),
  with some later accounts booked on the duplicate
- missing emails, digital addresses, and Ghana Card numbers (incomplete KYC)
- placeholder dates of birth
- the occasional double-posted transaction
"""

import datetime as dt

import numpy as np
import pandas as pd

from generator.projectors.common import (
    SourceBatch,
    null_out,
    stable_int,
    stable_uniform,
    system_id,
    typo,
)
from generator.timeline import Delta, Snapshot, customers_as_of
from generator.world import World

SYSTEM = "core_banking"
_DUPLICATE_ID_OFFSET = 50_000_000
_DOUBLE_POSTING_OFFSET = 10**11
PRODUCT_NUMBER = {
    "CA_PER": 10,
    "CA_BIZ": 11,
    "SA_REG": 20,
    "SA_SUSU": 21,
    "FD_TERM": 30,
    "LN_PERS": 40,
    "LN_SALADV": 41,
    "LN_MICRO": 42,
    "LN_SME_WC": 43,
    "CC_CLASSIC": 50,
}
TXN_CODE = {
    "SALARY_CREDIT": "SAL",
    "CASH_DEPOSIT": "CDP",
    "MOMO_TO_BANK": "MMI",
    "TRANSFER_IN": "TRI",
    "CASH_WITHDRAWAL": "CWD",
    "CARD_PURCHASE": "POS",
    "BANK_TO_MOMO": "MMO",
    "TRANSFER_OUT": "TRO",
    "BILL_PAYMENT": "BIL",
    "SAVINGS_SWEEP": "SWP",
    "LOAN_DISBURSEMENT": "LDS",
    "LOAN_REPAYMENT": "LRP",
    "FD_PLACEMENT": "FDP",
    "FD_MATURITY": "FDM",
    "CARD_REPAYMENT": "CCR",
    "MONTHLY_FEE": "FEE",
}


def snapshot_batch(world: World, snap: Snapshot) -> SourceBatch:
    """Full initial load of the core as of `snap.as_of`."""
    core = _Core(world)
    batch = SourceBatch(SYSTEM)
    batch.add("core_branches", core.branches(), "sql")
    batch.add("core_products", core.products(), "sql")
    batch.add("core_customers", core.customers(snap.customers, snap.as_of), "sql")
    batch.add("core_accounts", core.accounts(snap.accounts, snap.as_of), "sql")
    batch.add("core_cards", core.cards(snap.accounts), "sql")
    batch.add("core_transactions", core.transactions(snap.postings), "sql")
    return batch


def delta_batch(world: World, d: Delta) -> SourceBatch:
    """Rows inserted or updated in the core during (d.since, d.until]."""
    core = _Core(world)
    current = customers_as_of(world, d.until)
    touched = set(d.new_customers["person_id"]) | set(d.customer_changes["person_id"])
    lo, hi = pd.Timestamp(d.since), pd.Timestamp(d.until)
    dup_new = core.cif[(core.cif["dup_created_on"] > lo) & (core.cif["dup_created_on"] <= hi)]
    touched |= set(dup_new["person_id"])
    customers = core.customers(current[current["person_id"].isin(touched)], d.until)
    customers = customers[(customers["updated_at"] > lo) | (customers["created_at"] > lo)]

    changed_ids = set(d.opened_accounts["account_id"]) | set(d.closed_accounts["account_id"])
    accounts = world.holdings.accounts
    accounts = accounts[accounts["account_id"].isin(changed_ids)].merge(
        world.account_closures.rename(columns={"reason": "close_reason"}),
        on="account_id",
        how="left",
    )
    accounts.loc[accounts["closed_on"] > hi, ["closed_on", "close_reason"]] = [pd.NaT, pd.NA]

    batch = SourceBatch(SYSTEM)
    batch.add("core_customers", customers, "sql")
    batch.add("core_accounts", core.accounts(accounts, d.until), "sql")
    batch.add("core_cards", core.cards(d.opened_accounts), "sql")
    batch.add("core_transactions", core.transactions(d.postings), "sql")
    return batch


def _day(values: pd.Series) -> pd.Series:
    """Dates as midnight timestamps (datetime64[s]).

    Python date objects would make a column's Parquet type depend on its contents: a CDC file
    whose dates are all empty would be written as an integer column, and readers that load it
    together with the full extract would fail on the type mismatch.
    """
    return pd.to_datetime(values).dt.normalize().astype("datetime64[s]")


class _Core:
    def __init__(self, world: World) -> None:
        self.world = world
        self.seed = world.config.seed
        self.p = world.config.projectors[SYSTEM]
        self.cif = self._cif_table()
        self.account_numbers = self._account_numbers()

    # ------------------------------------------------------------------ identity

    def _cif_table(self) -> pd.DataFrame:
        """CIF per person, plus a duplicate CIF for a stable share of people."""
        c = self.world.customers
        pid = c["person_id"].to_numpy()
        cif = system_id(self.seed, "core:cif", pid, 10**8)
        dup = stable_uniform(self.seed, "core:dup", pid) < self.p["duplicate_customer_share"]
        dup_cif = system_id(self.seed, "core:cif", pid + _DUPLICATE_ID_OFFSET, 10**8)
        delay = stable_int(self.seed, "core:dup:delay", pid, 30, 1500)
        created = c["joined_on"] + pd.to_timedelta(delay, unit="D")
        branches = [b.branch_code for b in self.world.config.branches]
        shift = stable_int(self.seed, "core:dup:branch", pid, 1, len(branches) - 1)
        home = c["home_branch_code"].map({code: i for i, code in enumerate(branches)})
        dup_branch = [branches[(h + s) % len(branches)] for h, s in zip(home, shift, strict=True)]
        end = pd.Timestamp(self.world.config.end_date)
        dup &= (created <= end).to_numpy()
        return pd.DataFrame(
            {
                "person_id": pid,
                "cif_number": [f"CIF{v:08d}" for v in cif],
                "dup_cif_number": np.where(dup, [f"CIF{v:08d}" for v in dup_cif], None),
                "dup_created_on": created.where(dup),
                "dup_branch_code": np.where(dup, dup_branch, None),
            }
        )

    def _account_numbers(self) -> pd.DataFrame:
        acc = self.world.holdings.accounts
        ids = acc["account_id"].to_numpy()
        body = system_id(self.seed, "core:acct", ids, 10**9)
        branch = acc["branch_code"].str.slice(2).astype(int).to_numpy()
        product = acc["product_code"].map(PRODUCT_NUMBER).fillna(0).astype(int).to_numpy()
        numbers = [f"{b:02d}{p:02d}{v:09d}" for b, p, v in zip(branch, product, body, strict=True)]

        # Some accounts opened after a duplicate CIF exists are booked on it.
        owner = acc[["account_id", "person_id", "opened_on"]].merge(self.cif, on="person_id")
        on_dup = (
            owner["dup_cif_number"].notna()
            & (owner["opened_on"] >= owner["dup_created_on"])
            & (
                stable_uniform(self.seed, "core:dup:acct", owner["account_id"])
                < self.p["duplicate_account_share"]
            )
        )
        owner["cif_number"] = np.where(on_dup, owner["dup_cif_number"], owner["cif_number"])
        return pd.DataFrame({"account_id": ids, "account_number": numbers}).merge(
            owner[["account_id", "cif_number"]], on="account_id"
        )

    # ------------------------------------------------------------------ tables

    def branches(self) -> pd.DataFrame:
        b = self.world.config.branches
        return pd.DataFrame(
            {
                "branch_code": [x.branch_code for x in b],
                "branch_name": [x.name.upper() for x in b],
                "city": [x.city for x in b],
                "region": [x.region for x in b],
                "opened_date": [x.opened_on for x in b],
            }
        )

    def products(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "product_code": [p.product_code for p in self.world.config.products],
                "product_name": [p.name for p in self.world.config.products],
                "product_family": [p.family.upper() for p in self.world.config.products],
            }
        )

    def customers(self, customers: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
        """Primary CIF rows for `customers`, plus duplicate CIF rows that exist by `as_of`."""
        p, seed = self.p, self.seed
        c = customers.merge(self.cif, on="person_id")
        pid = c["person_id"]
        last_change = (
            self.world.customer_changes[
                self.world.customer_changes["changed_on"] <= pd.Timestamp(as_of)
            ]
            .groupby("person_id")["changed_on"]
            .max()
        )
        is_business = c["customer_type"] == "business"
        dob = c["date_of_birth"].where(
            stable_uniform(seed, "core:dob", pid) >= p["placeholder_dob_share"],
            pd.Timestamp(p["placeholder_dob"]),
        )
        created = c["joined_on"] + pd.to_timedelta(
            stable_int(seed, "core:created", pid, 8 * 3600, 16 * 3600), unit="s"
        )
        primary = pd.DataFrame(
            {
                "cif_number": c["cif_number"],
                "customer_type": np.where(is_business, "CORPORATE", "INDIVIDUAL"),
                "first_name": c["first_name"].str.upper().where(~is_business),
                "middle_name": c["middle_name"].str.upper().where(~is_business),
                "surname": c["last_name"]
                .str.upper()
                .where(~is_business, c["business_name"].str.upper()),
                "gender": c["gender"].where(~is_business),
                "date_of_birth": dob.where(~is_business),
                "ghana_card_no": null_out(
                    c["ghana_card"], seed, "core:gc", pid, p["missing_ghana_card_share"]
                ),
                "mobile_no": c["phone_number"],
                "email": null_out(c["email"], seed, "core:email", pid, p["missing_email_share"]),
                "digital_address": null_out(
                    c["digital_address"], seed, "core:gps", pid, p["missing_digital_address_share"]
                ),
                "branch_code": c["home_branch_code"],
                "created_at": created,
                "updated_at": pid.map(last_change)
                .fillna(created)
                .where(lambda s: s >= created, created),
            }
        )

        dup = c[c["dup_cif_number"].notna() & (c["dup_created_on"] <= pd.Timestamp(as_of))]
        if dup.empty:
            return primary
        dup_rows = primary[
            c["dup_cif_number"].notna().to_numpy()
            & (c["dup_created_on"] <= pd.Timestamp(as_of)).to_numpy()
        ].copy()
        dpid = dup["person_id"]
        has_typo = stable_uniform(seed, "core:dup:typo", dpid) < p["duplicate_name_typo_share"]
        which = stable_uniform(seed, "core:dup:typo:field", dpid) < 0.5
        surname_typo = typo(dup_rows["surname"].fillna(""), seed, "core:dup:surname", dpid)
        first_typo = typo(dup_rows["first_name"].fillna(""), seed, "core:dup:first", dpid)
        dup_rows["surname"] = np.where(has_typo & which, surname_typo, dup_rows["surname"])
        dup_rows["first_name"] = np.where(
            has_typo & ~which & dup_rows["first_name"].notna().to_numpy(),
            first_typo,
            dup_rows["first_name"],
        )
        dup_rows["cif_number"] = dup["dup_cif_number"].to_numpy()
        dup_rows["branch_code"] = dup["dup_branch_code"].to_numpy()
        dup_rows["created_at"] = dup["dup_created_on"].to_numpy() + pd.to_timedelta(
            stable_int(seed, "core:dup:created", dpid, 8 * 3600, 16 * 3600), unit="s"
        )
        dup_rows["updated_at"] = dup_rows["created_at"]
        return pd.concat([primary, dup_rows], ignore_index=True)

    def accounts(self, accounts: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
        """Deposit, loan, and credit-card accounts (debit cards live in core_cards)."""
        a = accounts[accounts["product_code"] != "DC_DEBIT"].merge(
            self.account_numbers, on="account_id"
        )
        linked = self.account_numbers.set_index("account_id")["account_number"]
        closed = a["closed_on"].notna() if "closed_on" in a else pd.Series(False, index=a.index)
        return pd.DataFrame(
            {
                "account_number": a["account_number"],
                "cif_number": a["cif_number"],
                "product_code": a["product_code"],
                "branch_code": a["branch_code"],
                "currency": "GHS",
                "opened_date": _day(a["opened_on"]),
                "status": np.where(closed, "CLOSED", "ACTIVE"),
                "closed_date": _day(a["closed_on"]) if "closed_on" in a else pd.NaT,
                "linked_account_number": a["linked_account_id"].map(linked),
                "contract_amount": a["contract_amount_pesewas"].astype("Float64") / 100,
                "interest_rate_pct": a["annual_rate_bps"].astype("Float64") / 100,
                "term_months": a["term_months"],
                "maturity_date": _day(a["maturity_date"]),
            }
        )

    def cards(self, accounts: pd.DataFrame) -> pd.DataFrame:
        cards = accounts[accounts["product_code"] == "DC_DEBIT"]
        linked = self.account_numbers.set_index("account_id")["account_number"]
        last4 = system_id(self.seed, "card:pan", cards["account_id"], 10**4)
        return pd.DataFrame(
            {
                "card_id": [
                    f"CRD{v:010d}"
                    for v in system_id(self.seed, "core:card", cards["account_id"], 10**10)
                ],
                "pan_masked": [f"990001******{v:04d}" for v in last4],
                "account_number": cards["linked_account_id"].map(linked).to_numpy(),
                "issued_date": _day(cards["opened_on"]).to_numpy(),
                "card_type": "DEBIT",
            }
        )

    def transactions(self, postings: pd.DataFrame) -> pd.DataFrame:
        """Posted transactions only, with a running balance, plus a few double postings."""
        p = postings[postings["status"] == "POSTED"]
        numbers = self.account_numbers.set_index("account_id")["account_number"]
        merchants = self.world.merchants.set_index("merchant_id")["merchant_name"]
        txn = p["txn_type"].astype(str)
        amount = p["amount_pesewas"].to_numpy()
        narration = (
            txn.str.replace("_", " ")
            + np.where(
                p["merchant_id"].notna(),
                " " + p["merchant_id"].map(merchants).fillna("").astype(str).str.upper(),
                "",
            )
            + np.where(
                p["counterparty"].notna(),
                " " + p["counterparty"].fillna("").astype(str).str.upper(),
                "",
            )
        )
        frame = pd.DataFrame(
            {
                "txn_ref": [
                    f"FT{v:011d}" for v in system_id(self.seed, "core:txn", p["posting_id"], 10**11)
                ],
                "account_number": p["account_id"].map(numbers).to_numpy(),
                "value_date": _day(p["posted_at"]).to_numpy(),
                "booking_ts": p["posted_at"].to_numpy(),
                "txn_code": txn.map(TXN_CODE).to_numpy(),
                "dr_cr": np.where(amount < 0, "DR", "CR"),
                "amount": np.abs(amount) / 100,
                "running_balance": p["balance_after_pesewas"].astype("Float64").to_numpy() / 100,
                "channel": p["channel"].astype(str).to_numpy(),
                "narration": narration.to_numpy(),
                "counterparty_account": p["counterparty_account"].to_numpy(),
            }
        )
        doubled = (
            stable_uniform(self.seed, "core:double", p["posting_id"])
            < self.p["double_posting_share"]
        )
        if doubled.any():
            extra = frame[doubled].copy()
            ids = p.loc[doubled, "posting_id"].to_numpy() + _DOUBLE_POSTING_OFFSET
            extra["txn_ref"] = [
                f"FT{v:011d}" for v in system_id(self.seed, "core:txn2", ids, 10**11)
            ]
            frame = pd.concat([frame, extra]).sort_values(["booking_ts", "txn_ref"], kind="stable")
        return frame.reset_index(drop=True)
