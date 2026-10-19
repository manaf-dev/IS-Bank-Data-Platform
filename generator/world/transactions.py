"""Simulate every account posting in the window, month by month, with running balances.

All customers are processed together for one calendar month at a time. Each month builds
that month's events (salary, irregular inflows, spending, loan and card repayments, fixed
deposit flows, fees), then posts them in time order per account. A declinable debit that
would take an account below its floor (zero for deposits, minus the limit for credit
cards) is DECLINED instead of posted; a declined transfer also cancels its credit leg.
Balances carry over to the next month.

Planted signals come from `Scenarios`: monthly income and activity multipliers (default
shocks, churn run-down), churn dates with a closing transfer, and labelled fraud events,
which are posted like any other debit and may themselves be declined.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from generator.config import WorldConfig
from generator.world.holdings import Holdings
from generator.world.sampling import add_months
from generator.world.scenarios import FRAUD_TYPES, Scenarios, _account_numbers

# Transaction types and channels are stored as small integer codes while simulating.
TXN_TYPES = (
    "SALARY_CREDIT",
    "CASH_DEPOSIT",
    "MOMO_TO_BANK",
    "TRANSFER_IN",
    "CASH_WITHDRAWAL",
    "CARD_PURCHASE",
    "BANK_TO_MOMO",
    "TRANSFER_OUT",
    "BILL_PAYMENT",
    "SAVINGS_SWEEP",
    "LOAN_DISBURSEMENT",
    "LOAN_REPAYMENT",
    "FD_PLACEMENT",
    "FD_MATURITY",
    "CARD_REPAYMENT",
    "MONTHLY_FEE",
)
CHANNELS = ("BRANCH", "ATM", "POS", "ONLINE", "MOBILE", "SYSTEM")
_T = {name: i for i, name in enumerate(TXN_TYPES)}
_C = {name: i for i, name in enumerate(CHANNELS)}

# Config flow names -> (transaction type, channel).
_FLOW = {
    "BRANCH_CASH_DEPOSIT": ("CASH_DEPOSIT", "BRANCH"),
    "MOMO_TO_BANK": ("MOMO_TO_BANK", "MOBILE"),
    "TRANSFER_IN": ("TRANSFER_IN", "SYSTEM"),
    "ATM_WITHDRAWAL": ("CASH_WITHDRAWAL", "ATM"),
    "BRANCH_WITHDRAWAL": ("CASH_WITHDRAWAL", "BRANCH"),
    "POS_PURCHASE": ("CARD_PURCHASE", "POS"),
    "ONLINE_PURCHASE": ("CARD_PURCHASE", "ONLINE"),
    "BANK_TO_MOMO": ("BANK_TO_MOMO", "MOBILE"),
    "TRANSFER_OUT": ("TRANSFER_OUT", "MOBILE"),
    "BILL_PAYMENT": ("BILL_PAYMENT", "MOBILE"),
}
_NAT = np.datetime64("NaT", "D")
_NO_FLOOR = np.iinfo("int64").min // 4
_DAY = np.timedelta64(1, "D")
_MAX_DECLINE_ROUNDS = 10_000
_FRAUD = {name: i + 1 for i, name in enumerate(FRAUD_TYPES)}
_FAR_FUTURE = np.datetime64("2262-01-01", "D")


@dataclass(frozen=True)
class Ledger:
    """Ground-truth ledger.

    postings: one row per account posting, POSTED or DECLINED, in time order.
    opening_balances: balance of every account at the start of the window.
    profiles: per-customer behavioural parameters (ground truth for later evaluation).
    """

    postings: pd.DataFrame
    opening_balances: pd.DataFrame
    profiles: pd.DataFrame


@dataclass
class _Month:
    """Postings accumulated for one month, as parallel lists of arrays."""

    cols: dict[str, list[np.ndarray]] = field(
        default_factory=lambda: {
            k: []
            for k in (
                "account",
                "person",
                "ts",
                "amount",
                "txn",
                "channel",
                "merchant",
                "counterparty",
                "counterparty_account",
                "device",
                "fraud",
                "scenario",
                "declinable",
                "event",
            )
        }
    )
    next_event: int = 0

    def add(
        self,
        account: np.ndarray,
        person: np.ndarray,
        ts: np.ndarray,
        amount: np.ndarray,
        txn: str,
        channel: np.ndarray | str,
        declinable: bool,
        event: np.ndarray | None = None,
        merchant: np.ndarray | None = None,
        counterparty: np.ndarray | None = None,
        counterparty_account: np.ndarray | None = None,
        device: np.ndarray | None = None,
        fraud: np.ndarray | None = None,
        scenario: np.ndarray | None = None,
    ) -> np.ndarray:
        """Append postings; returns the event keys used (pass them to the second leg)."""
        n = len(account)
        if event is None:
            event = np.arange(self.next_event, self.next_event + n, dtype="int64")
            self.next_event += n
        if isinstance(channel, str):
            channel = np.full(n, _C[channel], dtype="int8")
        c = self.cols
        c["account"].append(account.astype("int64"))
        c["person"].append(person.astype("int64"))
        c["ts"].append(ts.astype("datetime64[s]"))
        c["amount"].append(amount.astype("int64"))
        c["txn"].append(np.full(n, _T[txn], dtype="int8"))
        c["channel"].append(channel.astype("int8"))
        c["merchant"].append(
            merchant.astype("int64") if merchant is not None else np.full(n, -1, dtype="int64")
        )
        c["counterparty"].append(
            counterparty if counterparty is not None else np.full(n, None, dtype=object)
        )
        none = np.full(n, None, dtype=object)
        c["counterparty_account"].append(
            none if counterparty_account is None else counterparty_account
        )
        c["device"].append(none if device is None else device)
        c["fraud"].append(np.zeros(n, "int8") if fraud is None else fraud.astype("int8"))
        c["scenario"].append(np.zeros(n, "int64") if scenario is None else scenario.astype("int64"))
        c["declinable"].append(np.full(n, declinable))
        c["event"].append(event)
        return event

    def arrays(self) -> dict[str, np.ndarray]:
        return {k: np.concatenate(v) if v else np.array([]) for k, v in self.cols.items()}


def simulate_ledger(
    config: WorldConfig,
    customers: pd.DataFrame,
    holdings: Holdings,
    merchants: pd.DataFrame,
    profiles: pd.DataFrame,
    scenarios: Scenarios,
    rng: np.random.Generator,
) -> Ledger:
    """Simulate all postings from `config.start_date` to `config.today`."""
    sim = _Simulation(config, customers, holdings, merchants, profiles, scenarios, rng)
    return sim.run()


class _Simulation:
    def __init__(
        self,
        config: WorldConfig,
        customers: pd.DataFrame,
        holdings: Holdings,
        merchants: pd.DataFrame,
        profiles: pd.DataFrame,
        scenarios: Scenarios,
        rng: np.random.Generator,
    ) -> None:
        self.config = config
        self.b = config.behaviour
        self.sig = config.signals
        self.rng = rng
        self.scenarios = scenarios
        self.profiles_in = profiles
        self.start = np.datetime64(config.start_date, "D")
        self.today = np.datetime64(config.today, "D")
        self.accounts = holdings.accounts
        self._customer_arrays(customers, holdings)
        self._payees_and_devices(scenarios)
        self._account_arrays()
        self._merchant_pools(merchants)
        self.carry = self._opening_balances()
        self.opening = self.carry.copy()

    # ------------------------------------------------------------------ setup

    def _customer_arrays(self, customers: pd.DataFrame, holdings: Holdings) -> None:
        b = self.b
        n = len(customers)
        acc = self.accounts
        self.n = n
        self.person = customers["person_id"].to_numpy()
        if not np.array_equal(self.person, np.arange(1, n + 1)):
            raise ValueError("expected person_id to be 1..n in order")
        pos = pd.Series(np.arange(n), index=self.person)
        self.segment = customers["segment"].to_numpy(dtype=object)
        self.income = customers["monthly_income_pesewas"].to_numpy().astype(float)
        self.joined = customers["joined_on"].to_numpy().astype("datetime64[D]")
        branch_city = {br.branch_code: br.city for br in self.config.branches}
        self.city = customers["home_branch_code"].map(branch_city).to_numpy(dtype=object)

        def product_lookup(code: str) -> tuple[np.ndarray, np.ndarray]:
            rows = acc[acc["product_code"] == code]
            ids = np.full(n, -1, dtype="int64")
            opened = np.full(n, _NAT)
            idx = pos.loc[rows["person_id"]].to_numpy()
            ids[idx] = rows["account_id"].to_numpy()
            opened[idx] = rows["opened_on"].to_numpy().astype("datetime64[D]")
            return ids, opened

        # Operating account: the first held product in priority order.
        self.op_acc = np.full(n, -1, dtype="int64")
        self.op_from = np.full(n, _NAT)
        for code in b.operating_account_priority:
            ids, opened = product_lookup(code)
            take = (self.op_acc < 0) & (ids >= 0)
            self.op_acc[take], self.op_from[take] = ids[take], opened[take]
        if (self.op_acc < 0).any():
            raise ValueError("every customer needs an operating account; check priority list")

        sav, sav_from = product_lookup("SA_REG")
        is_other = sav != self.op_acc
        self.sav_acc = np.where(is_other, sav, -1)
        self.sav_from = np.where(is_other, sav_from, _NAT)
        self.debit_from = product_lookup("DC_DEBIT")[1]
        self.cc_acc, self.cc_from = product_lookup("CC_CLASSIC")
        cc_rows = acc[acc["product_code"] == "CC_CLASSIC"]
        self.cc_link = np.full(n, -1, dtype="int64")
        self.cc_link[pos.loc[cc_rows["person_id"]].to_numpy()] = cc_rows[
            "linked_account_id"
        ].to_numpy(dtype="int64")

        svc = holdings.services
        self.mobile_from = np.full(n, _NAT)
        self.momo_from = np.full(n, _NAT)
        self.wallet = np.full(n, None, dtype=object)
        mb = svc[svc["service"] == "mobile_banking"]
        self.mobile_from[pos.loc[mb["person_id"]].to_numpy()] = mb["enrolled_on"].to_numpy()
        mm = svc[svc["service"] == "momo_wallet_link"]
        mm_idx = pos.loc[mm["person_id"]].to_numpy()
        self.momo_from[mm_idx] = mm["enrolled_on"].to_numpy()
        self.wallet[mm_idx] = mm["wallet_number"].to_numpy(dtype=object)

        # Behavioural profile (shared with the scenario generator).
        prof = self.profiles_in
        if not np.array_equal(prof["person_id"].to_numpy(), self.person):
            raise ValueError("profiles must be aligned with customers")
        self.activity = prof["activity"].to_numpy(dtype=float)
        self.dormant = prof["dormant"].to_numpy(dtype=bool)
        self.spend_ratio = prof["spend_ratio"].to_numpy(dtype=float)
        self.salary_day = prof["salary_day"].to_numpy(dtype="int64")
        self.salaried = self.salary_day > 0
        churn = self.scenarios.churn_date
        self.churn_date = np.where(np.isnat(churn), _FAR_FUTURE, churn)
        shocks = self.scenarios.loan_outcomes.dropna(subset=["shock_date"])
        first_shock = shocks.groupby("person_id")["shock_date"].min()
        self.shock_date = np.full(n, _NAT)
        self.shock_date[first_shock.index.to_numpy() - 1] = first_shock.to_numpy().astype(
            "datetime64[D]"
        )

    def _payees_and_devices(self, scenarios: Scenarios) -> None:
        """Index each customer's regular payees and devices for vectorised picking."""
        ben = scenarios.beneficiaries
        self.payee = ben["beneficiary_account"].to_numpy(dtype=object)
        self.payee_bank = ben["bank"].to_numpy(dtype=object)
        self.payee_start, self.payee_count = _offsets(ben["person_id"].to_numpy(), self.n)
        self.payee_bank_of = dict(zip(self.payee, self.payee_bank, strict=True))
        dev = scenarios.devices
        self.device = dev["device_id"].to_numpy(dtype=object)
        self.device_start, self.device_count = _offsets(dev["person_id"].to_numpy(), self.n)

    def _account_arrays(self) -> None:
        acc = self.accounts
        self.n_accounts = len(acc)
        # account_id is 1..N, so index = id - 1.
        floors = np.full(self.n_accounts, _NO_FLOOR, dtype="int64")
        fam = acc["family"].to_numpy(dtype=object)
        code = acc["product_code"].to_numpy(dtype=object)
        floors[(fam == "deposit") & (code != "FD_TERM")] = 0
        cc = code == "CC_CLASSIC"
        floors[cc] = -acc.loc[cc, "contract_amount_pesewas"].to_numpy(dtype="int64")
        self.floor = floors
        self.acc_person = acc["person_id"].to_numpy()

        loans = acc[acc["family"] == "loan"]
        self.loan = _loan_schedule(loans)
        fds = acc[acc["product_code"] == "FD_TERM"]
        self.fd = {
            "account": fds["account_id"].to_numpy(),
            "person": fds["person_id"].to_numpy(),
            "link": fds["linked_account_id"].to_numpy(dtype="int64"),
            "opened": fds["opened_on"].to_numpy().astype("datetime64[D]"),
            "maturity": fds["maturity_date"].to_numpy().astype("datetime64[D]"),
            "amount": fds["contract_amount_pesewas"].to_numpy(dtype="int64"),
            "interest": np.round(
                fds["contract_amount_pesewas"].to_numpy(dtype=float)
                * fds["annual_rate_bps"].to_numpy(dtype=float)
                / 10_000
                * fds["term_months"].to_numpy(dtype=float)
                / 12
            ).astype("int64"),
        }
        fee_codes = list(self.b.monthly_fee_ghs)
        fee_rows = acc[acc["product_code"].isin(fee_codes)]
        self.fee = {
            "account": fee_rows["account_id"].to_numpy(),
            "person": fee_rows["person_id"].to_numpy(),
            "opened": fee_rows["opened_on"].to_numpy().astype("datetime64[D]"),
            "amount": np.round(
                fee_rows["product_code"].map(self.b.monthly_fee_ghs).to_numpy(dtype=float) * 100
            ).astype("int64"),
        }

    def _merchant_pools(self, merchants: pd.DataFrame) -> None:
        ch = merchants["channel"].to_numpy(dtype=object)
        ids = merchants["merchant_id"].to_numpy()
        city = merchants["city"].to_numpy(dtype=object, na_value=None)
        pos_ok = ch != "online"
        self.pos_all = ids[pos_ok]
        self.pos_by_city = {c: ids[pos_ok & (city == c)] for c in set(city[pos_ok])}
        self.online = ids[ch != "pos"]

    def _opening_balances(self) -> np.ndarray:
        """Balances at start_date for contracts opened before the window."""
        b, rng, acc = self.b, self.rng, self.accounts
        balance = np.zeros(self.n_accounts, dtype="int64")
        opened = acc["opened_on"].to_numpy().astype("datetime64[D]")
        pre = opened < self.start
        income_by_person = pd.Series(self.income, index=self.person)

        standalone = pre & (self.floor == 0)
        idx = np.flatnonzero(standalone)
        dormant_people = set(self.person[self.dormant])
        income = income_by_person.loc[self.acc_person[idx]].to_numpy()
        multiple = np.exp(
            np.log(b.opening_balance_median)
            + b.opening_balance_sigma * rng.standard_normal(idx.size)
        )
        balance[idx] = np.round(income * multiple).astype("int64")
        # Dormant customers left a small residue behind.
        is_dormant = np.isin(self.acc_person[idx], list(dormant_people))
        balance[idx[is_dormant]] //= 10

        fd = self.fd
        live = (fd["opened"] < self.start) & (fd["maturity"] >= self.start)
        balance[fd["account"][live] - 1] = fd["amount"][live]

        loan = self.loan
        paid = _months_between(loan["opened"], self.start - _DAY)
        paid = np.clip(paid, 0, loan["term"])
        live = (loan["opened"] < self.start) & (paid < loan["term"])
        outstanding = _outstanding(loan["principal"], loan["r"], loan["emi"], paid)
        balance[loan["account"][live] - 1] = -np.round(outstanding[live]).astype("int64")
        return balance

    # ------------------------------------------------------------------ run

    def run(self) -> Ledger:
        frames = []
        event_offset = 0
        month = self.start.astype("datetime64[M]")
        last = self.today.astype("datetime64[M]")
        while month <= last:
            d0 = month.astype("datetime64[D]")
            d1 = min((month + np.timedelta64(1, "M")).astype("datetime64[D]") - _DAY, self.today)
            m_idx = int((month - self.start.astype("datetime64[M]")).astype(int))
            m = self._build_month(d0, d1, int(month.astype(int) % 12) + 1, m_idx)
            frame = self._post_month(m, event_offset)
            event_offset += m.next_event
            frames.append(frame)
            month = month + np.timedelta64(1, "M")

        postings = pd.concat(frames, ignore_index=True)
        postings = postings.sort_values(["posted_at", "account_id", "event_id"], kind="stable")
        postings.insert(0, "posting_id", np.arange(1, len(postings) + 1, dtype="int64"))
        postings = postings.reset_index(drop=True)
        opening = pd.DataFrame(
            {
                "account_id": self.accounts["account_id"].to_numpy(),
                "balance_pesewas": self.opening,
            }
        )
        return Ledger(postings=postings, opening_balances=opening, profiles=self._profiles())

    def _profiles(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "person_id": self.person,
                "operating_account_id": self.op_acc,
                "savings_account_id": _nullable(self.sav_acc, self.sav_acc > 0),
                "activity": self.activity,
                "dormant": self.dormant,
                "spend_ratio": self.spend_ratio,
                "salary_day": _nullable(self.salary_day, self.salaried),
            }
        )

    def _fraud_month(self, m: _Month, d0, d1) -> None:
        """Post this month's labelled fraud events as ordinary (declinable) debits."""
        fe = self.scenarios.fraud_events
        if fe.empty:
            return
        day = fe["ts"].to_numpy().astype("datetime64[D]")
        rows = fe[
            (day >= d0) & (day <= d1) & (day < self.churn_date[fe["person_id"].to_numpy() - 1])
        ]
        for (txn, channel), g in rows.groupby(["txn_type", "channel"], sort=True):
            merchant = g["merchant_id"].to_numpy(dtype=float)
            m.add(
                g["account_id"].to_numpy(),
                g["person_id"].to_numpy(),
                g["ts"].to_numpy(),
                g["amount_pesewas"].to_numpy(),
                txn,
                channel,
                True,
                merchant=np.where(np.isnan(merchant), -1, merchant).astype("int64"),
                counterparty=g["counterparty"].to_numpy(dtype=object),
                counterparty_account=g["counterparty_account"].to_numpy(dtype=object),
                device=g["device_id"].to_numpy(dtype=object),
                fraud=g["fraud_type"].map(_FRAUD).to_numpy(),
                scenario=g["scenario_id"].to_numpy(),
            )

    def _shock_outflows(self, m: _Month, d0, d1) -> None:
        """A default shock takes a large share of the operating balance in one outflow."""
        idx = np.flatnonzero((self.shock_date >= d0) & (self.shock_date <= d1))
        if idx.size == 0:
            return
        share = self.sig.default.shock_balance_drain_share
        amount = np.floor(np.maximum(self.carry[self.op_acc[idx] - 1], 0) * share)
        keep = amount > 0
        idx, amount = idx[keep], amount[keep]
        ts = self.shock_date[idx].astype("datetime64[s]") + self._seconds(idx.size, 9, 16)
        m.add(
            self.op_acc[idx],
            self.person[idx],
            ts,
            -amount,
            "CASH_WITHDRAWAL",
            "BRANCH",
            True,
        )

    def _closing_transfers(self, m: _Month, d0, d1) -> None:
        """Churners move their money to another bank on the day they leave."""
        idx = np.flatnonzero((self.churn_date >= d0) & (self.churn_date <= d1))
        if idx.size == 0:
            return
        share = self.sig.churn.closing_transfer_share
        bank = self.rng.choice(np.array(self.b.counterparty_banks, dtype=object), size=idx.size)
        new_account = _account_numbers(self.rng, idx.size)
        ts = self.churn_date[idx].astype("datetime64[s]") + self._seconds(idx.size, 10, 16)
        for acc in (self.op_acc[idx], np.where(self.sav_acc[idx] > 0, self.sav_acc[idx], -1)):
            has = acc > 0
            amount = np.floor(np.maximum(self.carry[acc[has] - 1], 0) * share)
            keep = amount > 0
            sel = np.flatnonzero(has)[keep]
            m.add(
                acc[sel],
                self.person[idx[sel]],
                ts[sel],
                -amount[keep],
                "TRANSFER_OUT",
                "BRANCH",
                True,
                counterparty=bank[sel],
                counterparty_account=new_account[sel],
            )

    # ------------------------------------------------------------------ month

    def _build_month(
        self, d0: np.datetime64, d1: np.datetime64, month_no: int, m_idx: int
    ) -> _Month:
        m = _Month()
        n_days = int((d1 - d0).astype(int)) + 1
        season = self.b.seasonality[month_no]
        self.inc = self.scenarios.income_mult[:, m_idx].astype(float)
        self.act = self.scenarios.activity_mult[:, m_idx].astype(float)
        # Last day offset (exclusive) each customer can transact this month.
        self.hi = np.clip((self.churn_date - d0).astype("int64"), 0, n_days)
        active = (self.joined <= d1) & (self.op_from <= d1) & (self.churn_date > d0)
        earning = active & ~self.dormant

        self._salaries(m, d0, n_days, earning)
        self._irregular_inflows(m, d0, n_days, earning, season)
        emi_due = self._loans(m, d0, d1)
        self._fixed_deposits(m, d0, d1)
        sweep = self._savings_sweep(m, d1, earning)
        self._card_repayments(m, d0, n_days)
        self._spending(m, d0, n_days, active, season, emi_due, sweep)
        self._fraud_month(m, d0, d1)
        self._shock_outflows(m, d0, d1)
        self._closing_transfers(m, d0, d1)
        self._fees(m, d1)
        return m

    def _salaries(self, m: _Month, d0, n_days: int, earning: np.ndarray) -> None:
        pay_day = d0 + (np.minimum(self.salary_day, n_days) - 1).astype("timedelta64[D]")
        idx = np.flatnonzero(
            earning
            & self.salaried
            & (pay_day >= self.op_from)
            & (pay_day >= self.joined)
            & (pay_day < self.churn_date)
            & (self.inc > 0)
        )
        amount = (
            self.income[idx]
            * self.inc[idx]
            * (1 + self.b.income_noise_sd * self.rng.standard_normal(idx.size))
        )
        ts = pay_day[idx].astype("datetime64[s]") + self._seconds(idx.size, 1, 6)
        m.add(
            self.op_acc[idx],
            self.person[idx],
            ts,
            np.round(np.maximum(amount, 0)),
            "SALARY_CREDIT",
            "SYSTEM",
            False,
            counterparty=np.full(idx.size, "PAYROLL", dtype=object),
        )

    def _irregular_inflows(
        self, m: _Month, d0, n_days: int, earning: np.ndarray, season: float
    ) -> None:
        rng = self.rng
        for seg, flow in self.b.irregular_inflows.items():
            people = np.flatnonzero(earning & (self.segment == seg) & (self.inc > 0))
            people = people[self.hi[people] > self._lo_day(people, d0)]
            counts = rng.poisson(flow.events, size=people.size)
            idx = np.repeat(people, counts)
            if idx.size == 0:
                continue
            total = (
                self.income[people]
                * self.inc[people]
                * season
                * (1 + self.b.income_noise_sd * rng.standard_normal(people.size))
            )
            amount = _split(rng, np.maximum(total, 0), counts)
            kinds = _choose(rng, flow.channels, idx.size)
            momo_ok = _enabled(self.momo_from[idx], d0, n_days)  # approximate: by month end
            kinds = np.where((kinds == "MOMO_TO_BANK") & ~momo_ok, "BRANCH_CASH_DEPOSIT", kinds)
            lo = self._lo_day(idx, d0)
            for kind in np.unique(kinds):
                sel = kinds == kind
                txn, channel = _FLOW[kind]
                who = idx[sel]
                ts = self._times(d0, n_days, lo[sel], self.hi[who], branch=channel == "BRANCH")
                cp = self._counterparty(kind, who)
                m.add(
                    self.op_acc[who],
                    self.person[who],
                    ts,
                    np.round(amount[sel]),
                    txn,
                    channel,
                    False,
                    counterparty=cp,
                    counterparty_account=self._counterparty_account(kind, who),
                    device=self._devices(who) if channel == "MOBILE" else None,
                )

    def _spending(
        self,
        m: _Month,
        d0,
        n_days: int,
        active: np.ndarray,
        season: float,
        emi_due: np.ndarray,
        sweep: np.ndarray,
    ) -> None:
        rng, b = self.rng, self.b
        # Spending follows activity when income collapses (a shocked borrower keeps living
        # off savings), so the balance buffer drains and repayments start to fail.
        expected = self.income * np.maximum(self.inc, self.act) * season
        budget = np.maximum(self.spend_ratio * expected - emi_due - sweep, 0)
        for seg, flow in b.spending.items():
            people = np.flatnonzero(active & (self.segment == seg))
            people = people[self.hi[people] > self._lo_day(people, d0)]
            counts = rng.poisson(flow.events * self.activity[people] * self.act[people] * season)
            idx = np.repeat(people, counts)
            if idx.size == 0:
                continue
            amount = np.round(_split(rng, budget[people], counts))
            keep = amount > 0
            idx, amount = idx[keep], amount[keep]
            kinds = _choose(rng, flow.channels, idx.size)
            lo = self._lo_day(idx, d0)
            day = lo + (rng.random(idx.size) * (self.hi[idx] - lo)).astype("int64")
            date = d0 + day.astype("timedelta64[D]")
            has_debit = self.debit_from[idx] <= date
            has_cc = self.cc_from[idx] <= date
            card_kind = np.isin(kinds, ["ATM_WITHDRAWAL", "POS_PURCHASE", "ONLINE_PURCHASE"])
            no_card = card_kind & ~has_debit & ~((kinds != "ATM_WITHDRAWAL") & has_cc)
            no_momo = (kinds == "BANK_TO_MOMO") & ~(self.momo_from[idx] <= date)
            kinds = np.where(no_card | no_momo, "BRANCH_WITHDRAWAL", kinds)
            mobile = self.mobile_from[idx] <= date

            for kind in np.unique(kinds):
                sel = np.flatnonzero(kinds == kind)
                txn, channel = _FLOW[kind]
                who = idx[sel]
                if txn in {"TRANSFER_OUT", "BILL_PAYMENT"}:
                    ch = np.where(mobile[sel], _C["MOBILE"], _C["BRANCH"]).astype("int8")
                else:
                    ch = np.full(sel.size, _C[channel], dtype="int8")
                is_branch = ch == _C["BRANCH"]
                hours = self._hours(sel.size, is_branch)
                if txn == "CARD_PURCHASE":
                    # Some genuine customers shop late at night too.
                    night = rng.random(sel.size) < self.sig.legit_night_purchase_share
                    hours = np.where(night, rng.integers(0, 5 * 3600, size=sel.size), hours)
                ts = _at(date[sel], hours)
                ts = np.where(is_branch, self._skip_sunday(ts, d0, n_days, lo[sel]), ts)
                account = self.op_acc[who].copy()
                merchant = None
                if txn == "CARD_PURCHASE":
                    on_cc = has_cc[sel] & (
                        ~has_debit[sel] | (rng.random(sel.size) < b.credit_card.purchase_share)
                    )
                    account[on_cc] = self.cc_acc[who[on_cc]]
                    merchant = self._merchants(who, online=channel == "ONLINE")
                cp = self._counterparty(kind, who)
                cp_account = self._counterparty_account(kind, who)
                if kind == "TRANSFER_OUT":
                    cp = np.where(cp_account != None, self._payee_bank_for(who, cp_account), cp)  # noqa: E711
                on_mobile = ch == _C["MOBILE"]
                device = np.where(on_mobile, self._devices(who), None) if on_mobile.any() else None
                m.add(
                    account,
                    self.person[who],
                    ts,
                    -amount[sel],
                    txn,
                    ch,
                    True,
                    merchant=merchant,
                    counterparty=cp,
                    counterparty_account=cp_account,
                    device=device,
                )

    def _loans(self, m: _Month, d0, d1) -> np.ndarray:
        """Disburse loans opened this month and collect instalments due this month."""
        loan = self.loan
        emi_by_person = np.zeros(self.n)
        new = (loan["opened"] >= d0) & (loan["opened"] <= d1)
        idx = np.flatnonzero(new)
        if idx.size:
            ts = loan["opened"][idx].astype("datetime64[s]") + self._seconds(idx.size, 9, 16)
            ev = m.add(
                loan["link"][idx],
                loan["person"][idx],
                ts,
                loan["principal"][idx],
                "LOAN_DISBURSEMENT",
                "SYSTEM",
                False,
            )
            m.add(
                loan["account"][idx],
                loan["person"][idx],
                ts,
                -loan["principal"][idx],
                "LOAN_DISBURSEMENT",
                "SYSTEM",
                False,
                event=ev,
            )

        k = _months_between(loan["opened"], d0)
        due = add_months(loan["opened"], k)
        idx = np.flatnonzero((k >= 1) & (k <= loan["term"]) & (due >= d0) & (due <= d1))
        if idx.size:
            ts = due[idx].astype("datetime64[s]") + self._seconds(idx.size, 0, 3)
            emi = np.round(loan["emi"][idx])
            principal_part = np.round(
                _outstanding(loan["principal"][idx], loan["r"][idx], loan["emi"][idx], k[idx] - 1)
                - _outstanding(loan["principal"][idx], loan["r"][idx], loan["emi"][idx], k[idx])
            )
            ev = m.add(
                loan["link"][idx], loan["person"][idx], ts, -emi, "LOAN_REPAYMENT", "SYSTEM", True
            )
            m.add(
                loan["account"][idx],
                loan["person"][idx],
                ts,
                principal_part,
                "LOAN_REPAYMENT",
                "SYSTEM",
                False,
                event=ev,
            )
            person_pos = np.searchsorted(self.person, loan["person"][idx])
            np.add.at(emi_by_person, person_pos, emi)
        return emi_by_person

    def _fixed_deposits(self, m: _Month, d0, d1) -> None:
        fd = self.fd
        idx = np.flatnonzero((fd["opened"] >= d0) & (fd["opened"] <= d1))
        if idx.size:
            ts = fd["opened"][idx].astype("datetime64[s]") + self._seconds(idx.size, 9, 16)
            m.add(
                fd["account"][idx],
                fd["person"][idx],
                ts,
                fd["amount"][idx],
                "FD_PLACEMENT",
                "BRANCH",
                False,
            )
        idx = np.flatnonzero((fd["maturity"] >= d0) & (fd["maturity"] <= d1))
        if idx.size:
            ts = fd["maturity"][idx].astype("datetime64[s]") + self._seconds(idx.size, 0, 3)
            ev = m.add(
                fd["account"][idx],
                fd["person"][idx],
                ts,
                -fd["amount"][idx],
                "FD_MATURITY",
                "SYSTEM",
                False,
            )
            m.add(
                fd["link"][idx],
                fd["person"][idx],
                ts,
                fd["amount"][idx] + fd["interest"][idx],
                "FD_MATURITY",
                "SYSTEM",
                False,
                event=ev,
            )

    def _savings_sweep(self, m: _Month, d1, earning: np.ndarray) -> np.ndarray:
        swept = np.zeros(self.n)
        idx = np.flatnonzero(earning & (self.sav_acc > 0) & (self.sav_from <= d1))
        if idx.size == 0:
            return swept
        idx = idx[self.churn_date[idx] > d1]
        amount = np.round(
            (1 - self.spend_ratio[idx])
            * self.income[idx]
            * self.inc[idx]
            * self.b.savings_sweep_share
        )
        keep = amount > 0
        idx, amount = idx[keep], amount[keep]
        ts = np.full(idx.size, d1).astype("datetime64[s]") + self._seconds(idx.size, 20, 22)
        ev = m.add(self.op_acc[idx], self.person[idx], ts, -amount, "SAVINGS_SWEEP", "SYSTEM", True)
        m.add(
            self.sav_acc[idx],
            self.person[idx],
            ts,
            amount,
            "SAVINGS_SWEEP",
            "SYSTEM",
            False,
            event=ev,
        )
        swept[idx] = amount
        return swept

    def _card_repayments(self, m: _Month, d0, n_days: int) -> None:
        cc = self.b.credit_card
        day = d0 + np.timedelta64(min(cc.repayment_day, n_days) - 1, "D")
        idx = np.flatnonzero((self.cc_acc > 0) & (self.cc_from < d0))
        if idx.size == 0:
            return
        statement = -self.carry[self.cc_acc[idx] - 1]
        owing = statement > 0
        idx, statement = idx[owing], statement[owing]
        full = self.rng.random(idx.size) < cc.pay_full_share
        amount = np.where(full, statement, np.ceil(statement * cc.min_payment_pct / 100))
        ts = np.full(idx.size, day).astype("datetime64[s]") + self._seconds(idx.size, 6, 9)
        ev = m.add(
            self.cc_link[idx], self.person[idx], ts, -amount, "CARD_REPAYMENT", "SYSTEM", True
        )
        m.add(
            self.cc_acc[idx],
            self.person[idx],
            ts,
            amount,
            "CARD_REPAYMENT",
            "SYSTEM",
            False,
            event=ev,
        )

    def _fees(self, m: _Month, d1) -> None:
        fee = self.fee
        # Accounts of customers who have left are closed and no longer charged.
        idx = np.flatnonzero((fee["opened"] < d1) & (self.churn_date[fee["person"] - 1] > d1))
        ts = np.full(idx.size, d1).astype("datetime64[s]") + self._seconds(idx.size, 23, 24)
        m.add(
            fee["account"][idx],
            fee["person"][idx],
            ts,
            -fee["amount"][idx],
            "MONTHLY_FEE",
            "SYSTEM",
            True,
        )

    # ------------------------------------------------------------------ posting

    def _post_month(self, m: _Month, event_offset: int) -> pd.DataFrame:
        a = m.arrays()
        order = np.lexsort((a["event"], a["ts"], a["account"]))
        a = {k: v[order] for k, v in a.items()}
        acc = a["account"] - 1
        amount = a["amount"]
        posted = np.ones(len(acc), dtype=bool)
        start = np.r_[True, acc[1:] != acc[:-1]]
        group_start = np.maximum.accumulate(np.where(start, np.arange(len(acc)), 0))

        for _ in range(_MAX_DECLINE_ROUNDS):
            running = self._running(acc, amount * posted, group_start)
            bad = a["declinable"] & posted & (amount < 0) & (running < self.floor[acc])
            if not bad.any():
                break
            bad_idx = np.flatnonzero(bad)
            first = bad_idx[np.r_[True, acc[bad_idx[1:]] != acc[bad_idx[:-1]]]]
            # Declining a debit cancels every leg of the same event.
            posted &= ~np.isin(a["event"], a["event"][first])
        else:
            raise RuntimeError("decline resolution did not converge")

        running = self._running(acc, amount * posted, group_start)
        np.add.at(self.carry, acc, amount * posted)
        return pd.DataFrame(
            {
                "event_id": a["event"] + event_offset + 1,
                "account_id": a["account"],
                "person_id": a["person"],
                "posted_at": a["ts"],
                "amount_pesewas": amount,
                "txn_type": pd.Categorical.from_codes(a["txn"], TXN_TYPES),
                "channel": pd.Categorical.from_codes(a["channel"], CHANNELS),
                "status": pd.Categorical(
                    np.where(posted, "POSTED", "DECLINED"), ["POSTED", "DECLINED"]
                ),
                "merchant_id": _nullable(a["merchant"], a["merchant"] > 0),
                "counterparty": pd.array(a["counterparty"], dtype="string"),
                "counterparty_account": pd.array(a["counterparty_account"], dtype="string"),
                "device_id": pd.array(a["device"], dtype="string"),
                "balance_after_pesewas": _nullable(running, posted),
                "fraud_type": pd.Categorical.from_codes(
                    a["fraud"].astype("int64") - 1, FRAUD_TYPES
                ),
                "fraud_scenario_id": _nullable(a["scenario"], a["scenario"] > 0),
            }
        )

    def _running(self, acc, amount, group_start) -> np.ndarray:
        """Balance after each posting: carried balance plus the account's cumulative sum."""
        csum = np.cumsum(amount)
        group_offset = csum[group_start] - amount[group_start]
        return self.carry[acc] + csum - group_offset

    # ------------------------------------------------------------------ helpers

    def _lo_day(self, idx: np.ndarray, d0) -> np.ndarray:
        """First day offset in the month on which each customer can transact."""
        first = np.maximum(np.maximum(self.joined[idx], self.op_from[idx]), d0)
        return (first - d0).astype("int64")

    def _times(self, d0, n_days: int, lo: np.ndarray, hi: np.ndarray, branch: bool) -> np.ndarray:
        day = lo + (self.rng.random(lo.size) * (hi - lo)).astype("int64")
        ts = _at(d0 + day.astype("timedelta64[D]"), self._hours(lo.size, np.full(lo.size, branch)))
        return self._skip_sunday(ts, d0, n_days, lo) if branch else ts

    def _hours(self, n: int, branch: np.ndarray) -> np.ndarray:
        b = self.b
        weights = np.array(b.hour_weights) / sum(b.hour_weights)
        hour = np.where(
            branch,
            self.rng.integers(b.branch_open_hour, b.branch_close_hour, size=n),
            self.rng.choice(24, size=n, p=weights),
        )
        return hour * 3600 + self.rng.integers(0, 3600, size=n)

    def _seconds(self, n: int, hour_lo: int, hour_hi: int) -> np.ndarray:
        return self.rng.integers(hour_lo * 3600, hour_hi * 3600, size=n).astype("timedelta64[s]")

    def _skip_sunday(self, ts: np.ndarray, d0, n_days: int, lo: np.ndarray) -> np.ndarray:
        """Branches are closed on Sundays: move the event to Saturday, or Monday.

        Saturday is skipped when it falls before the customer's first eligible day (`lo`,
        a day offset within the month).
        """
        day = ts.astype("datetime64[D]")
        offset = (day - d0).astype("int64")
        sunday = (day.astype("int64") + 4) % 7 == 0
        back = sunday & (offset > lo)
        fwd = sunday & ~back & (offset < n_days - 1)
        return np.where(back, ts - _DAY, np.where(fwd, ts + _DAY, ts))

    def _merchants(self, who: np.ndarray, online: bool) -> np.ndarray:
        rng = self.rng
        if online:
            return rng.choice(self.online, size=who.size)
        out = rng.choice(self.pos_all, size=who.size)
        local = rng.random(who.size) < self.config.merchants.local_share
        for city in np.unique(self.city[who]):
            pool = self.pos_by_city.get(city)
            sel = np.flatnonzero(local & (self.city[who] == city))
            if pool is not None and pool.size and sel.size:
                out[sel] = rng.choice(pool, size=sel.size)
        return out

    def _counterparty_account(self, kind: str, who: np.ndarray) -> np.ndarray | None:
        """Account numbers on the other side of interbank transfers."""
        if kind == "TRANSFER_IN":
            return _account_numbers(self.rng, who.size)
        if kind != "TRANSFER_OUT":
            return None
        out = np.full(who.size, None, dtype=object)
        count = self.payee_count[who]
        regular = (count > 0) & (self.rng.random(who.size) >= self.sig.new_beneficiary_share)
        pick = self.payee_start[who] + (self.rng.random(who.size) * np.maximum(count, 1)).astype(
            int
        )
        out[regular] = self.payee[pick[regular]]
        out[~regular] = _account_numbers(self.rng, int((~regular).sum()))
        return out

    def _payee_bank_for(self, who: np.ndarray, accounts: np.ndarray) -> np.ndarray:
        """Bank of a regular payee (one-off payees keep the randomly drawn bank)."""
        return np.array([self.payee_bank_of.get(a) for a in accounts], dtype=object)

    def _devices(self, who: np.ndarray) -> np.ndarray:
        """A device the customer uses for mobile banking (None if they have none)."""
        count = self.device_count[who]
        pick = self.device_start[who] + (self.rng.random(who.size) * np.maximum(count, 1)).astype(
            int
        )
        out = np.full(who.size, None, dtype=object)
        has = count > 0
        out[has] = self.device[pick[has]]
        return out

    def _counterparty(self, kind: str, who: np.ndarray) -> np.ndarray | None:
        rng = self.rng
        if kind in {"MOMO_TO_BANK", "BANK_TO_MOMO"}:
            return self.wallet[who]
        if kind in {"TRANSFER_IN", "TRANSFER_OUT"}:
            return rng.choice(np.array(self.b.counterparty_banks, dtype=object), size=who.size)
        if kind == "BILL_PAYMENT":
            billers = self.b.billers
            return rng.choice(
                np.array(list(billers), dtype=object), size=who.size, p=list(billers.values())
            )
        return None


