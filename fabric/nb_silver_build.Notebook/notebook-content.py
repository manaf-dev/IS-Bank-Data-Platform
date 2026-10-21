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

# # nb_silver_build
# # Rebuilds every Silver table in `lh_silver` from the Bronze tables in `lh_bronze`: typed, standardised, deduplicated, quality-checked, with SCD2 customer history and a golden party id from entity resolution.
# # - **Idempotent:** each run rebuilds the Silver tables in full (overwrite).
# - **Data quality:** every rule's counts go to `dbo.dq_results`; failing rows go to `dbo.dq_quarantine` with the rule name, never silently dropped.
# - **Audited:** one row per table per run in `dbo.run_audit`.
# # All logic is in the `isbank` package (unit-tested locally); the wheel is read from `lh_bronze/Files/libs`.

# PARAMETERS CELL ********************

workspace_id = "fd3581c9-57eb-407a-9386-99a29953f818"
bronze_lakehouse_id = "ddc6a9ea-98f6-47d9-b7b8-120882abf8d5"
wheel_name = "isbank-0.2.1-py3-none-any.whl"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json
import sys
import uuid

onelake = f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com"
wheel_path = f"{onelake}/{bronze_lakehouse_id}/Files/libs/{wheel_name}"
local_wheel = f"/tmp/{wheel_name}"
notebookutils.fs.cp(wheel_path, f"file:{local_wheel}")
if local_wheel not in sys.path:
    sys.path.insert(0, local_wheel)
spark.sparkContext.addPyFile(local_wheel)

from isbank.audit import AUDIT_SCHEMA, AUDIT_TABLE, audit_rows
from isbank.dq import QUARANTINE_SCHEMA, RESULTS_SCHEMA
from isbank.silver import pipeline

bronze_tables = f"{onelake}/{bronze_lakehouse_id}/Tables/dbo"
run_id = str(uuid.uuid4())


def read_bronze(table: str):
    return spark.read.format("delta").load(f"{bronze_tables}/{table}")


def write_silver(table: str, df) -> None:
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"dbo.{table}")


def read_silver(table: str):
    return spark.table(f"dbo.{table}")


print(f"run {run_id}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

result = pipeline.run(spark, read_bronze, write_silver, run_id, read_silver=read_silver)

for o in result.outcomes:
    print(f"{o.table:<22} {o.status:<9} rows={o.rows:>9} {o.message[:200]}")

if result.dq_results:
    spark.createDataFrame(result.dq_results, RESULTS_SCHEMA).write.format("delta").mode("append").saveAsTable("dbo.dq_results")
quarantine = [q for q in result.quarantine]
if quarantine:
    combined = quarantine[0]
    for q in quarantine[1:]:
        combined = combined.unionByName(q)
    combined.write.format("delta").mode("append").saveAsTable("dbo.dq_quarantine")
spark.createDataFrame(audit_rows(run_id, "nb_silver_build", "silver", result.outcomes), AUDIT_SCHEMA) \
    .write.format("delta").mode("append").saveAsTable(f"dbo.{AUDIT_TABLE}")

failed = [o.table for o in result.outcomes if o.status != "SUCCEEDED"]
summary = {
    "run_id": run_id,
    "failed": failed,
    "tables": {o.table: o.rows for o in result.outcomes},
    "dq_failures": {f"{r['table_name']}.{r['rule_name']}": r["rows_failed"] for r in result.dq_results if r["rows_failed"]},
}
print(json.dumps(summary, indent=1))
if failed:
    raise RuntimeError(f"Silver build failed for: {failed}")
notebookutils.notebook.exit(json.dumps(summary))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
