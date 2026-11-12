# IS Bank Data Platform

An end-to-end data engineering and data science platform for **IS Bank**, a fictional Ghanaian retail and SME bank, built on **Microsoft Fabric**.

IS Bank's data lives in siloed source systems (core banking, card switch, mobile banking, loan origination, CRM, general ledger, credit bureau). This platform unifies them into a single source of truth, a BI layer, and an ML layer that answers questions like *which branches are profitable, who is about to churn, where is fraud happening, and how risky is the loan book*.

> All data is **synthetic**: every source system is simulated by code in this repo. There is no real customer data anywhere. The platform is still designed as if it handled real PII.

## Architecture

Medallion architecture on OneLake:

| Layer | Fabric item | Purpose |
|---|---|---|
| Bronze | Lakehouse `lh_bronze` | Raw, append-only data plus ingestion metadata |
| Silver | Lakehouse `lh_silver` | Cleaned, typed, deduplicated, conformed; PII handled; entity resolution; SCD2; DQ quarantine |
| Gold | Warehouse `wh_gold` | Star schema, business aggregates, ML features, and model outputs |

See [docs/architecture.md](docs/architecture.md) and the decision records in [docs/adr/](docs/adr/).

## Repository layout

```
generator/      Synthetic world simulator, per-system projectors, and emitters (runs outside Fabric)
src/isbank/     Shared platform library, packaged as a wheel for Fabric
fabric/         Fabric Git-synced items (managed by Fabric Git integration)
apps/           Fabric App (web dashboard) built on the sm_isbank semantic model
tests/          pytest suite
docs/           Architecture, data dictionary, source-system specs, ADRs
```

Only `fabric/` is synced to the Fabric workspace. Everything else is ordinary Python and documentation.

## Getting started

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.13 (the version used by Fabric Spark Runtime 2.0) if needed.

```bash
uv sync             # create .venv and install the project plus dev tools
uv run pytest       # run the tests
uv run ruff check . # lint
uv run ruff format . # format
uv build            # build the isbank wheel for Fabric
```

### Generating the source systems

```bash
uv run python -m generator summary --profile dev                  # build the world, print counts
uv run python -m generator backfill --profile dev --out output/dev  # land every source up to "today"
uv run python -m generator tick --out output/dev --days 1           # land the next simulated day
```

Use `--profile full` for the 50k-customer world (about 7M postings). Output is reproducible from config and seed, so it is never committed. See [docs/source_systems/](docs/source_systems/) for what each system emits and its planted flaws.

## Status

**Phases 0 to 3 are complete:** ingestion, Silver, Gold and the Power BI layer are built. A Fabric App version of the dashboard is in progress.

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundation | Done |
| 1 | Synthetic world and source emitters | Done |
| 2 | Batch pipeline: ingestion, Silver, entity resolution, DQ | Done |
| 3 | Gold and BI: star schema, semantic model, dashboards with RLS | Done |
| 4 | Real-time: Eventstream, Eventhouse, fraud rules, alerts | Planned |
| 5 | Data science: features, training, MLflow, batch scoring | Planned |
| 6 | Productionization: CI/CD, monitoring, documentation | Planned |

### What's built

- **Bronze** (`nb_bronze_ingest` → `lh_bronze`): lands every source system's files as raw, append-only data with ingestion metadata.
- **Silver** (`nb_silver_build` → `lh_silver`): typing, standardisation and deduplication (including double postings), SCD2 customer history, and entity resolution that links core CIFs, loan applicants and bureau records to one golden party id (Ghana Card, date of birth + mobile, or date of birth + fuzzy name match). Data quality rules quarantine failing rows instead of dropping them.
- **Gold** (`nb_gold_build` → `wh_gold`): a star schema of 7 dimensions and 7 fact tables, with masked names, account numbers and card PANs, plus a GL-vs-core fee reconciliation (`recon_gl_fees`).
- **Run audit**: every notebook step records what ran, rows written, status and duration in `run_audit`.
- **Power BI**: the `sm_isbank` semantic model, with row-level security for the Executive and Branch Manager roles (driven by `sec_user_branch`), and `rpt_isbank`, a six-page report: Executive summary, Branch performance, Branch detail (drill-through), Customers and digital, Credit risk, and Fraud and card activity.
- **Fabric App** (in progress, `apps/isbank-dashboard`): a web version of the dashboard on `sm_isbank`, with the Executive and Branches pages done.
