"""Read-only check of Bronze tables in OneLake against the local landing zone.

Usage: uv run python scripts/verify_bronze.py <workspace_id> <lakehouse_id> [landing_dir]

Expected counts come from the local files (one row per CSV data row, raw line, parquet row,
or non-empty workbook cell); actual counts come from the Delta tables, read with delta-rs
under the signed-in Azure CLI identity. Nothing is written.
"""

import sys
from fnmatch import fnmatch
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from azure.identity import AzureCliCredential
from deltalake import DeltaTable, QueryBuilder

from isbank.bronze.files import workbook_cells
from isbank.bronze.sources import enabled_sources


def expected(landing: Path) -> pd.DataFrame:
    """Expected files and rows per Bronze table, counted from the local landing files."""
    rows = []
    files = [p for p in landing.rglob("*") if p.is_file()]
    for spec in enabled_sources():
        matched = [p for p in files if fnmatch(p.relative_to(landing).as_posix(), spec.pattern)]
        if spec.fmt == "csv":
            n = sum(len(pd.read_csv(p, dtype=str)) for p in matched)
        elif spec.fmt == "text":
            n = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in matched)
        elif spec.fmt == "parquet":
            n = sum(pq.ParquetFile(p).metadata.num_rows for p in matched)
        else:
            n = sum(len(workbook_cells(p.read_bytes())) for p in matched)
        rows.append({"table": spec.table, "expected_files": len(matched), "expected_rows": n})
    return pd.DataFrame(rows)


def query(sql: str, tables: dict[str, str], opts: dict) -> pd.DataFrame:
    """Run SQL over Delta tables with delta-rs's DataFusion engine.

    Fabric writes tables with deletion vectors, which delta-rs's PyArrow reader rejects but
    its SQL engine reads correctly.
    """
    builder = QueryBuilder()
    for name, uri in tables.items():
        builder.register(name, DeltaTable(uri, storage_options=opts))
    return pa.table(builder.execute(sql).read_all()).to_pandas()


def actual(workspace: str, lakehouse: str, tables: list[str]) -> pd.DataFrame:
    """Rows, distinct files, and logged files per Bronze table, read from OneLake."""
    token = AzureCliCredential().get_token("https://storage.azure.com/.default").token
    opts = {"bearer_token": token, "use_fabric_endpoint": "true"}
    base = f"abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{lakehouse}/Tables/dbo"
    log = query(
        "select table_name, count(*) as logged_files from log group by table_name",
        {"log": f"{base}/bronze_file_log"},
        opts,
    ).set_index("table_name")["logged_files"]
    rows = []
    for table in tables:
        counts = query(
            "select count(*) as n, count(distinct _source_file) as f from t",
            {"t": f"{base}/{table}"},
            opts,
        ).iloc[0]
        rows.append(
            {
                "table": table,
                "actual_rows": int(counts["n"]),
                "distinct_files_in_table": int(counts["f"]),
                "logged_files": int(log.get(table, 0)),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    """Compare expected and actual counts; exit 1 on any mismatch."""
    workspace, lakehouse = sys.argv[1], sys.argv[2]
    landing = Path(sys.argv[3] if len(sys.argv) > 3 else "output/dev/landing")
    exp = expected(landing)
    act = actual(workspace, lakehouse, exp["table"].tolist())
    report = exp.merge(act, on="table")
    report["ok"] = (
        (report["expected_rows"] == report["actual_rows"])
        & (report["expected_files"] == report["logged_files"])
        & (report["logged_files"] == report["distinct_files_in_table"])
    )
    print(report.to_string(index=False))
    print("ALL OK" if report["ok"].all() else "MISMATCHES FOUND")
    return 0 if report["ok"].all() else 1


if __name__ == "__main__":
    sys.exit(main())
