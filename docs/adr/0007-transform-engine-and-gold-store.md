# ADR-0007: PySpark for transforms, a Warehouse for Gold, core SQL database deferred

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Phase 2 builds Bronze ingestion, Silver, and Gold. Three open questions decide how: the transform engine, where the Gold star schema lives (CLAUDE.md section 12), and whether the Fabric SQL database for core banking (ADR-0004) is built before the pipeline. The project is on a short timeline to a demo.

## Options

**Engine:** PySpark notebooks (industry standard; needs a local JDK for unit tests) or Fabric Python notebooks with pandas and delta-rs (cheaper and faster to build, but less of a showcase).

**Gold store:** Lakehouse `lh_gold` (one engine, simplest Direct Lake path) or Warehouse `wh_gold` (T-SQL star schema, demonstrates Warehouse skills, needs a cross-engine load).

**Core SQL database:** build it now (realistic CDC source, costs capacity and time) or defer it and use the interim core extracts already in `lh_bronze` (ADR-0004 amendment).

## Decision

- **PySpark** for Bronze and Silver transforms. Logic lives in `src/isbank` as DataFrame-API functions, unit-tested against a local Spark 4.1 session (Microsoft OpenJDK 21 installed locally; Windows workarounds are in `tests/conftest.py`). Notebooks stay thin.
- **Warehouse `wh_gold`** for the Gold star schema, loaded from `lh_silver`. The Direct Lake semantic model and Power BI report read from it.
- **Defer the core SQL database.** The pipeline reads the interim core extracts in `lh_bronze`. Mirroring can be added later without changing Silver, because Silver reads Bronze tables, not the transport.
- Demo scope first: pipeline and report, then ML if time allows.

## Consequences

- Spark tests are slow to start locally (about a minute), so they are grouped behind a session fixture.
- Loading `wh_gold` from `lh_silver` adds a step (T-SQL `INSERT ... SELECT` across the lakehouse SQL endpoint, or a pipeline copy). That step is decided when Gold is built.
- The demo shows file-based CDC for core banking rather than database mirroring; the ADR-0004 amendment explains why.
