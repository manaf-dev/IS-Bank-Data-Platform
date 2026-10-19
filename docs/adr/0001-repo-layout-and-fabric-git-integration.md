# ADR-0001: Repository layout, tooling, and Fabric Git integration

- **Status:** Proposed
- **Date:** 2026-10-19

## Context

The repo has to hold three different kinds of code:

1. **Fabric item definitions** (notebooks, pipelines, semantic models), serialized by Fabric Git integration.
2. **The platform library**: transformation, DQ, and entity-resolution logic that runs inside Fabric and must be unit-testable locally.
3. **The synthetic source-system generator**, which plays the part of the bank's source systems and owns the ground-truth answer key. It must stay clearly outside the platform.

The Dev workspace, *Sandbox - Manaf Jr.* (`fd3581c9-57eb-407a-9386-99a29953f818`), is already connected to GitHub `manaf-dev/is-bank-data-platform`, branch `main`, folder `/fabric`. Its default Spark runtime is Fabric Runtime 2.0 (Spark 4.1, Python 3.13, Delta 4.2). It runs on a paid, company-owned capacity.

## Options

**Layout**
1. *Fabric items at the repo root:* simplest Fabric setup, but generator and library code would sit inside the synced folder and pollute workspace sync.
2. *Fabric items in a dedicated `fabric/` folder, everything else beside it:* keeps sync clean and leaves the rest of the repo as an ordinary Python project.

**Packaging the library for Fabric**
1. *Logic written directly in notebooks:* no build step, but untestable locally and duplicated across notebooks.
2. *A wheel installed into a Fabric Environment:* testable, versioned, reusable; needs a build step.

**Build backend**
1. *setuptools:* mature, more configuration.
2. *uv_build:* simple, but oriented to one package per project.
3. *hatchling:* simple, and can ship `src/isbank` while excluding `generator/` from the wheel.

## Decision

- Fabric syncs only `fabric/`. Code, tests, and docs live outside it.
- `src/isbank/` is the platform library, built as a wheel with **hatchling** and installed in a Fabric Environment. Notebooks stay thin and call it.
- `generator/` is a local-only package, importable in tests via the repo root but excluded from the wheel. Dependencies flow one way: `generator` may import `isbank`, never the reverse (a test enforces this).
- **uv** manages Python, the virtual environment, and the lockfile (`uv.lock`). **ruff** lints and formats; **pytest** tests.
- Target **Python 3.13** to match Fabric Runtime 2.0.
- The Dev workspace syncs from `main`. Feature branches merge to `main` through PRs, and the workspace is then updated from Git.

## Consequences

- Library changes reach Fabric only after a wheel build and an Environment update (manual for now; CI/CD automates it in Phase 6).
- Changes on feature branches are not visible in the workspace until merged. If that slows iteration, revisit by connecting a dedicated branch.
- Fabric Runtime 1.3 (Python 3.11) is end-of-support-announced; if the workspace runtime changes, `requires-python` and ruff's `target-version` must follow.
- CI tooling (GitHub Actions vs Azure DevOps) and Test/Prod workspaces are still open and will get their own ADRs.
