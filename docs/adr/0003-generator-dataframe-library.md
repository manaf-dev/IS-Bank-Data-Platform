# ADR-0003: pandas and NumPy for the synthetic data generator

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

The generator must produce about 50k customers and 3 to 5M transactions, vectorised and reproducible from a seed (CLAUDE.md section 4). CLAUDE.md allows pandas or Polars. The generator runs locally, outside Fabric, so this choice does not affect the platform runtime.

## Options

1. **Polars:** faster, stricter types, lazy execution. Less familiar, and further from the pandas-on-Spark and pandas tooling used in Fabric notebooks.
2. **pandas + NumPy:** familiar, widely documented, and the same mental model as pandas usage in Fabric notebooks. Slower and looser with types.

## Decision

Use **pandas (3.x) for tabular output and NumPy for sampling**. All randomness comes from a single seeded `numpy.random.Generator` passed through the code, never global random state. String columns use pandas' `string` dtype, dates `datetime64[s]`, and money integer pesewas (`int64`).

## Consequences

- Performance is ample at the current scale: the full 50k-customer profile generates in about 0.5 s. Transaction generation is the real test; if it becomes a bottleneck, hot paths can move to pure NumPy before revisiting the library.
- pandas and NumPy live in the `generator` dependency group only, so they never enter the `isbank` wheel deployed to Fabric.
