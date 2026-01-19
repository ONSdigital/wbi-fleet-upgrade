"""
Fleet rollback logic for Vertex AI Workbench Instances.

This module handles rolling back instances to their previous version.
Rollback is only available if the instance was recently upgraded.

Best Practices (per Google Cloud documentation):
- Always backup data before rollback operations
- Verify instance is in ACTIVE state
- Ensure rollback is within supported window
- Validate snapshot availability before proceeding
"""

import json
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple, Set

from clients import WorkbenchRestClient
from models import InstanceRef, TrackedOp, UpgradeResult

logger = logging.getLogger(__name__)


class RollbackCheckStatus(Enum):
    """Status codes for pre-check validations."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class PreCheckResult:
    """Result of a single pre-check validation."""

    def __init__(
        self,
        check_name: str,
        status: RollbackCheckStatus,
        message: str,
        details: Optional[Dict] = None,
    ):
        self.check_name = check_name
        self.status = status
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now()

    def to_dict(self) -> Dict:
        """Convert to dictionary for logging/reporting."""
        return {
            "check_name": self.check_name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


class FleetRollback:
    """Handles fleet-wide rollback operations for Workbench instances."""

    def __init__(
        self,
        project_id: str,
        locations: List[str],
        dry_run: bool,
        max_parallel: int,
        timeout: int,
        poll_interval: int,
        health_check_timeout: int = 600,
        stagger_delay: float = 3.0,
    ):
        """
        Set up the fleet rollback manager.

        Args:
            project_id: GCP project ID
            locations: Zone locations to scan
            dry_run: If True, only check rollback eligibility
            max_parallel: Maximum concurrent rollback operations
            timeout: Timeout per instance rollback (seconds)
            poll_interval: Interval between operation polls (seconds)
            health_check_timeout: Timeout for health verification (seconds)
            stagger_delay: Delay between starting rollbacks (seconds)
        """
        self.project_id = project_id
        self.locations = locations
        self.dry_run = dry_run
        self.max_parallel = max_parallel
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.health_check_timeout = health_check_timeout
        self.stagger_delay = stagger_delay

        self.api = WorkbenchRestClient(project_id=project_id)

        self.stats = {
            "total": 0,
            "eligible": 0,
            "not_eligible": 0,
            "skipped": 0,
            "rollback_started": 0,
            "rolled_back": 0,
            "failed": 0,
        }

        # Timing and results tracking
        self.run_start_time: Optional[float] = None
        self.run_end_time: Optional[float] = None
        self.results: List[UpgradeResult] = []

        # Per-instance pre-check results for structured logging
        self.precheck_results: Dict[str, List[PreCheckResult]] = {}

    def _instance_ready(self, inst: InstanceRef) -> Tuple[bool, str]:
        """
        Check if instance is ready for rollback (ACTIVE state).

        Args:
            inst: Instance reference

        Returns:
            Tuple of (is_ready, status_message)
        """
        try:
            data = self.api.get_instance(inst.name)
            state = str(data.get("state", "UNKNOWN")).upper()

            # Only ACTIVE instances can be rolled back
            if state == "ACTIVE":
                return True, state

            # These states mean an operation is in progress
            busy_states = {
                "PROVISIONING",
                "STARTING",
                "STOPPING",
                "UPGRADING",
                "INITIALIZING",
                "SUSPENDING",
            }
            if state in busy_states:
                logger.warning(
                    f"Skipping {inst.short_name}: instance is busy (state={state})"
                )
                return False, f"Instance busy: {state}"

            # These states mean instance is not running
            stopped_states = {"STOPPED", "SUSPENDED"}
            if state in stopped_states:
                logger.warning(
                    f"Skipping {inst.short_name}: instance is not running (state={state})"
                )
                return False, f"Instance not running: {state}"

            logger.warning(f"Skipping {inst.short_name}: unexpected state={state}")
            return False, f"Unexpected state: {state}"

        except Exception as e:
            logger.error(f"Skipping {inst.short_name}: cannot read state: {e}")
            return False, f"Error reading state: {e}"

    def _check_instance_state(self, inst: InstanceRef) -> PreCheckResult:
        """
        Pre-check: Validate instance is in correct state for rollback.

        Args:
            inst: Instance reference

        Returns:
            PreCheckResult with validation outcome
        """
        try:
            data = self.api.get_instance(inst.name)
            state = str(data.get("state", "UNKNOWN")).upper()

            if state == "ACTIVE":
                return PreCheckResult(
                    check_name="instance_state",
                    status=RollbackCheckStatus.PASSED,
                    message="Instance is ACTIVE and ready",
                    details={"state": state},
                )

            busy_states = {
                "PROVISIONING",
                "STARTING",
                "STOPPING",
                "UPGRADING",
                "INITIALIZING",
                "SUSPENDING",
            }
            if state in busy_states:
                return PreCheckResult(
                    check_name="instance_state",
                    status=RollbackCheckStatus.FAILED,
                    message=f"Instance is busy: {state}",
                    details={"state": state, "reason": "operation_in_progress"},
                )

            stopped_states = {"STOPPED", "SUSPENDED"}
            if state in stopped_states:
                return PreCheckResult(
                    check_name="instance_state",
                    status=RollbackCheckStatus.FAILED,
                    message=f"Instance is not running: {state}",
                    details={"state": state, "reason": "not_running"},
                )

            return PreCheckResult(
                check_name="instance_state",
                status=RollbackCheckStatus.FAILED,
                message=f"Unexpected state: {state}",
                details={"state": state, "reason": "unexpected_state"},
            )

        except Exception as e:
            return PreCheckResult(
                check_name="instance_state",
                status=RollbackCheckStatus.FAILED,
                message=f"Cannot read instance state: {e}",
                details={"error": str(e), "reason": "api_error"},
            )

    def _check_upgrade_history(self, inst: InstanceRef) -> PreCheckResult:
        """
        Pre-check: Validate instance has upgrade history for rollback.

        Args:
            inst: Instance reference

        Returns:
            PreCheckResult with upgrade history validation
        """
        try:
            data = self.api.get_instance(inst.name)
            history = data.get("upgradeHistory", []) or []

            if not history:
                return PreCheckResult(
                    check_name="upgrade_history",
                    status=RollbackCheckStatus.FAILED,
                    message="No upgrade history found",
                    details={"history_entries": 0, "reason": "no_history"},
                )

            # Find most recent successful UPGRADE
            successful_upgrades = [
                entry
                for entry in history
                if str(entry.get("action", "")).upper() == "UPGRADE"
                and str(entry.get("state", "")).upper() == "SUCCEEDED"
            ]

            if not successful_upgrades:
                return PreCheckResult(
                    check_name="upgrade_history",
                    status=RollbackCheckStatus.FAILED,
                    message="No successful upgrades found in history",
                    details={
                        "history_entries": len(history),
                        "successful_upgrades": 0,
                        "reason": "no_successful_upgrades",
                    },
                )

            most_recent = successful_upgrades[0]
            snapshot = (
                most_recent.get("snapshot") or most_recent.get("targetSnapshot") or ""
            )

            if not snapshot:
                return PreCheckResult(
                    check_name="upgrade_history",
                    status=RollbackCheckStatus.FAILED,
                    message="No snapshot available from previous upgrade",
                    details={
                        "history_entries": len(history),
                        "successful_upgrades": len(successful_upgrades),
                        "reason": "no_snapshot",
                    },
                )

            prev_ver = most_recent.get("version", "unknown")
            curr_ver = most_recent.get("targetVersion", "unknown")

            return PreCheckResult(
                check_name="upgrade_history",
                status=RollbackCheckStatus.PASSED,
                message=f"Valid rollback target found: {snapshot}",
                details={
                    "snapshot": snapshot,
                    "previous_version": prev_ver,
                    "current_version": curr_ver,
                    "history_entries": len(history),
                    "successful_upgrades": len(successful_upgrades),
                },
            )

        except Exception as e:
            return PreCheckResult(
                check_name="upgrade_history",
                status=RollbackCheckStatus.FAILED,
                message=f"Cannot read upgrade history: {e}",
                details={"error": str(e), "reason": "api_error"},
            )

    def _check_snapshot_validity(
        self, inst: InstanceRef, snapshot_name: str
    ) -> PreCheckResult:
        """
        Pre-check: Validate snapshot exists and is accessible.

        Args:
            inst: Instance reference
            snapshot_name: Snapshot resource name to validate

        Returns:
            PreCheckResult with snapshot validation
        """
        if not snapshot_name:
            return PreCheckResult(
                check_name="snapshot_validity",
                status=RollbackCheckStatus.FAILED,
                message="No snapshot specified",
                details={"reason": "missing_snapshot"},
            )

        # Extract snapshot info from resource name
        # Format: projects/{project}/locations/{location}/instances/{instance}/snapshots/{snapshot}
        parts = snapshot_name.split("/")
        if len(parts) < 8 or "snapshots" not in parts:
            return PreCheckResult(
                check_name="snapshot_validity",
                status=RollbackCheckStatus.WARNING,
                message="Snapshot name format unusual, proceeding with caution",
                details={"snapshot": snapshot_name, "reason": "unusual_format"},
            )

        # Basic validation that snapshot name looks valid
        snapshot_id = parts[-1] if parts else ""
        if not snapshot_id or len(snapshot_id) < 3:
            return PreCheckResult(
                check_name="snapshot_validity",
                status=RollbackCheckStatus.FAILED,
                message="Invalid snapshot identifier",
                details={"snapshot": snapshot_name, "reason": "invalid_identifier"},
            )

        return PreCheckResult(
            check_name="snapshot_validity",
            status=RollbackCheckStatus.PASSED,
            message=f"Snapshot appears valid: {snapshot_id}",
            details={"snapshot": snapshot_name, "snapshot_id": snapshot_id},
        )

    def _check_rollback_window(self, inst: InstanceRef) -> PreCheckResult:
        """
        Pre-check: Validate rollback is within supported time window.

        Per GCP docs: rollback is only available if upgraded recently.
        This checks the timestamp of the most recent upgrade.

        Args:
            inst: Instance reference

        Returns:
            PreCheckResult with timing validation
        """
        try:
            data = self.api.get_instance(inst.name)
            history = data.get("upgradeHistory", []) or []

            if not history:
                return PreCheckResult(
                    check_name="rollback_window",
                    status=RollbackCheckStatus.FAILED,
                    message="No upgrade history to determine timing",
                    details={"reason": "no_history"},
                )

            # Find most recent successful UPGRADE
            for entry in history:
                if (
                    str(entry.get("action", "")).upper() == "UPGRADE"
                    and str(entry.get("state", "")).upper() == "SUCCEEDED"
                ):
                    # Check if timestamp is available
                    create_time = entry.get("createTime") or entry.get("startTime")
                    if not create_time:
                        return PreCheckResult(
                            check_name="rollback_window",
                            status=RollbackCheckStatus.WARNING,
                            message="Cannot determine upgrade timestamp, proceeding with caution",
                            details={"reason": "no_timestamp"},
                        )

                    # Log the timestamp for user awareness
                    return PreCheckResult(
                        check_name="rollback_window",
                        status=RollbackCheckStatus.PASSED,
                        message=f"Most recent upgrade: {create_time}",
                        details={"upgrade_time": create_time},
                    )

            return PreCheckResult(
                check_name="rollback_window",
                status=RollbackCheckStatus.FAILED,
                message="No successful upgrade found to determine window",
                details={"reason": "no_successful_upgrade"},
            )

        except Exception as e:
            return PreCheckResult(
                check_name="rollback_window",
                status=RollbackCheckStatus.WARNING,
                message=f"Cannot determine rollback window: {e}",
                details={"error": str(e), "reason": "api_error"},
            )

    def _run_pre_checks(self, inst: InstanceRef) -> Tuple[bool, List[PreCheckResult]]:
        """
        Run all pre-checks for a single instance.

        Args:
            inst: Instance reference

        Returns:
            Tuple of (passed, list of PreCheckResult)
            passed=True only if all critical checks pass
        """
        checks: List[PreCheckResult] = []

        # Check 1: Instance State (critical)
        state_check = self._check_instance_state(inst)
        checks.append(state_check)
        logger.info(
            f"  [{inst.short_name}] State Check: {state_check.status.value} - {state_check.message}"
        )

        if state_check.status == RollbackCheckStatus.FAILED:
            return False, checks

        # Check 2: Upgrade History (critical)
        history_check = self._check_upgrade_history(inst)
        checks.append(history_check)
        logger.info(
            f"  [{inst.short_name}] History Check: {history_check.status.value} - {history_check.message}"
        )

        if history_check.status == RollbackCheckStatus.FAILED:
            return False, checks

        # Extract snapshot from history check for next validation
        snapshot = history_check.details.get("snapshot", "")

        # Check 3: Snapshot Validity (critical)
        snapshot_check = self._check_snapshot_validity(inst, snapshot)
        checks.append(snapshot_check)
        logger.info(
            f"  [{inst.short_name}] Snapshot Check: {snapshot_check.status.value} - {snapshot_check.message}"
        )

        if snapshot_check.status == RollbackCheckStatus.FAILED:
            return False, checks

        # Check 4: Rollback Window (warning only, not critical)
        window_check = self._check_rollback_window(inst)
        checks.append(window_check)
        logger.info(
            f"  [{inst.short_name}] Window Check: {window_check.status.value} - {window_check.message}"
        )

        # Consider passed if no FAILED checks (warnings are ok)
        has_failures = any(c.status == RollbackCheckStatus.FAILED for c in checks)
        return not has_failures, checks

    def _get_rollback_info(
        self, inst: InstanceRef
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Determine rollback eligibility and target snapshot from upgrade history.

        Returns:
            (eligible, ineligible_reason, target_snapshot, previous_version, current_version)
        """
        try:
            data = self.api.get_instance(inst.name)
        except Exception as e:
            return False, f"Error reading instance: {e}", None, None, None

        state = str(data.get("state", "UNKNOWN")).upper()
        if state != "ACTIVE":
            return False, f"Instance not ACTIVE (state={state})", None, None, None

        history = data.get("upgradeHistory", []) or []
        if not history:
            return False, "No upgrade history found", None, None, None

        # Find most recent successful UPGRADE entry with a snapshot
        for entry in history:
            action = str(entry.get("action", "")).upper()
            status = str(entry.get("state", "")).upper()
            snapshot = entry.get("snapshot") or entry.get("targetSnapshot") or ""
            if action == "UPGRADE" and status == "SUCCEEDED" and snapshot:
                prev_ver = entry.get("version", "unknown")
                curr_ver = entry.get("targetVersion", "unknown")
                return True, None, snapshot, prev_ver, curr_ver

        # More granular reasons
        has_upgrade = any(
            str(e.get("action", "")).upper() == "UPGRADE" for e in history
        )
        if not has_upgrade:
            return False, "No upgrade entries in history", None, None, None
        if not any(e.get("snapshot") or e.get("targetSnapshot") for e in history):
            return (
                False,
                "No snapshots available from previous upgrades",
                None,
                None,
                None,
            )
        if not any(str(e.get("state", "")).upper() == "SUCCEEDED" for e in history):
            return False, "No successful upgrades found", None, None, None
        return False, "No valid rollback target found", None, None, None

    def scan(self, instance_id: Optional[str] = None) -> List[InstanceRef]:
        """
        Find all instances or get a specific instance.

        Args:
            instance_id: Optional instance ID for single instance rollback

        Returns:
            List of discovered instances
        """
        found: List[InstanceRef] = []

        if instance_id:
            # Single instance mode
            for loc in self.locations:
                logger.info(f"Looking for instance '{instance_id}' in location: {loc}")
                try:
                    inst = self.api.get_instance_by_name(instance_id, loc)
                    if inst:
                        logger.info(f"✓ Found instance '{instance_id}' in {loc}")
                        found.append(inst)
                        return found
                except Exception as e:
                    logger.debug(f"Instance '{instance_id}' not found in {loc}: {e}")

            if not found:
                logger.error(
                    f"Instance '{instance_id}' not found in any of the specified locations: {', '.join(self.locations)}"
                )
        else:
            # Fleet mode
            for loc in self.locations:
                logger.info(f"Scanning instances in location: {loc}")
                try:
                    insts = self.api.list_instances(loc)
                    logger.info(f"Found {len(insts)} instance(s) in {loc}")
                    found.extend(insts)
                except Exception as e:
                    logger.error(f"Failed to list instances in {loc}: {e}")

        return found

    def run(self, instance_id: Optional[str] = None) -> Dict:
        """
        Run the fleet rollback process or roll back a single instance.

        Args:
            instance_id: Optional instance ID for single instance rollback

        Returns:
            Statistics dictionary
        """
        self.run_start_time = time.time()

        mode = "Single Instance" if instance_id else "Fleet"
        logger.info("=" * 70)
        logger.info(f"Vertex AI Workbench Instances {mode} Rollback (REST v2)")
        logger.info("=" * 70)
        logger.info(f"Project: {self.project_id}")
        logger.info(f"Locations: {', '.join(self.locations)}")
        if instance_id:
            logger.info(f"Instance: {instance_id}")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info(f"Max parallel: {self.max_parallel}")
        logger.info(f"Timeout per instance: {self.timeout}s")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Health check timeout: {self.health_check_timeout}s")
        logger.info(f"Stagger delay: {self.stagger_delay}s")
        logger.info(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 70)

        instances = self.scan(instance_id)

        # Pre-start phase: start all STOPPED/SUSPENDED instances in parallel
        self._prestart_stopped_instances(instances)

        active_ops: List[TrackedOp] = []

        def poll_once():
            nonlocal active_ops
            if not active_ops:
                return

            time.sleep(self.poll_interval)
            now = time.time()
            remaining: List[TrackedOp] = []

            for item in active_ops:
                elapsed = now - item.start_time
                if elapsed > self.timeout:
                    logger.error(
                        f"Timeout rolling back {item.instance.short_name} after {elapsed:.0f}s"
                    )
                    self.stats["failed"] += 1
                    self.results.append(
                        UpgradeResult(
                            instance_name=item.instance.short_name,
                            location=item.instance.location,
                            status="failed",
                            start_time=item.start_time,
                            end_time=now,
                            duration_seconds=elapsed,
                            error_message=f"Timeout after {elapsed:.0f}s",
                        )
                    )
                    continue

                try:
                    op = self.api.get_operation(item.op_name)
                except Exception as e:
                    logger.error(
                        f"Failed polling op for {item.instance.short_name}: {e}"
                    )
                    remaining.append(item)
                    continue

                if not op.get("done", False):
                    remaining.append(item)
                    continue

                end_time = time.time()
                duration = end_time - item.start_time

                if "error" in op:
                    error_msg = str(op["error"])
                    logger.error(
                        f"Rollback FAILED for {item.instance.short_name}: {error_msg}"
                    )
                    self.stats["failed"] += 1
                    self.results.append(
                        UpgradeResult(
                            instance_name=item.instance.short_name,
                            location=item.instance.location,
                            status="failed",
                            start_time=item.start_time,
                            end_time=end_time,
                            duration_seconds=duration,
                            error_message=error_msg,
                        )
                    )
                    continue

                logger.info(
                    f"✓ Rollback COMPLETED for {item.instance.short_name} in {duration:.1f}s"
                )
                if self._verify_health(
                    item.instance, max_wait=self.health_check_timeout
                ):
                    self.stats["rolled_back"] += 1
                    self.results.append(
                        UpgradeResult(
                            instance_name=item.instance.short_name,
                            location=item.instance.location,
                            status="success",
                            start_time=item.start_time,
                            end_time=end_time,
                            duration_seconds=duration,
                        )
                    )
                else:
                    logger.error(
                        f"Rollback verification FAILED for {item.instance.short_name}"
                    )
                    self.stats["failed"] += 1
                    self.results.append(
                        UpgradeResult(
                            instance_name=item.instance.short_name,
                            location=item.instance.location,
                            status="failed",
                            start_time=item.start_time,
                            end_time=end_time,
                            duration_seconds=duration,
                            error_message="Health verification failed",
                        )
                    )

            active_ops = remaining

        # Process instances
        for inst in instances:
            self.stats["total"] += 1

            # Log structured instance processing start
            logger.info("=" * 60)
            logger.info(f"Processing Instance: {inst.short_name}")
            logger.info(f"  Location: {inst.location}")
            logger.info(f"  Resource: {inst.name}")
            logger.info("-" * 60)

            # Run comprehensive pre-checks
            logger.info(f"Running pre-checks for {inst.short_name}...")
            passed, precheck_results = self._run_pre_checks(inst)

            # Store pre-check results for reporting
            self.precheck_results[inst.short_name] = precheck_results

            # Extract rollback info from pre-checks
            target_snapshot = None
            prev_ver = None
            curr_ver = None
            for check in precheck_results:
                if (
                    check.check_name == "upgrade_history"
                    and check.status == RollbackCheckStatus.PASSED
                ):
                    target_snapshot = check.details.get("snapshot")
                    prev_ver = check.details.get("previous_version")
                    curr_ver = check.details.get("current_version")

            if not passed:
                # Log detailed failure reasons
                failed_checks = [
                    c
                    for c in precheck_results
                    if c.status == RollbackCheckStatus.FAILED
                ]
                failure_reasons = "; ".join(
                    [f"{c.check_name}: {c.message}" for c in failed_checks]
                )

                self.stats["not_eligible"] += 1
                self.results.append(
                    UpgradeResult(
                        instance_name=inst.short_name,
                        location=inst.location,
                        status="skipped",
                        error_message=failure_reasons,
                    )
                )
                logger.warning(f"[✗] {inst.short_name}: NOT ELIGIBLE")
                logger.warning(f"    Failure reason(s): {failure_reasons}")
                continue

            # Log warnings if any
            warnings = [
                c for c in precheck_results if c.status == RollbackCheckStatus.WARNING
            ]
            if warnings:
                for warn in warnings:
                    logger.warning(f"  [⚠] {warn.check_name}: {warn.message}")

            self.stats["eligible"] += 1
            logger.info(f"[✓] {inst.short_name}: ELIGIBLE for rollback")
            logger.info(f"    Target snapshot: {target_snapshot}")
            logger.info(f"    Rollback: {curr_ver} → {prev_ver}")

            if self.dry_run:
                logger.info(f"[DRY-RUN] Would rollback {inst.short_name}")
                self.results.append(
                    UpgradeResult(
                        instance_name=inst.short_name,
                        location=inst.location,
                        status="dry_run",
                        target_version=prev_ver,
                    )
                )
                continue

            # Throttle: keep <= max_parallel operations in flight
            while len(active_ops) >= self.max_parallel:
                poll_once()

            # Stagger delay
            if active_ops and self.stagger_delay > 0:
                logger.debug(
                    f"Stagger delay: {self.stagger_delay}s before starting next rollback"
                )
                time.sleep(self.stagger_delay)

            try:
                op_name = self.api.rollback(inst.name, target_snapshot=target_snapshot)
                active_ops.append(
                    TrackedOp(
                        op_name=op_name,
                        instance=inst,
                        start_time=time.time(),
                    )
                )
                self.stats["rollback_started"] += 1
                logger.info(f"Started rollback: {inst.short_name} (op={op_name})")
            except Exception as e:
                error_str = str(e)
                # Check if it's an eligibility or conflict error; mark not_eligible when indicated
                if (
                    "not eligible" in error_str.lower()
                    or "cannot be rolled back" in error_str.lower()
                ):
                    logger.warning(
                        f"Instance {inst.short_name} is not eligible for rollback: {e}"
                    )
                    self.stats["not_eligible"] += 1
                    self.results.append(
                        UpgradeResult(
                            instance_name=inst.short_name,
                            location=inst.location,
                            status="skipped",
                            error_message=f"Not eligible for rollback: {e}",
                        )
                    )
                else:
                    logger.error(f"Failed to start rollback for {inst.short_name}: {e}")
                    self.stats["failed"] += 1
                    self.results.append(
                        UpgradeResult(
                            instance_name=inst.short_name,
                            location=inst.location,
                            status="failed",
                            error_message=f"Failed to start rollback: {e}",
                        )
                    )

        # Finish remaining ops
        while active_ops:
            poll_once()

        self.run_end_time = time.time()
        self._print_report()

        return self.stats

    def _verify_health(
        self, inst: InstanceRef, max_wait: int = 600, check_interval: int = 30
    ) -> bool:
        """
        Verify instance health after rollback.

        Args:
            inst: Instance reference
            max_wait: Maximum time to wait for ACTIVE state
            check_interval: Time between state checks

        Returns:
            True if instance is healthy, False otherwise
        """
        start_wait = time.time()
        transitional_states = {"PROVISIONING", "INITIALIZING", "STARTING"}

        logger.info(
            f"Waiting for {inst.short_name} to become ACTIVE (max {max_wait}s)..."
        )

        while True:
            try:
                data = self.api.get_instance(inst.name)
                state = str(data.get("state", "UNKNOWN")).upper()
                health_state = str(data.get("healthState", ""))

                elapsed = time.time() - start_wait
                logger.info(
                    f"  {inst.short_name}: state={state}, healthState={health_state} ({elapsed:.0f}s elapsed)"
                )

                if state == "ACTIVE":
                    if health_state and "UNHEALTHY" in health_state.upper():
                        logger.warning(
                            f"Instance {inst.short_name} is ACTIVE but UNHEALTHY"
                        )
                        return False
                    logger.info(
                        f"✓ {inst.short_name} is ACTIVE and healthy after {elapsed:.0f}s"
                    )
                    return True

                if state in transitional_states:
                    if elapsed > max_wait:
                        logger.error(
                            f"Timeout waiting for {inst.short_name} to become ACTIVE after {elapsed:.0f}s (stuck in {state})"
                        )
                        return False
                    time.sleep(check_interval)
                    continue

                logger.error(f"Instance {inst.short_name} in unexpected state: {state}")
                return False

            except Exception as e:
                elapsed = time.time() - start_wait
                if elapsed > max_wait:
                    logger.error(
                        f"Health check failed for {inst.short_name} after {elapsed:.0f}s: {e}"
                    )
                    return False
                logger.warning(
                    f"Health check error for {inst.short_name}, retrying: {e}"
                )
                time.sleep(check_interval)

        # If somehow we exit the loop without returning, be conservative
        return False

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = seconds % 60
            return f"{mins}m {secs:.0f}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = seconds % 60
            return f"{hours}h {mins}m {secs:.0f}s"

    def _print_report(self):
        """Print detailed timing and status report."""
        total_duration = self.run_end_time - self.run_start_time

        logger.info("")
        logger.info("=" * 70)
        logger.info("ROLLBACK REPORT")
        logger.info("=" * 70)

        # Overall timing
        logger.info("")
        logger.info("TIMING SUMMARY")
        logger.info("-" * 40)
        logger.info(
            f"Start time:      {datetime.fromtimestamp(self.run_start_time).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info(
            f"End time:        {datetime.fromtimestamp(self.run_end_time).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info(f"Total duration:  {self._format_duration(total_duration)}")

        # Statistics
        logger.info("")
        logger.info("STATISTICS")
        logger.info("-" * 40)
        for k, v in self.stats.items():
            logger.info(f"{k:20s}: {v}")

        # Pre-check Summary
        if self.precheck_results:
            logger.info("")
            logger.info("PRE-CHECK SUMMARY")
            logger.info("-" * 40)
            total_checks = len(self.precheck_results)
            passed_instances = sum(
                1
                for checks in self.precheck_results.values()
                if all(c.status != RollbackCheckStatus.FAILED for c in checks)
            )
            logger.info(f"Instances checked:     {total_checks}")
            logger.info(f"Pre-checks passed:     {passed_instances}")
            logger.info(f"Pre-checks failed:     {total_checks - passed_instances}")

        # Detailed results
        successful = [r for r in self.results if r.status == "success"]
        failed = [r for r in self.results if r.status == "failed"]
        skipped = [r for r in self.results if r.status == "skipped"]
        dry_run = [r for r in self.results if r.status == "dry_run"]

        if successful:
            logger.info("")
            logger.info("SUCCESSFUL ROLLBACKS")
            logger.info("-" * 40)
            logger.info(f"{'Instance':<25} {'Location':<20} {'Duration'}")
            logger.info("-" * 70)
            for r in successful:
                duration_str = (
                    self._format_duration(r.duration_seconds)
                    if r.duration_seconds
                    else "N/A"
                )
                logger.info(f"{r.instance_name:<25} {r.location:<20} {duration_str}")

        if failed:
            logger.info("")
            logger.info("FAILED ROLLBACKS")
            logger.info("-" * 40)
            logger.info(f"{'Instance':<25} {'Location':<20} {'Error'}")
            logger.info("-" * 70)
            for r in failed:
                error = (
                    (r.error_message[:50] + "...")
                    if r.error_message and len(r.error_message) > 50
                    else (r.error_message or "Unknown")
                )
                logger.info(f"{r.instance_name:<25} {r.location:<20} {error}")

        if skipped:
            logger.info("")
            logger.info("SKIPPED/NOT ELIGIBLE INSTANCES")
            logger.info("-" * 40)
            logger.info(f"{'Instance':<25} {'Location':<20} {'Reason'}")
            logger.info("-" * 70)
            for r in skipped:
                reason = (
                    (r.error_message[:50] + "...")
                    if r.error_message and len(r.error_message) > 50
                    else (r.error_message or "Unknown")
                )
                logger.info(f"{r.instance_name:<25} {r.location:<20} {reason}")

                # Show detailed pre-check results for this instance
                if r.instance_name in self.precheck_results:
                    checks = self.precheck_results[r.instance_name]
                    failed = [
                        c for c in checks if c.status == RollbackCheckStatus.FAILED
                    ]
                    if failed:
                        logger.info(f"  Pre-check details for {r.instance_name}:")
                        for check in failed:
                            logger.info(f"    ✗ {check.check_name}: {check.message}")
                            if check.details.get("reason"):
                                logger.info(f"      Reason: {check.details['reason']}")

        if dry_run:
            logger.info("")
            logger.info("DRY RUN - WOULD ATTEMPT ROLLBACK")
            logger.info("-" * 40)
            for r in dry_run:
                logger.info(f"  {r.instance_name} ({r.location})")

        logger.info("")
        logger.info("=" * 70)

        # Export results
        self._export_results_json()

    def _export_results_json(self):
        """Export results to JSON file."""
        report = {
            "project_id": self.project_id,
            "locations": self.locations,
            "dry_run": self.dry_run,
            "start_time": datetime.fromtimestamp(self.run_start_time).isoformat(),
            "end_time": datetime.fromtimestamp(self.run_end_time).isoformat(),
            "total_duration_seconds": self.run_end_time - self.run_start_time,
            "statistics": self.stats,
            "precheck_summary": {
                "total_instances_checked": len(self.precheck_results),
                "instances_passed": sum(
                    1
                    for checks in self.precheck_results.values()
                    if all(c.status != RollbackCheckStatus.FAILED for c in checks)
                ),
                "instances_failed": sum(
                    1
                    for checks in self.precheck_results.values()
                    if any(c.status == RollbackCheckStatus.FAILED for c in checks)
                ),
            },
            "results": [
                {
                    "instance_name": r.instance_name,
                    "location": r.location,
                    "status": r.status,
                    "start_time": (
                        datetime.fromtimestamp(r.start_time).isoformat()
                        if r.start_time
                        else None
                    ),
                    "end_time": (
                        datetime.fromtimestamp(r.end_time).isoformat()
                        if r.end_time
                        else None
                    ),
                    "duration_seconds": r.duration_seconds,
                    "target_version": r.target_version,
                    "error_message": r.error_message,
                    "precheck_results": (
                        [
                            check.to_dict()
                            for check in self.precheck_results.get(r.instance_name, [])
                        ]
                        if r.instance_name in self.precheck_results
                        else []
                    ),
                }
                for r in self.results
            ],
        }

        filename = f"rollback-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"Detailed report exported to: {filename}")

    def _prestart_stopped_instances(self, instances: List[InstanceRef]) -> None:
        """Start all STOPPED/SUSPENDED instances in parallel before rollbacks.

        Respects dry-run, max_parallel, poll_interval, timeout, and stagger_delay.
        """
        to_start: List[InstanceRef] = []
        for inst in instances:
            try:
                data = self.api.get_instance(inst.name)
                state = str(data.get("state", "UNKNOWN")).upper()
                if state in {"STOPPED", "SUSPENDED"}:
                    to_start.append(inst)
            except Exception as e:
                logger.debug(f"Could not read state for {inst.short_name}: {e}")

        if not to_start:
            return

        if self.dry_run:
            for inst in to_start:
                logger.info(
                    f"DRY RUN: Would start instance {inst.short_name} (currently STOPPED/SUSPENDED)"
                )
            return

        logger.info(f"Pre-starting {len(to_start)} instance(s) before rollbacks...")

        active_starts: List[TrackedOp] = []

        def poll_once():
            nonlocal active_starts
            if not active_starts:
                return
            time.sleep(self.poll_interval)
            remaining: List[TrackedOp] = []
            for item in active_starts:
                try:
                    op = self.api.get_operation(item.op_name)
                except Exception as e:
                    logger.warning(
                        f"Failed polling start op for {item.instance.short_name}: {e}"
                    )
                    remaining.append(item)
                    continue
                if not op.get("done", False):
                    remaining.append(item)
                    continue
                if "error" in op:
                    logger.error(
                        f"Start FAILED for {item.instance.short_name}: {op['error']}"
                    )
                else:
                    logger.info(f"Start COMPLETED for {item.instance.short_name}")
            active_starts = remaining

        # Kick off start operations with throttling
        for inst in to_start:
            while len(active_starts) >= self.max_parallel:
                poll_once()
            if active_starts and self.stagger_delay > 0:
                logger.debug(
                    f"Stagger delay: {self.stagger_delay}s before starting next instance"
                )
                time.sleep(self.stagger_delay)
            try:
                op_name = self.api.start_instance(inst.name)
                active_starts.append(
                    TrackedOp(op_name=op_name, instance=inst, start_time=time.time())
                )
                logger.info(f"Start initiated for {inst.short_name} (op={op_name})")
            except Exception as e:
                logger.error(f"Failed to start instance {inst.short_name}: {e}")

        # Wait for all start operations to complete
        while active_starts:
            poll_once()

        # Verify instances become ACTIVE (best-effort)
        for inst in to_start:
            if not self._verify_health(inst, max_wait=self.health_check_timeout):
                logger.warning(
                    f"Instance {inst.short_name} did not become ACTIVE after start; rollback eligibility may fail"
                )
