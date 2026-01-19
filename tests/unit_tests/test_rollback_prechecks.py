"""
Unit tests for FleetRollback pre-check functions.

Tests the modular pre-check validations added to the rollback module.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from rollback import FleetRollback, RollbackCheckStatus, PreCheckResult
from models import InstanceRef


class TestPreCheckResult(unittest.TestCase):
    """Tests for PreCheckResult class."""

    def test_to_dict(self):
        """Test PreCheckResult serialization to dictionary."""
        result = PreCheckResult(
            check_name="test_check",
            status=RollbackCheckStatus.PASSED,
            message="Test message",
            details={"key": "value"},
        )

        data = result.to_dict()

        self.assertEqual(data["check_name"], "test_check")
        self.assertEqual(data["status"], "passed")
        self.assertEqual(data["message"], "Test message")
        self.assertEqual(data["details"]["key"], "value")
        self.assertIn("timestamp", data)

    def test_precheck_result_with_no_details(self):
        """Test PreCheckResult handles missing details gracefully."""
        result = PreCheckResult(
            check_name="test",
            status=RollbackCheckStatus.WARNING,
            message="Warning message",
        )

        self.assertEqual(result.details, {})
        self.assertEqual(result.to_dict()["details"], {})


class TestRollbackPreChecks(unittest.TestCase):
    """Tests for FleetRollback pre-check methods."""

    def setUp(self):
        """Set up test fixtures."""
        self.rollback = FleetRollback(
            project_id="test-project",
            locations=["europe-west2-a"],
            dry_run=True,
            max_parallel=5,
            timeout=3600,
            poll_interval=20,
        )

        self.instance = InstanceRef(
            name="projects/test-project/locations/europe-west2-a/instances/test-instance",
            short_name="test-instance",
            location="europe-west2-a",
        )

    @patch.object(FleetRollback, "_check_instance_state")
    @patch.object(FleetRollback, "_check_upgrade_history")
    @patch.object(FleetRollback, "_check_snapshot_validity")
    @patch.object(FleetRollback, "_check_rollback_window")
    def test_run_pre_checks_all_pass(
        self, mock_window, mock_snapshot, mock_history, mock_state
    ):
        """Test _run_pre_checks when all checks pass."""
        mock_state.return_value = PreCheckResult(
            "instance_state", RollbackCheckStatus.PASSED, "ACTIVE"
        )
        mock_history.return_value = PreCheckResult(
            "upgrade_history",
            RollbackCheckStatus.PASSED,
            "Valid",
            details={
                "snapshot": "snap-123",
                "previous_version": "m137",
                "current_version": "m138",
            },
        )
        mock_snapshot.return_value = PreCheckResult(
            "snapshot_validity", RollbackCheckStatus.PASSED, "Valid snapshot"
        )
        mock_window.return_value = PreCheckResult(
            "rollback_window", RollbackCheckStatus.PASSED, "Recent upgrade"
        )

        passed, checks = self.rollback._run_pre_checks(self.instance)

        self.assertTrue(passed)
        self.assertEqual(len(checks), 4)
        self.assertEqual(mock_state.call_count, 1)
        self.assertEqual(mock_history.call_count, 1)
        self.assertEqual(mock_snapshot.call_count, 1)
        self.assertEqual(mock_window.call_count, 1)

    @patch.object(FleetRollback, "_check_instance_state")
    def test_run_pre_checks_state_fails(self, mock_state):
        """Test _run_pre_checks stops early when state check fails."""
        mock_state.return_value = PreCheckResult(
            "instance_state", RollbackCheckStatus.FAILED, "Instance busy"
        )

        passed, checks = self.rollback._run_pre_checks(self.instance)

        self.assertFalse(passed)
        self.assertEqual(len(checks), 1)

    @patch.object(FleetRollback, "_check_instance_state")
    @patch.object(FleetRollback, "_check_upgrade_history")
    def test_run_pre_checks_history_fails(self, mock_history, mock_state):
        """Test _run_pre_checks stops when history check fails."""
        mock_state.return_value = PreCheckResult(
            "instance_state", RollbackCheckStatus.PASSED, "ACTIVE"
        )
        mock_history.return_value = PreCheckResult(
            "upgrade_history", RollbackCheckStatus.FAILED, "No history"
        )

        passed, checks = self.rollback._run_pre_checks(self.instance)

        self.assertFalse(passed)
        self.assertEqual(len(checks), 2)

    @patch.object(FleetRollback, "_check_instance_state")
    @patch.object(FleetRollback, "_check_upgrade_history")
    @patch.object(FleetRollback, "_check_snapshot_validity")
    @patch.object(FleetRollback, "_check_rollback_window")
    def test_run_pre_checks_with_warnings(
        self, mock_window, mock_snapshot, mock_history, mock_state
    ):
        """Test _run_pre_checks passes with warnings."""
        mock_state.return_value = PreCheckResult(
            "instance_state", RollbackCheckStatus.PASSED, "ACTIVE"
        )
        mock_history.return_value = PreCheckResult(
            "upgrade_history",
            RollbackCheckStatus.PASSED,
            "Valid",
            details={"snapshot": "snap-123"},
        )
        mock_snapshot.return_value = PreCheckResult(
            "snapshot_validity", RollbackCheckStatus.WARNING, "Unusual format"
        )
        mock_window.return_value = PreCheckResult(
            "rollback_window", RollbackCheckStatus.WARNING, "No timestamp"
        )

        passed, checks = self.rollback._run_pre_checks(self.instance)

        self.assertTrue(passed)  # Warnings don't fail the check
        self.assertEqual(len(checks), 4)


class TestInstanceStateCheck(unittest.TestCase):
    """Tests for _check_instance_state method."""

    def setUp(self):
        """Set up test fixtures."""
        self.rollback = FleetRollback(
            project_id="test-project",
            locations=["europe-west2-a"],
            dry_run=True,
            max_parallel=5,
            timeout=3600,
            poll_interval=20,
        )

        self.instance = InstanceRef(
            name="projects/test-project/locations/europe-west2-a/instances/test-instance",
            short_name="test-instance",
            location="europe-west2-a",
        )

    @patch("rollback.WorkbenchRestClient")
    def test_check_instance_state_active(self, mock_client_class):
        """Test instance state check with ACTIVE instance."""
        mock_api = MagicMock()
        mock_api.get_instance.return_value = {"state": "ACTIVE"}
        self.rollback.api = mock_api

        result = self.rollback._check_instance_state(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.PASSED)
        self.assertIn("ACTIVE", result.message)
        self.assertEqual(result.details["state"], "ACTIVE")

    @patch("rollback.WorkbenchRestClient")
    def test_check_instance_state_busy(self, mock_client_class):
        """Test instance state check with busy instance."""
        mock_api = MagicMock()
        mock_api.get_instance.return_value = {"state": "UPGRADING"}
        self.rollback.api = mock_api

        result = self.rollback._check_instance_state(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.FAILED)
        self.assertIn("busy", result.message)
        self.assertEqual(result.details["reason"], "operation_in_progress")

    @patch("rollback.WorkbenchRestClient")
    def test_check_instance_state_stopped(self, mock_client_class):
        """Test instance state check with stopped instance."""
        mock_api = MagicMock()
        mock_api.get_instance.return_value = {"state": "STOPPED"}
        self.rollback.api = mock_api

        result = self.rollback._check_instance_state(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.FAILED)
        self.assertIn("not running", result.message)
        self.assertEqual(result.details["reason"], "not_running")

    @patch("rollback.WorkbenchRestClient")
    def test_check_instance_state_api_error(self, mock_client_class):
        """Test instance state check with API error."""
        mock_api = MagicMock()
        mock_api.get_instance.side_effect = Exception("API Error")
        self.rollback.api = mock_api

        result = self.rollback._check_instance_state(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.FAILED)
        self.assertIn("Cannot read", result.message)
        self.assertEqual(result.details["reason"], "api_error")


class TestUpgradeHistoryCheck(unittest.TestCase):
    """Tests for _check_upgrade_history method."""

    def setUp(self):
        """Set up test fixtures."""
        self.rollback = FleetRollback(
            project_id="test-project",
            locations=["europe-west2-a"],
            dry_run=True,
            max_parallel=5,
            timeout=3600,
            poll_interval=20,
        )

        self.instance = InstanceRef(
            name="projects/test-project/locations/europe-west2-a/instances/test-instance",
            short_name="test-instance",
            location="europe-west2-a",
        )

    @patch("rollback.WorkbenchRestClient")
    def test_check_upgrade_history_valid(self, mock_client_class):
        """Test upgrade history check with valid history."""
        mock_api = MagicMock()
        mock_api.get_instance.return_value = {
            "upgradeHistory": [
                {
                    "action": "UPGRADE",
                    "state": "SUCCEEDED",
                    "snapshot": "projects/test/locations/europe-west2-a/instances/test/snapshots/snap-123",
                    "version": "m137",
                    "targetVersion": "m138",
                }
            ]
        }
        self.rollback.api = mock_api

        result = self.rollback._check_upgrade_history(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.PASSED)
        self.assertIn("snap-123", result.message)
        self.assertEqual(
            result.details["snapshot"],
            "projects/test/locations/europe-west2-a/instances/test/snapshots/snap-123",
        )
        self.assertEqual(result.details["previous_version"], "m137")
        self.assertEqual(result.details["current_version"], "m138")

    @patch("rollback.WorkbenchRestClient")
    def test_check_upgrade_history_no_history(self, mock_client_class):
        """Test upgrade history check with no history."""
        mock_api = MagicMock()
        mock_api.get_instance.return_value = {"upgradeHistory": []}
        self.rollback.api = mock_api

        result = self.rollback._check_upgrade_history(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.FAILED)
        self.assertIn("No upgrade history", result.message)
        self.assertEqual(result.details["reason"], "no_history")

    @patch("rollback.WorkbenchRestClient")
    def test_check_upgrade_history_no_snapshot(self, mock_client_class):
        """Test upgrade history check with no snapshot."""
        mock_api = MagicMock()
        mock_api.get_instance.return_value = {
            "upgradeHistory": [
                {
                    "action": "UPGRADE",
                    "state": "SUCCEEDED",
                    "version": "m137",
                    "targetVersion": "m138",
                }
            ]
        }
        self.rollback.api = mock_api

        result = self.rollback._check_upgrade_history(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.FAILED)
        self.assertIn("No snapshot available", result.message)
        self.assertEqual(result.details["reason"], "no_snapshot")


class TestSnapshotValidityCheck(unittest.TestCase):
    """Tests for _check_snapshot_validity method."""

    def setUp(self):
        """Set up test fixtures."""
        self.rollback = FleetRollback(
            project_id="test-project",
            locations=["europe-west2-a"],
            dry_run=True,
            max_parallel=5,
            timeout=3600,
            poll_interval=20,
        )

        self.instance = InstanceRef(
            name="projects/test-project/locations/europe-west2-a/instances/test-instance",
            short_name="test-instance",
            location="europe-west2-a",
        )

    def test_check_snapshot_validity_valid(self):
        """Test snapshot validity check with valid snapshot."""
        snapshot = (
            "projects/test/locations/europe-west2-a/instances/test/snapshots/snap-123"
        )

        result = self.rollback._check_snapshot_validity(self.instance, snapshot)

        self.assertEqual(result.status, RollbackCheckStatus.PASSED)
        self.assertIn("snap-123", result.message)
        self.assertEqual(result.details["snapshot_id"], "snap-123")

    def test_check_snapshot_validity_empty(self):
        """Test snapshot validity check with empty snapshot."""
        result = self.rollback._check_snapshot_validity(self.instance, "")

        self.assertEqual(result.status, RollbackCheckStatus.FAILED)
        self.assertIn("No snapshot specified", result.message)

    def test_check_snapshot_validity_invalid_format(self):
        """Test snapshot validity check with invalid format."""
        result = self.rollback._check_snapshot_validity(self.instance, "invalid-format")

        self.assertEqual(result.status, RollbackCheckStatus.WARNING)
        self.assertIn("unusual", result.message.lower())


class TestRollbackWindowCheck(unittest.TestCase):
    """Tests for _check_rollback_window method."""

    def setUp(self):
        """Set up test fixtures."""
        self.rollback = FleetRollback(
            project_id="test-project",
            locations=["europe-west2-a"],
            dry_run=True,
            max_parallel=5,
            timeout=3600,
            poll_interval=20,
        )

        self.instance = InstanceRef(
            name="projects/test-project/locations/europe-west2-a/instances/test-instance",
            short_name="test-instance",
            location="europe-west2-a",
        )

    @patch("rollback.WorkbenchRestClient")
    def test_check_rollback_window_with_timestamp(self, mock_client_class):
        """Test rollback window check with timestamp."""
        mock_api = MagicMock()
        mock_api.get_instance.return_value = {
            "upgradeHistory": [
                {
                    "action": "UPGRADE",
                    "state": "SUCCEEDED",
                    "createTime": "2026-01-16T10:00:00Z",
                }
            ]
        }
        self.rollback.api = mock_api

        result = self.rollback._check_rollback_window(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.PASSED)
        self.assertIn("2026-01-16", result.message)
        self.assertEqual(result.details["upgrade_time"], "2026-01-16T10:00:00Z")

    @patch("rollback.WorkbenchRestClient")
    def test_check_rollback_window_no_timestamp(self, mock_client_class):
        """Test rollback window check without timestamp."""
        mock_api = MagicMock()
        mock_api.get_instance.return_value = {
            "upgradeHistory": [{"action": "UPGRADE", "state": "SUCCEEDED"}]
        }
        self.rollback.api = mock_api

        result = self.rollback._check_rollback_window(self.instance)

        self.assertEqual(result.status, RollbackCheckStatus.WARNING)
        self.assertIn("Cannot determine", result.message)


if __name__ == "__main__":
    unittest.main()
