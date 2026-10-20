import numpy as np
import pytest

from generator.config import load_config
from generator.world.merchants import generate_merchants


@pytest.fixture(scope="module")
def setup():
    config = load_config(profile="dev")
    return config, generate_merchants(config, np.random.default_rng(config.seed))


def test_merchant_count_scales_with_customers(setup):
    config, merchants = setup
    expected = round(config.merchants.per_1000_customers * config.customers.count / 1000)
    assert len(merchants) == expected
    assert merchants["merchant_id"].is_unique


def test_categories_and_cities(setup):
    config, merchants = setup
    categories = config.merchants.categories
    assert set(merchants["category"]) <= set(categories)
    assert (
        merchants["mcc"] == merchants["category"].map({k: c.mcc for k, c in categories.items()})
    ).all()
    online = merchants["channel"] == "online"
    assert merchants.loc[online, "city"].isna().all()
    assert set(merchants.loc[~online, "city"]) <= {b.city for b in config.branches}
