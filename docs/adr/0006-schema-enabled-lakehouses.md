# ADR-0006: Schema-enabled lakehouses

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Fabric lakehouses can be created with or without schemas. A schema-enabled lakehouse organises tables as `schema.table` (default schema `dbo`) and supports four-part names (`workspace.lakehouse.schema.table`). A lakehouse created through the REST API has schemas **off** unless `creationPayload.enableSchemas` is `true`, and an existing lakehouse cannot yet be converted.

The first `lh_bronze` (2026-10-20) was created without schemas.

## Options

1. **Non-schema lakehouses throughout:** simplest, with a flat table namespace (`core_customers`).
2. **Schemas only from Silver onward:** keeps the first `lh_bronze` but leaves the layers inconsistent.
3. **Schema-enabled lakehouses everywhere:** consistent four-part naming, room to group tables by source or domain.

## Decision

Option 3. Every lakehouse is created with `enableSchemas: true`. `lh_bronze` was deleted and recreated schema-enabled (new item `ddc6a9ea-98f6-47d9-b7b8-120882abf8d5`) while it held only regenerable landing files, and those files were re-uploaded and verified.

## Consequences

- Code refers to tables with schema-qualified (or four-part) names. Whether Bronze tables use per-source schemas (`core.customers`) or `dbo` with source prefixes (`dbo.core_customers`) is still open: CLAUDE.md section 5 currently says source-prefixed names, so propose any change before Phase 2 builds tables.
- Recreating the item changed its logical ID. Fabric wrote the new definition to a logical-ID-named folder, which is renamed back to `fabric/lh_bronze.Lakehouse/` (renaming item folders is supported and causes no incoming workspace change).
- Lakehouses are created through the REST API with the schema flag, never by default.
