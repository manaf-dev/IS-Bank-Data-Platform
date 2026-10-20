"""Bronze source registry: which landing files feed which Bronze table, and how to read them.

One generic ingestion routine loops over this registry (metadata-driven ingestion), so adding
a source means adding an entry here, not writing a new pipeline. Paths are relative to the
landing root (`Files/landing` in `lh_bronze`).
"""

from dataclasses import dataclass, field

# How Bronze keeps each format "as received":
#   csv      - header-mapped columns, all strings (schema drift appears as new columns)
#   text     - one row per raw line (JSON lines, fixed-width), parsed in Silver
#   parquet  - typed columns as extracted by the core banking system
#   xlsx     - one row per non-empty worksheet cell (sheet, row, column, value)
FORMATS = frozenset({"csv", "text", "parquet", "xlsx"})


@dataclass(frozen=True)
class SourceSpec:
    """One landing-file pattern and the Bronze table it loads into."""

    system: str
    table: str
    pattern: str
    fmt: str
    enabled: bool = True
    note: str = ""
    options: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.fmt not in FORMATS:
            raise ValueError(f"{self.table}: unknown format {self.fmt!r}")


CORE_TABLES = ("branches", "products", "customers", "accounts", "cards", "transactions")

SOURCES: tuple[SourceSpec, ...] = (
    *(
        SourceSpec("core_banking", f"core_{t}", f"core_banking/*/core_{t}.parquet", "parquet")
        for t in CORE_TABLES
    ),
    SourceSpec("card_switch", "card_authorisations", "card_switch/*/*/*/*.jsonl", "text"),
    SourceSpec("mobile_banking", "mobile_app_events", "mobile_banking/*/*/*/*.jsonl", "text"),
    SourceSpec(
        "loan_origination",
        "los_applications",
        "loan_origination/*/*/*/los_applications_*.csv",
        "csv",
    ),
    SourceSpec(
        "loan_origination", "los_repayments", "loan_origination/*/*/*/los_repayments_*.csv", "csv"
    ),
    SourceSpec("general_ledger", "gl_pnl_cells", "general_ledger/*/*/*.xlsx", "xlsx"),
    SourceSpec("credit_bureau", "bureau_report_lines", "credit_bureau/*/*/*/*.txt", "text"),
    SourceSpec("compliance", "compliance_watchlist", "compliance/*/*/*/*.csv", "csv"),
    SourceSpec("reference_data", "ref_fx_rates", "reference_data/*/*/*/fx_rates_*.csv", "csv"),
    *(
        SourceSpec(
            "crm",
            f"crm_{t}",
            f"crm/*/*/*/crm_{t}_*.csv",
            "csv",
            enabled=False,
            note="CRM files are delivered to Google Drive; enabled once that path is set up.",
        )
        for t in ("contacts", "cases", "nps_responses", "campaign_sends")
    ),
)


def enabled_sources(systems: set[str] | None = None) -> list[SourceSpec]:
    """Enabled sources, optionally limited to some systems."""
    return [s for s in SOURCES if s.enabled and (systems is None or s.system in systems)]
