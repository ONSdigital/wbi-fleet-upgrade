"""
Google Cloud Function entry point for WBI Fleet Upgrade & Rollback.

This module provides HTTP endpoints for:
- /upgrade: Upgrade Workbench instances
- /rollback: Rollback Workbench instances
- /status: Check instance status
- /health: Health check endpoint

All configuration is done via environment variables.
"""

import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

import functions_framework
from flask import Request

# Add src to path for local imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from clients import WorkbenchRestClient
from config import CloudFunctionConfig
from models import InstanceRef, UpgradeResult
from upgrader import FleetUpgrader
from rollback import FleetRollback

# Configure logging for Cloud Functions (JSON structured logging)
logging.basicConfig(
    level=logging.INFO,
    format='{"severity": "%(levelname)s", "message": "%(message)s", "timestamp": "%(asctime)s"}',
)
logger = logging.getLogger(__name__)


# =============================================================================
# Security and Validation
# =============================================================================


def validate_request(func: Callable) -> Callable:
    """
    Decorator to validate incoming requests.

    Checks:
    - Content-Type for POST requests
    - Required parameters
    - Parameter types and formats
    """

    @wraps(func)
    def wrapper(request: Request) -> Tuple[Dict[str, Any], int]:
        # Check content type for POST requests
        if request.method == "POST":
            content_type = request.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                return {
                    "error": "Invalid content type",
                    "message": "Content-Type must be application/json",
                }, 415

        return func(request)

    return wrapper


def sanitize_input(value: str, max_length: int = 256) -> str:
    """Sanitize string input to prevent injection attacks."""
    if not value:
        return ""
    # Remove null bytes and control characters
    sanitized = "".join(c for c in value if c.isprintable())
    # Truncate to max length
    return sanitized[:max_length]


def validate_project_id(project_id: str) -> bool:
    """Validate GCP project ID format."""
    import re

    # GCP project IDs: 6-30 chars, lowercase letters, digits, hyphens
    # Must start with letter, cannot end with hyphen
    pattern = r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$"
    return bool(re.match(pattern, project_id))


def validate_location(location: str) -> bool:
    """Validate GCP zone format."""
    import re

    # Format: region-zone (e.g., europe-west2-a, us-central1-b)
    pattern = r"^[a-z]+-[a-z]+\d+-[a-z]$"
    return bool(re.match(pattern, location))


def validate_instance_id(instance_id: str) -> bool:
    """Validate instance ID format."""
    import re

    # Instance IDs: 1-63 chars, lowercase letters, digits, hyphens
    pattern = r"^[a-z][a-z0-9-]{0,61}[a-z0-9]?$"
    return bool(re.match(pattern, instance_id))


# =============================================================================
# Configuration Loading
# =============================================================================


def get_config_from_request(request: Request) -> CloudFunctionConfig:
    """
    Build configuration from request body and environment variables.

    Priority: Request body > Environment variables > Defaults
    """
    # Get request JSON (may be None for GET requests)
    request_json = request.get_json(silent=True) or {}

    # Required: project_id from request or env
    project_id = sanitize_input(
        request_json.get("project_id") or os.environ.get("GCP_PROJECT_ID", "")
    )
    if not project_id:
        raise ValueError(
            "project_id is required (request body or GCP_PROJECT_ID env var)"
        )
    if not validate_project_id(project_id):
        raise ValueError(f"Invalid project_id format: {project_id}")

    # Required: locations from request or env
    locations_raw = request_json.get("locations") or os.environ.get("LOCATIONS", "")
    if isinstance(locations_raw, str):
        locations = [
            sanitize_input(loc.strip()) for loc in locations_raw.split() if loc.strip()
        ]
    elif isinstance(locations_raw, list):
        locations = [sanitize_input(loc) for loc in locations_raw if loc]
    else:
        locations = []

    if not locations:
        raise ValueError("locations is required (request body or LOCATIONS env var)")

    for loc in locations:
        if not validate_location(loc):
            raise ValueError(f"Invalid location format: {loc}")

    # Optional: instance_id for single instance mode
    instance_id = sanitize_input(request_json.get("instance_id", ""))
    if instance_id and not validate_instance_id(instance_id):
        raise ValueError(f"Invalid instance_id format: {instance_id}")

    # Optional parameters with env var fallbacks
    def get_bool(key: str, default: bool) -> bool:
        req_val = request_json.get(key)
        if req_val is not None:
            return bool(req_val)
        env_val = os.environ.get(key.upper(), "").lower()
        if env_val in ("true", "1", "yes"):
            return True
        if env_val in ("false", "0", "no"):
            return False
        return default

    def get_int(key: str, default: int) -> int:
        req_val = request_json.get(key)
        if req_val is not None:
            return int(req_val)
        env_val = os.environ.get(key.upper(), "")
        if env_val:
            return int(env_val)
        return default

    def get_float(key: str, default: float) -> float:
        req_val = request_json.get(key)
        if req_val is not None:
            return float(req_val)
        env_val = os.environ.get(key.upper(), "")
        if env_val:
            return float(env_val)
        return default

    return CloudFunctionConfig(
        project_id=project_id,
        locations=locations,
        instance_id=instance_id if instance_id else None,
        dry_run=get_bool("dry_run", True),  # Default to dry_run=True for safety
        max_parallel=min(get_int("max_parallel", 5), 20),  # Cap at 20
        timeout=min(
            get_int("timeout", 7200), 9 * 60 * 60
        ),  # Cap at 9 hours (Cloud Function max)
        poll_interval=max(get_int("poll_interval", 20), 10),  # Min 10 seconds
        rollback_on_failure=get_bool("rollback_on_failure", False),
        health_check_timeout=get_int("health_check_timeout", 600),
        stagger_delay=get_float("stagger_delay", 3.0),
    )


