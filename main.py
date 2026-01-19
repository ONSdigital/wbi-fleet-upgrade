#!/usr/bin/env python3
"""
Vertex AI Workbench Instances Fleet Upgrade & Rollback Tool (REST v2)

- Upgrade instances (default)
- Rollback instances with --rollback

This script supports running directly from a source checkout that uses a
modern src/ layout. If the package is not installed and `fleet_upgrader`
cannot be imported, it will add the local `src/` directory to sys.path and
retry the import. For production use, prefer installing the project and
using the provided console script.
"""

import argparse
import os
import sys
from typing import List

# Add src/ to path to import modules directly
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_PATH = os.path.join(REPO_ROOT, "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from upgrader import FleetUpgrader
from config import UpgraderConfig
from log_utils import setup_logging
from rollback import FleetRollback


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Vertex AI Workbench Instances Fleet Upgrade & Rollback Tool (REST v2)"
        )
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
    parser.add_argument(
        "--instance",
        help="Specific instance ID (single instance mode)",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help=(
            "Rollback mode - revert instances to previous version instead of "
            "upgrading"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate / only check")
    parser.add_argument(
        "--max-parallel", type=int, default=5, help="Max concurrent operations"
    )
    parser.add_argument(
        "--timeout", type=int, default=7200, help="Timeout per instance (seconds)"
    )
    parser.add_argument(
        "--poll-interval", type=int, default=20, help="Seconds between polls"
    )
    parser.add_argument(
        "--rollback-on-failure",
        action="store_true",
        help=(
            "Attempt rollback if an upgrade fails or times out (upgrade mode " "only)"
        ),
    )
    parser.add_argument(
        "--health-check-timeout",
        type=int,
        default=600,
        help=(
            "Timeout waiting for instance to become ACTIVE after operation " "(seconds)"
        ),
    )
    parser.add_argument(
        "--stagger-delay",
        type=float,
        default=3.0,
        help="Delay between starting operations to avoid API throttling (seconds)",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    return parser


def main(argv: List[str] | None = None) -> None:
    """Entry point for CLI execution."""
    parser = build_parser()
    args = parser.parse_args(args=argv)

    # Use a dedicated log file for rollback runs (optional but nice)
    log_file = "workbench-rollback.log" if args.rollback else "workbench-upgrade.log"
    setup_logging(verbose=args.verbose, log_file=log_file)

    # Build config (assuming UpgraderConfig expects these arg names)
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
        stats = runner.run(instance_id=args.instance)
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

    sys.exit(1 if stats.get("failed", 0) > 0 else 0)


if __name__ == "__main__":
    main()
