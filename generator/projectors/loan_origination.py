"""Loan origination system (LOS): daily CSV extracts of applications and repayments.

The LOS is a separate vendor system. It identifies applicants by Ghana Card (formatted its
own way) and name, stores amounts as human-formatted strings, and links approved loans to
the core loan account number. Flaws:

- applications appear twice when the decision comes days after filing (PENDING, then the
  decision) - Silver must keep the latest row per application
- `monthly_income` is renamed `net_monthly_income` from `rename_income_column_from`
- a few daily files are delivered twice
- declined applications exist that never became loans
"""

import datetime as dt

import numpy as np
import pandas as pd

from generator.projectors.common import (
    SourceBatch,
    ghana_card_variants,
    ghs_string,
    stable_int,
    stable_uniform,
    system_id,
)
from generator.projectors.core_banking import _Core
from generator.world import World

SYSTEM = "loan_origination"
_DECLINED_OFFSET = 10**7
_EMPLOYER = {
    "civil_servant": "Government",
    "corporate_employee": "Private sector",
    "market_trader": "Self-employed",
    "susu_saver": "Self-employed",
    "sme": "Business",
}
_COLLATERAL = {
    "LN_PERS": "Salary assignment",
    "LN_SALADV": "Salary assignment",
    "LN_MICRO": "Group guarantee",
    "LN_SME_WC": "Inventory",
}
_DECLINE_REASONS = ["DTI above policy", "Insufficient verifiable income", "Adverse bureau record"]


def extracts(world: World, since: dt.date | None, until: dt.date) -> SourceBatch:
    """Applications and repayments in the daily extracts dated (since, until]."""
    los = _Los(world)
    lo = pd.Timestamp.min if since is None else pd.Timestamp(since)
    hi = pd.Timestamp(until)

    apps = los.application_rows()
    apps = apps[(apps["extract_date"] > lo) & (apps["extract_date"] <= hi)]
    renamed = apps["extract_date"] >= pd.Timestamp(los.p["rename_income_column_from"])
    old, new = (
        apps[~renamed],
        apps[renamed].rename(columns={"monthly_income": "net_monthly_income"}),
    )

    repay = los.repayment_rows()
    repay = repay[(repay["extract_date"] > lo) & (repay["extract_date"] <= hi)]

    resend = los.resend_days(lo, hi)
    batch = SourceBatch(SYSTEM)
    batch.add("los_applications", old, "csv", partition_by="extract_date", resend_partitions=resend)
    batch.add(
        "los_applications_v2",
        new,
        "csv",
        partition_by="extract_date",
        resend_partitions=resend,
        file_stem="los_applications",
    )
    batch.add("los_repayments", repay, "csv", partition_by="extract_date", resend_partitions=resend)
    return batch


