"""Delta Lake implementation of `BronzeStore`, used in Fabric notebooks.

Data tables are written with `replaceWhere` on the batch's source files, so a re-run replaces
exactly those files' rows. The file log is an append-only Delta table.
"""

from isbank.bronze.ingest import relative_key

FILE_LOG_TABLE = "bronze_file_log"
FILE_LOG_SCHEMA = (
    "table_name string, source_system string, relative_path string, source_file string, "
    "file_size long, modified_ms long, batch_id string, ingested_at timestamp, row_count long"
)


class DeltaBronzeStore:
    """Bronze tables and the file log in the attached (default) lakehouse."""

    def __init__(self, spark, schema: str = "dbo") -> None:
        self.spark = spark
        self.schema = schema

    def _name(self, table: str) -> str:
        return f"{self.schema}.{table}"

    def ingested_keys(self, table: str) -> set[str]:
        """Relative paths already logged for `table` (empty before the first run)."""
        log = self._name(FILE_LOG_TABLE)
        if not self.spark.catalog.tableExists(log):
            return set()
        rows = (
            self.spark.table(log)
            .where(f"table_name = '{table}'")
            .select("relative_path")
            .distinct()
            .collect()
        )
        return {r.relative_path for r in rows}

    def write(self, df, table: str, source_files: list[str]) -> None:
        """Create the table on first write; afterwards replace these files' rows."""
        name = self._name(table)
        writer = df.write.format("delta").option("mergeSchema", "true")
        if not self.spark.catalog.tableExists(name):
            writer.mode("append").saveAsTable(name)
            return
        quoted = ", ".join("'" + f.replace("'", "''") + "'" for f in source_files)
        writer.mode("overwrite").option("replaceWhere", f"_source_file IN ({quoted})").saveAsTable(
            name
        )

    def log(self, rows: list[dict]) -> None:
        """Append rows to the file log."""
        if not rows:
            return
        df = self.spark.createDataFrame(rows, FILE_LOG_SCHEMA)
        df.write.format("delta").mode("append").saveAsTable(self._name(FILE_LOG_TABLE))


__all__ = ["DeltaBronzeStore", "FILE_LOG_TABLE", "relative_key"]
