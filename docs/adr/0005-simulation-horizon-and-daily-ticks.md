# ADR-0005: Simulation horizon and deterministic daily ticks

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

The platform needs a historical backfill (2023-01-01 to 2025-12-31, per ADR-0002) and then a "tick" per simulated day that emits new and changed records. That exercises incremental loads, CDC, late data, and SCD2. Ticks must be idempotent: re-running a day must not create duplicates or different data.

## Options

1. **Simulate incrementally:** keep simulator state and generate each new day on demand. Results depend on how many ticks ran before and in what order, and the state must be persisted and versioned.
2. **Simulate once to a horizon and reveal it day by day:** generate the whole world up to an `end_date` in one deterministic run, and treat `today` as a cursor. A tick only exposes what happened in the next day.

## Decision

Option 2. `simulation.end_date` (2026-12-31) is the horizon and `simulation.today` (2025-12-31) is the backfill cursor. `generator.timeline` provides:

- `snapshot(world, as_of)`: what the bank's systems know at the end of a day (attributes as of that day, closures and complaint resolutions only once they happen).
- `delta(world, since, until)`: what happened in the day range `(since, until]`.

Snapshots and deltas compose exactly (`snapshot(t0) + delta(t0, t1) == snapshot(t1)`), and a run of daily deltas equals the range delta. Tests enforce both.

The world also records customer attribute changes (phone, digital address, email, surname on marriage) and account closures (churn, matured, repaid), which the snapshots apply.

## Consequences

- A tick is a pure function of config, seed, and date: idempotent and order-independent.
- Each full build simulates to the horizon, so generating ticks costs a full world build (about 30 s at full scale). Caching the built world between ticks is a later optimisation if needed.
- Ticks cannot go past `end_date`. Extending the horizon changes nothing before the old horizon only if the generator's random draws do not depend on the window length, which is **not** guaranteed today. Treat a horizon change as a new world and reload.
