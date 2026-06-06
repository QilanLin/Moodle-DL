# -*- coding: utf-8 -*-
"""
Tests for moodle_dl.utils.ProcessLock.

The legacy implementation was TOCTOU:
    if Path(path).exists():
        raise ...
    Path(path).touch()

This breaks under concurrent invocations and leaves stale locks
on crash. The new implementation uses fcntl.flock (POSIX) for
true mutual exclusion, and writes the PID to the lock file so
the unlock path can verify ownership.

Pin points:
  1. First lock() succeeds; second concurrent lock() raises
  2. unlock() releases the lock; a subsequent lock() succeeds
  3. PID is written to the lock file
  4. unlock() of a foreign-owned lock (different PID) does NOT
     remove the file
  5. Cross-workspace: lock in dir A doesn't block lock in dir B
"""
import os
import tempfile
import unittest
from multiprocessing import Process, Value

import pytest

from moodle_dl.utils import ProcessLock


def _try_lock(workspace_path, return_code):
    """Helper for the cross-process test: try to lock, store the
    return code in a shared Value."""
    try:
        ProcessLock.lock(workspace_path)
    except ProcessLock.LockError:
        return_code.value = 1
    else:
        return_code.value = 0


def _hold_and_release(workspace_path, barrier, return_code, hold_seconds):
    """Top-level helper: take the lock, wait at barrier, hold for
    a few seconds, then release. Top-level so multiprocessing can
    pickle it."""
    from multiprocessing import Barrier as _B
    import time as _time
    ProcessLock.lock(workspace_path)
    barrier.wait()  # signal: lock acquired
    _time.sleep(hold_seconds)
    ProcessLock.unlock(workspace_path)
    return_code.value = 0


def _try_acquire_after_barrier(workspace_path, barrier, return_code):
    """Top-level helper: wait at barrier, then attempt to acquire
    the lock. Should raise LockError while p1 is holding."""
    barrier.wait()
    try:
        ProcessLock.lock(workspace_path)
    except ProcessLock.LockError:
        return_code.value = 1
    else:
        return_code.value = 0
        ProcessLock.unlock(workspace_path)


class TestProcessLockBasics(unittest.TestCase):
    def test_first_lock_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            # Should not raise
            ProcessLock.lock(td)

    def test_second_lock_in_same_dir_raises(self):
        with tempfile.TemporaryDirectory() as td:
            ProcessLock.lock(td)
            with self.assertRaises(ProcessLock.LockError):
                ProcessLock.lock(td)

    def test_unlock_then_relock_works(self):
        with tempfile.TemporaryDirectory() as td:
            ProcessLock.lock(td)
            ProcessLock.unlock(td)
            # Should not raise
            ProcessLock.lock(td)

    def test_unlock_unknown_dir_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            # No lock taken
            ProcessLock.unlock(td)  # should not raise


class TestProcessLockPIDFile(unittest.TestCase):
    def test_pid_written_to_lock_file(self):
        with tempfile.TemporaryDirectory() as td:
            ProcessLock.lock(td)
            # The lock file should contain our PID
            from pathlib import Path
            lock_path = Path(td) / "running.lock"
            self.assertTrue(lock_path.exists())
            with open(lock_path) as f:
                contents = f.read().strip()
            self.assertEqual(int(contents), os.getpid())

    def test_unlock_does_not_remove_foreign_lock(self):
        """A different process's lock file should not be removed
        by our unlock (it would be lying about the PID check
        otherwise)."""
        with tempfile.TemporaryDirectory() as td:
            from pathlib import Path
            lock_path = Path(td) / "running.lock"
            # Simulate another process's lock
            lock_path.write_text(str(os.getpid() + 10000))
            ProcessLock.unlock(td)
            # The foreign lock file is still there
            self.assertTrue(lock_path.exists())


class TestProcessLockCrossWorkspace(unittest.TestCase):
    def test_lock_in_dir_a_does_not_block_dir_b(self):
        with tempfile.TemporaryDirectory() as base:
            dir_a = os.path.join(base, "workspace_a")
            dir_b = os.path.join(base, "workspace_b")
            os.makedirs(dir_a)
            os.makedirs(dir_b)
            ProcessLock.lock(dir_a)
            # dir_b should still be lockable
            ProcessLock.lock(dir_b)
            # Cleanup
            ProcessLock.unlock(dir_a)
            ProcessLock.unlock(dir_b)


class TestProcessLockConcurrentProcesses(unittest.TestCase):
    """Real cross-process test using multiprocessing — proves
    the fcntl.flock actually excludes."""

    def test_concurrent_locks_exclude_each_other(self):
        with tempfile.TemporaryDirectory() as td:
            from multiprocessing import Barrier
            barrier = Barrier(2)
            p1_return = Value("i", -1)
            p2_return = Value("i", -1)

            # p1 takes the lock, signals via barrier, holds 0.5s,
            # then releases.
            p1 = Process(
                target=_hold_and_release,
                args=(td, barrier, p1_return, 0.5),
            )
            p1.start()
            # p2 waits for the barrier, then tries to lock (while
            # p1 is holding).
            p2 = Process(
                target=_try_acquire_after_barrier,
                args=(td, barrier, p2_return),
            )
            p2.start()
            p1.join()
            p2.join()
            # p1 succeeded
            self.assertEqual(p1_return.value, 0)
            # p2 was blocked by fcntl.flock → raised LockError
            self.assertEqual(p2_return.value, 1)

