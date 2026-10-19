"""General ledger / ERP: monthly P&L by branch cost centre, delivered as human-formatted Excel.

Income lines are derived from the core ledger (loan interest, fees, fixed-deposit interest
paid), and operating costs are synthetic per branch. Finance occasionally books a fee
income figure that differs slightly from the core: a deliberate reconciliation break for
the DQ framework to find. The answer key records every planted break.
"""

import datetime as dt

import pandas as pd

from generator.projectors.common import SourceBatch, stable_int, stable_uniform
from generator.world import World

SYSTEM = "general_ledger"
_LINES = [
    ("4000", "Interest income - loans"),
    ("4100", "Fee and commission income"),
    ("4200", "Treasury income on deposits"),
    ("5000", "Interest expense - fixed deposits"),
    ("6000", "Staff costs"),
    ("6100", "Rent and utilities"),
    ("6900", "Other operating expenses"),
]


def monthly_statements(world: World, since: dt.date | None, until: dt.date) -> SourceBatch:
    """P&L rows for every month that closed in (since, until]; one workbook per month."""
    frame, breaks = pnl(world)
    lo = pd.Timestamp.min if since is None else pd.Timestamp(since)
    hi = pd.Timestamp(until)
    month_end = frame["period_end"]
    frame = frame[(month_end > lo) & (month_end <= hi)]
    batch = SourceBatch(SYSTEM)
    batch.add(
        "gl_pnl", frame, "xlsx", partition_by="period", sheet_title="IS BANK - Management P&L"
    )
    return batch


def pnl(world: World) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (P&L rows by period, branch, and GL line; planted reconciliation breaks)."""
    seed = world.config.seed
    p = world.config.projectors[SYSTEM]
    acc = world.holdings.accounts.set_index("account_id")
    posted = world.ledger.postings
    posted = posted[posted["status"] == "POSTED"]
    posted = posted.assign(
        branch_code=posted["account_id"].map(acc["branch_code"]),
        period=posted["posted_at"].dt.to_period("M"),
    )
    periods = pd.period_range(world.config.start_date, world.config.end_date, freq="M")

    interest = _loan_interest(posted, acc)
    fees = (
        -posted[posted["txn_type"] == "MONTHLY_FEE"]
        .groupby(["period", "branch_code"])["amount_pesewas"]
        .sum()
    )
    fd = posted[posted["txn_type"] == "FD_MATURITY"]
    fd_interest = fd.groupby(["period", "branch_code"])["amount_pesewas"].sum()
    treasury = _treasury_income(world, posted, acc, periods, p["treasury"])
    customers = _active_customers(world, periods)

    rows, breaks = [], []
    for period in periods:
        years = (period.start_time - pd.Timestamp(world.config.start_date)).days / 365.25
        inflation = (1 + p["opex_annual_growth"]) ** years
        for b in world.config.branches:
            key = period.year * 10_000 + period.month * 100 + int(b.branch_code[2:])
            active = customers.get((period, b.branch_code), 0) / 1000
            opex = {}
            for name, base in p["opex_per_1000_customers_ghs"].items():
                u = stable_uniform(seed, f"gl:opex:{name}", [key])[0]
                opex[name] = round(
                    base * active * inflation * (1 + p["opex_noise_sd"] * (2 * u - 1)), 2
                )
            fee = fees.get((period, b.branch_code), 0) / 100
            if stable_uniform(seed, "gl:break", [key])[0] < p["reconciliation_break_share"]:
                amount = stable_int(
                    seed,
                    "gl:break:amt",
                    [key],
                    p["break_amount_ghs"]["min"],
                    p["break_amount_ghs"]["max"],
                )[0]
                sign = 1 if stable_uniform(seed, "gl:break:sign", [key])[0] < 0.5 else -1
                breaks.append(
                    {
                        "period": str(period),
                        "branch_code": b.branch_code,
                        "gl_line": "4100",
                        "difference_ghs": float(sign * amount),
                    }
                )
                fee += sign * amount
            values = {
                "4000": interest.get((period, b.branch_code), 0) / 100,
                "4100": fee,
                "4200": treasury.get((period, b.branch_code), 0) / 100,
                "5000": -fd_interest.get((period, b.branch_code), 0) / 100,
                "6000": -opex["staff"],
                "6100": -opex["rent_utilities"],
                "6900": -opex["other"],
            }
            for code, label in _LINES:
                rows.append(
                    {
                        "period": str(period),
                        "period_end": period.end_time.normalize(),
                        "cost_centre": f"CC-{b.branch_code[2:]}",
                        "branch_name": b.name,
                        "gl_account": code,
                        "gl_description": label,
                        "amount_ghs": round(float(values[code]), 2),
                    }
                )
    breaks_frame = pd.DataFrame(
        breaks, columns=["period", "branch_code", "gl_line", "difference_ghs"]
    )
    return pd.DataFrame(rows), breaks_frame


def _loan_interest(posted: pd.DataFrame, acc: pd.DataFrame) -> pd.Series:
    """Interest = instalment debited minus principal credited to the loan account."""
    repay = posted[posted["txn_type"] == "LOAN_REPAYMENT"]
    loan_ids = acc.index[acc["family"] == "loan"]
    debit = (
        repay[repay["amount_pesewas"] < 0]
        .groupby("event_id")
        .agg(
            paid=("amount_pesewas", "sum"),
            branch=("branch_code", "first"),
            period=("period", "first"),
        )
    )
    principal = (
        repay[repay["account_id"].isin(loan_ids)].groupby("event_id")["amount_pesewas"].sum()
    )
    debit["interest"] = -debit["paid"] - principal.reindex(debit.index).fillna(0)
    return debit.groupby(["period", "branch"])["interest"].sum()


def _treasury_income(world: World, posted, acc, periods, params) -> pd.Series:
    """Yield on the invested share of month-end deposit balances, by branch."""
    deposits = acc.index[(acc["family"] == "deposit") & (acc["product_code"] != "FD_TERM")]
    dep = posted[posted["account_id"].isin(deposits)]
    month_end = (
        dep.groupby(["account_id", "period"])["balance_after_pesewas"].last().unstack("period")
    )
    month_end = month_end.reindex(index=deposits, columns=periods).astype("float64")
    opening = world.ledger.opening_balances.set_index("account_id")["balance_pesewas"]
    month_end = month_end.T.ffill().T
    month_end = month_end.apply(lambda col: col.fillna(opening.reindex(month_end.index)))
    by_branch = month_end.groupby(acc.loc[month_end.index, "branch_code"]).sum()
    monthly_yield = params["invested_share"] * params["annual_yield"] / 12
    return (by_branch * monthly_yield).stack().swaplevel().rename("treasury")


def _active_customers(world: World, periods) -> pd.Series:
    """Customers on each branch's books at each month end (joined, not yet churned)."""
    c = world.customers[["person_id", "home_branch_code", "joined_on"]]
    churn = world.scenarios.churn.set_index("person_id")["churn_date"]
    left = c["person_id"].map(churn).fillna(pd.Timestamp("2262-01-01"))
    counts = {}
    for period in periods:
        end = period.end_time
        active = c[(c["joined_on"] <= end) & (left.to_numpy() > end)]
        for branch, n in active["home_branch_code"].value_counts().items():
            counts[(period, branch)] = n
    return pd.Series(counts)
