# -*- coding: utf-8 -*-
"""
Tests for ConfigHelper's lazy auth_manager initialization.

Before this fix, ConfigHelper.__init__ always called
AuthSessionManager(self._db_file) at the end. Since
AuthSessionManager.__init__ is cheap (just stores the path),
this didn't matter much for production — but it did mean
that the 'raise if not self._auth_manager' check could
NEVER trigger (since the constructor always returns a
truthy object). That check was dead code.

After this fix:
  - When validate_db=False, _auth_manager stays None
  - Calling get_auth_manager() lazily constructs it
  - The dead-code RuntimeError check is removed
  - Test that 'just read config' doesn't need auth_manager

Pin points:
  1. ConfigHelper(validate_db=False) leaves _auth_manager=None
  2. get_auth_manager() called twice returns the same instance
  3. get_auth_manager() called when validate_db=False constructs
     the AuthSessionManager on first call
  4. validate_db=True initializes _auth_manager at __init__
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
                "moodle_domain": "k",
                "moodle_path": "/",
                "token": "fake_token",
            }, f)


class TestAuthManagerLazyInit(unittest.TestCase):
    def test_validate_false_does_not_init_auth_manager(self):
        """validate_db=False should skip the AuthSessionManager
        constructor too, since the caller doesn't need it for
        just reading config or building a one-off StateRecorder."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            with patch(
                "moodle_dl.auth_session_manager.AuthSessionManager"
            ) as MockASM:
                opts = MoodleDlOpts()
                opts.path = td
                ConfigHelper(opts, validate_db=False)
                # AuthSessionManager should NOT have been constructed
                MockASM.assert_not_called()

    def test_validate_true_does_init_auth_manager(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            with patch(
                "moodle_dl.auth_session_manager.AuthSessionManager"
            ) as MockASM:
                opts = MoodleDlOpts()
                opts.path = td
                ConfigHelper(opts, validate_db=True)
                MockASM.assert_called_once()

    def test_get_auth_manager_returns_instance(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            opts = MoodleDlOpts()
            opts.path = td
            ch = ConfigHelper(opts, validate_db=False)
            # First call constructs lazily
            am = ch.get_auth_manager()
            self.assertIsNotNone(am)

    def test_get_auth_manager_is_idempotent(self):
        """get_auth_manager() called twice should return the SAME
        instance (not reconstruct)."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            opts = MoodleDlOpts()
            opts.path = td
            ch = ConfigHelper(opts, validate_db=False)
            am1 = ch.get_auth_manager()
            am2 = ch.get_auth_manager()
            self.assertIs(am1, am2)

    def test_get_auth_manager_constructs_only_once(self):
        """verify the lazy-init pattern: even if get_auth_manager
        is called many times, AuthSessionManager is only
        constructed once (and _auth_manager is cached)."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            opts = MoodleDlOpts()
            opts.path = td
            ch = ConfigHelper(opts, validate_db=False)
            with patch(
                "moodle_dl.auth_session_manager.AuthSessionManager"
            ) as MockASM:
                ch.get_auth_manager()
                ch.get_auth_manager()
                ch.get_auth_manager()
                MockASM.assert_called_once()

    def test_validate_true_initializes_in_init(self):
        """validate_db=True: _auth_manager is set during __init__."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, minimal=True)
            opts = MoodleDlOpts()
            opts.path = td
            ch = ConfigHelper(opts, validate_db=True)
            # After init, _auth_manager is already set
            self.assertIsNotNone(ch._auth_manager)
