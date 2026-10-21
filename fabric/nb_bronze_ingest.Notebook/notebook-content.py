# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "ddc6a9ea-98f6-47d9-b7b8-120882abf8d5",
# META       "default_lakehouse_name": "lh_bronze",
# META       "default_lakehouse_workspace_id": "fd3581c9-57eb-407a-9386-99a29953f818",
# META       "known_lakehouses": [
# META         {
# META           "id": "ddc6a9ea-98f6-47d9-b7b8-120882abf8d5"
# META         }
# META       ]
# META     }
# META   }
# META }

# MARKDOWN ********************

# # nb_bronze_ingest
# # Loads new landing files from `lh_bronze/Files/landing` into Bronze Delta tables (`dbo.*`), exactly as received, with ingestion metadata (`_source_system`, `_ingested_at`, `_batch_id`, `_source_file`).
# # - **Metadata-driven:** the source list lives in `isbank.bronze.sources`; this notebook only orchestrates.
# - **Idempotent:** a file log (`dbo.bronze_file_log`) skips files already loaded, and each write replaces rows by source file, so a re-run never duplicates.
# - **Audited:** every run writes one row per source to `dbo.run_audit`.
# # All logic is in the `isbank` package (unit-tested locally); the wheel is read from `Files/libs`.

# PARAMETERS CELL ********************

workspace_id = "fd3581c9-57eb-407a-9386-99a29953f818"
lakehouse_id = "ddc6a9ea-98f6-47d9-b7b8-120882abf8d5"
wheel_name = "isbank-0.3.0-py3-none-any.whl"
systems = ""  # comma-separated subset of source systems; empty = all enabled sources

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import datetime as dt
import json
import sys
import uuid

# A pure-Python wheel can be imported straight from the file (zipimport): no environment publish.
wheel_path = f"/lakehouse/default/Files/libs/{wheel_name}"
if wheel_path not in sys.path:
    sys.path.insert(0, wheel_path)
spark.sparkContext.addPyFile(wheel_path)

from isbank.audit import AUDIT_SCHEMA, AUDIT_TABLE, StepOutcome, audit_rows
from isbank.bronze.delta_store import DeltaBronzeStore
from isbank.bronze.ingest import ingest_source, relative_key
from isbank.bronze.sources import enabled_sources

root = f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Files/landing"
mount = "/lakehouse/default/Files/landing/"


def first_line(path: str) -> str:
    """Header line of a landing file, read through the default lakehouse mount."""
    with open(mount + relative_key(path), encoding="utf-8") as f:
        return f.readline()


run_id = str(uuid.uuid4())
store = DeltaBronzeStore(spark, schema="dbo")
selected = enabled_sources(set(systems.split(",")) if systems else None)
print(f"run {run_id}: {len(selected)} sources")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

outcomes = []
for spec in selected:
    started = dt.datetime.now(dt.UTC)
    try:
        result = ingest_source(spark, spec, root, store, first_line)
        outcomes.append(StepOutcome(spec.table, result.files, result.rows, started, dt.datetime.now(dt.UTC)))
        print(f"{spec.table:<24} files={result.files:>5} rows={result.rows:>9} batches={result.batches}")
    except Exception as exc:  # record the failure, keep going, fail the run at the end
        outcomes.append(StepOutcome(spec.table, 0, 0, started, dt.datetime.now(dt.UTC), "FAILED", repr(exc)))
        print(f"{spec.table:<24} FAILED: {exc!r}")

spark.createDataFrame(audit_rows(run_id, "nb_bronze_ingest", "bronze", outcomes), AUDIT_SCHEMA) \
    .write.format("delta").mode("append").saveAsTable(f"dbo.{AUDIT_TABLE}")

failed = [o.table for o in outcomes if o.status != "SUCCEEDED"]
summary = {"run_id": run_id, "failed": failed, "files": sum(o.files for o in outcomes), "rows": sum(o.rows for o in outcomes)}
print(json.dumps(summary))
if failed:
    raise RuntimeError(f"Bronze ingestion failed for: {failed}")
notebookutils.notebook.exit(json.dumps(summary))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
