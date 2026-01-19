"""
Unit tests for configuration.
"""

import unittest
from argparse import Namespace
from config import UpgraderConfig


class TestUpgraderConfig(unittest.TestCase):
    """Test UpgraderConfig data model."""

    def test_config_defaults(self):
        """Test default configuration values."""
        config = UpgraderConfig(project_id="my-project", locations=["europe-west2-a"])
        self.assertEqual(config.project_id, "my-project")
        self.assertEqual(config.locations, ["europe-west2-a"])
        self.assertFalse(config.dry_run)
        self.assertEqual(config.max_parallel, 10)
        self.assertEqual(config.timeout, 7200)
        self.assertEqual(config.poll_interval, 20)
        self.assertFalse(config.rollback_on_failure)
        self.assertEqual(config.health_check_timeout, 800)
        self.assertEqual(config.stagger_delay, 5.0)
        self.assertFalse(config.verbose)

    def test_config_from_args(self):
        """Test creating config from command-line arguments."""
        args = Namespace(
            project="test-project",
            locations=["europe-west2-a", "europe-west2-b"],
            dry_run=True,
            max_parallel=10,
            timeout=3600,
            poll_interval=30,
            rollback_on_failure=True,
            health_check_timeout=900,
            stagger_delay=5.0,
            verbose=True,
        )
        config = UpgraderConfig.from_args(args)

        self.assertEqual(config.project_id, "test-project")
        self.assertEqual(config.locations, ["europe-west2-a", "europe-west2-b"])
        self.assertTrue(config.dry_run)
        self.assertEqual(config.max_parallel, 10)
        self.assertEqual(config.timeout, 3600)
        self.assertEqual(config.poll_interval, 30)
        self.assertTrue(config.rollback_on_failure)
        self.assertEqual(config.health_check_timeout, 900)
        self.assertEqual(config.stagger_delay, 5.0)
        self.assertTrue(config.verbose)


if __name__ == "__main__":
    unittest.main()
