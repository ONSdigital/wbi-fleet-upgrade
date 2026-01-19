"""Console entry point for the Workbench Fleet Upgrader CLI."""

from __future__ import annotations

import argparse
from typing import List

from upgrader import FleetUpgrader
from config import UpgraderConfig
from log_utils import setup_logging
from rollback import FleetRollback


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    pkg_doc = "Workbench Fleet Upgrader & Rollback CLI"

    parser = argparse.ArgumentParser(
        description=(pkg_doc or "Workbench Fleet Upgrader & Rollback CLI").strip()
    )
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument(
        "--locations",
        required=True,
        nargs="+",
        help=(
            "Workbench zone locations (e.g. europe-west2-a europe-west2-b "
            "europe-west2-c)"
        ),
    )
    parser.add_argument("--instance", help="Specific instance ID (single mode)")
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Rollback mode instead of upgrading",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate / only check")
    parser.add_argument("--max-parallel", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--poll-interval", type=int, default=20)
    parser.add_argument("--rollback-on-failure", action="store_true")
    parser.add_argument("--health-check-timeout", type=int, default=600)
    parser.add_argument("--stagger-delay", type=float, default=3.0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: List[str] | None = None) -> int:
    """CLI main for console_scripts entry point."""
    parser = build_parser()
    args = parser.parse_args(args=argv)

    log_file = "workbench-rollback.log" if args.rollback else "workbench-upgrade.log"
    setup_logging(verbose=args.verbose, log_file=log_file)

    config = UpgraderConfig.from_args(args)

    if args.rollback:
        runner = FleetRollback(
            project_id=config.project_id,
            locations=config.locations,
            dry_run=config.dry_run,
            max_parallel=config.max_parallel,
            timeout=config.timeout,
            poll_interval=config.poll_interval,
            health_check_timeout=config.health_check_timeout,
            stagger_delay=config.stagger_delay,
        )
    else:
        runner = FleetUpgrader(
            project_id=config.project_id,
            locations=config.locations,
            dry_run=config.dry_run,
            max_parallel=config.max_parallel,
            timeout=config.timeout,
            poll_interval=config.poll_interval,
            rollback_on_failure=config.rollback_on_failure,
            health_check_timeout=config.health_check_timeout,
            stagger_delay=config.stagger_delay,
        )

    stats = runner.run(instance_id=args.instance)
    return 1 if stats.get("failed", 0) > 0 else 0
