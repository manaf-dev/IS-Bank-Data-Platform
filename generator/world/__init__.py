"""World simulator: ground-truth customers, accounts, and behaviour."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from generator.config import WorldConfig
from generator.world.customers import generate_customers
from generator.world.holdings import Holdings, generate_holdings
from generator.world.merchants import generate_merchants
from generator.world.profiles import generate_profiles
from generator.world.scenarios import Scenarios, generate_scenarios
from generator.world.transactions import Ledger, simulate_ledger


@dataclass(frozen=True)
class World:
    """Everything the simulator knows. Projectors read it; platform code never does."""

    config: WorldConfig
    customers: pd.DataFrame
    holdings: Holdings
    merchants: pd.DataFrame
    profiles: pd.DataFrame
    scenarios: Scenarios
    ledger: Ledger


def build_world(config: WorldConfig) -> World:
    """Generate the whole world deterministically from `config.seed`.

    Every step draws from one seeded generator in a fixed order, so the same config and
    seed always produce the same world.
    """
    rng = np.random.default_rng(config.seed)
    customers = generate_customers(config, rng)
    holdings = generate_holdings(config, customers, rng)
    merchants = generate_merchants(config, rng)
    profiles = generate_profiles(config, customers, holdings, rng)
    scenarios = generate_scenarios(config, customers, holdings, merchants, profiles, rng)
    ledger = simulate_ledger(config, customers, holdings, merchants, profiles, scenarios, rng)
    return World(config, customers, holdings, merchants, profiles, scenarios, ledger)
