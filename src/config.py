"""
Configuration management for the Vertex AI Workbench Fleet Upgrader.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class UpgraderConfig:
    """Configuration for fleet upgrader operations."""

    project_id: str
    locations: List[str]
    dry_run: bool = False
    max_parallel: int = 10
    timeout: int = 7200
    poll_interval: int = 20
    rollback_on_failure: bool = False
    health_check_timeout: int = 800
    stagger_delay: float = 5.0
    verbose: bool = False

    @classmethod
    def from_args(cls, args) -> "UpgraderConfig":
        """
        Create configuration from command-line arguments.

        Args:
            args: Parsed argparse arguments

        Returns:
            UpgraderConfig instance
        """
        return cls(
            project_id=args.project,
            locations=args.locations,
            dry_run=args.dry_run,
            max_parallel=args.max_parallel,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            rollback_on_failure=args.rollback_on_failure,
            health_check_timeout=args.health_check_timeout,
            stagger_delay=args.stagger_delay,
            verbose=args.verbose,
        )
