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

## Status

**Phase 0: Foundation.** Repo scaffold, conventions, and ADR-001.

| Phase | Scope |
|---|---|
| 0 | Foundation |
| 1 | Synthetic world and source emitters |
| 2 | Batch pipeline: ingestion, Silver, entity resolution, DQ |
| 3 | Gold and BI: star schema, semantic model, dashboards with RLS |
| 4 | Real-time: Eventstream, Eventhouse, fraud rules, alerts |
| 5 | Data science: features, training, MLflow, batch scoring |
| 6 | Productionization: CI/CD, monitoring, documentation |
