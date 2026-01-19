"""
Unit tests for data models.
"""

import unittest
from models import InstanceRef, UpgradeResult, TrackedOp


class TestInstanceRef(unittest.TestCase):
    """Test InstanceRef data model."""

    def test_instance_ref_creation(self):
        """Test creating an InstanceRef."""
        inst = InstanceRef(
            name="projects/my-project/locations/europe-west2-a/instances/my-instance",
            short_name="my-instance",
            location="europe-west2-a",
        )
        self.assertEqual(
            inst.name,
            "projects/my-project/locations/europe-west2-a/instances/my-instance",
        )
        self.assertEqual(inst.short_name, "my-instance")
        self.assertEqual(inst.location, "europe-west2-a")


class TestUpgradeResult(unittest.TestCase):
    """Test UpgradeResult data model."""

    def test_upgrade_result_success(self):
        """Test creating a successful UpgradeResult."""
        result = UpgradeResult(
            instance_name="my-instance",
            location="europe-west2-a",
            status="success",
            start_time=1000.0,
            end_time=1100.0,
            duration_seconds=100.0,
            target_version="v2.0",
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.duration_seconds, 100.0)
        self.assertFalse(result.rolled_back)

    def test_upgrade_result_failed(self):
        """Test creating a failed UpgradeResult."""
        result = UpgradeResult(
            instance_name="my-instance",
            location="europe-west2-a",
            status="failed",
            error_message="Timeout",
            rolled_back=True,
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_message, "Timeout")
        self.assertTrue(result.rolled_back)


class TestTrackedOp(unittest.TestCase):
    """Test TrackedOp data model."""

    def test_tracked_op_creation(self):
        """Test creating a TrackedOp."""
        inst = InstanceRef(
            name="projects/my-project/locations/europe-west2-a/instances/my-instance",
            short_name="my-instance",
            location="europe-west2-a",
        )
        op = TrackedOp(
            op_name="operations/12345",
            instance=inst,
            start_time=1000.0,
            target_version="v2.0",
        )
        self.assertEqual(op.op_name, "operations/12345")
        self.assertEqual(op.instance.short_name, "my-instance")
        self.assertEqual(op.target_version, "v2.0")


if __name__ == "__main__":
    unittest.main()
