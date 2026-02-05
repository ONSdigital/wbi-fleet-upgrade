"""
Configuration management for the Cloud Function.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CloudFunctionConfig:
    """Configuration for Cloud Function operations."""

    project_id: str
    locations: List[str]
    instance_id: Optional[str] = None
    dry_run: bool = True  # Default to True for safety
    max_parallel: int = 5
    timeout: int = 7200
    poll_interval: int = 20
    rollback_on_failure: bool = False
    health_check_timeout: int = 600
    stagger_delay: float = 3.0
