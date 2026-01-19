"""
Unit tests for CLI module.
"""

import unittest
from argparse import Namespace
from unittest.mock import MagicMock, patch
from cli import build_parser, main


class TestCLI(unittest.TestCase):
    """Test CLI argument parsing and entry point."""

    def test_build_parser_creates_parser(self):
        """Test parser is created with expected arguments."""
        parser = build_parser()

        self.assertIsNotNone(parser)

        # Parse a basic command to verify parser works
        args = parser.parse_args(
            [
                "--project",
                "test-project",
                "--locations",
                "europe-west2-a",
            ]
        )

        self.assertEqual(args.project, "test-project")
        self.assertEqual(args.locations, ["europe-west2-a"])

    def test_parser_with_all_options(self):
        """Test parser handles all command-line options."""
        parser = build_parser()

        args = parser.parse_args(
            [
                "--project",
                "test-project",
                "--locations",
                "europe-west2-a",
                "europe-west2-b",
                "--instance",
                "my-instance",
                "--rollback",
                "--dry-run",
                "--max-parallel",
                "10",
                "--timeout",
                "3600",
                "--poll-interval",
                "30",
                "--rollback-on-failure",
                "--health-check-timeout",
                "900",
                "--stagger-delay",
                "5.0",
                "--verbose",
            ]
        )

        self.assertEqual(args.project, "test-project")
        self.assertEqual(args.locations, ["europe-west2-a", "europe-west2-b"])
        self.assertEqual(args.instance, "my-instance")
        self.assertTrue(args.rollback)
        self.assertTrue(args.dry_run)
        self.assertEqual(args.max_parallel, 10)
        self.assertEqual(args.timeout, 3600)
        self.assertEqual(args.poll_interval, 30)
        self.assertTrue(args.rollback_on_failure)
        self.assertEqual(args.health_check_timeout, 900)
        self.assertEqual(args.stagger_delay, 5.0)
        self.assertTrue(args.verbose)

    def test_parser_requires_project(self):
        """Test parser requires project argument."""
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["--locations", "europe-west2-a"])

    def test_parser_requires_locations(self):
        """Test parser requires locations argument."""
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["--project", "test-project"])

    @patch("cli.FleetUpgrader")
    @patch("cli.setup_logging")
    @patch("cli.UpgraderConfig")
    def test_main_upgrade_mode(
        self, mock_config_class, mock_setup_logging, mock_upgrader_class
    ):
        """Test main function in upgrade mode."""
        mock_config = MagicMock()
        mock_config.project_id = "test-project"
        mock_config.locations = ["europe-west2-a"]
        mock_config.dry_run = False
        mock_config.max_parallel = 5
        mock_config.timeout = 7200
        mock_config.poll_interval = 20
        mock_config.rollback_on_failure = False
        mock_config.health_check_timeout = 600
        mock_config.stagger_delay = 3.0

        mock_config_class.from_args.return_value = mock_config

        mock_upgrader = MagicMock()
        mock_upgrader.run.return_value = {"failed": 0}
        mock_upgrader_class.return_value = mock_upgrader

        result = main(
            [
                "--project",
                "test-project",
                "--locations",
                "europe-west2-a",
            ]
        )

        self.assertEqual(result, 0)
        mock_upgrader_class.assert_called_once()
        mock_upgrader.run.assert_called_once_with(instance_id=None)

    @patch("cli.FleetRollback")
    @patch("cli.setup_logging")
    @patch("cli.UpgraderConfig")
    def test_main_rollback_mode(
        self, mock_config_class, mock_setup_logging, mock_rollback_class
    ):
        """Test main function in rollback mode."""
        mock_config = MagicMock()
        mock_config.project_id = "test-project"
        mock_config.locations = ["europe-west2-a"]
        mock_config.dry_run = False
        mock_config.max_parallel = 5
        mock_config.timeout = 7200
        mock_config.poll_interval = 20
        mock_config.health_check_timeout = 600
        mock_config.stagger_delay = 3.0

        mock_config_class.from_args.return_value = mock_config

        mock_rollback = MagicMock()
        mock_rollback.run.return_value = {"failed": 0}
        mock_rollback_class.return_value = mock_rollback

        result = main(
            [
                "--project",
                "test-project",
                "--locations",
                "europe-west2-a",
                "--rollback",
            ]
        )

        self.assertEqual(result, 0)
        mock_rollback_class.assert_called_once()
        mock_rollback.run.assert_called_once_with(instance_id=None)

    @patch("cli.FleetUpgrader")
    @patch("cli.setup_logging")
    @patch("cli.UpgraderConfig")
    def test_main_single_instance_mode(
        self, mock_config_class, mock_setup_logging, mock_upgrader_class
    ):
        """Test main function with single instance."""
        mock_config = MagicMock()
        mock_config.project_id = "test-project"
        mock_config.locations = ["europe-west2-a"]
        mock_config.dry_run = False
        mock_config.max_parallel = 5
        mock_config.timeout = 7200
        mock_config.poll_interval = 20
        mock_config.rollback_on_failure = False
        mock_config.health_check_timeout = 600
        mock_config.stagger_delay = 3.0

        mock_config_class.from_args.return_value = mock_config

        mock_upgrader = MagicMock()
        mock_upgrader.run.return_value = {"failed": 0}
        mock_upgrader_class.return_value = mock_upgrader

        result = main(
            [
                "--project",
                "test-project",
                "--locations",
                "europe-west2-a",
                "--instance",
                "my-instance",
            ]
        )

        self.assertEqual(result, 0)
        mock_upgrader.run.assert_called_once_with(instance_id="my-instance")

    @patch("cli.FleetUpgrader")
    @patch("cli.setup_logging")
    @patch("cli.UpgraderConfig")
    def test_main_returns_failure_exit_code(
        self, mock_config_class, mock_setup_logging, mock_upgrader_class
    ):
        """Test main function returns exit code 1 when upgrades fail."""
        mock_config = MagicMock()
        mock_config.project_id = "test-project"
        mock_config.locations = ["europe-west2-a"]
        mock_config.dry_run = False
        mock_config.max_parallel = 5
        mock_config.timeout = 7200
        mock_config.poll_interval = 20
        mock_config.rollback_on_failure = False
        mock_config.health_check_timeout = 600
        mock_config.stagger_delay = 3.0

        mock_config_class.from_args.return_value = mock_config

        mock_upgrader = MagicMock()
        mock_upgrader.run.return_value = {"failed": 2}
        mock_upgrader_class.return_value = mock_upgrader

        result = main(
            [
                "--project",
                "test-project",
                "--locations",
                "europe-west2-a",
            ]
        )

        self.assertEqual(result, 1)

    @patch("cli.setup_logging")
    @patch("cli.UpgraderConfig")
    def test_main_uses_correct_log_file_for_rollback(
        self, mock_config_class, mock_setup_logging
    ):
        """Test main uses rollback log file when in rollback mode."""
        mock_config = MagicMock()
        mock_config.project_id = "test-project"
        mock_config.locations = ["europe-west2-a"]

        mock_config_class.from_args.return_value = mock_config

        with patch("cli.FleetRollback") as mock_rollback_class:
            mock_rollback = MagicMock()
            mock_rollback.run.return_value = {"failed": 0}
            mock_rollback_class.return_value = mock_rollback

            main(
                [
                    "--project",
                    "test-project",
                    "--locations",
                    "europe-west2-a",
                    "--rollback",
                ]
            )

            mock_setup_logging.assert_called_once()
            call_args = mock_setup_logging.call_args
            self.assertEqual(call_args[1]["log_file"], "workbench-rollback.log")

    @patch("cli.setup_logging")
    @patch("cli.UpgraderConfig")
    def test_main_uses_correct_log_file_for_upgrade(
        self, mock_config_class, mock_setup_logging
    ):
        """Test main uses upgrade log file when in upgrade mode."""
        mock_config = MagicMock()
        mock_config.project_id = "test-project"
        mock_config.locations = ["europe-west2-a"]

        mock_config_class.from_args.return_value = mock_config

        with patch("cli.FleetUpgrader") as mock_upgrader_class:
            mock_upgrader = MagicMock()
            mock_upgrader.run.return_value = {"failed": 0}
            mock_upgrader_class.return_value = mock_upgrader

            main(
                [
                    "--project",
                    "test-project",
                    "--locations",
                    "europe-west2-a",
                ]
            )

            mock_setup_logging.assert_called_once()
            call_args = mock_setup_logging.call_args
            self.assertEqual(call_args[1]["log_file"], "workbench-upgrade.log")


if __name__ == "__main__":
    unittest.main()
