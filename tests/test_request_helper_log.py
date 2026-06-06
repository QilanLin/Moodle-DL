# -*- coding: utf-8 -*-
"""
Tests for RequestHelper response logging.

Before the fix, the response log was opened with mode='w' on
init (truncating any existing content) and then opened with
mode='a' on every log call. If two moodle-dl instances shared
the same misc_files_path (e.g. ~/.moodle-dl), the second
instance would truncate the first's log.

After the fix, the log is opened in append mode, never
truncated by moodle-dl. A separate file 'responses.meta'
records the PID and start time of each session that wrote
to the log, so a user can tell when a new session was
launched.

Pin points:
  1. log_responses_to is set to the right path
  2. The log file is NOT truncated on init
  3. Writing through log_response() appends to the file
  4. A header line is written to identify each session
"""
import os
import tempfile
import unittest

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.types import MoodleDlOpts, MoodleURL


def make_workspace(tmpdir, log_responses=True):
    os.makedirs(tmpdir, exist_ok=True)
    with open(os.path.join(tmpdir, "config.json"), "w") as f:
        import json
        json.dump({
            "moodle_domain": "keats.kcl.ac.uk",
            "moodle_path": "/",
            "token": "fake",
            "log_responses": log_responses,
        }, f)


def make_helper(tmpdir, log_responses=True):
    make_workspace(tmpdir, log_responses=log_responses)
    opts = MoodleDlOpts()
    opts.path = tmpdir
    opts.log_responses = log_responses
    config = ConfigHelper(opts)
    config.load()
    moodle_url = MoodleURL(use_http=False, domain="keats.kcl.ac.uk", path="/")
    return RequestHelper(config, opts, moodle_url, "fake-token"), config


class TestRequestHelperLog(unittest.TestCase):
    def test_log_path_set_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            helper, _ = make_helper(td, log_responses=True)
            self.assertIsNotNone(helper.log_responses_to)
            self.assertTrue(helper.log_responses_to.endswith("responses.log"))

    def test_log_path_none_when_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            helper, _ = make_helper(td, log_responses=False)
            self.assertIsNone(helper.log_responses_to)

    def test_init_does_not_truncate_existing_log(self):
        """If a previous session wrote to responses.log, the new
        session must NOT truncate it. (Old behavior: mode='w'
        truncated. New behavior: append-only, with a session
        header.)"""
        with tempfile.TemporaryDirectory() as td:
            log_path = os.path.join(td, "moodle-dl", "moodle_dl_misc_files", "responses.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            # Pre-populate with a marker line
            with open(log_path, "w") as f:
                f.write("PREVIOUS SESSION DATA\n")
            # Create a new helper — it must NOT erase the marker
            helper, _ = make_helper(td, log_responses=True)
            with open(log_path) as f:
                content = f.read()
            self.assertIn("PREVIOUS SESSION DATA", content)

    def test_log_response_appends_to_file(self):
        with tempfile.TemporaryDirectory() as td:
            helper, _ = make_helper(td, log_responses=True)
            helper.log_response(
                function="test_func",
                data={"x": 1},
                url="https://keats.kcl.ac.uk/api",
                json_result={"ok": True},
            )
            with open(helper.log_responses_to) as f:
                content = f.read()
            self.assertIn("test_func", content)
            self.assertIn("https://keats.kcl.ac.uk/api", content)

    def test_concurrent_sessions_both_write(self):
        """Two RequestHelper instances pointing at the same
        workspace must both be able to write to responses.log
        without overwriting each other."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td, log_responses=True)
            # First session writes
            helper1, _ = make_helper(td, log_responses=True)
            helper1.log_response(
                function="func1", data={}, url="u1", json_result={},
            )
            # Second session starts (would have truncated the log
            # in the old code)
            helper2, _ = make_helper(td, log_responses=True)
            helper2.log_response(
                function="func2", data={}, url="u2", json_result={},
            )
            with open(helper1.log_responses_to) as f:
                content = f.read()
            self.assertIn("func1", content)
            self.assertIn("func2", content)

    def test_session_header_includes_pid(self):
        """Each new RequestHelper writes a session header so a
        user can tell where one session ends and the next begins."""
        import re
        with tempfile.TemporaryDirectory() as td:
            helper, _ = make_helper(td, log_responses=True)
            with open(helper.log_responses_to) as f:
                content = f.read()
            # The header should mention the PID
            self.assertRegex(content, r"PID:\s*\d+")
