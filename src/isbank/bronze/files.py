"""Pure helpers for Bronze ingestion: file selection, batch identity, and raw parsing.

Nothing here needs Spark, so the rules that make ingestion idempotent are unit-tested on
plain Python values.
"""

import hashlib
import io
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class LandedFile:
    """A file in the landing zone."""

    path: str
    size: int
    modified_ms: int


def new_files(
    available: Iterable[LandedFile],
    already_ingested: set[str],
    key: Callable[[str], str] = lambda path: path,
) -> list[LandedFile]:
    """Files whose `key(path)` is not yet ingested, in path order (deterministic).

    `key` maps a full path to the identity stored in the file log (e.g. the path relative to
    the landing root), so the comparison never depends on URI scheme or mount point.
    """
    return sorted(
        (f for f in available if key(f.path) not in already_ingested), key=lambda f: f.path
    )


def batch_id(table: str, files: Iterable[LandedFile]) -> str:
    """A batch id that depends only on the table and the exact files in the batch.

    Re-running a failed batch selects the same files, so it gets the same id, and the write
    replaces that batch's rows instead of appending duplicates.
    """
    digest = hashlib.sha1(table.encode())
    for f in sorted(files, key=lambda f: f.path):
        digest.update(f"\n{f.path}|{f.size}|{f.modified_ms}".encode())
    return f"{table}-{digest.hexdigest()[:16]}"


def group_by_header(paths: Iterable[str], first_line: Callable[[str], str]) -> dict[str, list[str]]:
    """Group CSV files by their header line.

    Spark maps CSV columns by position using the first file's header, which silently
    misaligns files whose header changed (schema drift). Reading each header group on its own
    and unioning by name keeps every column where it belongs.
    """
    groups: dict[str, list[str]] = {}
    for path in sorted(paths):
        groups.setdefault(first_line(path).strip(), []).append(path)
    return groups


def workbook_cells(content: bytes) -> list[tuple[str, int, int, str]]:
    """Every non-empty cell of an .xlsx file as (sheet, row, column, value-as-text).

    Human-formatted workbooks (titles, merged headers, totals, footnotes) don't map to a
    table, so Bronze keeps the raw grid and Silver interprets it.
    """
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    cells = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is not None and str(cell.value).strip() != "":
                    cells.append((sheet.title, cell.row, cell.column, str(cell.value)))
    return cells
