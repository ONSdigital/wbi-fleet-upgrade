"""
Data models for the Vertex AI Workbench Fleet Upgrader.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class InstanceRef:
    """Reference to a Workbench instance."""

    name: str  # full resource name: projects/.../locations/.../instances/...
    short_name: str  # instance id
    location: str  # zone location


@dataclass
class UpgradeResult:
    """Result of an upgrade operation."""

    instance_name: str
    location: str
    status: str  # "success", "failed", "skipped", "up_to_date", "dry_run"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration_seconds: Optional[float] = None
    target_version: Optional[str] = None
    error_message: Optional[str] = None
    rolled_back: bool = False


@dataclass
class TrackedOp:
    """Tracked upgrade operation."""

    op_name: str  # operations/<id> (full name returned by API)
    instance: InstanceRef
    start_time: float
    target_version: str = ""
