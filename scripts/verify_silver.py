"""Read-only check of the Silver tables in OneLake, plus offline evaluation against the answer key.

Usage: uv run python scripts/verify_silver.py <workspace_id> <silver_lakehouse_id> [answer_key_dir]

The answer key is read locally for evaluation only; the platform never reads it.
"""

import io
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient
from deltalake import DeltaTable

from isbank.silver.pipeline import TABLES

TODAY = "2025-12-31"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"


def connect(workspace: str, lakehouse: str):
    """Return a reader for the lakehouse's dbo tables as pandas DataFrames.

    The Delta log (via delta-rs) gives the active files; the Parquet files are downloaded in
    parallel and read locally. That is much faster than delta-rs scanning OneLake directly
    when a table has many small files. Tables with deletion vectors are refused, because
    reading their files directly would include deleted rows.
    """
    cred = AzureCliCredential()
    token = cred.get_token("https://storage.azure.com/.default").token
    opts = {"bearer_token": token, "use_fabric_endpoint": "true"}
    root = f"abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{lakehouse}/Tables/dbo"
    fs = DataLakeServiceClient(ONELAKE, credential=cred).get_file_system_client(workspace)

    def read(table: str, columns: list[str] | None = None) -> pd.DataFrame:
        actions = pa.table(
            DeltaTable(f"{root}/{table}", storage_options=opts).get_add_actions(flatten=True)
        ).to_pandas()
        dv = [c for c in actions.columns if "deletion" in c.lower()]
        if any(actions[c].notna().any() for c in dv):
            raise RuntimeError(f"{table} has deletion vectors; direct file reads would be wrong")

        def fetch(path: str) -> pa.Table:
            client = fs.get_file_client(f"{lakehouse}/Tables/dbo/{table}/{path}")
            return pq.read_table(io.BytesIO(client.download_file().readall()), columns=columns)

        with ThreadPoolExecutor(max_workers=32) as pool:
            parts = list(pool.map(fetch, actions["path"].tolist()))
        return pa.concat_tables(parts).to_pandas() if parts else pd.DataFrame(columns=columns)

    return read


def pairs(groups) -> set[tuple[str, str]]:
    """All unordered pairs within each group."""
    out = set()
    for members in groups:
        out |= set(combinations(sorted(members), 2))
    return out


def main() -> int:
    """Print table checks, the latest run, DQ results, and offline evaluation."""
    workspace, lakehouse = sys.argv[1], sys.argv[2]
    key_dir = Path(sys.argv[3] if len(sys.argv) > 3 else "answer_key/dev")
    read = connect(workspace, lakehouse)
    ok = True

    rows = []
    for t in (*TABLES, None):
        name = t.name if t else "party_xref"
        keys = list(t.keys) if t else ["source_system", "source_id"]
        data = read(name, keys)
        rows.append(
            {"table": name, "rows": len(data), "duplicate_keys": int(data.duplicated().sum())}
        )
    tables = pd.DataFrame(rows)
    print(tables.to_string(index=False))
    ok &= bool((tables["duplicate_keys"] == 0).all() and (tables["rows"] > 0).all())

    audit = read("run_audit")
    latest = audit[audit["run_id"] == audit.sort_values("started_at")["run_id"].iloc[-1]]
    print("latest run:", latest["status"].value_counts().to_dict())
    ok &= bool((latest["status"] == "SUCCEEDED").all())

    results = read("dq_results")
    last = results[results["run_id"] == results.sort_values("checked_at")["run_id"].iloc[-1]]
    dq = last[last["rows_failed"] > 0][
        ["table_name", "rule_name", "severity", "rows_checked", "rows_failed"]
    ]
    print("DQ rules with failures (latest run):")
    print(dq.sort_values(["table_name", "rule_name"]).to_string(index=False))

    # Offline evaluation against the answer key.
    double = pd.read_parquet(key_dir / "dq_double_postings.parquet")
    planted = int((double["posted_at"] < pd.Timestamp(TODAY) + pd.Timedelta(days=1)).sum())
    found = dq.loc[dq["rule_name"] == "not_double_posted", "rows_failed"].sum()
    print(f"\ndouble postings: planted (to {TODAY}) {planted}, quarantined {int(found)}")
    ok &= int(found) == planted

    people = pd.read_parquet(key_dir / "person_crosswalk.parquet")
    truth = {}
    for _, p in people.iterrows():
        truth[f"core|{p['cif_number']}"] = p["person_id"]
        if pd.notna(p["dup_cif_number"]):
            truth[f"core|{p['dup_cif_number']}"] = p["person_id"]
        truth[f"bureau|{p['bureau_ref']}"] = p["person_id"]
    by_card = people.set_index("ghana_card")["person_id"]
    apps = read("loan_application", ["application_ref", "ghana_card"])
    for _, a in apps.dropna().iterrows():
        truth[f"los|{a['application_ref']}"] = by_card.get(a["ghana_card"])
    xref = read("party_xref", ["source_system", "source_id", "party_id"])
    xref["node"] = xref["source_system"] + "|" + xref["source_id"]
    xref["person"] = xref["node"].map(truth)
    scored = xref.dropna(subset=["person"])
    predicted = pairs(scored.groupby("party_id")["node"].apply(list))
    actual = pairs(scored.groupby("person")["node"].apply(list))
    precision = len(predicted & actual) / max(len(predicted), 1)
    recall = len(predicted & actual) / max(len(actual), 1)
    print(f"entity resolution: precision {precision:.4f}, recall {recall:.4f}")
    print(f"  scored over {len(actual)} true record pairs")
    ok &= precision >= 0.99 and recall >= 0.95

    print("\nALL OK" if ok else "\nCHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
