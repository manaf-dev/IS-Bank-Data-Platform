"""Card and payments switch: ISO 8583-flavoured authorisation events as JSON lines.

The switch sees every card authorisation, approved or declined, but knows nothing about
customers or accounts: only a masked PAN, a card token, the merchant, and the terminal.
Events are delivered as files per day of *delivery*: most arrive within seconds, some are
late (hours or days), and a few are delivered twice.
"""

import numpy as np
import pandas as pd

from generator.projectors.common import SourceBatch, stable_int, stable_uniform, system_id
from generator.world import World

SYSTEM = "card_switch"
_LOOKBACK = pd.Timedelta(days=4)
_DUPLICATE_OFFSET = 10**12
_ENTRY_MODE = {"POS": "07", "ONLINE": "81", "ATM": "05"}  # contactless, e-commerce, chip


def events_delivered(world: World, since: pd.Timestamp | None, until: pd.Timestamp) -> SourceBatch:
    """Card events delivered after `since` (None = from the start) up to the end of `until`."""
    p = world.config.projectors[SYSTEM]
    seed = world.config.seed
    hi = until + pd.Timedelta(days=1)
    postings = world.ledger.postings
    lo_posted = postings["posted_at"].min() if since is None else since - _LOOKBACK
    cards = postings[
        postings["channel"].isin(["POS", "ONLINE", "ATM"])
        & postings["txn_type"].isin(["CARD_PURCHASE", "CASH_WITHDRAWAL"])
        & (postings["posted_at"] >= lo_posted)
        & (postings["posted_at"] < hi)
    ]
    events = _events(world, cards, p, seed)
    lo = pd.Timestamp.min if since is None else since + pd.Timedelta(days=1)
    events = events[(events["delivered_at"] >= lo) & (events["delivered_at"] < hi)]
    batch = SourceBatch(SYSTEM)
    batch.add("card_authorisations", events.sort_values("delivered_at", kind="stable"), "jsonl")
    return batch


def _events(world: World, cards: pd.DataFrame, p: dict, seed: int) -> pd.DataFrame:
    acc = world.holdings.accounts
    debit = acc[acc["product_code"] == "DC_DEBIT"].set_index("person_id")["account_id"]
    product = acc.set_index("account_id")["product_code"]
    on_credit = cards["account_id"].map(product).eq("CC_CLASSIC").to_numpy()
    # The card used: the credit card account itself, else the customer's debit card.
    card_key = np.where(on_credit, cards["account_id"], cards["person_id"].map(debit).fillna(-1))
    card_key = card_key.astype("int64")
    bin_ = np.where(on_credit, "990002", "990001")
    last4 = system_id(seed, "card:pan", card_key, 10**4)

    merchants = world.merchants.set_index("merchant_id")
    mid = cards["merchant_id"]
    branch_city = {b.branch_code: b.city for b in world.config.branches}
    home_city = cards["person_id"].map(
        world.customers.set_index("person_id")["home_branch_code"].map(branch_city)
    )
    is_atm = cards["channel"].astype(str).eq("ATM").to_numpy()
    pid = cards["posting_id"].to_numpy()

    late = stable_uniform(seed, "card:late", pid) < p["late_event_share"]
    lag_s = np.where(
        late,
        stable_int(seed, "card:late:min", pid, p["late_minutes"]["min"], p["late_minutes"]["max"])
        * 60,
        stable_int(seed, "card:lag", pid, 1, 30),
    )
    txn_time = cards["posted_at"].to_numpy()
    events = pd.DataFrame(
        {
            "event_id": [f"evt_{v:016x}" for v in system_id(seed, "card:evt", pid, 2**62)],
            "mti": "0200",
            "processing_code": np.where(is_atm, "010000", "000000"),
            "pan_masked": [f"{b}******{v:04d}" for b, v in zip(bin_, last4, strict=True)],
            "card_token": [f"tok_{v:012x}" for v in system_id(seed, "card:token", card_key, 2**48)],
            "stan": [f"{v:06d}" for v in stable_int(seed, "card:stan", pid, 0, 999_999)],
            "rrn": [f"{v:012d}" for v in system_id(seed, "card:rrn", pid, 10**12)],
            "amount_minor": -cards["amount_pesewas"].to_numpy(),
            "currency_code": "936",
            "txn_datetime": txn_time,
            "mcc": np.where(is_atm, "6011", mid.map(merchants["mcc"]).astype(object).to_numpy()),
            "merchant_id": np.where(
                is_atm,
                None,
                [
                    f"MID{v:08d}"
                    for v in system_id(seed, "card:mid", mid.fillna(0).astype("int64"), 10**8)
                ],
            ),
            "merchant_name": np.where(
                is_atm, "IS BANK ATM", mid.map(merchants["merchant_name"]).astype(object)
            ),
            "merchant_city": np.where(
                is_atm, home_city.to_numpy(), mid.map(merchants["city"]).astype(object).to_numpy()
            ),
            "terminal_id": _terminal_ids(seed, cards, is_atm),
            "entry_mode": cards["channel"].astype(str).map(_ENTRY_MODE).to_numpy(),
            "response_code": np.where(cards["status"].astype(str) == "POSTED", "00", "51"),
            "delivered_at": txn_time + pd.to_timedelta(lag_s, unit="s").to_numpy(),
        }
    )
    dup = stable_uniform(seed, "card:dup", pid) < p["duplicate_delivery_share"]
    if dup.any():
        again = events[dup].copy()
        again["delivered_at"] = (
            again["delivered_at"]
            + pd.to_timedelta(
                stable_int(seed, "card:dup:lag", pid[dup], 60, 3600), unit="s"
            ).to_numpy()
        )
        events = pd.concat([events, again], ignore_index=True)
    return events


def _terminal_ids(seed: int, cards: pd.DataFrame, is_atm: np.ndarray) -> np.ndarray:
    """Merchants have 1-3 terminals; ATMs are numbered per home branch."""
    pid = cards["posting_id"].to_numpy()
    k = stable_int(seed, "card:term:k", pid, 1, 3)
    merchant = cards["merchant_id"].fillna(0).astype("int64").to_numpy()
    atm_branch = cards["person_id"].to_numpy()  # stable per customer: their usual ATM
    atm = system_id(seed, "card:atm", atm_branch, 10**4)
    return np.where(
        is_atm,
        [f"ATM{v:04d}" for v in atm],
        [f"T{m:06d}{t}" for m, t in zip(merchant, k, strict=True)],
    )
