"""Mobile and online banking: app clickstream events as JSON lines.

The app knows users by its own user id and sees devices, IP addresses, logins, and the
actions taken in a session (transfers, bill payments, MoMo moves, new payees). It does not
see branch or card activity. Flaws and drift:

- late and out-of-order delivery (events are filed by the day they arrive)
- app 3.0 (from `app_v3_from`) adds a `biometric_used` field: older events lack it
- account-takeover sessions start with failed logins from an unknown device, add a new
  payee, then transfer; genuine users sometimes fail a login or add a payee too
"""

import zlib

import numpy as np
import pandas as pd

from generator.projectors.common import (
    SourceBatch,
    stable_choice,
    stable_int,
    stable_uniform,
    system_id,
)
from generator.world import World

SYSTEM = "mobile_banking"
_LOOKBACK = pd.Timedelta(days=2)
_ACTION = {
    "TRANSFER_OUT": "TRANSFER",
    "BILL_PAYMENT": "BILL_PAYMENT",
    "BANK_TO_MOMO": "MOMO_SEND",
    "MOMO_TO_BANK": "MOMO_PULL",
}
_KIND = {
    "LOGIN_FAILED": 1,
    "LOGIN_SUCCESS": 2,
    "BENEFICIARY_ADDED": 3,
    "ACTION": 4,
    "LOGOUT": 5,
    "BROWSE_LOGIN": 6,
    "BROWSE_LOGOUT": 7,
}


def events_delivered(world: World, since: pd.Timestamp | None, until: pd.Timestamp) -> SourceBatch:
    """App events delivered after `since` (None = from the start) up to the end of `until`."""
    builder = _Events(world)
    hi = until + pd.Timedelta(days=1)
    lo_event = pd.Timestamp(world.config.start_date) if since is None else since - _LOOKBACK
    events = pd.concat(
        [builder.session_events(lo_event, hi), builder.browse_events(lo_event, hi)],
        ignore_index=True,
    )
    events = builder.finish(events)
    lo = pd.Timestamp.min if since is None else since + pd.Timedelta(days=1)
    events = events[(events["delivered_at"] >= lo) & (events["delivered_at"] < hi)]
    batch = SourceBatch(SYSTEM)
    batch.add(
        "app_events", events.sort_values(["delivered_at", "event_id"], kind="stable"), "jsonl"
    )
    return batch


