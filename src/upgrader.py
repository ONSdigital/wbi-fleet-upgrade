"""
Fleet upgrader logic for Vertex AI Workbench Instances.
"""

import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from clients import WorkbenchRestClient
from models import InstanceRef, TrackedOp, UpgradeResult

logger = logging.getLogger(__name__)


class FleetUpgrader:
    """Manages fleet-wide upgrade operations for Workbench instances."""

    def __init__(
        self,
        project_id: str,
        locations: List[str],
        dry_run: bool,
        max_parallel: int,
        timeout: int,
        poll_interval: int,
        rollback_on_failure: bool,
        health_check_timeout: int = 600,
        stagger_delay: float = 3.0,
    ):
        """
        Initialize the fleet upgrader.

        Args:
            project_id: GCP project ID
            locations: List of zone locations to scan
            dry_run: If True, only check upgradeability without upgrading
            max_parallel: Maximum number of concurrent upgrade operations
            timeout: Timeout per instance upgrade (seconds)
            poll_interval: Interval between operation polls (seconds)
            rollback_on_failure: Whether to rollback on upgrade failure
            health_check_timeout: Timeout for health verification (seconds)
            stagger_delay: Delay between starting upgrades (seconds)
        """
        self.project_id = project_id
        self.locations = locations
        self.dry_run = dry_run
        self.max_parallel = max_parallel
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.rollback_on_failure = rollback_on_failure
        self.health_check_timeout = health_check_timeout
        self.stagger_delay = stagger_delay

        self.api = WorkbenchRestClient(project_id=project_id)

        self.stats = {
            "total": 0,
            "upgradeable": 0,
            "up_to_date": 0,
            "skipped": 0,
            "upgrade_started": 0,
            "upgraded": 0,
            "failed": 0,
            "rolled_back": 0,
        }

        # Timing and results tracking
        self.run_start_time: Optional[float] = None
        self.run_end_time: Optional[float] = None
        self.results: List[UpgradeResult] = []

    def _instance_ready(self, inst: InstanceRef) -> Tuple[bool, str]:
        """
        Check if instance is ready for upgrade (ACTIVE state, no pending operations).

        If the instance is STOPPED or SUSPENDED, this will attempt to start it
        automatically (unless in dry-run mode) and wait until it becomes ACTIVE.

        Args:
            inst: Instance reference

        Returns:
            Tuple of (is_ready, status_message)
        """
        try:
            data = self.api.get_instance(inst.name)
            state = str(data.get("state", "UNKNOWN")).upper()

            # Only ACTIVE instances can be upgraded
            if state == "ACTIVE":
                return True, state

            # These states mean an operation is in progress - instance is busy
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

            # If the instance is stopped/suspended, try to start it automatically
            if state in {"STOPPED", "SUSPENDED"}:
                if self.dry_run:
                    logger.info(
                        f"DRY RUN: Would start instance {inst.short_name} (current state={state})"
                    )
                    return False, f"Would start instance from {state}"

                logger.info(
                    f"Instance {inst.short_name} is {state}. Attempting to start before upgrade..."
                )
                try:
                    op_name = self.api.start_instance(inst.name)
                    logger.info(
                        f"Start operation initiated for {inst.short_name} (op={op_name})"
                    )
                    # Poll start operation until done or timeout
                    start_time = time.time()
                    while True:
                        if time.time() - start_time > self.timeout:
                            logger.error(
                                f"Timeout starting {inst.short_name} after {self.timeout}s"
                            )
                            return False, "Timeout starting instance"
                        op = self.api.get_operation(op_name)
                        if op.get("done", False):
                            if "error" in op:
                                logger.error(
                                    f"Start FAILED for {inst.short_name}: {op['error']}"
                                )
                                return False, "Failed to start instance"
                            logger.info(f"Start COMPLETED for {inst.short_name}")
                            break
                        time.sleep(self.poll_interval)

                    # Verify instance becomes ACTIVE
                    if self._verify_health(inst, max_wait=self.health_check_timeout):
                        logger.info(
                            f"{inst.short_name} is ACTIVE after start; proceeding with upgrade"
                        )
                        return True, "ACTIVE"
                    else:
                        return False, "Instance did not become ACTIVE after start"
                except Exception as e:
                    logger.error(f"Failed to start instance {inst.short_name}: {e}")
                    return False, f"Failed to start instance: {e}"

            logger.warning(f"Skipping {inst.short_name}: unexpected state={state}")
            return False, f"Unexpected state: {state}"

        except Exception as e:
            logger.error(f"Skipping {inst.short_name}: cannot read state: {e}")
            return False, f"Error reading state: {e}"

    def scan(self, instance_id: Optional[str] = None) -> List[InstanceRef]:
        """
        Scan all locations for instances or get a specific instance.

        Args:
            instance_id: Optional instance ID to fetch a single instance

        Returns:
            List of discovered instances
        """
        found: List[InstanceRef] = []

        if instance_id:
            # Single instance mode - search in all locations
            for loc in self.locations:
                logger.info(f"Looking for instance '{instance_id}' in location: {loc}")
                try:
                    inst = self.api.get_instance_by_name(instance_id, loc)
                    if inst:
                        logger.info(f"✓ Found instance '{instance_id}' in {loc}")
                        found.append(inst)
                        return found  # Return immediately when found
                except Exception as e:
                    logger.debug(f"Instance '{instance_id}' not found in {loc}: {e}")

            if not found:
                logger.error(
                    f"Instance '{instance_id}' not found in any of the specified locations: {', '.join(self.locations)}"
                )
        else:
            # Fleet mode - scan all instances in all locations
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
        Execute the fleet upgrade process or upgrade a single instance.

        Args:
            instance_id: Optional instance ID for single instance upgrade

        Returns:
            Statistics dictionary
        """
        self.run_start_time = time.time()

        mode = "Single Instance" if instance_id else "Fleet"
        logger.info("=" * 70)
        logger.info(f"Vertex AI Workbench Instances {mode} Upgrade (REST v2)")
        logger.info("=" * 70)
        logger.info(f"Project: {self.project_id}")
        logger.info(f"Locations: {', '.join(self.locations)}")
        if instance_id:
            logger.info(f"Instance: {instance_id}")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info(f"Max parallel: {self.max_parallel}")
        logger.info(f"Timeout per instance: {self.timeout}s")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Rollback on failure: {self.rollback_on_failure}")
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
                        f"Timeout upgrading {item.instance.short_name} after {elapsed:.0f}s"
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
                            target_version=item.target_version,
                            error_message=f"Timeout after {elapsed:.0f}s",
                        )
                    )
                    if self.rollback_on_failure:
                        self._try_rollback(item.instance)
                        self.results[-1].rolled_back = True
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
                        f"Upgrade FAILED for {item.instance.short_name}: {error_msg}"
                    )
                    self.stats["failed"] += 1
                    result = UpgradeResult(
                        instance_name=item.instance.short_name,
                        location=item.instance.location,
                        status="failed",
                        start_time=item.start_time,
                        end_time=end_time,
                        duration_seconds=duration,
                        target_version=item.target_version,
                        error_message=error_msg,
                    )
                    if self.rollback_on_failure:
                        self._try_rollback(item.instance)
                        result.rolled_back = True
                    self.results.append(result)
                    continue

                logger.info(
                    f"✓ Upgrade COMPLETED for {item.instance.short_name} in {duration:.1f}s"
                )
                if self._verify_health(
                    item.instance, max_wait=self.health_check_timeout
                ):
                    self.stats["upgraded"] += 1
                    self.results.append(
                        UpgradeResult(
                            instance_name=item.instance.short_name,
                            location=item.instance.location,
                            status="success",
                            start_time=item.start_time,
                            end_time=end_time,
                            duration_seconds=duration,
                            target_version=item.target_version,
                        )
                    )
                else:
                    logger.error(
                        f"Upgrade verification FAILED for {item.instance.short_name}"
                    )
                    self.stats["failed"] += 1
                    result = UpgradeResult(
                        instance_name=item.instance.short_name,
                        location=item.instance.location,
                        status="failed",
                        start_time=item.start_time,
                        end_time=end_time,
                        duration_seconds=duration,
                        target_version=item.target_version,
                        error_message="Health verification failed",
                    )
                    if self.rollback_on_failure:
                        self._try_rollback(item.instance)
                        result.rolled_back = True
                    self.results.append(result)

            active_ops = remaining

        # Process instances
        for inst in instances:
            self.stats["total"] += 1

            ready, reason = self._instance_ready(inst)
            if not ready:
                self.stats["skipped"] += 1
                self.results.append(
                    UpgradeResult(
                        instance_name=inst.short_name,
                        location=inst.location,
                        status="skipped",
                        error_message=reason,
                    )
                )
                continue

            try:
                upgradeable, info = self.api.check_upgradability(inst.name)
            except Exception as e:
                logger.error(
                    f"Skipping {inst.short_name}: checkUpgradability failed: {e}"
                )
                self.stats["skipped"] += 1
                self.results.append(
                    UpgradeResult(
                        instance_name=inst.short_name,
                        location=inst.location,
                        status="skipped",
                        error_message=f"checkUpgradability failed: {e}",
                    )
                )
                continue

            if not upgradeable:
                logger.info(f"[-] {inst.short_name} is up to date ({info})")
                self.stats["up_to_date"] += 1
                self.results.append(
                    UpgradeResult(
                        instance_name=inst.short_name,
                        location=inst.location,
                        status="up_to_date",
                    )
                )
                continue

            self.stats["upgradeable"] += 1
            logger.info(f"[>] Upgradeable: {inst.short_name} -> {info}")

            if self.dry_run:
                logger.info(f"DRY RUN: Would upgrade {inst.short_name}")
                self.results.append(
                    UpgradeResult(
                        instance_name=inst.short_name,
                        location=inst.location,
                        status="dry_run",
                        target_version=info,
                    )
                )
                continue

            # Throttle: keep <= max_parallel operations in flight
            while len(active_ops) >= self.max_parallel:
                poll_once()

            # Stagger delay to avoid overwhelming GCP operation queue
            if active_ops and self.stagger_delay > 0:
                logger.debug(
                    f"Stagger delay: {self.stagger_delay}s before starting next upgrade"
                )
                time.sleep(self.stagger_delay)

            try:
                op_name = self.api.upgrade(inst.name)
                active_ops.append(
                    TrackedOp(
                        op_name=op_name,
                        instance=inst,
                        start_time=time.time(),
                        target_version=info,
                    )
                )
                self.stats["upgrade_started"] += 1
                logger.info(f"Started upgrade: {inst.short_name} (op={op_name})")
            except Exception as e:
                logger.error(f"Failed to start upgrade for {inst.short_name}: {e}")
                self.stats["failed"] += 1
                self.results.append(
                    UpgradeResult(
                        instance_name=inst.short_name,
                        location=inst.location,
                        status="failed",
                        target_version=info,
                        error_message=f"Failed to start upgrade: {e}",
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
        Verify instance health after upgrade.

        Args:
            inst: Instance reference
            max_wait: Maximum time to wait for ACTIVE state (seconds)
            check_interval: Time between state checks (seconds)

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

                # Success - instance is active
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

                # Still transitioning - keep waiting
                if state in transitional_states:
                    if elapsed > max_wait:
                        logger.error(
                            f"Timeout waiting for {inst.short_name} to become ACTIVE after {elapsed:.0f}s (stuck in {state})"
                        )
                        return False
                    time.sleep(check_interval)
                    continue

                # Unexpected state (STOPPED, DELETED, etc.)
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

        # Fallback (should not reach here)
        return False

    def _try_rollback(self, inst: InstanceRef) -> None:
        """
        Attempt to rollback an instance upgrade.

        Args:
            inst: Instance reference
        """
        try:
            op_name = self.api.rollback(inst.name)
            logger.warning(f"Rollback started for {inst.short_name} (op={op_name})")
            start = time.time()
            while True:
                if time.time() - start > self.timeout:
                    logger.error(f"Rollback timed out for {inst.short_name}")
                    return
                op = self.api.get_operation(op_name)
                if op.get("done", False):
                    if "error" in op:
                        logger.error(
                            f"Rollback FAILED for {inst.short_name}: {op['error']}"
                        )
                        return
                    logger.warning(f"Rollback COMPLETED for {inst.short_name}")
                    self.stats["rolled_back"] += 1
                    return
                time.sleep(self.poll_interval)
        except Exception as e:
            logger.error(f"Rollback failed for {inst.short_name}: {e}")

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
        logger.info("UPGRADE REPORT")
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

        # Detailed results by status
        successful = [r for r in self.results if r.status == "success"]
        failed = [r for r in self.results if r.status == "failed"]
        skipped = [r for r in self.results if r.status == "skipped"]
        up_to_date = [r for r in self.results if r.status == "up_to_date"]
        dry_run = [r for r in self.results if r.status == "dry_run"]

        if successful:
            logger.info("")
            logger.info("SUCCESSFUL UPGRADES")
            logger.info("-" * 40)
            logger.info(
                f"{'Instance':<25} {'Location':<20} {'Duration':<12} {'Version'}"
            )
            logger.info("-" * 70)
            total_upgrade_time = 0
            for r in successful:
                duration_str = (
                    self._format_duration(r.duration_seconds)
                    if r.duration_seconds
                    else "N/A"
                )
                total_upgrade_time += r.duration_seconds or 0
                logger.info(
                    f"{r.instance_name:<25} {r.location:<20} {duration_str:<12} {r.target_version or 'N/A'}"
                )

            if len(successful) > 1:
                avg_time = total_upgrade_time / len(successful)
                logger.info("-" * 70)
                logger.info(f"Average upgrade time: {self._format_duration(avg_time)}")
                logger.info(
                    f"Total upgrade time:   {self._format_duration(total_upgrade_time)}"
                )

                # Find min/max
                times = [r.duration_seconds for r in successful if r.duration_seconds]
                if times:
                    min_time = min(times)
                    max_time = max(times)
                    min_inst = next(
                        r.instance_name
                        for r in successful
                        if r.duration_seconds == min_time
                    )
                    max_inst = next(
                        r.instance_name
                        for r in successful
                        if r.duration_seconds == max_time
                    )
                    logger.info(
                        f"Fastest upgrade:      {self._format_duration(min_time)} ({min_inst})"
                    )
                    logger.info(
                        f"Slowest upgrade:      {self._format_duration(max_time)} ({max_inst})"
                    )

        if failed:
            logger.info("")
            logger.info("FAILED UPGRADES")
            logger.info("-" * 40)
            logger.info(
                f"{'Instance':<25} {'Location':<20} {'Rolled Back':<12} {'Error'}"
            )
            logger.info("-" * 70)
            for r in failed:
                rb = "Yes" if r.rolled_back else "No"
                error = (
                    (r.error_message[:40] + "...")
                    if r.error_message and len(r.error_message) > 40
                    else (r.error_message or "Unknown")
                )
                logger.info(f"{r.instance_name:<25} {r.location:<20} {rb:<12} {error}")

        if skipped:
            logger.info("")
            logger.info("SKIPPED INSTANCES")
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

        if up_to_date:
            logger.info("")
            logger.info("UP-TO-DATE INSTANCES")
            logger.info("-" * 40)
            for r in up_to_date:
                logger.info(f"  {r.instance_name} ({r.location})")

        if dry_run:
            logger.info("")
            logger.info("DRY RUN - WOULD UPGRADE")
            logger.info("-" * 40)
            logger.info(f"{'Instance':<25} {'Location':<20} {'Target Version'}")
            logger.info("-" * 70)
            for r in dry_run:
                logger.info(
                    f"{r.instance_name:<25} {r.location:<20} {r.target_version or 'N/A'}"
                )

        logger.info("")
        logger.info("=" * 70)

        # Export results to JSON
        self._export_results_json()

    def _export_results_json(self):
        """Export results to JSON file for further processing."""
        report = {
            "project_id": self.project_id,
            "locations": self.locations,
            "dry_run": self.dry_run,
            "start_time": datetime.fromtimestamp(self.run_start_time).isoformat(),
            "end_time": datetime.fromtimestamp(self.run_end_time).isoformat(),
            "total_duration_seconds": self.run_end_time - self.run_start_time,
            "statistics": self.stats,
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
                    "rolled_back": r.rolled_back,
                }
                for r in self.results
            ],
        }

        filename = f"upgrade-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"Detailed report exported to: {filename}")

    def _prestart_stopped_instances(self, instances: List[InstanceRef]) -> None:
        """Start all STOPPED/SUSPENDED instances in parallel before upgrades.

        Respects dry-run, max_parallel, poll_interval, timeout, and stagger_delay.
        """
        # Identify instances that are STOPPED or SUSPENDED
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

        logger.info(f"Pre-starting {len(to_start)} instance(s) before upgrades...")

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
            # Throttle concurrent start operations
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

        # Optionally verify each instance becomes ACTIVE (best-effort)
        for inst in to_start:
            if not self._verify_health(inst, max_wait=self.health_check_timeout):
                logger.warning(
                    f"Instance {inst.short_name} did not become ACTIVE after start; upgrade may be skipped"
                )
