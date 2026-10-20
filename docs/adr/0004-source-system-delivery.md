# ADR-0004: How simulated source systems deliver data to Fabric

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

Each simulated source system needs a delivery mechanism into the Dev workspace. The choice shapes the Bronze ingestion patterns the platform demonstrates (CDC, streaming, batch files). These were open questions in CLAUDE.md section 12.

## Options and decision

| Source | Options considered | Decision |
|---|---|---|
| Core banking (customers, accounts, transactions, branches, products) | Fabric SQL database + mirroring; Azure SQL Database + mirroring; file extracts with a watermark pipeline | **Fabric SQL database, mirrored to OneLake.** A real relational source inside the workspace with automatic CDC-style replication and no separate Azure resource. |
| Card switch and mobile banking events | Eventstream custom endpoint now; file replay now with Eventstream later | **Time-ordered JSON-lines files now** (with late and out-of-order events). An Eventstream custom-endpoint producer replaying the same files is added in Phase 4. |
| CRM | File drop to the lakehouse; mock REST API; Google Drive | **Files written to a Google Drive folder.** How Fabric ingests from Google Drive (connector support, authentication) must be verified against current Microsoft docs before the CRM ingestion is built. |
| LOS, GL, credit bureau, compliance lists, reference data | (as in CLAUDE.md section 3) | Batch files (CSV, Excel, fixed-width) landed in `lh_bronze` Files. |

## Consequences

- The Fabric SQL database consumes capacity while it is used. Loads must start with the dev profile and be costed before full-scale runs.
- Writing to the SQL database needs an authenticated connection from the local generator. Secrets stay in environment variables and are never committed.
- Event files double as a replay source, so the streaming path in Phase 4 can be tested deterministically.
- If Fabric cannot ingest from Google Drive cleanly, the CRM fallback is the lakehouse file drop. That would be recorded as an amendment to this ADR.

## Amendment (2026-10-20): interim core banking landing

Until the Fabric SQL database exists (a separate, costed approval), the core banking extracts the generator already writes as Parquet (`full_<date>` initial load, `cdc_<date>` daily upserts) are also uploaded to `lh_bronze/Files/landing/core_banking/`. This lets Phase 2 build Silver against core data straight away. When the SQL database and mirroring are in place, core ingestion switches to the mirrored tables and the interim files are retired. Because the files are regenerable from config and seed, nothing is lost by retiring them.
