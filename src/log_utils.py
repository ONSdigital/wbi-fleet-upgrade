"""
Logging utilities for the Vertex AI Workbench Fleet Upgrader.
"""

import logging
import sys


def setup_logging(
    verbose: bool = False, log_file: str = "workbench-upgrade.log"
) -> logging.Logger:
    """
    Set up logging configuration.

    Args:
        verbose: Enable verbose (DEBUG) logging
        log_file: Path to log file

    Returns:
        Logger instance
    """
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )

    return logging.getLogger(__name__)
