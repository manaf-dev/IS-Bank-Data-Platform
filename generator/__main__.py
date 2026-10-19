"""Command line for the synthetic source-system generator.

uv run python -m generator summary --profile dev
uv run python -m generator summary --profile dev --as-of 2026-01-15
"""

import argparse
import datetime as dt
import sys
import time

from generator.config import load_config
from generator.timeline import snapshot
from generator.world import build_world


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns a process exit code."""
    parser = argparse.ArgumentParser(prog="python -m generator")
    sub = parser.add_subparsers(dest="command", required=True)
    summary = sub.add_parser("summary", help="build the world and print what is visible")
    summary.add_argument("--profile", default="dev", help="config profile (default: dev)")
    summary.add_argument(
        "--as-of", type=dt.date.fromisoformat, help="date to view (default: simulation today)"
    )
    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
