"""
Unit tests for logging utilities.
"""

import logging
import unittest
from log_utils import setup_logging


class TestLogUtils(unittest.TestCase):
    """Test logging utilities."""

    def test_setup_logging_default(self):
        """Test default logging setup."""
        logger = setup_logging()
        self.assertIsInstance(logger, logging.Logger)

    def test_setup_logging_verbose(self):
        """Test verbose logging setup."""
        logger = setup_logging(verbose=True)
        self.assertIsInstance(logger, logging.Logger)


if __name__ == "__main__":
    unittest.main()
