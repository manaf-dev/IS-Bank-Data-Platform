"""Command line for the synthetic source-system generator.

    uv run python -m generator summary  --profile dev [--as-of 2026-01-15]
    uv run python -m generator backfill --profile dev --out output/dev
    uv run python -m generator tick     --out output/dev [--days 3]

CRM files go to `--crm-dir`, else $ISBANK_CRM_DRIVE_DIR (e.g. a Google Drive for desktop
folder), else `<out>/crm`. The answer key goes to `answer_key/<profile>` by default.
"""

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

from generator.config import load_config
from generator.emit.local import LandingWriter
from generator.emit.run import State, backfill, fingerprint, new_state, tick
from generator.timeline import snapshot
from generator.world import build_world


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns a process exit code."""
    parser = argparse.ArgumentParser(prog="python -m generator")
    sub = parser.add_subparsers(dest="command", required=True)

    summary = sub.add_parser("summary", help="build the world and print what is visible")
    summary.add_argument("--profile", default="dev")
    summary.add_argument("--as-of", type=dt.date.fromisoformat)

    fill = sub.add_parser("backfill", help="emit all sources up to the simulated today")
    fill.add_argument("--profile", default="dev")
    fill.add_argument("--out", type=Path, required=True)
    fill.add_argument("--crm-dir", type=Path)
    fill.add_argument("--answer-key-dir", type=Path)
    fill.add_argument("--force", action="store_true", help="allow writing into a non-empty --out")

    step = sub.add_parser("tick", help="emit the next simulated day(s) after the cursor")
    step.add_argument("--out", type=Path, required=True)
    step.add_argument("--days", type=int, default=1)
    step.add_argument("--crm-dir", type=Path)

    args = parser.parse_args(argv)
    if args.command == "summary":
        return _summary(args)
    if args.command == "backfill":
        return _backfill(args)
    return _tick(args)


def _writer(out: Path, crm_dir: Path | None) -> LandingWriter:
    crm = crm_dir or (
        Path(os.environ["ISBANK_CRM_DRIVE_DIR"]) if os.environ.get("ISBANK_CRM_DRIVE_DIR") else None
    )
    return LandingWriter(root=out / "landing", redirect={"crm": crm} if crm else {})


def _summary(args) -> int:
    config = load_config(profile=args.profile)
    started = time.perf_counter()
    world = build_world(config)
    as_of = args.as_of or config.today
    view = snapshot(world, as_of)
    elapsed = time.perf_counter() - started
    print(f"profile {config.profile} | seed {config.seed} | built in {elapsed:.1f}s")
    print(f"simulated {config.start_date}..{config.end_date}; viewing as of {as_of}")
    rows = {
        "customers": len(view.customers),
        "accounts": len(view.accounts),
        "accounts closed": int(view.accounts["closed_on"].notna().sum()),
        "postings": len(view.postings),
        "complaints": len(view.complaints),
        "merchants": len(view.merchants),
    }
    width = max(map(len, rows))
    for name, count in rows.items():
        print(f"  {name:<{width}}  {count:>12,}")
    return 0


def _backfill(args) -> int:
    out: Path = args.out
    if out.exists() and any(out.iterdir()) and not args.force:
        print(f"{out} is not empty; pass --force to write into it", file=sys.stderr)
        return 2
    config = load_config(profile=args.profile)
    started = time.perf_counter()
    world = build_world(config)
    out.mkdir(parents=True, exist_ok=True)
    key_dir = args.answer_key_dir or Path("answer_key") / config.profile
    n = backfill(world, _writer(out, args.crm_dir), key_dir)
    new_state(config).save(out)
    print(f"backfill to {config.today}: {n:,} files in {time.perf_counter() - started:.0f}s")
    print(f"landing zone: {out / 'landing'}   answer key: {key_dir}")
    return 0


def _tick(args) -> int:
    out: Path = args.out
    state = State.load(out)
    config = load_config(profile=state.profile)
    if config.seed != state.seed or fingerprint() != state.config_fingerprint:
        print(
            "config or seed changed since the backfill; re-run backfill into a new --out",
            file=sys.stderr,
        )
        return 2
    world = build_world(config)
    writer = _writer(out, args.crm_dir)
    cursor = state.cursor
    for _ in range(args.days):
        n = tick(world, writer, cursor)
        cursor = cursor + dt.timedelta(days=1)
        State(state.profile, state.seed, state.config_fingerprint, cursor).save(out)
        print(f"tick {cursor}: {n} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
