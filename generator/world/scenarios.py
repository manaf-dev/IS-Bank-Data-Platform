"""Planted signals: credit defaults, complaints, churn, fraud, and the noise around them.

Everything here is ground truth. The ledger turns it into observable behaviour (missed
repayments, run-down accounts, suspicious postings); projectors ship only what each source
system would see; the answer key keeps the labels for offline evaluation.

Signals are probabilistic: each label is drawn from a true probability that depends on
features plus noise, so models have real structure to learn but no perfect separator.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from generator.config import WorldConfig
from generator.world.holdings import Holdings
from generator.world.sampling import add_months

FRAUD_TYPES = ("card_testing", "night_geo_jump", "account_takeover")
_NAT = np.datetime64("NaT", "D")
_DAY = np.timedelta64(1, "D")
_MONTH = np.timedelta64(1, "M")


@dataclass(frozen=True)
class Scenarios:
    """Ground truth that shapes the ledger.

    income_mult / activity_mult: [customers x months] multipliers on income and on
        discretionary activity (1.0 = unaffected), row order = customers.
    churn_date: per customer, the day they leave (NaT if they stay).
    """

    months: np.ndarray
    income_mult: np.ndarray
    activity_mult: np.ndarray
    churn_date: np.ndarray
    complaints: pd.DataFrame
    churn: pd.DataFrame
    loan_outcomes: pd.DataFrame
    beneficiaries: pd.DataFrame
    devices: pd.DataFrame
    fraud_events: pd.DataFrame
    fraud_rings: pd.DataFrame


def generate_scenarios(
    config: WorldConfig,
    customers: pd.DataFrame,
    holdings: Holdings,
    merchants: pd.DataFrame,
    profiles: pd.DataFrame,
    rng: np.random.Generator,
) -> Scenarios:
    """Draw every planted signal for the world."""
    builder = _Builder(config, customers, holdings, merchants, profiles, rng)
    return builder.build()


class _Builder:
    def __init__(self, config, customers, holdings, merchants, profiles, rng) -> None:
        self.config = config
        self.sig = config.signals
        self.rng = rng
        self.customers = customers
        self.accounts = holdings.accounts
        self.services = holdings.services
        self.merchants = merchants
        self.profiles = profiles
        self.n = len(customers)
        self.start = np.datetime64(config.start_date, "D")
        self.today = np.datetime64(config.today, "D")
        first, last = self.start.astype("datetime64[M]"), self.today.astype("datetime64[M]")
        self.months = np.arange(first, last + _MONTH, _MONTH)
        self.income_mult = np.ones((self.n, len(self.months)), dtype="float32")
        self.activity_mult = np.ones((self.n, len(self.months)), dtype="float32")
        self.income = customers["monthly_income_pesewas"].to_numpy(dtype=float)
        self.joined = customers["joined_on"].to_numpy().astype("datetime64[D]")

    def build(self) -> Scenarios:
        beneficiaries = self._beneficiaries()
        devices = self._devices()
        loan_outcomes = self._defaults()
        fraud_events, rings = self._fraud(beneficiaries, devices)
        complaints = self._complaints(fraud_events)
        churn, churn_date = self._churn(complaints, loan_outcomes)
        return Scenarios(
            months=self.months,
            income_mult=self.income_mult,
            activity_mult=self.activity_mult,
            churn_date=churn_date,
            complaints=complaints,
            churn=churn,
            loan_outcomes=loan_outcomes,
            beneficiaries=beneficiaries,
            devices=devices,
            fraud_events=fraud_events,
            fraud_rings=rings,
        )

    # ------------------------------------------------------------------ payees, devices

    def _beneficiaries(self) -> pd.DataFrame:
        r = self.sig.beneficiaries_per_customer
        counts = self.rng.integers(int(r.min), int(r.max) + 1, size=self.n)
        person = np.repeat(self.customers["person_id"].to_numpy(), counts)
        return pd.DataFrame(
            {
                "person_id": person,
                "beneficiary_account": _account_numbers(self.rng, person.size),
                "bank": self.rng.choice(
                    np.array(self.config.behaviour.counterparty_banks, dtype=object),
                    size=person.size,
                ),
            }
        ).astype({"beneficiary_account": "string", "bank": "string"})

    def _devices(self) -> pd.DataFrame:
        mb = self.services[self.services["service"] == "mobile_banking"]
        weights = self.sig.device_count_weights
        counts = self.rng.choice(np.array(list(weights)), size=len(mb), p=list(weights.values()))
        person = np.repeat(mb["person_id"].to_numpy(), counts)
        first_seen = np.repeat(mb["enrolled_on"].to_numpy().astype("datetime64[D]"), counts)
        # Additional devices appear some time after enrolment.
        extra = np.r_[[False], person[1:] == person[:-1]] if person.size else np.array([], bool)
        delay = self.rng.integers(30, 720, size=person.size).astype("timedelta64[D]")
        first_seen = np.where(extra, np.minimum(first_seen + delay, self.today), first_seen)
        return pd.DataFrame(
            {
                "person_id": person,
                "device_id": _device_ids(self.rng, person.size),
                "first_seen": first_seen.astype("datetime64[s]"),
            }
        ).astype({"device_id": "string"})

    # ------------------------------------------------------------------ credit default

    def _defaults(self) -> pd.DataFrame:
        d = self.sig.default
        rng = self.rng
        loans = self.accounts[self.accounts["family"] == "loan"]
        pos = loans["person_id"].to_numpy() - 1
        principal = loans["contract_amount_pesewas"].to_numpy(dtype=float)
        r = loans["annual_rate_bps"].to_numpy(dtype=float) / 10_000 / 12
        term = loans["term_months"].to_numpy(dtype="int64")
        emi = principal * r / (1 - (1 + r) ** -term)
        dti = emi / self.income[pos]
        opened = loans["opened_on"].to_numpy().astype("datetime64[D]")
        maturity = loans["maturity_date"].to_numpy().astype("datetime64[D]")

        stress = np.array([self._stress(o) for o in opened])
        segment = self.customers["segment"].to_numpy(dtype=object)[pos]
        seg_effect = np.array([d.segment_effect[s] for s in segment])
        median_income = np.median(self.income)
        logit = (
            d.intercept
            + d.dti * dti
            + d.stress * stress
            + d.log_income * np.log(self.income[pos] / median_income)
            + seg_effect
            + d.noise_sd * rng.standard_normal(len(loans))
        )
        p_default = 1 / (1 + np.exp(-logit))
        defaults = rng.random(len(loans)) < p_default

        lo = np.maximum(add_months(opened, np.full(len(loans), d.shock_min_months)), self.start)
        latest = np.maximum(np.ceil(term * d.shock_max_term_share), d.shock_min_months)
        hi = np.minimum(
            np.minimum(add_months(opened, latest.astype("int64")), maturity), self.today
        )
        in_window = defaults & (lo <= hi)
        span = np.maximum((hi - lo).astype("int64"), 0) + 1
        shock = np.where(
            in_window, lo + (rng.random(len(loans)) * span).astype("timedelta64[D]"), _NAT
        )

        # A borrower's earliest shock drives their income from then on.
        earliest = pd.Series(shock).groupby(pos).min().dropna()
        for p, when in earliest.items():
            m = self._month_index(np.datetime64(when, "D"))
            share = rng.uniform(d.shock_income_share.min, d.shock_income_share.max)
            self.income_mult[p, m:] *= share
            self.activity_mult[p, m:] *= d.shock_spend_share

        return pd.DataFrame(
            {
                "account_id": loans["account_id"].to_numpy(),
                "person_id": loans["person_id"].to_numpy(),
                "dti": dti,
                "macro_stress": stress,
                "p_default": p_default,
                "will_default": defaults,
                "shock_date": shock.astype("datetime64[s]"),
            }
        )

    def _stress(self, date: np.datetime64) -> float:
        if date < self.start:
            return self.sig.pre_window_macro_stress
        d = pd.Timestamp(date)
        return self.sig.macro_stress_by_quarter[f"{d.year}Q{d.quarter}"]

    # ------------------------------------------------------------------ fraud

    def _fraud(self, beneficiaries: pd.DataFrame, devices: pd.DataFrame):
        events: list[pd.DataFrame] = []
        scenario = [0]

        def next_id() -> int:
            scenario[0] += 1
            return scenario[0]

        cards = self._card_holders()
        events.append(self._card_testing(cards, next_id))
        events.append(self._geo_jumps(cards, next_id))
        ato, rings = self._takeovers(next_id)
        events.append(ato)
        frame = pd.concat([e for e in events if len(e)], ignore_index=True)
        frame = frame.sort_values(["ts", "person_id"], kind="stable").reset_index(drop=True)
        return frame, rings

    def _card_holders(self) -> pd.DataFrame:
        """Return the account each customer's card draws on (debit account, else credit card).

        Card fraud lands on the account the compromised card draws money from.
        """
        acc = self.accounts
        debit = acc[acc["product_code"] == "DC_DEBIT"][
            ["person_id", "opened_on", "linked_account_id"]
        ]
        debit = debit.rename(columns={"linked_account_id": "account_id"})
        cc = acc[acc["product_code"] == "CC_CLASSIC"][["person_id", "opened_on", "account_id"]]
        cards = pd.concat([debit, cc[~cc["person_id"].isin(debit["person_id"])]])
        return cards.reset_index(drop=True)

    def _monthly_victims(self, cards: pd.DataFrame, rate_per_1000: float):
        """Yield (month_start, victim rows) with Poisson victim counts per month."""
        for month in self.months:
            d0 = month.astype("datetime64[D]")
            live = cards[cards["opened_on"].to_numpy().astype("datetime64[D]") < d0]
            k = min(self.rng.poisson(rate_per_1000 * len(live) / 1000), len(live))
            if k:
                yield d0, live.iloc[self.rng.choice(len(live), size=k, replace=False)]

    def _card_testing(self, cards: pd.DataFrame, next_id) -> pd.DataFrame:
        p, rng = self.sig.card_testing, self.rng
        online = self.merchants.loc[self.merchants["channel"] != "pos", "merchant_id"].to_numpy()
        rows = []
        for d0, victims in self._monthly_victims(cards, p.monthly_victims_per_1000_cards):
            for person, account in zip(victims["person_id"], victims["account_id"], strict=True):
                sid = next_id()
                t0 = self._random_time(d0)
                k = int(rng.integers(p.probes.min, p.probes.max + 1))
                offsets = np.sort(rng.integers(0, p.probe_window_minutes * 60, size=k))
                probe_merchant = rng.choice(online)
                amount = rng.uniform(p.probe_amount_ghs.min, p.probe_amount_ghs.max, size=k)
                for off, amt in zip(offsets, amount, strict=True):
                    rows.append(
                        _fraud_row(
                            person,
                            account,
                            t0 + _secs(off),
                            -_ghs(amt),
                            "CARD_PURCHASE",
                            "ONLINE",
                            "card_testing",
                            sid,
                            merchant=probe_merchant,
                        )
                    )
                cashout = rng.uniform(p.cashout_income_multiple.min, p.cashout_income_multiple.max)
                ts = t0 + _secs(offsets[-1] + rng.integers(60, 3 * 3600))
                rows.append(
                    _fraud_row(
                        person,
                        account,
                        ts,
                        -round(self.income[person - 1] * cashout),
                        "CARD_PURCHASE",
                        "ONLINE",
                        "card_testing",
                        sid,
                        merchant=rng.choice(online),
                    )
                )
        return pd.DataFrame(rows)

    def _geo_jumps(self, cards: pd.DataFrame, next_id) -> pd.DataFrame:
        p, rng = self.sig.geo_jump, self.rng
        m = self.merchants
        home_city = (
            self.customers["home_branch_code"]
            .map({b.branch_code: b.city for b in self.config.branches})
            .to_numpy(dtype=object)
        )
        pos_merchants = m[m["channel"] != "online"]
        rows = []
        for d0, victims in self._monthly_victims(cards, p.monthly_victims_per_1000_cards):
            for person, account in zip(victims["person_id"], victims["account_id"], strict=True):
                sid = next_id()
                away = pos_merchants[pos_merchants["city"] != home_city[person - 1]]
                k = int(rng.integers(p.purchases.min, p.purchases.max + 1))
                city = rng.choice(away["city"].unique())
                pool = away.loc[away["city"] == city, "merchant_id"].to_numpy()
                day = d0 + int(rng.integers(0, 28)) * _DAY
                t = day.astype("datetime64[s]") + _secs(
                    rng.choice(p.hours) * 3600 + rng.integers(0, 3600)
                )
                for _ in range(k):
                    mult = rng.uniform(p.amount_income_multiple.min, p.amount_income_multiple.max)
                    rows.append(
                        _fraud_row(
                            person,
                            account,
                            t,
                            -round(self.income[person - 1] * mult),
                            "CARD_PURCHASE",
                            "POS",
                            "night_geo_jump",
                            sid,
                            merchant=rng.choice(pool),
                        )
                    )
                    t = t + _secs(rng.integers(5 * 60, 40 * 60))
        return pd.DataFrame(rows)

    def _takeovers(self, next_id) -> tuple[pd.DataFrame, pd.DataFrame]:
        p, rng = self.sig.takeover, self.rng
        years = (self.today - self.start).astype(int) / 365.25
        # At least one ring, so every world (even the small dev profile) contains the typology.
        n_rings = max(1, rng.poisson(p.rings_per_year_per_10k_customers * self.n / 10_000 * years))
        mb = self.services[self.services["service"] == "mobile_banking"]
        op_account = self._operating_accounts()
        op_opened = (
            self.accounts.set_index("account_id")
            .loc[op_account, "opened_on"]
            .to_numpy()
            .astype("datetime64[D]")
        )
        rows, rings = [], []
        for _ in range(n_rings):
            sid = next_id()
            span = (self.today - self.start).astype(int) - p.campaign_days
            begin = self.start + int(rng.integers(0, max(span, 1))) * _DAY
            enrolled = mb["enrolled_on"].to_numpy().astype("datetime64[D]")
            eligible = mb[(enrolled < begin) & (op_opened[mb["person_id"].to_numpy() - 1] < begin)]
            k = min(int(rng.integers(p.victims.min, p.victims.max + 1)), len(eligible))
            victims = eligible["person_id"].to_numpy()[
                rng.choice(len(eligible), size=k, replace=False)
            ]
            payees = _account_numbers(
                rng, int(rng.integers(p.beneficiaries.min, p.beneficiaries.max + 1))
            )
            devices = _device_ids(rng, int(rng.integers(p.devices.min, p.devices.max + 1)))
            bank = rng.choice(np.array(self.config.behaviour.counterparty_banks, dtype=object))
            rings.append(
                {
                    "scenario_id": sid,
                    "start": begin,
                    "victims": list(map(int, victims)),
                    "beneficiary_accounts": list(payees),
                    "devices": list(devices),
                }
            )
            for person in victims:
                for _ in range(
                    int(rng.integers(p.transfers_per_victim.min, p.transfers_per_victim.max + 1))
                ):
                    day = begin + int(rng.integers(0, p.campaign_days)) * _DAY
                    hour = (
                        rng.integers(0, 5) if rng.random() < p.night_share else rng.integers(0, 24)
                    )
                    ts = day.astype("datetime64[s]") + _secs(hour * 3600 + rng.integers(0, 3600))
                    mult = rng.uniform(
                        p.transfer_income_multiple.min, p.transfer_income_multiple.max
                    )
                    rows.append(
                        _fraud_row(
                            person,
                            op_account[person - 1],
                            ts,
                            -round(self.income[person - 1] * mult),
                            "TRANSFER_OUT",
                            "MOBILE",
                            "account_takeover",
                            sid,
                            counterparty=bank,
                            counterparty_account=rng.choice(payees),
                            device=rng.choice(devices),
                        )
                    )
        ring_frame = pd.DataFrame(
            rings, columns=["scenario_id", "start", "victims", "beneficiary_accounts", "devices"]
        )
        return pd.DataFrame(rows), ring_frame

    def _operating_accounts(self) -> np.ndarray:
        """Mirror the ledger's choice of operating account (first held, in priority order)."""
        acc = self.accounts
        out = np.full(self.n, -1, dtype="int64")
        for code in self.config.behaviour.operating_account_priority:
            rows = acc[acc["product_code"] == code]
            idx = rows["person_id"].to_numpy() - 1
            take = out[idx] < 0
            out[idx[take]] = rows["account_id"].to_numpy()[take]
        return out

    # ------------------------------------------------------------------ complaints, churn

    def _complaints(self, fraud_events: pd.DataFrame) -> pd.DataFrame:
        c, rng = self.sig.complaints, self.rng
        lo = np.maximum(self.joined, self.start)
        years = np.maximum((self.today - lo).astype(int), 0) / 365.25
        counts = rng.poisson(c.per_customer_per_year * years)
        person = np.repeat(np.arange(1, self.n + 1), counts)
        span = (self.today - lo[person - 1]).astype(int) + 1
        opened = lo[person - 1] + (rng.random(person.size) * span).astype("timedelta64[D]")
        category = rng.choice(
            np.array(list(c.categories), dtype=object),
            size=person.size,
            p=list(c.categories.values()),
        )

        # Most fraud victims report it a few days later.
        if len(fraud_events):
            victims = fraud_events.drop_duplicates(["scenario_id", "person_id"])[
                ["person_id", "ts"]
            ]
            report = rng.random(len(victims)) < c.fraud_report_share
            v = victims[report]
            person = np.r_[person, v["person_id"].to_numpy()]
            delay = rng.integers(1, 6, size=len(v)).astype("timedelta64[D]")
            opened = np.r_[opened, v["ts"].to_numpy().astype("datetime64[D]") + delay]
            category = np.r_[category, np.full(len(v), "fraud_report", dtype=object)]

        opened = np.minimum(opened, self.today)
        resolved = rng.random(person.size) < c.resolved_share
        days = rng.integers(
            int(c.resolution_days.min), int(c.resolution_days.max) + 1, size=person.size
        )
        resolved_on = opened + days.astype("timedelta64[D]")
        resolved &= resolved_on <= self.today
        frame = pd.DataFrame(
            {
                "person_id": person,
                "opened_on": opened.astype("datetime64[s]"),
                "category": category,
                "resolved_on": np.where(resolved, resolved_on, _NAT).astype("datetime64[s]"),
            }
        )
        frame = frame.sort_values(["opened_on", "person_id"], kind="stable").reset_index(drop=True)
        frame.insert(0, "complaint_id", np.arange(1, len(frame) + 1, dtype="int64"))
        return frame.astype({"category": "string"})

    def _churn(self, complaints: pd.DataFrame, loans: pd.DataFrame):
        ch, rng = self.sig.churn, self.rng
        segment = self.customers["segment"].to_numpy(dtype=object)
        base = np.array([ch.base_monthly_hazard[s] for s in segment])
        low_activity = self.profiles["activity"].to_numpy() < ch.low_activity_threshold
        frailty = ch.noise_sd * rng.standard_normal(self.n)
        dormant = self.profiles["dormant"].to_numpy()

        # Customers holding credit or a fixed deposit past a date can't leave before it.
        acc = self.accounts
        tied = acc[(acc["family"] == "loan") | acc["product_code"].isin(["CC_CLASSIC", "FD_TERM"])]
        tied_until = np.full(self.n, _NAT)
        end = np.where(
            tied["maturity_date"].isna(),
            np.datetime64("2262-01-01"),
            tied["maturity_date"].to_numpy(),
        ).astype("datetime64[D]")
        until = pd.Series(end).groupby(tied["person_id"].to_numpy() - 1).max()
        tied_until[until.index.to_numpy()] = until.to_numpy()
        shocked = loans.loc[loans["shock_date"].notna(), "person_id"].unique() - 1

        unresolved = complaints[complaints["resolved_on"].isna()]
        churn_date = np.full(self.n, _NAT)
        for m_idx in range(ch.earliest_month, len(self.months)):
            d0 = self.months[m_idx].astype("datetime64[D]")
            alive = np.isnat(churn_date) & (self.joined < d0) & ~dormant
            alive &= np.isnat(tied_until) | (tied_until < d0)
            alive[shocked] = False
            recent = unresolved["opened_on"].to_numpy().astype("datetime64[D]") < d0
            has_unresolved = np.zeros(self.n, dtype=bool)
            has_unresolved[unresolved.loc[recent, "person_id"].to_numpy() - 1] = True
            hazard = base * np.exp(
                ch.unresolved_complaint * has_unresolved + ch.low_activity * low_activity + frailty
            )
            leave = alive & (rng.random(self.n) < np.minimum(hazard, 0.5))
            idx = np.flatnonzero(leave)
            days = (self.months[m_idx] + _MONTH).astype("datetime64[D]") - d0
            churn_date[idx] = d0 + (rng.random(idx.size) * days.astype(int)).astype(
                "timedelta64[D]"
            )

        idx = np.flatnonzero(~np.isnat(churn_date))
        ramp = rng.integers(int(ch.ramp_months.min), int(ch.ramp_months.max) + 1, size=idx.size)
        ramp_start = add_months(churn_date[idx], -ramp)
        for i, p in enumerate(idx):
            m_churn = self._month_index(churn_date[p])
            m_start = max(self._month_index(ramp_start[i]), 0)
            steps = m_churn - m_start + 1
            curve = np.linspace(1.0, ch.ramp_floor, steps, dtype="float32")
            self.activity_mult[p, m_start : m_churn + 1] *= curve
            self.income_mult[p, m_start : m_churn + 1] *= curve
            self.activity_mult[p, m_churn + 1 :] = 0
            self.income_mult[p, m_churn + 1 :] = 0

        churn = pd.DataFrame(
            {
                "person_id": idx + 1,
                "churn_date": churn_date[idx].astype("datetime64[s]"),
                "ramp_start": np.maximum(ramp_start, self.start).astype("datetime64[s]"),
                "had_unresolved_complaint": np.isin(idx + 1, unresolved["person_id"].to_numpy()),
            }
        )
        return churn, churn_date

    # ------------------------------------------------------------------ helpers

    def _month_index(self, date: np.datetime64) -> int:
        return int((np.datetime64(date, "M") - self.months[0]).astype(int))

    def _random_time(self, d0: np.datetime64) -> np.datetime64:
        return d0.astype("datetime64[s]") + _secs(self.rng.integers(0, 28 * 86_400))