class _Los:
    def __init__(self, world: World) -> None:
        self.world = world
        self.seed = world.config.seed
        self.p = world.config.projectors[SYSTEM]
        self.core = _Core(world)
        self.customers = world.customers.set_index("person_id")

    def application_rows(self) -> pd.DataFrame:
        approved = self._approved()
        declined = self._declined()
        apps = pd.concat([approved, declined], ignore_index=True)
        seed, p = self.seed, self.p
        key = apps["app_key"].to_numpy()
        same_day = stable_uniform(seed, "los:sameday", key) < p["same_day_decision_share"]
        wait = np.where(same_day, 0, stable_int(seed, "los:wait", key, 1, 5))
        decided = apps["application_date"] + pd.to_timedelta(wait, unit="D")
        end = pd.Timestamp(self.world.config.end_date)

        people = self.customers.loc[apps["person_id"]]
        names = people["last_name"].str.upper() + " " + people["first_name"]
        cards = ghana_card_variants(
            people["ghana_card"].reset_index(drop=True),
            seed,
            "los:gc",
            apps["person_id"],
            p["ghana_card_formats"],
        )
        base = pd.DataFrame(
            {
                "application_ref": [
                    f"APP-{d.year}-{v:06d}"
                    for d, v in zip(
                        apps["application_date"],
                        system_id(seed, "los:ref", key, 10**6),
                        strict=True,
                    )
                ],
                "applicant_name": names.to_numpy(),
                "applicant_ghana_card": cards.to_numpy(),
                "date_of_birth": people["date_of_birth"].dt.strftime("%d/%m/%Y").to_numpy(),
                "mobile_no": people["phone_number"].to_numpy(),
                "employer_type": people["segment"].map(_EMPLOYER).to_numpy(),
                "monthly_income": ghs_string(people["monthly_income_pesewas"]),
                "product": apps["product_name"].to_numpy(),
                "requested_amount": ghs_string(apps["requested_pesewas"]),
                "tenor_months": apps["tenor_months"].to_numpy(),
                "branch_code": people["home_branch_code"].to_numpy(),
                "collateral_type": apps["product_code"].map(_COLLATERAL).to_numpy(),
                "application_date": apps["application_date"].dt.strftime("%Y-%m-%d").to_numpy(),
            }
        )
        pending = base.assign(
            status="PENDING",
            decision_date=None,
            approved_amount=None,
            interest_rate_pct=None,
            decline_reason=None,
            loan_account_number=None,
            extract_date=apps["application_date"].to_numpy(),
        )
        decision = base.assign(
            status=apps["decision"].to_numpy(),
            decision_date=decided.dt.strftime("%Y-%m-%d").to_numpy(),
            approved_amount=np.where(
                apps["decision"] == "APPROVED", ghs_string(apps["requested_pesewas"]), None
            ),
            interest_rate_pct=apps["rate_pct"].to_numpy(),
            decline_reason=apps["decline_reason"].to_numpy(),
            loan_account_number=apps["loan_account_number"].to_numpy(),
            extract_date=decided.to_numpy(),
        )
        rows = pd.concat([pending[~same_day], decision], ignore_index=True)
        return rows[rows["extract_date"] <= end].sort_values(
            ["extract_date", "application_ref"], kind="stable"
        )

    def _approved(self) -> pd.DataFrame:
        acc = self.world.holdings.accounts
        loans = acc[acc["family"] == "loan"]
        lead = stable_int(
            self.seed,
            "los:lead",
            loans["account_id"],
            self.p["application_lead_days"]["min"],
            self.p["application_lead_days"]["max"],
        )
        numbers = self.core.account_numbers.set_index("account_id")["account_number"]
        names = {p.product_code: p.name for p in self.world.config.products}
        return pd.DataFrame(
            {
                "app_key": loans["account_id"].to_numpy(),
                "person_id": loans["person_id"].to_numpy(),
                "product_code": loans["product_code"].to_numpy(),
                "product_name": loans["product_code"].map(names).to_numpy(),
                "requested_pesewas": loans["contract_amount_pesewas"].to_numpy(dtype="int64"),
                "tenor_months": loans["term_months"].to_numpy(),
                "rate_pct": (loans["annual_rate_bps"].astype("Float64") / 100).to_numpy(),
                "application_date": (
                    loans["opened_on"] - pd.to_timedelta(lead, unit="D")
                ).to_numpy(),
                "decision": "APPROVED",
                "decline_reason": None,
                "loan_account_number": loans["account_id"].map(numbers).to_numpy(),
            }
        )

    def _declined(self) -> pd.DataFrame:
        """Declined applications: stably chosen applicants among likely borrowers."""
        seed = self.seed
        acc = self.world.holdings.accounts
        n_loans = int((acc["family"] == "loan").sum())
        k = round(n_loans * self.p["declined_application_share"])
        products = [p for p in self.world.config.products if p.family == "loan"]
        c = self.world.customers
        eligible = c[c["segment"].isin({s for p in products for s in p.take_up})]
        pid = eligible["person_id"].to_numpy()
        # Lower incomes apply-and-get-declined more often.
        income_rank = eligible["monthly_income_pesewas"].rank(pct=True).to_numpy()
        score = stable_uniform(seed, "los:decl:pick", pid) * (1.5 - income_rank)
        chosen = eligible.iloc[np.argsort(-score)[:k]]
        cpid = chosen["person_id"].to_numpy()
        key = cpid + _DECLINED_OFFSET
        prod_idx = stable_int(seed, "los:decl:prod", key, 0, len(products) - 1)
        product = [products[i] for i in prod_idx]
        start, end = (
            pd.Timestamp(self.world.config.start_date),
            pd.Timestamp(self.world.config.end_date),
        )
        joined = chosen["joined_on"].clip(lower=start)
        span = (end - joined).dt.days.to_numpy()
        when = joined + pd.to_timedelta(
            np.floor(stable_uniform(seed, "los:decl:when", key) * span), unit="D"
        )
        multiple = 2 + 6 * stable_uniform(seed, "los:decl:amt", key)
        requested = np.round(chosen["monthly_income_pesewas"].to_numpy() * multiple / 5000) * 5000
        tenors = [p.terms.term_months for p in product]
        tenor = [
            t[stable_int(seed, "los:decl:tenor", [kk], 0, len(t) - 1)[0]]
            for t, kk in zip(tenors, key, strict=True)
        ]
        return pd.DataFrame(
            {
                "app_key": key,
                "person_id": cpid,
                "product_code": [p.product_code for p in product],
                "product_name": [p.name for p in product],
                "requested_pesewas": requested.astype("int64"),
                "tenor_months": tenor,
                "rate_pct": None,
                "application_date": when.to_numpy(),
                "decision": "DECLINED",
                "decline_reason": np.asarray(_DECLINE_REASONS, dtype=object)[
                    stable_int(seed, "los:decl:reason", key, 0, len(_DECLINE_REASONS) - 1)
                ],
                "loan_account_number": None,
            }
        )

    def repayment_rows(self) -> pd.DataFrame:
        """One row per scheduled instalment that has fallen due, PAID or MISSED."""
        postings = self.world.ledger.postings
        acc = self.world.holdings.accounts
        loans = acc[acc["family"] == "loan"].set_index("account_id")
        repay = postings[postings["txn_type"] == "LOAN_REPAYMENT"]
        debit = repay[repay["amount_pesewas"] < 0][
            ["event_id", "posted_at", "amount_pesewas", "status"]
        ]
        credit_leg = repay[repay["account_id"].isin(loans.index)][["event_id", "account_id"]]
        rows = debit.merge(credit_leg, on="event_id")
        rows = rows.sort_values(["account_id", "posted_at"], kind="stable")
        rows["installment_no"] = rows.groupby("account_id").cumcount() + 1
        numbers = self.core.account_numbers.set_index("account_id")["account_number"]
        paid = rows["status"].astype(str) == "POSTED"
        due = -rows["amount_pesewas"].to_numpy()
        return pd.DataFrame(
            {
                "loan_account_number": rows["account_id"].map(numbers).to_numpy(),
                "installment_no": rows["installment_no"].to_numpy(),
                "due_date": rows["posted_at"].dt.strftime("%Y-%m-%d").to_numpy(),
                "amount_due": ghs_string(due),
                "amount_paid": ghs_string(np.where(paid, due, 0)),
                "payment_status": np.where(paid, "PAID", "MISSED"),
                "extract_date": rows["posted_at"].dt.normalize().to_numpy(),
            }
        )

    def resend_days(self, lo: pd.Timestamp, hi: pd.Timestamp) -> list[str]:
        """Extract dates whose files are (stably) delivered twice."""
        start = max(lo + pd.Timedelta(days=1), pd.Timestamp(self.world.config.start_date))
        days = pd.date_range(start, hi, freq="D")
        u = stable_uniform(self.seed, "los:resend", days.strftime("%Y%m%d").astype(int))
        return [d.strftime("%Y-%m-%d") for d in days[u < self.p["duplicate_file_share"]]]
