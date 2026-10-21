"""Read-only check of the Gold star schema in wh_gold, plus offline answer-key checks.

Usage: uv run python scripts/verify_gold.py <workspace_id> <warehouse_id> [answer_key_dir]

Warehouse tables are Delta tables in OneLake, so they are read the same way as lakehouse
tables. The answer key is read locally for evaluation only.
"""

import sys
from pathlib import Path

import pandas as pd
from verify_silver import connect

from isbank.gold.model import UNKNOWN_KEY

DIMENSIONS = {
    "dim_date": "date_key",
    "dim_branch": "branch_key",
    "dim_product": "product_key",
    "dim_channel": "channel_key",
    "dim_customer": "customer_key",
    "dim_account": "account_key",
    "dim_merchant": "merchant_key",
}
FOREIGN_KEYS = [
    ("fact_transaction", "account_key", "dim_account"),
    ("fact_transaction", "customer_key", "dim_customer"),
    ("fact_transaction", "branch_key", "dim_branch"),
    ("fact_transaction", "channel_key", "dim_channel"),
    ("fact_transaction", "date_key", "dim_date"),
    ("fact_account_balance_monthly", "account_key", "dim_account"),
    ("fact_loan", "account_key", "dim_account"),
    ("fact_loan_payment", "account_key", "dim_account"),
    ("fact_loan_payment", "branch_key", "dim_branch"),
    ("fact_loan_payment", "customer_key", "dim_customer"),
    ("sec_user_branch", "branch_key", "dim_branch"),
    ("fact_card_transaction", "merchant_key", "dim_merchant"),
    ("fact_card_transaction", "date_key", "dim_date"),
    ("fact_digital_event", "customer_key", "dim_customer"),
    ("fact_gl_entry", "branch_key", "dim_branch"),
    ("dim_account", "customer_key", "dim_customer"),
]
FACTS = [
    "fact_transaction",
    "fact_account_balance_monthly",
    "fact_loan",
    "fact_loan_payment",
    "fact_card_transaction",
    "fact_digital_event",
    "fact_gl_entry",
    "recon_gl_fees",
    "app_user_link",
    "sec_user_branch",
]


def main() -> int:
    """Print counts, key checks, integrity checks, and the GL reconciliation result."""
    workspace, warehouse = sys.argv[1], sys.argv[2]
    key_dir = Path(sys.argv[3] if len(sys.argv) > 3 else "answer_key/dev")
    read = connect(workspace, warehouse)
    ok = True

    keys = {}
    rows = []
    for dim, column in DIMENSIONS.items():
        data = read(dim, [column])
        keys[dim] = set(data[column])
        rows.append({"table": dim, "rows": len(data), "unique_key": data[column].is_unique})
    for fact in FACTS:
        rows.append({"table": fact, "rows": len(read(fact)), "unique_key": None})
    report = pd.DataFrame(rows)
    print(report.to_string(index=False))
    ok &= bool((report["rows"] > 0).all() and report["unique_key"].dropna().all())

    print("\nreferential integrity (orphans, excluding the explicit unknown member):")
    for fact, column, dim in FOREIGN_KEYS:
        values = set(read(fact, [column])[column]) - {UNKNOWN_KEY}
        orphans = len(values - keys[dim])
        print(f"  {fact}.{column} -> {dim}: {orphans} orphans")
        ok &= orphans == 0

    recon = read("recon_gl_fees")
    found = {
        (str(m), b)
        for m, b in zip(
            recon.loc[recon["is_break"], "month_key"],
            recon.loc[recon["is_break"], "branch_code"],
            strict=True,
        )
    }
    planted = pd.read_parquet(key_dir / "dq_gl_breaks.parquet")
    planted = planted[planted["period"] <= "2025-12"]
    expected = {
        (p.replace("-", ""), b)
        for p, b in zip(planted["period"], planted["branch_code"], strict=True)
    }
    matching = len(found & expected)
    print(f"\nGL fee breaks: planted {len(expected)}, found {len(found)}, matching {matching}")
    ok &= found == expected

    print("\nALL OK" if ok else "\nCHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
