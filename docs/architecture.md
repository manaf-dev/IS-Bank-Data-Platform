# Architecture

> Stub: this document grows as decisions are made. Decisions themselves are recorded in [adr/](adr/).

## Overview

```
 Simulated source systems (generator/, runs outside Fabric)
   core banking · card switch · mobile banking · LOS · CRM · GL · bureau · watchlists · reference data
        │  emitters: SQL writes, event streams, file drops
        ▼
 ┌──────────────────────── Microsoft Fabric (OneLake) ────────────────────────┐
 │  Bronze  lh_bronze   raw, append-only, + ingestion metadata               │
 │     │                                                                      │
 │  Silver  lh_silver   typed, deduplicated, conformed, PII handled,          │
 │     │                entity resolution, SCD2, DQ quarantine                │
 │  Gold    wh_gold     star schema, aggregates, ML features, model outputs   │
 │     │                                                                      │
 │  Direct Lake semantic model → Power BI (RLS)   ·   ML (MLflow, registry)   │
 └────────────────────────────────────────────────────────────────────────────┘
```

## Code boundaries

- `generator/` simulates the source systems and owns the ground-truth answer key. It is never deployed to Fabric.
- `src/isbank/` is the platform library. It is packaged as a wheel and installed in a Fabric Environment. It must never import `generator` or read the answer key.
- `fabric/` holds Fabric item definitions, synced through Fabric Git integration. Notebooks there are thin orchestration layers that call `isbank`.

## Environments

| Environment | Workspace | Git branch / folder |
|---|---|---|
| Dev | Sandbox - Manaf Jr. (`fd3581c9-57eb-407a-9386-99a29953f818`) | `main` / `/fabric` |

The workspace's default Spark runtime is Fabric Runtime 2.0 (Spark 4.1, Python 3.13, Delta 4.2). Test and Prod are not in scope yet.
