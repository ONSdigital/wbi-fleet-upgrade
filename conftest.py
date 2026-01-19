"""
Pytest configuration for test discovery and imports.

Ensures src/ is on sys.path so tests can import modules directly.
"""

import os
import sys

ROOT_DIR = os.path.dirname(__file__)
SRC_DIR = os.path.join(ROOT_DIR, "src")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
