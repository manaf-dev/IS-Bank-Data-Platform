"""Read-only: active data files and rows per Delta table in a lakehouse (from the Delta log).

Usage: uv run python scripts/table_files.py <workspace_id> <lakehouse_id> [schema]

Useful for spotting the small-file problem and confirming compaction worked. Nothing is
written.
"""

import sys

import pandas as pd
import pyarrow as pa
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient
from deltalake import DeltaTable


def main() -> int:
    """Print one row per table: active files, rows, and average rows per file."""
    workspace, lakehouse = sys.argv[1], sys.argv[2]
    schema = sys.argv[3] if len(sys.argv) > 3 else "dbo"
    cred = AzureCliCredential()
    opts = {
        "bearer_token": cred.get_token("https://storage.azure.com/.default").token,
        "use_fabric_endpoint": "true",
    }
    fs = DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=cred)
    folder = f"{lakehouse}/Tables/{schema}"
    tables = sorted(
        p.name.rsplit("/", 1)[-1]
        for p in fs.get_file_system_client(workspace).get_paths(path=folder, recursive=False)
        if p.is_directory
    )
    root = f"abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{folder}"
    rows = []
    for table in tables:
        actions = pa.table(
            DeltaTable(f"{root}/{table}", storage_options=opts).get_add_actions(flatten=True)
        ).to_pandas()
        n = int(actions["num_records"].sum()) if "num_records" in actions else -1
        rows.append({"table": table, "files": len(actions), "rows": n})
    report = pd.DataFrame(rows)
    report["rows_per_file"] = (report["rows"] / report["files"]).round(0)
    print(report.to_string(index=False))
    print(f"total files: {report['files'].sum()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