# =============================================================================
# Response Helpers
# =============================================================================


def create_response(
    success: bool,
    data: Optional[Dict] = None,
    error: Optional[str] = None,
    message: Optional[str] = None,
    status_code: int = 200,
) -> Tuple[Dict[str, Any], int]:
    """Create a standardized API response."""
    response = {
        "success": success,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    if data:
        response["data"] = data
    if error:
        response["error"] = error
    if message:
        response["message"] = message

    return response, status_code


def format_results(results: List[UpgradeResult], stats: Dict) -> Dict[str, Any]:
    """Format operation results for API response."""
    return {
        "statistics": stats,
        "results": [
            {
                "instance_name": r.instance_name,
                "location": r.location,
                "status": r.status,
                "duration_seconds": r.duration_seconds,
                "target_version": r.target_version,
                "error_message": r.error_message,
                "rolled_back": r.rolled_back,
            }
            for r in results
        ],
    }


# =============================================================================
# HTTP Endpoint Handlers
# =============================================================================


@functions_framework.http
def main(request: Request) -> Tuple[Dict[str, Any], int]:
    """
    Main entry point for Cloud Function.

    Routes requests based on path:
    - POST /upgrade: Upgrade instances
    - POST /rollback: Rollback instances
    - GET /status: Get instance status
    - GET /health: Health check
    - GET /: API info
    """
    path = request.path.rstrip("/")

    # Route based on path
    routes = {
        "": handle_info,
        "/": handle_info,
        "/upgrade": handle_upgrade,
        "/rollback": handle_rollback,
        "/status": handle_status,
        "/health": handle_health,
        "/check-upgradability": handle_check_upgradability,
    }

    handler = routes.get(path)
    if not handler:
        return create_response(
            success=False,
            error="Not Found",
            message=f"Unknown endpoint: {path}",
            status_code=404,
        )

    try:
        return handler(request)
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return create_response(
            success=False,
            error="Validation Error",
            message=str(e),
            status_code=400,
        )
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return create_response(
            success=False,
            error="Internal Server Error",
            message="An unexpected error occurred. Check Cloud Function logs for details.",
            status_code=500,
        )


def handle_info(request: Request) -> Tuple[Dict[str, Any], int]:
    """Handle API info request."""
    return create_response(
        success=True,
        data={
            "service": "WBI Fleet Upgrade & Rollback",
            "version": os.environ.get("APP_VERSION", "1.0.0"),
            "endpoints": {
                "POST /upgrade": "Upgrade Workbench instances",
                "POST /rollback": "Rollback Workbench instances",
                "GET /status": "Get instance status",
                "GET /check-upgradability": "Check if instances are upgradeable",
                "GET /health": "Health check",
            },
            "documentation": "https://github.com/erayguner/wbi-fleet-upgrade",
        },
    )


@validate_request
def handle_upgrade(request: Request) -> Tuple[Dict[str, Any], int]:
    """
    Handle upgrade request.

    Request body:
    {
        "project_id": "my-project",  // or use GCP_PROJECT_ID env var
        "locations": ["europe-west2-a"],  // or use LOCATIONS env var
        "instance_id": "my-instance",  // optional, for single instance mode
        "dry_run": true,  // default: true
        "max_parallel": 5,
        "timeout": 7200,
        "rollback_on_failure": false
    }
    """
    if request.method != "POST":
        return create_response(
            success=False,
            error="Method Not Allowed",
            message="Use POST for upgrade operations",
            status_code=405,
        )

    config = get_config_from_request(request)

    logger.info(
        f"Starting upgrade: project={config.project_id}, locations={config.locations}, "
        f"instance={config.instance_id}, dry_run={config.dry_run}"
    )

    upgrader = FleetUpgrader(
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

    stats = upgrader.run(instance_id=config.instance_id)

    # Determine success based on failures
    success = stats.get("failed", 0) == 0

    return create_response(
        success=success,
        data=format_results(upgrader.results, stats),
        message="Upgrade completed" if not config.dry_run else "Dry run completed",
        status_code=200 if success else 207,  # 207 Multi-Status for partial failures
    )


@validate_request
def handle_rollback(request: Request) -> Tuple[Dict[str, Any], int]:
    """
    Handle rollback request.

    Request body:
    {
        "project_id": "my-project",
        "locations": ["europe-west2-a"],
        "instance_id": "my-instance",  // optional
        "dry_run": true
    }
    """
    if request.method != "POST":
        return create_response(
            success=False,
            error="Method Not Allowed",
            message="Use POST for rollback operations",
            status_code=405,
        )

    config = get_config_from_request(request)

    logger.info(
        f"Starting rollback: project={config.project_id}, locations={config.locations}, "
        f"instance={config.instance_id}, dry_run={config.dry_run}"
    )

    rollback = FleetRollback(
        project_id=config.project_id,
        locations=config.locations,
        dry_run=config.dry_run,
        max_parallel=config.max_parallel,
        timeout=config.timeout,
        poll_interval=config.poll_interval,
        health_check_timeout=config.health_check_timeout,
        stagger_delay=config.stagger_delay,
    )

    stats = rollback.run(instance_id=config.instance_id)

    success = stats.get("failed", 0) == 0

    return create_response(
        success=success,
        data=format_results(rollback.results, stats),
        message="Rollback completed" if not config.dry_run else "Dry run completed",
        status_code=200 if success else 207,
    )


@validate_request
def handle_status(request: Request) -> Tuple[Dict[str, Any], int]:
    """
    Handle status request - get current state of instances.

    Query parameters or JSON body:
    - project_id: GCP project ID
    - locations: List of zones
    - instance_id: Optional specific instance
    """
    config = get_config_from_request(request)

    client = WorkbenchRestClient(project_id=config.project_id)

    instances = []

    if config.instance_id:
        # Single instance mode
        for loc in config.locations:
            try:
                inst = client.get_instance_by_name(config.instance_id, loc)
                if inst:
                    data = client.get_instance(inst.name)
                    instances.append(
                        {
                            "name": inst.short_name,
                            "location": inst.location,
                            "state": data.get("state"),
                            "health_state": data.get("healthState"),
                            "create_time": data.get("createTime"),
                            "update_time": data.get("updateTime"),
                        }
                    )
                    break
            except Exception as e:
                logger.debug(f"Instance not found in {loc}: {e}")
    else:
        # Fleet mode
        for loc in config.locations:
            try:
                insts = client.list_instances(loc)
                for inst in insts:
                    try:
                        data = client.get_instance(inst.name)
                        instances.append(
                            {
                                "name": inst.short_name,
                                "location": inst.location,
                                "state": data.get("state"),
                                "health_state": data.get("healthState"),
                                "create_time": data.get("createTime"),
                                "update_time": data.get("updateTime"),
                            }
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to get details for {inst.short_name}: {e}"
                        )
            except Exception as e:
                logger.error(f"Failed to list instances in {loc}: {e}")

    return create_response(
        success=True,
        data={
            "project_id": config.project_id,
            "locations": config.locations,
            "instance_count": len(instances),
            "instances": instances,
        },
    )


@validate_request
def handle_check_upgradability(request: Request) -> Tuple[Dict[str, Any], int]:
    """
    Check which instances are upgradeable.

    Query parameters or JSON body:
    - project_id: GCP project ID
    - locations: List of zones
    - instance_id: Optional specific instance
    """
    config = get_config_from_request(request)

    client = WorkbenchRestClient(project_id=config.project_id)

    results = []

    if config.instance_id:
        for loc in config.locations:
            try:
                inst = client.get_instance_by_name(config.instance_id, loc)
                if inst:
                    upgradeable, info = client.check_upgradability(inst.name)
                    results.append(
                        {
                            "name": inst.short_name,
                            "location": inst.location,
                            "upgradeable": upgradeable,
                            "target_version": info if upgradeable else None,
                        }
                    )
                    break
            except Exception as e:
                logger.debug(f"Instance not found in {loc}: {e}")
    else:
        for loc in config.locations:
            try:
                insts = client.list_instances(loc)
                for inst in insts:
                    try:
                        upgradeable, info = client.check_upgradability(inst.name)
                        results.append(
                            {
                                "name": inst.short_name,
                                "location": inst.location,
                                "upgradeable": upgradeable,
                                "target_version": info if upgradeable else None,
                            }
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to check upgradability for {inst.short_name}: {e}"
                        )
                        results.append(
                            {
                                "name": inst.short_name,
                                "location": inst.location,
                                "upgradeable": None,
                                "error": str(e),
                            }
                        )
            except Exception as e:
                logger.error(f"Failed to list instances in {loc}: {e}")

    upgradeable_count = sum(1 for r in results if r.get("upgradeable"))

    return create_response(
        success=True,
        data={
            "project_id": config.project_id,
            "locations": config.locations,
            "total_instances": len(results),
            "upgradeable_count": upgradeable_count,
            "instances": results,
        },
    )


def handle_health(request: Request) -> Tuple[Dict[str, Any], int]:
    """Handle health check request."""
    # Basic health check - verify we can import dependencies
    try:
        import google.auth

        return create_response(
            success=True,
            data={"status": "healthy"},
        )
    except Exception as e:
        return create_response(
            success=False,
            error="Unhealthy",
            message=str(e),
            status_code=503,
        )
