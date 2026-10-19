"""Write source batches to a local landing zone that mirrors `lh_bronze/Files`.

Layout (one folder per delivery day, like an SFTP drop or a landing container):

    <root>/<system>/<YYYY>/<MM>/<DD>/<file>
    <root>/core_banking/<load>/<table>.parquet      (relational extracts for the SQL load)

Files are named by their source and partition (e.g. `los_applications_20240626.csv`), so
re-emitting a day overwrites the same files with identical content: emitting is
idempotent. A file the source "delivers twice" is written again in the next day's folder
under the same name, which is exactly what the Bronze loader must de-duplicate.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from generator.projectors.common import SourceBatch

# Columns that only exist to route rows into files; they are not part of the source data.
_ROUTING_COLUMNS = {"extract_date", "export_date", "delivered_at", "period_end"}
_EXTENSION = {
    "csv": "csv",
    "jsonl": "jsonl",
    "xlsx": "xlsx",
    "fixed_width": "txt",
    "sql": "parquet",
}


@dataclass
class LandingWriter:
    """Writes batches under `root`.

    `redirect` sends whole systems elsewhere, e.g. CRM to a Google Drive for desktop folder.
    """

    root: Path
    redirect: dict[str, Path] = field(default_factory=dict)

    def write(self, batch: SourceBatch, load_label: str) -> list[Path]:
        """Write every table in `batch`; `load_label` names relational loads (full_20251231)."""
        base = self.redirect.get(batch.system, self.root / batch.system)
        written: list[Path] = []
        for name, frame in batch.tables.items():
            fmt, meta = batch.formats[name], batch.meta.get(name, {})
            if fmt == "sql":
                path = base / load_label / f"{name}.parquet"
                written.append(_write(frame, path, fmt, meta))
                continue
            column = meta["partition_by"]
            if frame.empty:
                continue
            keys = _partition_keys(frame[column])
            stem = meta.get("file_stem", name)
            resend = set(meta.get("resend_partitions", []))
            for key, rows in frame.groupby(keys, sort=True):
                folder, tag = _folder_and_tag(key)
                data = rows.drop(columns=[c for c in _ROUTING_COLUMNS if c in rows.columns])
                file_name = f"{stem}_{tag}.{_EXTENSION[fmt]}"
                written.append(_write(data, base / folder / file_name, fmt, meta))
                if key in resend:
                    next_day = (pd.Timestamp(key) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    again, _ = _folder_and_tag(next_day)
                    written.append(_write(data, base / again / file_name, fmt, meta))
        return written


def _partition_keys(values: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(values):
        return values.dt.strftime("%Y-%m-%d")
    parsed = pd.to_datetime(values, format="%Y%m%d", errors="coerce")
    if parsed.notna().all():
        return parsed.dt.strftime("%Y-%m-%d")
    return values.astype(str)


def _folder_and_tag(key: str) -> tuple[Path, str]:
    """'2024-06-26' -> (2024/06/26, 20240626); a period '2024-12' -> (2024/12, 202412)."""
    parts = key.split("-")
    return Path(*parts), "".join(parts)


def _write(frame: pd.DataFrame, path: Path, fmt: str, meta: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "sql":
        frame.to_parquet(path, index=False)
    elif fmt == "csv":
        frame.to_csv(path, index=False, date_format="%Y-%m-%d %H:%M:%S")
    elif fmt == "jsonl":
        _write_jsonl(frame, path)
    elif fmt == "fixed_width":
        _write_fixed_width(frame, path, meta["layout"])
    elif fmt == "xlsx":
        _write_pnl_workbook(frame, path, meta.get("sheet_title", ""))
    else:
        raise ValueError(f"unknown format {fmt!r}")
    return path


def _write_jsonl(frame: pd.DataFrame, path: Path) -> None:
    """One JSON object per line; null fields are omitted (so schema drift shows as new keys)."""
    with path.open("w", encoding="utf-8", newline="\n") as out:
        for record in frame.to_dict(orient="records"):
            clean = {k: _json_value(v) for k, v in record.items() if not _is_missing(v)}
            out.write(json.dumps(clean, ensure_ascii=False) + "\n")


def _is_missing(value) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    return isinstance(value, float) and math.isnan(value)


def _json_value(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    return value


def _write_fixed_width(frame: pd.DataFrame, path: Path, layout: list) -> None:
    """Header, fixed-width records, and a trailer with the record count."""
    lines = []
    for record in frame.itertuples(index=False):
        row = record._asdict()
        parts = []
        for column, width in layout:
            value = row[column]
            if isinstance(value, int | float) and not isinstance(value, bool):
                parts.append(str(int(value)).rjust(width, "0")[-width:])
            else:
                parts.append(("" if _is_missing(value) else str(value)).ljust(width)[:width])
        lines.append("".join(parts))
    report = str(frame["report_date"].iloc[0]) if len(frame) else ""
    with path.open("w", encoding="ascii", errors="replace", newline="\n") as out:
        out.write(f"HDR{report}ISBANK\n")
        out.writelines(line + "\n" for line in lines)
        out.write(f"TRL{len(lines):09d}\n")


def _write_pnl_workbook(frame: pd.DataFrame, path: Path, title: str) -> None:
    """A finance-style workbook: title block, lines x cost centres, totals, a footnote."""
    period = str(frame["period"].iloc[0])
    pivot = frame.pivot_table(
        index=["gl_account", "gl_description"],
        columns="cost_centre",
        values="amount_ghs",
        aggfunc="sum",
    ).sort_index()
    pivot["TOTAL"] = pivot.sum(axis=1)
    wb = Workbook()
    ws = wb.active
    ws.title = f"P&L {period}"
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Period: {pd.Period(period).strftime('%B %Y')}"
    ws["A3"] = "Amounts in GHS"
    header = ["GL account", "Description", *pivot.columns]
    ws.append([])
    ws.append(header)
    for cell in ws[5]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for (code, label), values in pivot.iterrows():
        ws.append([code, label, *[round(float(v), 2) for v in values]])
    ws.append(["", "Net profit / (loss)", *[round(float(v), 2) for v in pivot.sum(axis=0)]])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=6, min_col=3):
        for cell in row:
            cell.number_format = "#,##0.00;(#,##0.00)"
    ws.append([])
    ws.append(["Prepared by Finance. Unaudited management figures."])
    ws.column_dimensions["B"].width = 34
    wb.save(path)