class _Events:
    def __init__(self, world: World) -> None:
        self.world = world
        self.seed = world.config.seed
        self.p = world.config.projectors[SYSTEM]
        branch_city = {b.branch_code: b.city for b in world.config.branches}
        self.home_city = world.customers.set_index("person_id")["home_branch_code"].map(branch_city)
        self.devices = world.scenarios.devices
        self.ring_devices = {
            d for devices in world.scenarios.fraud_rings["devices"] for d in devices
        }

    # ------------------------------------------------------------------ sessions with actions

    def session_events(self, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
        seed = self.seed
        postings = self.world.ledger.postings
        actions = postings[
            postings["device_id"].notna()
            & postings["txn_type"].isin(list(_ACTION))
            & (postings["posted_at"] >= lo)
            & (postings["posted_at"] < hi)
        ]
        if actions.empty:
            return pd.DataFrame()
        pid = actions["posting_id"].to_numpy()
        ts = actions["posted_at"]
        takeover = actions["fraud_type"].astype(object).eq("account_takeover").to_numpy()

        base = pd.DataFrame(
            {
                "person_id": actions["person_id"].to_numpy(),
                "device_id": actions["device_id"].to_numpy(dtype=object),
                "source_id": pid,
                "takeover": takeover,
            }
        )
        login_at = ts - pd.to_timedelta(stable_int(seed, "mb:login", pid, 60, 300), unit="s")
        logout_at = ts + pd.to_timedelta(stable_int(seed, "mb:logout", pid, 60, 600), unit="s")
        frames = [
            base.assign(kind="LOGIN_SUCCESS", seq=0, ts=login_at.to_numpy(), status="SUCCESS"),
            base.assign(
                kind="ACTION",
                seq=0,
                ts=ts.to_numpy(),
                event_type=actions["txn_type"].astype(str).map(_ACTION).to_numpy(),
                status=np.where(actions["status"].astype(str) == "POSTED", "SUCCESS", "FAILED"),
                amount_minor=-actions["amount_pesewas"].to_numpy(),
                beneficiary_account=actions["counterparty_account"].to_numpy(dtype=object),
                beneficiary_bank=actions["counterparty"].to_numpy(dtype=object),
            ),
            base.assign(kind="LOGOUT", seq=0, ts=logout_at.to_numpy(), status="SUCCESS"),
        ]

        # Failed logins: a few genuine ones, several before every takeover session.
        fail_p = self.p["failed_login_share"]
        genuine_fail = ~takeover & (stable_uniform(seed, "mb:fail", pid) < fail_p)
        k = stable_int(
            seed,
            "mb:atofail",
            pid,
            self.p["takeover_failed_logins"]["min"],
            self.p["takeover_failed_logins"]["max"],
        )
        counts = np.where(takeover, k, genuine_fail.astype(int))
        rep = np.repeat(np.arange(len(base)), counts)
        if rep.size:
            seq = np.concatenate([np.arange(c) for c in counts if c])
            fail_at = (
                login_at.to_numpy()[rep] - pd.to_timedelta((seq + 1) * 40, unit="s").to_numpy()
            )
            frames.append(
                base.iloc[rep].assign(kind="LOGIN_FAILED", seq=seq, ts=fail_at, status="FAILED")
            )

        # A new payee is added just before the first transfer to it.
        first = self._first_transfers()
        is_first = actions["posting_id"].isin(first).to_numpy()
        if is_first.any():
            add_at = ts[is_first] - pd.to_timedelta(
                stable_int(seed, "mb:benef", pid[is_first], 10, 50), unit="s"
            )
            frames.append(
                base[is_first].assign(
                    kind="BENEFICIARY_ADDED",
                    seq=0,
                    ts=add_at.to_numpy(),
                    status="SUCCESS",
                    beneficiary_account=actions.loc[is_first, "counterparty_account"].to_numpy(
                        dtype=object
                    ),
                    beneficiary_bank=actions.loc[is_first, "counterparty"].to_numpy(dtype=object),
                )
            )
        return pd.concat(frames, ignore_index=True)

    def _first_transfers(self) -> set[int]:
        """Posting ids of each customer's first transfer to each payee (over all history)."""
        p = self.world.ledger.postings
        out = p[(p["txn_type"] == "TRANSFER_OUT") & p["counterparty_account"].notna()]
        first = out.drop_duplicates(["person_id", "counterparty_account"], keep="first")
        return set(first["posting_id"])

    # ------------------------------------------------------------------ browse-only sessions

    def browse_events(self, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
        """Logins without a transaction: a stable number per user per month."""
        seed, mean = self.seed, self.p["extra_logins_per_user_per_month"]
        devices = self.devices.drop_duplicates("person_id")
        months = pd.period_range(lo, hi - pd.Timedelta(seconds=1), freq="M")
        frames = []
        churn = self.world.scenarios.churn.set_index("person_id")["churn_date"]
        for month in months:
            start = month.start_time
            users = devices[devices["first_seen"] <= start]
            if users.empty:
                continue
            key = users["person_id"].to_numpy() * 1000 + (month.year * 12 + month.month) % 1000
            counts = stable_int(seed, "mb:browse:n", key, 0, 2 * mean)
            rep = np.repeat(np.arange(len(users)), counts)
            seq = np.concatenate([np.arange(c) for c in counts if c]) if rep.size else rep
            ekey = key[rep] * 100 + seq
            offset = stable_int(seed, "mb:browse:t", ekey, 0, month.days_in_month * 86_400 - 1)
            ts = start + pd.to_timedelta(offset, unit="s")
            person = users["person_id"].to_numpy()[rep]
            keep = (ts >= lo) & (ts < hi) & (ts < person_churn(churn, person))
            base = pd.DataFrame(
                {
                    "person_id": person[keep],
                    "device_id": users["device_id"].to_numpy()[rep][keep],
                    "source_id": ekey[keep],
                    "takeover": False,
                }
            )
            frames.append(base.assign(kind="BROWSE_LOGIN", seq=0, ts=ts[keep], status="SUCCESS"))
            out_at = ts[keep] + pd.to_timedelta(
                stable_int(seed, "mb:browse:out", ekey[keep], 30, 900), unit="s"
            )
            frames.append(base.assign(kind="BROWSE_LOGOUT", seq=0, ts=out_at, status="SUCCESS"))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # ------------------------------------------------------------------ shared fields

    def finish(self, ev: pd.DataFrame) -> pd.DataFrame:
        seed, p = self.seed, self.p
        kind_code = ev["kind"].map(_KIND).to_numpy()
        key = ev["source_id"].to_numpy() * 64 + kind_code * 8 + ev["seq"].to_numpy()
        event_type = ev.get("event_type", pd.Series(index=ev.index, dtype=object))
        event_type = event_type.fillna(
            ev["kind"].replace({"BROWSE_LOGIN": "LOGIN_SUCCESS", "BROWSE_LOGOUT": "LOGOUT"})
        )
        ts = pd.to_datetime(ev["ts"])
        device = ev["device_id"].astype(object)
        is_ring = device.isin(self.ring_devices).to_numpy()
        v3 = (ts >= pd.Timestamp(p["app_v3_from"])).to_numpy()
        biometric = np.where(
            v3, np.where(is_ring, False, stable_uniform(seed, "mb:bio", key) < 0.6), None
        )
        late = stable_uniform(seed, "mb:late", key) < p["late_event_share"]
        lag = np.where(
            late,
            stable_int(seed, "mb:late:min", key, p["late_minutes"]["min"], p["late_minutes"]["max"])
            * 60,
            stable_int(seed, "mb:lag", key, 0, 5),
        )
        # crc32, not hash(): Python's str hash is randomised per process.
        device_hash = np.array([zlib.crc32(str(d).encode()) for d in device], dtype="int64")
        os_ = stable_choice(
            seed, "mb:os", device_hash, list(p["device_os"]), list(p["device_os"].values())
        )
        ip_tail = stable_int(seed, "mb:ip", device_hash, 1, 254)
        ip = np.where(
            is_ring, [f"192.0.2.{v}" for v in ip_tail], [f"203.0.113.{v}" for v in ip_tail]
        )
        city = np.where(
            is_ring,
            stable_choice(
                seed, "mb:ring:city", key, sorted({b.city for b in self.world.config.branches})
            ),
            ev["person_id"].map(self.home_city).to_numpy(),
        )
        return pd.DataFrame(
            {
                "event_id": [f"mb_{v:016x}" for v in system_id(seed, "mb:evt", key, 2**62)],
                "event_type": event_type.to_numpy(),
                "event_ts": ts.to_numpy(),
                "user_id": [
                    f"MB{v:07d}" for v in system_id(seed, "mb:user", ev["person_id"], 10**7)
                ],
                "device_id": device.to_numpy(),
                "device_os": os_,
                "app_version": np.where(v3, "3.1.0", "2.8.4"),
                "ip_address": ip,
                "geo_city": city,
                "status": ev["status"].to_numpy(),
                "amount_minor": ev.get(
                    "amount_minor", pd.Series(index=ev.index, dtype="float")
                ).astype("Int64"),
                "beneficiary_account": ev.get(
                    "beneficiary_account", pd.Series(index=ev.index, dtype=object)
                ).to_numpy(),
                "beneficiary_bank": ev.get(
                    "beneficiary_bank", pd.Series(index=ev.index, dtype=object)
                ).to_numpy(),
                "biometric_used": biometric,
                "delivered_at": (ts + pd.to_timedelta(lag, unit="s")).to_numpy(),
            }
        )


def person_churn(churn: pd.Series, person: np.ndarray) -> np.ndarray:
    """Churn timestamp per person (far future if they never leave)."""
    far = pd.Timestamp("2262-01-01")
    return pd.Series(person).map(churn).fillna(far).to_numpy()
