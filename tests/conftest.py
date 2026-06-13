"""conftest.py for tests directory.

Pytest auto-discovers fixtures from conftest.py. By importing
the shared fixtures from tests._support.fixtures here, they
become available to all test files in the tests/ subtree
without each test needing an explicit import.

This is a TEST-LEVEL conftest.py, not a shared-fixture
conftest. The shared fixtures live in tests/_support/fixtures.py
and are re-exported here for convenience.
"""
import os
import sys

# Make moodle_dl importable from the helper module (it does
# `from moodle_dl...`).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Now import the helpers
sys.path.insert(0, os.path.dirname(__file__))
from _support.fixtures import (  # noqa: E402
    make_task_for_tests,
    query_count,
    query_one,
    range_http_server,
    tmp_db,
    tmp_db_recorder,
    write_part_file,
)

__all__ = [
    'make_task_for_tests',
    'query_count',
    'query_one',
    'range_http_server',
    'tmp_db',
    'tmp_db_recorder',
    'write_part_file',
]