def _fraud_row(
    person,
    account,
    ts,
    amount,
    txn,
    channel,
    fraud_type,
    scenario_id,
    merchant=None,
    counterparty=None,
    counterparty_account=None,
    device=None,
) -> dict:
    return {
        "person_id": int(person),
        "account_id": int(account),
        "ts": np.datetime64(ts, "s"),
        "amount_pesewas": int(amount),
        "txn_type": txn,
        "channel": channel,
        "merchant_id": None if merchant is None else int(merchant),
        "counterparty": counterparty,
        "counterparty_account": counterparty_account,
        "device_id": device,
        "fraud_type": fraud_type,
        "scenario_id": int(scenario_id),
    }


def _secs(value) -> np.timedelta64:
    return np.timedelta64(int(value), "s")


def _ghs(amount: float) -> int:
    return round(amount * 100)


def _account_numbers(rng: np.random.Generator, n: int) -> np.ndarray:
    """Random 13-digit account numbers at other banks (unique within the call)."""
    numbers = rng.choice(9 * 10**12, size=n, replace=False) + 10**12
    return numbers.astype(str).astype(object)


def _device_ids(rng: np.random.Generator, n: int) -> np.ndarray:
    return np.array([f"dev-{v:012x}" for v in rng.integers(0, 16**12, size=n)], dtype=object)