# ---------------------------------------------------------------------- pure helpers


def _loan_schedule(loans: pd.DataFrame) -> dict[str, np.ndarray]:
    """Fixed-instalment (annuity) schedule parameters for each loan."""
    principal = loans["contract_amount_pesewas"].to_numpy(dtype=float)
    r = loans["annual_rate_bps"].to_numpy(dtype=float) / 10_000 / 12
    term = loans["term_months"].to_numpy(dtype="int64")
    emi = principal * r / (1 - (1 + r) ** -term)
    return {
        "account": loans["account_id"].to_numpy(),
        "person": loans["person_id"].to_numpy(),
        "link": loans["linked_account_id"].to_numpy(dtype="int64"),
        "opened": loans["opened_on"].to_numpy().astype("datetime64[D]"),
        "principal": principal,
        "r": r,
        "term": term,
        "emi": emi,
    }


def _outstanding(principal, r, emi, k) -> np.ndarray:
    """Scheduled outstanding principal after k instalments of an annuity loan."""
    growth = (1 + r) ** k
    return np.maximum(principal * growth - emi * (growth - 1) / r, 0)


def _months_between(start: np.ndarray, end) -> np.ndarray:
    """Whole calendar months from start's month to end's month (may be negative)."""
    return (np.asarray(end).astype("datetime64[M]") - start.astype("datetime64[M]")).astype("int64")


