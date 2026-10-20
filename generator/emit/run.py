"""Backfill and daily ticks: build the world, project every source, write the landing zone.

State (profile, seed, config fingerprint, and the cursor date) lives in `<out>/state.json`.
A tick emits the day after the cursor and advances it. Re-emitting a day rewrites the same
files with the same content, so recovering from a failed run is just running it again.
"""

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from generator import answer_key
from generator.config import DEFAULT_CONFIG_PATH, WorldConfig
from generator.emit.local import LandingWriter
from generator.projectors import (
    card_switch,
    compliance,
    core_banking,
    credit_bureau,
    crm,
    general_ledger,
    loan_origination,
    mobile_banking,
    reference_data,
)
from generator.timeline import delta, snapshot
from generator.world import World

# Batch sources share one signature: (world, since | None, until) -> SourceBatch.
_DAILY_SOURCES = (
    loan_origination.extracts,
    crm.exports,
    general_ledger.monthly_statements,
    credit_bureau.weekly_reports,
    compliance.monthly_lists,
    reference_data.fx_rates,
)


@dataclass(frozen=True)
class State:
    """Where a landing zone is up to."""

    profile: str
    seed: int
    config_fingerprint: str
    cursor: dt.date

    def save(self, out: Path) -> None:
        """Write the state to `<out>/state.json`."""
        payload = {**self.__dict__, "cursor": self.cursor.isoformat()}
        (out / "state.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, out: Path) -> "State":
        """Read the state from `<out>/state.json`."""
        raw = json.loads((out / "state.json").read_text(encoding="utf-8"))
        return cls(
            raw["profile"],
            raw["seed"],
            raw["config_fingerprint"],
            dt.date.fromisoformat(raw["cursor"]),
        )


def fingerprint(config_path: Path = DEFAULT_CONFIG_PATH) -> str:
    """Short hash of the config file: a tick against a different config is refused."""
    return hashlib.sha256(config_path.read_bytes()).hexdigest()[:16]


def backfill(world: World, writer: LandingWriter, answer_key_dir: Path | None) -> int:
    """Emit everything up to `config.today` and (optionally) the answer key. Returns file count."""
    today = world.config.today
    files = writer.write(
        core_banking.snapshot_batch(world, snapshot(world, today)), f"full_{today:%Y%m%d}"
    )
    until = pd.Timestamp(today)
    files += writer.write(card_switch.events_delivered(world, None, until), "")
    files += writer.write(mobile_banking.events_delivered(world, None, until), "")
    for source in _DAILY_SOURCES:
        files += writer.write(source(world, None, today), "")
    if answer_key_dir is not None:
        files += answer_key.write(world, answer_key_dir)
    return len(files)


def tick(world: World, writer: LandingWriter, since: dt.date) -> int:
    """Emit the single day after `since`. Returns the number of files written."""
    until = since + dt.timedelta(days=1)
    if until > world.config.end_date:
        raise ValueError(f"cannot tick past the simulation horizon {world.config.end_date}")
    files = writer.write(
        core_banking.delta_batch(world, delta(world, since, until)), f"cdc_{until:%Y%m%d}"
    )
    lo, hi = pd.Timestamp(since), pd.Timestamp(until)
    files += writer.write(card_switch.events_delivered(world, lo, hi), "")
    files += writer.write(mobile_banking.events_delivered(world, lo, hi), "")
    for source in _DAILY_SOURCES:
        files += writer.write(source(world, since, until), "")
    return len(files)


def new_state(config: WorldConfig) -> State:
    """State for a fresh backfill: cursor at the simulated today."""
    return State(config.profile, config.seed, fingerprint(), config.today)
