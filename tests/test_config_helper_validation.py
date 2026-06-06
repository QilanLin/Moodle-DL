# -*- coding: utf-8 -*-
"""
Tests for ConfigHelper / StateRecorder validation laziness.

Currently, ConfigHelper.__init__ always instantiates a
StateRecorder, which in turn validates the schema (calls
PRAGMA user_version, lists all required tables). This is the
correct behaviour for `--init` and main(), but is wasteful
for the many call sites that just need a ConfigHelper to
read a single property.

The Task._get_or_create_database fallback path also calls
ConfigHelper, which triggers yet another full schema
validation. In a task pipeline of 1000 files, this is 1000
unnecessary schema validations.

After the fix, ConfigHelper can be constructed in two
modes:
  - validate=True (default, preserves main() behavior)
  - validate=False (skips the StateRecorder construction,
    used by call sites that just need property access)

Pin points:
  1. ConfigHelper(validate=False) does NOT create a
     StateRecorder on init
  2. ConfigHelper(validate=True) DOES create a StateRecorder
     (preserves backward compat)
  3. ConfigHelper(validate=False) can still be used to read
     config properties without raising
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts


def make_workspace(tmpdir, minimal=False):
    os.makedirs(tmpdir, exist_ok=True)
    with open(os.path.join(tmpdir, "config.json"), "w") as f:
        import json
        if minimal:
            json.dump({"moodle_domain": "k", "moodle_path": "/", "token": "t"}, f)
        else:
            json.dump({
                "moodle_domain": "k", "moodle_path": "/",
                "token": "fake",
            }, f)


class TestConfigHelperValidation(unittest.TestCase):
    def test_validate_false_skips_state_recorder(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            with patch("moodle_dl.database.StateRecorder") as MockSR:
                opts = MoodleDlOpts()
                opts.path = td
                ConfigHelper(opts, validate_db=False)
                MockSR.assert_not_called()

    def test_validate_true_creates_state_recorder(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            with patch("moodle_dl.database.StateRecorder") as MockSR:
                opts = MoodleDlOpts()
                opts.path = td
                ConfigHelper(opts, validate_db=True)
                MockSR.assert_called_once()

    def test_default_validates(self):
        """Backward compat: ConfigHelper() still validates by
        default so existing callers (which rely on the side
        effect) keep working."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            with patch("moodle_dl.database.StateRecorder") as MockSR:
                opts = MoodleDlOpts()
                opts.path = td
                ConfigHelper(opts)
                MockSR.assert_called_once()

    def test_validate_false_can_still_read_config(self):
        """validate=False doesn't break property access, but the
        caller must call .load() first (this is the standard
        ConfigHelper contract — validate_db only controls
        whether StateRecorder is constructed as a side effect)."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            opts = MoodleDlOpts()
            opts.path = td
            ch = ConfigHelper(opts, validate_db=False)
            ch.load()  # explicit load (the standard contract)
            # Property access works after load
            self.assertEqual(ch.get_moodle_domain(), "k")
