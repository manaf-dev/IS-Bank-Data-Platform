"""Generate which products and services each customer holds, and on what terms.

This slice fixes the *contracts*: what was opened, when, linked to which account, for
how much, at what rate, and for how long. What happens afterwards (balances, repayments,
defaults, closures) is decided by the behaviour simulation, not here.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from generator.config import Product, WorldConfig
from generator.world.sampling import add_months, uniform_dates
from isbank.reference.ghana import MOBILE_PREFIXES

_NAT = np.datetime64("NaT", "D")


@dataclass(frozen=True)
class Holdings:
    """Ground-truth product holdings.

    accounts: one row per product contract (deposit account, card, or loan).
    services: one row per customer-level service enrolment (mobile banking, MoMo link).
    """

    accounts: pd.DataFrame
    services: pd.DataFrame


def generate_holdings(
    config: WorldConfig, customers: pd.DataFrame, rng: np.random.Generator
) -> Holdings:
    """Assign products and services to `customers` (output of `generate_customers`)."""
    return Holdings(
        accounts=_generate_accounts(config, customers, rng),
        services=_generate_services(config, customers, rng),
    )


def _generate_accounts(
    config: WorldConfig, customers: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    n = len(customers)
    segment = customers["segment"].to_numpy(dtype=object)
    joined = customers["joined_on"].to_numpy().astype("datetime64[D]")
    end = np.full(n, np.datetime64(config.end_date, "D"))
    income = customers["monthly_income_pesewas"].to_numpy()

    standalone = [p for p in config.products if not p.requires_any]
    dependent = [p for p in config.products if p.requires_any]
    held: dict[str, np.ndarray] = {}
    opened: dict[str, np.ndarray] = {}
    linked_to: dict[str, np.ndarray] = {}

    # 1. Standalone deposits. Everyone holds at least one: the bank's most likely
    #    account for their segment if the independent draws gave them none.
    for p in standalone:
        held[p.product_code] = rng.random(n) < _take_up(p, segment)
    has_any = np.logical_or.reduce([held[p.product_code] for p in standalone])
    fallback = _most_likely_product(standalone, segment)
    for p in standalone:
        held[p.product_code] |= ~has_any & (fallback == p.product_code)

    # 2. One held deposit is the account the customer joined with (opened on joined_on);
    #    any others were opened at a random later date.
    held_matrix = np.column_stack([held[p.product_code] for p in standalone])
    anchor = np.argmax(np.where(held_matrix, rng.random(held_matrix.shape), -1.0), axis=1)
    later = uniform_dates(rng, joined, end)
    for j, p in enumerate(standalone):
        opened[p.product_code] = np.where(
            held[p.product_code], np.where(anchor == j, joined, later), _NAT
        )
        later = uniform_dates(rng, joined, end)

    # 3. Cards, loans, and fixed deposits need an account to link to; they link to the
    #    earliest-opened eligible account and open on or after it, and no earlier than the
    #    lookback horizon so that most contracts are live inside the simulation window.
    horizon = add_months(
        np.array([np.datetime64(config.start_date, "D")]),
        np.array([-config.dependent_open_lookback_months]),
    )[0]
    for p in dependent:
        candidates = np.column_stack([opened[code] for code in p.requires_any])
        eligible = ~np.isnat(candidates)
        has_link = eligible.any(axis=1)
        first = np.argmin(
            np.where(eligible, candidates.astype("int64"), np.iinfo("int64").max), axis=1
        )
        link_opened = candidates[np.arange(n), first]

        is_held = has_link & (rng.random(n) < _take_up(p, segment))
        low = np.where(is_held, np.maximum(np.maximum(joined, link_opened), horizon), joined)
        held[p.product_code] = is_held
        opened[p.product_code] = np.where(is_held, uniform_dates(rng, low, end), _NAT)
        linked_to[p.product_code] = np.where(
            is_held, np.array(p.requires_any, dtype=object)[first], None
        )

    frames = []
    for order, p in enumerate(config.products):
        idx = np.flatnonzero(held[p.product_code])
        frame = pd.DataFrame(
            {
                "person_id": customers["person_id"].to_numpy()[idx],
                "product_code": p.product_code,
                "family": p.family,
                "branch_code": customers["home_branch_code"].to_numpy(dtype=object)[idx],
                "opened_on": opened[p.product_code][idx],
                "_linked_product": linked_to.get(p.product_code, np.full(n, None))[idx],
                "_product_order": order,
            }
        )
        frames.append(_with_terms(config, p, frame, income[idx], rng))

    accounts = pd.concat(frames, ignore_index=True)
    accounts = accounts.sort_values(["opened_on", "person_id", "_product_order"], kind="stable")
    accounts["account_id"] = np.arange(1, len(accounts) + 1, dtype="int64")

    # Resolve the linked product code to the linked account's id. Each person holds at
    # most one contract per product, so (person_id, product_code) is unique.
    key = accounts.set_index(["person_id", "product_code"])["account_id"]
    link_key = pd.MultiIndex.from_arrays([accounts["person_id"], accounts["_linked_product"]])
    accounts["linked_account_id"] = pd.array(key.reindex(link_key).to_numpy(), dtype="Int64")

    columns = [
        "account_id",
        "person_id",
        "product_code",
        "family",
        "branch_code",
        "linked_account_id",
        "opened_on",
        "maturity_date",
        "contract_amount_pesewas",
        "annual_rate_bps",
        "term_months",
    ]
    accounts = accounts[columns].reset_index(drop=True)
    for col in ("opened_on", "maturity_date"):
        accounts[col] = accounts[col].astype("datetime64[s]")
    return accounts.astype(
        {
            "product_code": "string",
            "family": "string",
            "branch_code": "string",
            "contract_amount_pesewas": "Int64",
            "annual_rate_bps": "Int64",
            "term_months": "Int64",
        }
    )


def _with_terms(
    config: WorldConfig,
    product: Product,
    frame: pd.DataFrame,
    income: np.ndarray,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Attach contract amount, rate, tenor, and maturity drawn from the product's terms."""
    n = len(frame)
    frame["contract_amount_pesewas"] = pd.array([pd.NA] * n, dtype="Int64")
    frame["annual_rate_bps"] = pd.array([pd.NA] * n, dtype="Int64")
    frame["term_months"] = pd.array([pd.NA] * n, dtype="Int64")
    frame["maturity_date"] = np.full(n, _NAT)
    terms = product.terms
    if terms is None or n == 0:
        return frame

    multiple = np.exp(
        np.log(terms.amount_median_income_multiple) + terms.amount_sigma * rng.standard_normal(n)
    )
    step = config.contract_amount_rounding_ghs * 100
    amount = np.maximum(np.round(income * multiple / step), 1).astype("int64") * step
    frame["contract_amount_pesewas"] = pd.array(amount, dtype="Int64")
    rate_pct = rng.uniform(terms.rate_min_pct, terms.rate_max_pct, size=n)
    frame["annual_rate_bps"] = pd.array(np.round(rate_pct * 100).astype("int64"), dtype="Int64")

    if terms.term_months is not None:
        months = rng.choice(np.array(terms.term_months), size=n, p=terms.term_weights)
        frame["term_months"] = pd.array(months, dtype="Int64")
        frame["maturity_date"] = add_months(frame["opened_on"].to_numpy(), months)
    return frame


