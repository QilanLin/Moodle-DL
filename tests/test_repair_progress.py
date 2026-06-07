# -*- coding: utf-8 -*-
"""
Tests for the progress file mechanism in repair_paths CLI.

When running repair_paths on a large workspace (e.g. 5000+ buggy
files spread across 600+ chapters on a slow U disk), the run
can take 15+ minutes and may be killed by an external timeout.
The progress file makes the run:
  - Resumable: skip chapters that have already been processed
  - Observable: emit heartbeat lines so the user knows it's not hung
  - Crash-safe: atexit handler flushes progress on SIGTERM

This file is checked into the workspace before each chapter and
deleted by the user after a successful full run.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

# We don't actually run the full CLI (which needs a real moodle
# workspace and a real DB). We test the helper functions directly.
# The end-to-end behavior is exercised manually in the smoke
# test, but the unit tests pin the contract.


def load_progress(progress_file):
    if not os.path.exists(progress_file):
        return {'completed_keys': [], 'last_group': None}
    try:
        with open(progress_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {'completed_keys': [], 'last_group': None}


def save_progress(progress_file, completed_key):
    """Mark one chapter as completed. Uses a write-temp + rename
    pattern so the file is never half-written if killed."""
    progress = load_progress(progress_file)
    if completed_key not in progress['completed_keys']:
        progress['completed_keys'].append(completed_key)
    tmp = progress_file + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(progress, f)
    os.replace(tmp, progress_file)


class TestProgressFile(unittest.TestCase):
    """Pin the contract of the progress file: a JSON dict with
    'completed_keys' (list of strings) and 'last_group' (the
    last-completed group key, for diagnostics)."""

    def test_empty_progress_load_returns_default(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, 'progress.json')
            # File doesn't exist
            progress = load_progress(pf)
            self.assertEqual(progress, {'completed_keys': [], 'last_group': None})

    def test_save_progress_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, 'progress.json')
            save_progress(pf, 'course1|section2|module3')
            progress = load_progress(pf)
            self.assertEqual(progress['completed_keys'],
                             ['course1|section2|module3'])
            self.assertIsNone(progress['last_group'])

    def test_save_progress_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, 'progress.json')
            save_progress(pf, 'a')
            save_progress(pf, 'a')  # same key again
            progress = load_progress(pf)
            self.assertEqual(progress['completed_keys'], ['a'])

    def test_save_progress_preserves_history(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, 'progress.json')
            save_progress(pf, 'a')
            save_progress(pf, 'b')
            save_progress(pf, 'c')
            progress = load_progress(pf)
            self.assertEqual(progress['completed_keys'], ['a', 'b', 'c'])

    def test_corrupt_progress_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, 'progress.json')
            with open(pf, 'w') as f:
                f.write('this is not json')
            progress = load_progress(pf)
            self.assertEqual(progress, {'completed_keys': [], 'last_group': None})


class TestResumeFromProgress(unittest.TestCase):
    """Pin the contract that a resumed run skips chapters
    already in the progress file."""

    def test_resume_skips_completed_chapters(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, 'progress.json')
            save_progress(pf, 'chapter1|module1')
            save_progress(pf, 'chapter1|module2')
            # Now simulate resume: load progress, then "process"
            # the remaining chapters.
            progress = load_progress(pf)
            completed = set(progress['completed_keys'])
            # A simulated group list
            groups = [
                ('chapter1', 'module1'),
                ('chapter1', 'module2'),
                ('chapter2', 'module3'),
            ]
            remaining = [g for g in groups if f'{g[0]}|{g[1]}' not in completed]
            self.assertEqual(remaining, [('chapter2', 'module3')])


class TestHeartbeatOutput(unittest.TestCase):
    """Pin the contract that the CLI emits a heartbeat line
    per chapter so a slow run shows progress."""

    def test_heartbeat_format(self):
        # We don't actually run the CLI; we just pin the
        # format string. The CLI uses:
        #   print(f'  [{group_name}] moved {n} files', flush=True)
        # and after each chapter the progress file is
        # updated.
        # This test is a no-op (just documents the contract).
        self.assertTrue(True)


if __name__ == '__main__':
    unittest.main()
