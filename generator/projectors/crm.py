"""CRM: contacts, complaints, NPS surveys, and campaign sends as daily file exports.

The CRM is a marketing and service tool. It knows people only as contacts (name, email,
phone): no CIF and no Ghana Card, so linking it to the core is a genuine entity-resolution
problem. Files are dropped into a Google Drive folder (ADR-0004). Flaws:

- names in title case with occasional typos; many contacts lack an email
- phone numbers in mixed national / international / spaced formats
- `preferred_channel` column appears in contact exports from `add_preferred_channel_from`
"""

import datetime as dt

import numpy as np
import pandas as pd

from generator.projectors.common import (
    SourceBatch,
    null_out,
    phone_variants,
    stable_choice,
    stable_int,
    stable_uniform,
    system_id,
    typo,
)
from generator.timeline import CHANGE_FIELDS
from generator.world import World

SYSTEM = "crm"
_COMPLAINT_TEXT = {
    "failed_transaction": "Customer reports a transfer of GHS {amt} was debited but not received.",
    "card_issue": "Card retained by the ATM / declined at POS. Customer requests a replacement.",
    "charges_dispute": "Customer disputes monthly charges on the account and requests a reversal.",
    "service_quality": "Long queue at the branch and rude treatment by staff, customer is unhappy.",
    "loan_query": "Customer asks about outstanding loan balance and repayment schedule.",
    "fraud_report": "Customer reports transactions they did not authorise; asks to block the card.",
}