def _generate_services(
    config: WorldConfig, customers: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """Enrol customers in services; uptake scales with the home branch's digital index."""
    n = len(customers)
    segment = customers["segment"].to_numpy(dtype=object)
    digital = (
        customers["home_branch_code"]
        .map({b.branch_code: b.digital_index for b in config.branches})
        .to_numpy(dtype=float)
    )
    joined = customers["joined_on"].to_numpy().astype("datetime64[D]")
    end = np.full(n, np.datetime64(config.end_date, "D"))
    phones = customers["phone_number"].to_numpy(dtype=object)

    frames = []
    for service, take_up in config.services.items():
        base = np.array([take_up.get(s, 0.0) for s in segment])
        idx = np.flatnonzero(rng.random(n) < np.minimum(1.0, base * digital))
        wallet = np.full(idx.size, None, dtype=object)
        if service == "momo_wallet_link":
            wallet = _wallet_numbers(config, rng, phones[idx], set(phones))
        frames.append(
            pd.DataFrame(
                {
                    "person_id": customers["person_id"].to_numpy()[idx],
                    "service": service,
                    "enrolled_on": uniform_dates(rng, joined[idx], end[idx]).astype(
                        "datetime64[s]"
                    ),
                    "wallet_number": wallet,
                }
            )
        )
    services = pd.concat(frames, ignore_index=True)
    return services.astype({"service": "string", "wallet_number": "string"})


def _wallet_numbers(
    config: WorldConfig, rng: np.random.Generator, own_phones: np.ndarray, taken: set[str]
) -> np.ndarray:
    """Most wallets sit on the customer's own number; the rest on a second SIM."""
    wallets = own_phones.copy()
    second_sim = np.flatnonzero(rng.random(len(own_phones)) >= config.momo_same_number_share)
    prefixes = np.array([p for ps in MOBILE_PREFIXES.values() for p in ps], dtype=object)
    used = set(taken)
    for i in second_sim:
        while True:
            number = f"{rng.choice(prefixes)}{rng.integers(0, 10**7):07d}"
            if number not in used:
                break
        used.add(number)
        wallets[i] = number
    return wallets


def _take_up(product: Product, segment: np.ndarray) -> np.ndarray:
    return np.array([product.take_up.get(s, 0.0) for s in segment])


def _most_likely_product(products: list[Product], segment: np.ndarray) -> np.ndarray:
    """For each customer, the standalone product with the highest take-up for their segment."""
    best = {
        s: max(products, key=lambda p: p.take_up.get(s, 0.0)).product_code for s in set(segment)
    }
    return np.array([best[s] for s in segment], dtype=object)