def _split(rng: np.random.Generator, totals: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Split each total into `counts` random positive parts that sum to it."""
    weights = rng.gamma(1.0, size=int(counts.sum()))
    owner = np.repeat(np.arange(len(counts)), counts)
    sums = np.bincount(owner, weights=weights, minlength=len(counts))
    return totals[owner] * weights / sums[owner]


def _choose(rng: np.random.Generator, mix: dict[str, float], n: int) -> np.ndarray:
    return rng.choice(np.array(list(mix), dtype=object), size=n, p=list(mix.values()))


def _enabled(since: np.ndarray, d0, n_days: int) -> np.ndarray:
    return since <= d0 + np.timedelta64(n_days - 1, "D")


def _offsets(person_ids: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Start index and row count per person for a frame sorted by person_id (1..n)."""
    if person_ids.size and np.any(np.diff(person_ids) < 0):
        raise ValueError("expected rows sorted by person_id")
    count = np.bincount(person_ids - 1, minlength=n) if person_ids.size else np.zeros(n, int)
    start = np.r_[0, np.cumsum(count)[:-1]]
    return start, count


def _nullable(values: np.ndarray, valid: np.ndarray) -> pd.Series:
    """Int64 values where `valid`, <NA> elsewhere."""
    return pd.Series(np.where(valid, values, 0).astype("int64")).astype("Int64").where(valid)


def _at(date: np.ndarray, seconds: np.ndarray) -> np.ndarray:
    return date.astype("datetime64[s]") + seconds.astype("timedelta64[s]")
