"""Silver pipeline: rebuild every Silver table from Bronze, apply DQ, resolve parties.

Each table is a pure transform of its Bronze inputs plus a list of DQ rules. A run rebuilds
the tables in full (overwrite), so it is idempotent; DQ results and quarantined rows are
appended per run so quality can be tracked over time. Storage is injected (`read_bronze`,
`write_silver`) so the same code runs locally in tests and in Fabric against Delta tables.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from pyspark.sql import functions as F

from isbank.audit import StepOutcome, output_files
from isbank.dq import Rule, apply_rules
from isbank.silver import core, events, finance, loans, resolution
from isbank.silver.standardize import latest_per_key


def branches(bronze):
    """Branch reference data (latest delivery per branch)."""
    typed = bronze.select(
        "branch_code",
        "branch_name",
        "city",
        "region",
        F.to_date("opened_date").alias("opened_date"),
        "_ingested_at",
    )
    return latest_per_key(typed, ["branch_code"], [F.col("_ingested_at").desc()]).drop(
        "_ingested_at"
    )


def products(bronze):
    """Product reference data (latest delivery per product)."""
    typed = bronze.select("product_code", "product_name", "product_family", "_ingested_at")
    return latest_per_key(typed, ["product_code"], [F.col("_ingested_at").desc()]).drop(
        "_ingested_at"
    )


@dataclass(frozen=True)
class SilverTable:
    """One Silver table: its Bronze inputs, transform, DQ rules, and record key."""

    name: str
    sources: tuple[str, ...]
    build: Callable
    rules: tuple[Rule, ...]
    keys: tuple[str, ...]


TABLES: tuple[SilverTable, ...] = (
    SilverTable("branch", ("core_branches",), branches, (), ("branch_code",)),
    SilverTable("product", ("core_products",), products, (), ("product_code",)),
    SilverTable(
        "customer_history",
        ("core_customers",),
        core.customer_history,
        tuple(core.CUSTOMER_RULES),
        ("cif_number", "valid_from"),
    ),
    SilverTable(
        "account", ("core_accounts",), core.accounts, tuple(core.ACCOUNT_RULES), ("account_number",)
    ),
    SilverTable(
        "transaction",
        ("core_transactions",),
        core.transactions,
        tuple(core.TRANSACTION_RULES),
        ("txn_ref",),
    ),
    SilverTable(
        "card_authorisation",
        ("card_authorisations",),
        events.card_authorisations,
        tuple(events.CARD_RULES),
        ("event_id",),
    ),
    SilverTable(
        "app_event",
        ("mobile_app_events",),
        events.app_events,
        tuple(events.APP_RULES),
        ("event_id",),
    ),
    SilverTable(
        "loan_application",
        ("los_applications",),
        loans.applications,
        tuple(loans.APPLICATION_RULES),
        ("application_ref",),
    ),
    SilverTable(
        "loan_repayment",
        ("los_repayments",),
        loans.repayments,
        tuple(loans.REPAYMENT_RULES),
        ("loan_account_number", "installment_no"),
    ),
    SilverTable(
        "gl_pnl",
        ("gl_pnl_cells",),
        finance.gl_pnl,
        tuple(finance.GL_RULES),
        ("period", "cost_centre", "gl_account"),
    ),
    SilverTable(
        "bureau_score",
        ("bureau_report_lines",),
        finance.bureau_scores,
        tuple(finance.BUREAU_RULES),
        ("bureau_ref", "report_date"),
    ),
    SilverTable(
        "fx_rate",
        ("ref_fx_rates",),
        finance.fx_rates,
        tuple(finance.FX_RULES),
        ("rate_date", "base_currency"),
    ),
    SilverTable(
        "watchlist_entry",
        ("compliance_watchlist",),
        finance.watchlist,
        tuple(finance.WATCHLIST_RULES),
        ("list_entry_id",),
    ),
)


@dataclass
class SilverRun:
    """Everything a run produced besides the Silver tables themselves."""

    outcomes: list[StepOutcome]
    dq_results: list[dict]
    quarantine: list  # DataFrames to append to dq_quarantine


def run(
    spark,
    read_bronze: Callable[[str], object],
    write_silver: Callable[[str, object], None],
    run_id: str,
    tables: tuple[SilverTable, ...] = TABLES,
    read_silver: Callable[[str], object] | None = None,
) -> SilverRun:
    """Build every Silver table, then the party cross-reference from the written tables."""
    result = SilverRun(outcomes=[], dq_results=[], quarantine=[])
    built = {}
    for table in tables:
        started = dt.datetime.now(dt.UTC)
        try:
            df = table.build(*[read_bronze(s) for s in table.sources])
            dq = (
                apply_rules(df, table.name, list(table.rules), list(table.keys), run_id)
                if table.rules
                else None
            )
            clean = (dq.clean if dq else df).cache()
            rows = clean.count()
            write_silver(table.name, clean.coalesce(output_files(rows)))
            built[table.name] = clean
            if dq:
                result.dq_results.extend(dq.results)
                result.quarantine.append(dq.quarantine)
            result.outcomes.append(
                StepOutcome(table.name, len(table.sources), rows, started, dt.datetime.now(dt.UTC))
            )
        except Exception as exc:  # keep going; the run reports every failure
            result.outcomes.append(
                StepOutcome(
                    table.name,
                    len(table.sources),
                    0,
                    started,
                    dt.datetime.now(dt.UTC),
                    "FAILED",
                    repr(exc),
                )
            )

    started = dt.datetime.now(dt.UTC)
    try:
        read = read_silver or built.__getitem__
        recs = resolution.records(
            read("customer_history").where("is_current"),
            read("loan_application"),
            read("bureau_score"),
        )
        xref = resolution.resolve(spark, recs).cache()
        rows = xref.count()
        write_silver("party_xref", xref.coalesce(output_files(rows)))
        result.outcomes.append(StepOutcome("party_xref", 3, rows, started, dt.datetime.now(dt.UTC)))
    except Exception as exc:
        result.outcomes.append(
            StepOutcome("party_xref", 3, 0, started, dt.datetime.now(dt.UTC), "FAILED", repr(exc))
        )
    return result
