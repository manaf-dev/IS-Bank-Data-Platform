"""Metadata-driven, idempotent Bronze ingestion with PySpark.

For each enabled source: list landing files, skip files already in the file log, read the
new ones as received, add ingestion metadata, and write them to the source's Bronze table.
Each write replaces rows *by source file*, so re-running after a failure (whatever the
batching) can never duplicate a file's rows. Bronze is otherwise append-only.

Storage I/O is injected (`BronzeStore`), so the orchestration is unit-tested locally with an
in-memory store while Fabric uses the Delta implementation in `isbank.bronze.delta_store`.
"""

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Protocol

from isbank.bronze.files import LandedFile, group_by_header, new_files, workbook_cells
from isbank.bronze.sources import SourceSpec

METADATA_COLUMNS = ("_source_system", "_ingested_at", "_batch_id", "_source_file")
LANDING_MARKER = "landing/"
DEFAULT_CHUNK = 400


class BronzeStore(Protocol):
    """Where Bronze data and the file log live."""

    def ingested_keys(self, table: str) -> set[str]:
        """Relative paths already ingested into `table`."""

    def write(self, df, table: str, source_files: list[str]) -> None:
        """Write `df` to `table`, replacing any existing rows from `source_files`."""

    def log(self, rows: list[dict]) -> None:
        """Append file-log rows."""


@dataclass(frozen=True)
class IngestResult:
    """What one source's ingestion did."""

    table: str
    files: int
    rows: int
    batches: int


def relative_key(path: str) -> str:
    """Path relative to the landing root, independent of URI scheme or mount point."""
    normalised = path.replace("\\", "/")
    index = normalised.rfind("/" + LANDING_MARKER)
    if index == -1:
        raise ValueError(f"{path!r} is not under a '{LANDING_MARKER}' folder")
    return normalised[index + 1 + len(LANDING_MARKER) :]


def list_landing(spark, root: str, pattern: str) -> list[LandedFile]:
    """Files under `root` matching `pattern`, via Hadoop globbing (works on OneLake and locally)."""
    jvm = spark._jvm
    conf = spark._jsparkSession.sessionState().newHadoopConf()
    glob = jvm.org.apache.hadoop.fs.Path(f"{root.rstrip('/')}/{pattern}")
    fs = glob.getFileSystem(conf)
    statuses = fs.globStatus(glob) or []
    return [
        LandedFile(
            path=s.getPath().toString(), size=s.getLen(), modified_ms=s.getModificationTime()
        )
        for s in statuses
        if s.isFile()
    ]


def read_files(spark, spec: SourceSpec, paths: list[str], first_line: Callable[[str], str]):
    """Read landing files as received, with `_source_file` set to each row's file path."""
    from pyspark.sql import functions as F

    if spec.fmt == "parquet":
        df = spark.read.parquet(*paths)
        return df.withColumn("_source_file", F.col("_metadata.file_path"))
    if spec.fmt == "text":
        df = spark.read.text(paths).withColumnRenamed("value", "raw")
        return df.withColumn("_source_file", F.col("_metadata.file_path"))
    if spec.fmt == "csv":
        frames = []
        for group in group_by_header(paths, first_line).values():
            frame = (
                spark.read.option("header", True)
                .option("inferSchema", False)
                .option("escape", '"')
                .csv(group)
            )
            frames.append(frame.withColumn("_source_file", F.col("_metadata.file_path")))
        df = frames[0]
        for frame in frames[1:]:
            df = df.unionByName(frame, allowMissingColumns=True)
        return df
    if spec.fmt == "xlsx":
        import pandas as pd

        files = spark.read.format("binaryFile").load(paths).select("path", "content").collect()
        cells = pd.DataFrame(
            [
                (row.path, sheet, r, c, value)
                for row in files
                for sheet, r, c, value in workbook_cells(bytes(row.content))
            ],
            columns=["_source_file", "sheet", "row_number", "column_number", "value"],
        ).astype({"row_number": "int32", "column_number": "int32"})
        # From pandas (Arrow), not a list of tuples, so no Python worker processes are needed.
        return spark.createDataFrame(cells)
    raise ValueError(f"unsupported format {spec.fmt!r}")


def with_metadata(df, system: str, batch: str, ingested_at: dt.datetime):
    """Add the standard Bronze ingestion metadata columns (moving `_source_file` last)."""
    from pyspark.sql import functions as F

    data_columns = [c for c in df.columns if c not in METADATA_COLUMNS]
    return df.select(
        *data_columns,
        F.lit(system).alias("_source_system"),
        F.lit(ingested_at).cast("timestamp").alias("_ingested_at"),
        F.lit(batch).alias("_batch_id"),
        F.col("_source_file"),
    )


def ingest_source(
    spark,
    spec: SourceSpec,
    root: str,
    store: BronzeStore,
    first_line: Callable[[str], str],
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    chunk: int = DEFAULT_CHUNK,
    files: Iterable[LandedFile] | None = None,
) -> IngestResult:
    """Ingest every not-yet-ingested landing file of one source."""
    from isbank.bronze.files import batch_id

    available = list(files) if files is not None else list_landing(spark, root, spec.pattern)
    available = [f for f in available if fnmatch(relative_key(f.path), spec.pattern)]
    pending = new_files(available, store.ingested_keys(spec.table), key=relative_key)
    total_rows = 0
    batches = 0
    for start in range(0, len(pending), chunk):
        part = pending[start : start + chunk]
        batch = batch_id(spec.table, part)
        ingested_at = now()
        df = with_metadata(
            read_files(spark, spec, [f.path for f in part], first_line),
            spec.system,
            batch,
            ingested_at,
        )
        df = df.cache()
        counts = {
            r["_source_file"]: r["n"]
            for r in df.groupBy("_source_file").count().withColumnRenamed("count", "n").collect()
        }
        store.write(df, spec.table, list(counts))
        df.unpersist()
        by_key = {relative_key(path): n for path, n in counts.items()}
        store.log(
            [
                {
                    "table_name": spec.table,
                    "source_system": spec.system,
                    "relative_path": relative_key(f.path),
                    "source_file": f.path,
                    "file_size": f.size,
                    "modified_ms": f.modified_ms,
                    "batch_id": batch,
                    "ingested_at": ingested_at,
                    "row_count": by_key.get(relative_key(f.path), 0),
                }
                for f in part
            ]
        )
        total_rows += sum(counts.values())
        batches += 1
    return IngestResult(spec.table, len(pending), total_rows, batches)
