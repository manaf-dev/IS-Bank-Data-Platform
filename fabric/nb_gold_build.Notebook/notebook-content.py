# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "7f6cfe30-ccf8-4b52-b0b6-6606f8451ecc",
# META       "default_lakehouse_name": "lh_silver",
# META       "default_lakehouse_workspace_id": "fd3581c9-57eb-407a-9386-99a29953f818",
# META       "known_lakehouses": [
# META         {
# META           "id": "7f6cfe30-ccf8-4b52-b0b6-6606f8451ecc"
# META         },
# META         {
# META           "id": "ddc6a9ea-98f6-47d9-b7b8-120882abf8d5"
# META         }
# META       ]
# META     }
# META   }
# META }

# MARKDOWN ********************

# # nb_gold_build
# # Builds the Gold star schema from the Silver tables in `lh_silver` and writes it to the `wh_gold` Warehouse with the Spark connector for Fabric Data Warehouse.
# # - **Star schema:** conformed dimensions (`dim_*`) and facts (`fact_*`) with deterministic surrogate keys; no raw PII.
# - **Linkage:** card events linked by unique masked PAN; app users linked by matching app actions to core postings.
# - **Reconciliation:** `recon_gl_fees` compares GL fee income with the core ledger.
# - **Idempotent:** every table is overwritten on each run. One audit row per table in `lh_silver.dbo.run_audit`.

# PARAMETERS CELL ********************

workspace_id = "fd3581c9-57eb-407a-9386-99a29953f818"
bronze_lakehouse_id = "ddc6a9ea-98f6-47d9-b7b8-120882abf8d5"
wheel_name = "isbank-0.3.0-py3-none-any.whl"
warehouse = "wh_gold"
history_start = "2005-01-01"
history_end = "2026-12-31"
as_of = "2025-12-31"

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

import com.microsoft.spark.fabric  # registers df.write.synapsesql

onelake = f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com"
local_wheel = f"/tmp/{wheel_name}"
notebookutils.fs.cp(f"{onelake}/{bronze_lakehouse_id}/Files/libs/{wheel_name}", f"file:{local_wheel}")
if local_wheel not in sys.path:
    sys.path.insert(0, local_wheel)
spark.sparkContext.addPyFile(local_wheel)

from isbank.audit import AUDIT_SCHEMA, AUDIT_TABLE, StepOutcome, audit_rows, output_files
from isbank.gold import model

SILVER = [
    "branch", "product", "card", "customer_history", "account", "transaction",
    "card_authorisation", "app_event", "loan_application", "loan_repayment",
    "gl_pnl", "bureau_score", "party_xref",
]
silver = {name: spark.table(f"dbo.{name}") for name in SILVER}
run_id = str(uuid.uuid4())
print(f"run {run_id}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

tables = model.build(
    spark,
    silver,
    dt.date.fromisoformat(history_start),
    dt.date.fromisoformat(history_end),
    dt.date.fromisoformat(as_of),
)

order = sorted(tables, key=lambda name: (not name.startswith("dim_"), name))
outcomes = []
for name in order:
    started = dt.datetime.now(dt.UTC)
    try:
        df = tables[name].cache()
        rows = df.count()
        df.coalesce(output_files(rows)).write.mode("overwrite").synapsesql(f"{warehouse}.dbo.{name}")
        outcomes.append(StepOutcome(name, 0, rows, started, dt.datetime.now(dt.UTC)))
        print(f"{name:<30} rows={rows:>9}")
    except Exception as exc:  # record and continue; fail the run at the end
        outcomes.append(StepOutcome(name, 0, 0, started, dt.datetime.now(dt.UTC), "FAILED", repr(exc)))
        print(f"{name:<30} FAILED: {exc!r}"[:500])

spark.createDataFrame(audit_rows(run_id, "nb_gold_build", "gold", outcomes), AUDIT_SCHEMA) \
    .coalesce(1).write.format("delta").mode("append").saveAsTable(f"dbo.{AUDIT_TABLE}")

failed = [o.table for o in outcomes if o.status != "SUCCEEDED"]
summary = {"run_id": run_id, "failed": failed, "tables": {o.table: o.rows for o in outcomes}}
print(json.dumps(summary, indent=1))
if failed:
    raise RuntimeError(f"Gold build failed for: {failed}")
notebookutils.notebook.exit(json.dumps(summary))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
