"""Read-only DAX checks of sm_isbank against figures computed independently from wh_gold.

Usage (from scripts/):
  uv run python verify_semantic_model.py <workspace_id> <warehouse_id> <model_id> <granted_upn>

Runs DAX through the Power BI executeQueries API with the az CLI identity. The RLS check
evaluates the Branch Manager rule for <granted_upn>, which nb_gold_build must have granted BR001.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from verify_silver import connect

PBI = "https://api.powerbi.com/v1.0/myorg"
GOLD_TABLES = [
    "dim_customer",
    "dim_branch",
    "dim_product",
    "fact_transaction",
    "fact_loan",
    "fact_gl_entry",
    "fact_card_transaction",
    "fact_digital_event",
    "recon_gl_fees",
    "fact_account_balance_monthly",
]
# Measures without an independent Gold figure; printed for review only.
UNCHECKED = [
    "Customers at Risk",
    "At-risk Rate",
    "Card-testing Alerts",
    "Digital Adoption",
    "Collection Rate",
    "Biometric Login Share",
    "Deposit Growth 3M",
]


def dax(workspace: str, model: str, query: str) -> list[dict]:
    """Run one DAX query and return its rows; the body goes through a temp file for az."""
    body = {"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "body.json"
        path.write_text(json.dumps(body))
        r = subprocess.run(
            [
                "az",
                "rest",
                "--resource",
                "https://analysis.windows.net/powerbi/api",
                "--method",
                "post",
                "--url",
                f"{PBI}/groups/{workspace}/datasets/{model}/executeQueries",
                "--headers",
                "Content-Type=application/json",
                "--body",
                f"@{path}",
            ],
            capture_output=True,
            text=True,
            shell=True,
        )
    if r.returncode:
        raise RuntimeError(r.stderr[-1500:])
    result = json.loads(r.stdout)["results"][0]
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["tables"][0]["rows"]


def expected_figures(g: dict) -> dict[str, float]:
    """Reference values computed with pandas straight from the Gold tables."""
    loan = g["fact_loan"]
    principal = loan.principal.astype(float)
    open_book = loan.loan_status.isin(["PERFORMING", "ARREARS", "DEFAULT"])
    non_performing = loan.loan_status.isin(["ARREARS", "DEFAULT"])
    gl = g["fact_gl_entry"].assign(amount=lambda d: d.amount.astype(float))
    income = gl[gl.line_type == "INCOME"].amount.sum()
    expense = -gl[gl.line_type == "EXPENSE"].amount.sum()  # GL stores expenses as negatives
    bal = g["fact_account_balance_monthly"].merge(
        g["dim_product"][["product_key", "product_family"]], on="product_key"
    )
    deposits = bal[(bal.month_key == bal.month_key.max()) & (bal.product_family == "DEPOSIT")]
    return {
        "Customers": len(g["dim_customer"]),
        "Transactions": len(g["fact_transaction"]),
        "Transaction Value": g["fact_transaction"].amount.astype(float).sum(),
        "Loans": len(loan),
        "Loan Principal": principal.sum(),
        "NPL Ratio": principal[non_performing].sum() / principal[open_book].sum(),
        "Net Profit": income - expense,
        "Cost-to-Income Ratio": expense / income,
        "Card Transactions": len(g["fact_card_transaction"]),
        "Digital Events": len(g["fact_digital_event"]),
        "Reconciliation Breaks": int(g["recon_gl_fees"].is_break.sum()),
        "Deposits": deposits.balance.astype(float).sum(),
    }


def main() -> int:
    """Compare model measures with Gold, then check the RLS rule for the granted user."""
    workspace, warehouse, model, upn = sys.argv[1:5]
    upn = upn.lower()
    read = connect(workspace, warehouse)
    g = {t: read(t) for t in GOLD_TABLES}
    expected = expected_figures(g)
    measures = list(expected) + UNCHECKED
    query = "EVALUATE ROW(" + ", ".join(f'"{m}", [{m}]' for m in measures) + ")"
    got = {k.strip("[]"): v for k, v in dax(workspace, model, query)[0].items()}

    ok = True
    print(f"{'measure':<24}{'model':>20}{'gold':>20}")
    for m in measures:
        e = expected.get(m)
        match = e is None or abs((got[m] or 0) - e) <= max(1e-6, abs(e) * 1e-9) + 0.005
        ok &= match
        verdict = "" if e is None else ("OK" if match else "MISMATCH")
        gold = "" if e is None else round(e, 4)
        print(f"{m:<24}{got[m]!s:>20}{gold!s:>20}  {verdict}")

    # RLS: the role's rule, evaluated for the granted user, must select exactly BR001.
    granted = (
        "CALCULATETABLE ( VALUES ( sec_user_branch[branch_code] ), "
        f'sec_user_branch[user_upn] = "{upn}" )'
    )
    codes = sorted(
        r["sec_user_branch[branch_code]"] for r in dax(workspace, model, f"EVALUATE {granted}")
    )
    scoped_query = (
        f'EVALUATE ROW ( "txns", CALCULATE ( [Transactions], '
        f"TREATAS ( {granted}, dim_branch[branch_code] ) ) )"
    )
    scoped = next(iter(dax(workspace, model, scoped_query)[0].values()))
    branch_key = g["dim_branch"].set_index("branch_code").branch_key["BR001"]
    want = int((g["fact_transaction"].branch_key == branch_key).sum())
    print(f"\nRLS grant: {codes}; BR001 transactions model {scoped}, gold {want}")
    ok &= codes == ["BR001"] and scoped == want

    print("\nALL OK" if ok else "\nCHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
