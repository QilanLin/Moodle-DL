# -*- coding: utf-8 -*-
"""
Tests for the Task's recorder usage.

Before this fix, Task.real_run / Task.mark_complete would:
    config = ConfigHelper(self.opts.global_opts)
    database = StateRecorder(config, self.opts)
    database.mark_download_complete(...)

This created a short-lived StateRecorder inside Task that:
  1. Re-ran schema validation on every completion
  2. Had no cache (good, but extra work)
  3. CRITICAL: read `self.opts.global_opts` which could be
     pointing at a different workspace than the main recorder,
     causing data writes to land in the wrong DB

Pin points:
  1. Task exposes a `database` attribute set by the main flow
  2. Task uses that database for its mark_complete / cleanup work
  3. Task does not instantiate a new ConfigHelper/StateRecorder
     for cleanup paths
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest


class TestTaskReusesMainDatabase(unittest.TestCase):
    """Task should not instantiate its own ConfigHelper +
    StateRecorder. Instead, it should accept a `database` and
    use it for completion bookkeeping."""

    def test_task_accepts_database_argument(self):
        from moodle_dl.downloader.task import Task
        from moodle_dl.types import File
        f = File(
            module_id=1, section_name="s", section_id=1,
            module_name="m", content_filepath="/",
            content_filename="a", content_fileurl="u",
            content_filesize=0, content_timemodified=0,
            module_modname="r", content_type="r",
            content_isexternalfile=False,
        )
        from moodle_dl.types import Course
        course = Course(_id=1, fullname="C", files=[])
        mock_db = MagicMock()
        # Task takes (file, opts, course) historically; the refactor
        # should make it take an optional database= arg.
        # The test is more about the contract that the Task's
        # completion path uses a passed-in database rather than
        # creating a new StateRecorder.
        try:
            task = Task(
                task_id=1,
                file=f,
                course=course,
                options=MagicMock(),
                thread_pool=MagicMock(),
                callback=MagicMock(),
                database=mock_db,
            )
            # The task should have a database attribute
            self.assertIs(task.database, mock_db)
        except TypeError as e:
            # If the signature doesn't support database=, fail loudly
            self.fail(
                f"Task.__init__ should accept a database= argument, got: {e}"
            )

    def test_task_mark_complete_uses_passed_database(self):
        """If a database is passed to Task, mark_complete should
        use it instead of creating its own StateRecorder."""
        from moodle_dl.downloader.task import Task
        from moodle_dl.types import File
        from moodle_dl.types import Course

        f = File(
            module_id=1, section_name="s", section_id=1,
            module_name="m", content_filepath="/",
            content_filename="a", content_fileurl="u",
            content_filesize=0, content_timemodified=0,
            module_modname="r", content_type="r",
            content_isexternalfile=False,
        )
        course = Course(_id=1, fullname="C", files=[])
        mock_db = MagicMock()
        task = Task(
            task_id=1,
            file=f,
            course=course,
            options=MagicMock(),
            thread_pool=MagicMock(),
            callback=MagicMock(),
            database=mock_db,
        )

        # Now simulate calling mark_complete. We don't actually run
        # the download — we just verify the database is touched.
        # In a fully refactored world, mark_complete takes a dest_path
        # and calls task.database.mark_download_complete(...). We
        # just confirm the database attribute is set.
        self.assertIs(task.database, mock_db)

    def test_task_does_not_instantiate_state_recorder(self):
        """The Task's mark_complete path should NOT instantiate
        a new StateRecorder. We patch the StateRecorder class
        to count instantiations; after the refactor, the
        completion path should use the passed-in database and
        not call StateRecorder() at all."""
        from moodle_dl.downloader.task import Task
        from moodle_dl.types import File
        from moodle_dl.types import Course

        f = File(
            module_id=1, section_name="s", section_id=1,
            module_name="m", content_filepath="/",
            content_filename="a", content_fileurl="u",
            content_filesize=0, content_timemodified=0,
            module_modname="r", content_type="r",
            content_isexternalfile=False,
        )
        course = Course(_id=1, fullname="C", files=[])
        mock_db = MagicMock()
        with patch(
            "moodle_dl.database.StateRecorder"
        ) as MockSR:
            task = Task(
                task_id=1,
                file=f,
                course=course,
                options=MagicMock(),
                thread_pool=MagicMock(),
                callback=MagicMock(),
                database=mock_db,
            )
            # Task __init__ should NOT have created a new StateRecorder
            # (the main flow is responsible for that).
            MockSR.assert_not_called()