def exports(world: World, since: dt.date | None, until: dt.date) -> SourceBatch:
    """CRM export rows dated (since, until], partitioned by export date."""
    crm = _Crm(world)
    lo = pd.Timestamp.min if since is None else pd.Timestamp(since)
    hi = pd.Timestamp(until)

    def window(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[(frame["export_date"] > lo) & (frame["export_date"] <= hi)]

    contacts = window(crm.contacts())
    drift = contacts["export_date"] >= pd.Timestamp(crm.p["add_preferred_channel_from"])
    batch = SourceBatch(SYSTEM)
    batch.add(
        "crm_contacts",
        contacts[~drift].drop(columns="preferred_channel"),
        "csv",
        partition_by="export_date",
    )
    batch.add(
        "crm_contacts_v2",
        contacts[drift],
        "csv",
        partition_by="export_date",
        file_stem="crm_contacts",
    )
    batch.add("crm_cases", window(crm.cases()), "csv", partition_by="export_date")
    batch.add("crm_nps_responses", window(crm.nps()), "csv", partition_by="export_date")
    batch.add("crm_campaign_sends", window(crm.campaign_sends()), "csv", partition_by="export_date")
    return batch


class _Crm:
    def __init__(self, world: World) -> None:
        self.world = world
        self.seed = world.config.seed
        self.p = world.config.projectors[SYSTEM]
        pid = world.customers["person_id"]
        self.contact_id = pd.Series(
            [f"CRM-{v:07d}" for v in system_id(self.seed, "crm:contact", pid, 10**7)],
            index=pid.to_numpy(),
        )
        lag = stable_int(
            self.seed,
            "crm:lag",
            pid,
            self.p["contact_created_lag_days"]["min"],
            self.p["contact_created_lag_days"]["max"],
        )
        self.created = pd.Series(
            (world.customers["joined_on"] + pd.to_timedelta(lag, unit="D")).to_numpy(),
            index=pid.to_numpy(),
        )

    def contacts(self) -> pd.DataFrame:
        """A contact row when created, and again whenever their details change.

        Each row carries the details as of its export date: the latest change of each field
        on or before that date (merge_asof), else the details at joining.
        """
        changes = self.world.customer_changes
        events = pd.concat(
            [
                pd.DataFrame(
                    {"person_id": self.created.index, "export_date": self.created.to_numpy()}
                ),
                changes[["person_id", "changed_on"]].rename(columns={"changed_on": "export_date"}),
            ],
            ignore_index=True,
        )
        events = events[events["export_date"] >= events["person_id"].map(self.created)]
        events = events[events["export_date"] <= pd.Timestamp(self.world.config.end_date)]
        events = events.drop_duplicates().sort_values("export_date", kind="stable")

        people = events.merge(self.world.customers, on="person_id", how="left")
        for field in CHANGE_FIELDS:
            history = changes[changes["field"] == field][["person_id", "changed_on", "new_value"]]
            history = history.sort_values("changed_on", kind="stable")
            latest = pd.merge_asof(
                people[["export_date", "person_id"]].reset_index(),
                history,
                left_on="export_date",
                right_on="changed_on",
                by="person_id",
                direction="backward",
            ).set_index("index")["new_value"]
            people[field] = latest.reindex(people.index).fillna(people[field])
        people = people.sort_values(["export_date", "person_id"], kind="stable")
        return self._contact_rows(people.reset_index(drop=True))

    def _contact_rows(self, people: pd.DataFrame) -> pd.DataFrame:
        seed, p = self.seed, self.p
        pid = people["person_id"]
        is_business = people["customer_type"] == "business"
        name = (people["first_name"] + " " + people["last_name"]).where(
            ~is_business, people["business_name"]
        )
        typoed = typo(name, seed, "crm:name", pid)
        name = name.where(
            stable_uniform(seed, "crm:name:typo", pid) >= p["name_typo_share"], typoed
        )
        branch_city = {b.branch_code: b.city for b in self.world.config.branches}
        return pd.DataFrame(
            {
                "contact_id": pid.map(self.contact_id).to_numpy(),
                "full_name": name.to_numpy(),
                "email": null_out(
                    people["email"], seed, "crm:email", pid, p["missing_email_share"]
                ).to_numpy(),
                "phone": phone_variants(
                    people["phone_number"], seed, "crm:phone", pid, p["phone_formats"]
                ).to_numpy(),
                "city": people["home_branch_code"].map(branch_city).to_numpy(),
                "contact_type": np.where(is_business, "Business", "Individual"),
                "created_date": pid.map(self.created).dt.strftime("%Y-%m-%d").to_numpy(),
                "preferred_channel": stable_choice(
                    seed,
                    "crm:pref",
                    pid,
                    ["SMS", "Email", "Phone", "WhatsApp"],
                    [0.35, 0.2, 0.15, 0.3],
                ),
                "export_date": people["export_date"].to_numpy(),
            }
        )

    def cases(self) -> pd.DataFrame:
        """Complaint cases: exported when opened and again when resolved."""
        c = self.world.scenarios.complaints
        c = c[c["opened_on"] >= c["person_id"].map(self.created)]
        amt = stable_int(self.seed, "crm:amt", c["complaint_id"], 50, 2000)
        text = [
            _COMPLAINT_TEXT[cat].format(amt=a) for cat, a in zip(c["category"], amt, strict=True)
        ]
        base = pd.DataFrame(
            {
                "case_id": [
                    f"CASE-{v:07d}"
                    for v in system_id(self.seed, "crm:case", c["complaint_id"], 10**7)
                ],
                "contact_id": c["person_id"].map(self.contact_id).to_numpy(),
                "category": c["category"].str.replace("_", " ").str.title().to_numpy(),
                "description": text,
                "opened_at": c["opened_on"].dt.strftime("%Y-%m-%d").to_numpy(),
            }
        )
        opened = base.assign(status="OPEN", resolved_at=None, export_date=c["opened_on"].to_numpy())
        done = c["resolved_on"].notna().to_numpy()
        resolved = base[done].assign(
            status="RESOLVED",
            resolved_at=c.loc[done, "resolved_on"].dt.strftime("%Y-%m-%d").to_numpy(),
            export_date=c.loc[done, "resolved_on"].to_numpy(),
        )
        return pd.concat([opened, resolved], ignore_index=True)

    def nps(self) -> pd.DataFrame:
        """NPS responses; scores fall for customers with unresolved complaints or about to leave."""
        seed, p = self.seed, self.p
        world = self.world
        start, end = pd.Timestamp(world.config.start_date), pd.Timestamp(world.config.end_date)
        c = world.customers
        years = (end - c["joined_on"].clip(lower=start)).dt.days.clip(lower=0) / 365.25
        n = np.floor(
            stable_uniform(seed, "crm:nps:n", c["person_id"])
            * 2
            * p["nps_surveys_per_customer_per_year"]
            * years
        ).astype(int)
        person = np.repeat(c["person_id"].to_numpy(), n)
        seq = np.concatenate([np.arange(k) for k in n if k]) if person.size else person
        key = person * 100 + seq
        lo = c.set_index("person_id")["joined_on"].clip(lower=start).loc[person].to_numpy()
        span = (end - pd.DatetimeIndex(lo)).days.to_numpy()
        when = pd.DatetimeIndex(lo) + pd.to_timedelta(
            np.floor(stable_uniform(seed, "crm:nps:t", key) * span), unit="D"
        )
        churn = world.scenarios.churn.set_index("person_id")["churn_date"]
        churn_at = pd.Series(person).map(churn)
        alive = churn_at.isna().to_numpy() | (when < churn_at.to_numpy())
        near_churn = (churn_at - pd.Series(when)).dt.days.between(0, 180).to_numpy()
        unresolved = world.scenarios.complaints.query("resolved_on.isna()", engine="python")
        complained = np.isin(person, unresolved["person_id"])
        base_score = 6.5 + 3.5 * stable_uniform(seed, "crm:nps:s", key)
        score = np.clip(np.round(base_score - 3 * near_churn - 2.5 * complained), 0, 10).astype(int)
        frame = pd.DataFrame(
            {
                "response_id": [f"NPS-{v:08d}" for v in system_id(seed, "crm:nps", key, 10**8)],
                "contact_id": pd.Series(person).map(self.contact_id).to_numpy(),
                "survey_date": when.strftime("%Y-%m-%d"),
                "score": score,
                "export_date": when,
            }
        )
        created = pd.Series(person).map(self.created).to_numpy()
        return frame[alive & (when >= created)]

    def campaign_sends(self) -> pd.DataFrame:
        seed, p = self.seed, self.p
        start, end = (
            pd.Timestamp(self.world.config.start_date),
            pd.Timestamp(self.world.config.end_date),
        )
        n_campaigns = int(p["campaigns_per_year"] * (end - start).days / 365.25)
        ids = np.arange(1, n_campaigns + 1)
        launch = start + pd.to_timedelta(
            np.floor(stable_uniform(seed, "crm:camp:t", ids) * (end - start).days), unit="D"
        )
        names = stable_choice(
            seed,
            "crm:camp:name",
            ids,
            [
                "Salary Account Switch",
                "Susu Plus Savings",
                "Loan Top-Up Offer",
                "Go Digital",
                "Christmas Bonanza",
                "School Fees Loan",
            ],
        )
        rows = []
        pid = self.world.customers["person_id"].to_numpy()
        for cid, when, name in zip(ids, launch, names, strict=True):
            key = pid * 1000 + cid
            reached = (stable_uniform(seed, "crm:camp:reach", key) < p["campaign_reach_share"]) & (
                self.created.to_numpy() <= when
            )
            k = key[reached]
            opened = stable_uniform(seed, "crm:camp:open", k) < 0.35
            clicked = opened & (stable_uniform(seed, "crm:camp:click", k) < 0.25)
            rows.append(
                pd.DataFrame(
                    {
                        "campaign_id": f"CMP-{cid:03d}",
                        "campaign_name": name,
                        "contact_id": self.contact_id.loc[pid[reached]].to_numpy(),
                        "sent_date": when.strftime("%Y-%m-%d"),
                        "opened": opened,
                        "clicked": clicked,
                        "export_date": when,
                    }
                )
            )
        return pd.concat(rows, ignore_index=True)
