# -*- coding: utf-8 -*-
"""
Tests for StateRecorder._query_cache workspace isolation.

Before this fix, the cache_key for cached methods did NOT include
self.db_file. As a result, the same cache_key would be reused
across different workspaces (different db_file) in the same
process, returning stale data from one workspace while the
caller believes they're reading from another.

Pin points:
  1. _get_cache_key includes db_file in the key
  2. Two recs with different db_file get different cache keys
  3. After a write to rec A, rec B (different db_file) does NOT
     return rec A's cached stale data
"""
import tempfile
import unittest

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import File, MoodleDlOpts


def make_recorder_with_workspace(workspace):
    """Create a StateRecorder pointing at a specific workspace."""
    os.makedirs(workspace, exist_ok=True)
    cfg_path = os.path.join(workspace, "config.json")
    with open(cfg_path, "w") as f:
        import json
        json.dump({
            "moodle_domain": "keats.kcl.ac.uk",
            "moodle_path": "/",
            "token": "fake",
        }, f)
    opts = MoodleDlOpts()
    opts.path = workspace
    config = ConfigHelper(opts)
    return StateRecorder(config, opts), config


import os


def make_file(mid, course_id=86124, **kw):
    defaults = dict(
        module_id=mid,
        section_name="S", section_id=1,
        module_name=f"M{mid}",
        content_filepath="/", content_filename=f"f{mid}.pdf",
        content_fileurl=f"https://keats.kcl.ac.uk/m/{mid}",
        content_filesize=1024, content_timemodified=1700000000,
        module_modname="resource", content_type="resource_file",
        content_isexternalfile=False,
    )
    defaults.update(kw)
    return File(**defaults)


class TestQueryCacheWorkspaceIsolation(unittest.TestCase):
    def test_cache_key_includes_db_file(self):
        """The cache key must include db_file so that different
        workspaces don't share cache entries."""
        with tempfile.TemporaryDirectory() as td:
            workspace_a = os.path.join(td, "ws_a")
            workspace_b = os.path.join(td, "ws_b")
            os.makedirs(workspace_a)
            os.makedirs(workspace_b)

            rec_a, _ = make_recorder_with_workspace(workspace_a)
            rec_b, _ = make_recorder_with_workspace(workspace_b)

            key_a = rec_a._get_cache_key("get_stored_files")
            key_b = rec_b._get_cache_key("get_stored_files")
            self.assertNotEqual(key_a, key_b)

    def test_cache_does_not_leak_across_workspaces(self):
        """A write in workspace A should not be visible via
        rec B's cache (different db_file)."""
        with tempfile.TemporaryDirectory() as td:
            workspace_a = os.path.join(td, "ws_a")
            workspace_b = os.path.join(td, "ws_b")
            os.makedirs(workspace_a)
            os.makedirs(workspace_b)

            rec_a, _ = make_recorder_with_workspace(workspace_a)
            rec_b, _ = make_recorder_with_workspace(workspace_b)

            # Plant a file in A
            rec_a.new_file(make_file(1), 86124, "Course A")
            # Warm up A's cache
            stored_a = rec_a.get_stored_files()
            self.assertEqual(
                len([f for c in stored_a for f in c.files]), 1
            )

            # B's get_stored_files must return EMPTY (B has no files)
            stored_b = rec_b.get_stored_files()
            self.assertEqual(
                len([f for c in stored_b for f in c.files]), 0
            )

    def test_cache_invalidated_after_save_in_workspace_b(self):
        """If a write happens in workspace A and the caller then
        switches to rec B, rec B's cache must not return A's data."""
        with tempfile.TemporaryDirectory() as td:
            workspace_a = os.path.join(td, "ws_a")
            workspace_b = os.path.join(td, "ws_b")
            os.makedirs(workspace_a)
            os.makedirs(workspace_b)

            rec_a, _ = make_recorder_with_workspace(workspace_a)
            rec_b, _ = make_recorder_with_workspace(workspace_b)

            rec_a.new_file(make_file(1), 86124, "Course A")
            rec_a.new_file(make_file(2), 86124, "Course A")
            # A has 2 files
            self.assertEqual(
                len([f for c in rec_a.get_stored_files() for f in c.files]), 2
            )
            # B's view is independent
            self.assertEqual(
                len([f for c in rec_b.get_stored_files() for f in c.files]), 0
            )

            # Now add a file to B
            rec_b.new_file(make_file(99), 86124, "Course B")
            # A's count must remain 2 (cached or fresh), not 3
            self.assertEqual(
                len([f for c in rec_a.get_stored_files() for f in c.files]), 2
            )
            # B's count is now 1
            self.assertEqual(
                len([f for c in rec_b.get_stored_files() for f in c.files]), 1
            )
