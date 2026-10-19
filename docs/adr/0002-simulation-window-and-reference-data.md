# ADR-0002: Simulation window, branches, segments, and products

- **Status:** Proposed
- **Date:** 2026-10-19

## Context

The world simulator (Phase 1) needs a fixed time frame and the bank's reference data before it can generate anything: where the branches are, what kinds of customers the bank serves, and which products they hold. These were open questions in CLAUDE.md section 12. Everything lives in `generator/config.yaml` so it can be tuned without code changes, and `tests/test_config.py` checks its internal consistency.

## Options

**Simulation window**
1. *Fixed 2023-01-01 to 2025-12-31:* three full calendar years, deterministic regardless of when it runs.
2. *Anchored to the real current date:* data always looks current, but output depends on the run date.
3. *Fixed, two years:* faster, with fewer seasonal cycles for the models to learn from.

**Reference data**
1. *Draft a realistic catalogue now, review it in a PR.*
2. *Start minimal* (current/savings, one card, one loan) and extend later.

## Decision

**Window:** history runs from **2023-01-01** to a simulated "today" of **2025-12-31**. Daily ticks continue from 2026-01-01. Three Christmases, Easters, and school-fees seasons give the seasonality and churn/default models enough cycles.

**Branches:** 15 branches across the 10 planned cities, weighted towards Greater Accra (6, including Tema) and Kumasi (2). Each has a customer share, an income index, and a digital-adoption index. Two market-area branches (Makola, Suame) are trader-heavy and Airport City is skewed to corporate and SME customers. **Accra Madina opens on 2024-06-03**, inside the window, so the data includes a branch opening.

**Segments:** five archetypes: civil servant (20%), corporate employee (22%), market trader (28%), susu saver (18%), and SME (12%, business customers). Each has a lognormal income (or turnover) distribution, an income-regularity score, and a cash-intensity score.

**Products:** 11 products in three families:
- Deposits: personal and business current accounts, regular savings, susu savings, fixed deposits.
- Cards: debit card, classic credit card.
- Loans: personal loan, salary advance, trader micro loan, SME working capital.

Each product has a take-up probability per eligible segment. Mobile banking and mobile money wallet linking are modelled as customer-level services.

All names and figures are fictional and illustrative. Incomes are rough GHS magnitudes chosen for plausible behaviour, not market statistics.

## Consequences

- Simulated dates are fixed, so "today" in dashboards is 2025-12-31 until ticks advance it. Reports must use the data's max date, not the system clock.
- The Madina opening means customer-to-branch assignment must respect `opened_on`. This is a useful test of SCD2 and branch-level reporting.
- Take-up rates are marginal probabilities. The simulator still needs rules for correlations between products (e.g. a credit card usually implies a current account); those are decided in Phase 1.
- Interest rates, fees, loan terms, and the credit-default and fraud signal parameters are not covered here and need their own config sections.
