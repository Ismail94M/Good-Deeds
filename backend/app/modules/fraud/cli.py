"""Fraud command line (docs/M5-DESIGN.md D82). Manual detector re-runs; there is no HTTP route.

    uv run python -m app.modules.fraud.cli sweep --detector reciprocal|ring --since YYYY-MM-DD

Re-checks every deed verified from ``--since`` (00:00 in the ``fraud`` config's time zone) until
now, as if each had just been verified, and records a ``fraud_detector_runs`` row (trigger
``cli``). Idempotent: cases and deeds already raised don't change. A detector switched off in
config is not run.

In Docker: ``docker compose exec api python -m app.modules.fraud.cli sweep ...``.
"""

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime

from app.core.db import SessionLocal
from app.modules.fraud import detectors
from app.modules.fraud._detect import sweep_detector
from app.modules.fraud._limits import load_fraud
from app.modules.fraud.detectors.base import SweepWindow
from app.modules.fraud.models import DetectorRunTrigger


async def _sweep(name: str, since: date) -> int:
    detector = detectors.detector_by_name(name)
    if detector is None:  # argparse choices already guard this
        print(f"Unknown detector {name!r}.", file=sys.stderr)
        return 2
    async with SessionLocal() as session:
        _, config = await load_fraud(session)
    start = datetime(since.year, since.month, since.day, tzinfo=config.zone).astimezone(UTC)
    now = datetime.now(UTC)
    if start >= now:
        print("--since must be in the past.", file=sys.stderr)
        return 2
    run = await sweep_detector(
        detector, SweepWindow(since=start, until=now), DetectorRunTrigger.CLI
    )
    if run is None:
        print(f"Detector {name} is switched off in the fraud config; nothing ran.")
        return 1
    print(
        f"{name}: {run.status.value}, {run.edges_scanned} edges scanned, "
        f"{run.cases_opened} cases opened, {run.deeds_added} deeds added (run {run.id})."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules.fraud.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("sweep", help="re-run one detector over deeds verified since a date")
    cmd.add_argument("--detector", required=True, choices=[d.name for d in detectors.DETECTORS])
    cmd.add_argument("--since", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    args = parser.parse_args(argv)
    run = _sweep(args.detector, args.since)
    if sys.platform == "win32":
        # psycopg's async mode needs a selector event loop on Windows.
        return asyncio.run(run, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(run)


if __name__ == "__main__":
    raise SystemExit(main())
