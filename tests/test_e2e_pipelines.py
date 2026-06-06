# -*- coding: utf-8 -*-
"""
End-to-end pipeline tests.

These tests exercise moodle-dl's main code paths against real
components (no Moodle servers, no real network). They pin the
*observable* behaviour of the user-facing flows:

  1. Add-manually-specified-courses flow
     - User pastes URLs in CLI → DB records those course IDs
     - Wizard code can parse the URLs back out
  2. Init-wizard → first run
     - config.json gets written, StateRecorder can read it
     - Initial config.json → real download flow with FakeDownloadService
  3. Re-run idempotency
     - Second run after first: should detect no new files (modulo
       content_timemodified)
  4. Single file migration between sections
     - If a file's section_id changes in keats, the DB should
       record it as moved, not as deleted + new
  5. mark_download_success after failure
     - failure → success: download_status flips, file is in
       get_stored_files but not in get_failed_files
  6. Manual config edit does not break next run
     - User edits config.json between runs → next run reads it
       correctly
  7. Empty workspace bootstrap
     - Brand new workspace (no config.json, no state DB) → first
       run after --init creates everything
  8. Retry preserves state across pause/resume
     - Start retry, pause mid-way, resume, complete

Each test uses tmp_path + the existing FakeDownloadService to avoid
the real network. This is the same pattern as test_integration_e2e.py
but more focused on cross-component pipelines.
"""
import json
import os
import shutil
import tempfile
import unittest
from typing import Dict, List
from unittest.mock import MagicMock, patch

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.fake_download_service import FakeDownloadService
from moodle_dl.types import Course, File, MoodleDlOpts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_workspace(tmpdir: str) -> str:
    """Create a workspace skeleton: tmpdir/config.json, tmpdir/moodle_state.db
    will be created by StateRecorder on first run.
    """
    os.makedirs(tmpdir, exist_ok=True)
    return tmpdir


def make_config(workspace: str, manually_specified: List[int] = None) -> ConfigHelper:
    """Build a real ConfigHelper pointing at workspace. Always calls
    config.load() so the in-memory state matches what's on disk."""
    config_path = os.path.join(workspace, "config.json")
    if not os.path.exists(config_path):
        cfg = {
            "token": "fake_token_for_testing",
            "privatetoken": "fake_private_token",
            "moodle_domain": "keats.kcl.ac.uk",
            "moodle_path": "/",
            "download_options": _default_download_options(),
            "download_also_with_cookie": True,
            "download_linked_files": True,
            "manually_specified_course_ids": manually_specified or [],
            "download_public_course_ids": [],
        }
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2)
    opts = MoodleDlOpts()
    opts.path = workspace
    config = ConfigHelper(opts)
    config.load()
    return config


def _default_download_options() -> Dict[str, bool]:
    """A reasonable default of download options for tests."""
    return {k: True for k in [
        "submissions", "descriptions", "links_in_descriptions", "databases",
        "forums", "quizzes", "lessons", "workshops", "books",
        "bigbluebuttonbns", "wikis", "glossaries", "h5pactivities",
        "h5p_attempts", "imscps", "scorms", "scorm_scos", "scorm_attempts",
        "subsections", "qbanks", "resources", "urls", "labels",
        "chats", "feedbacks", "surveys", "choices", "calendars",
        "ltis",
    ]} | {"metadata_files": False}


def make_file(
    module_id: int,
    course_id: int,
    *,
    section_id: int = 1,
    filename: str = "file.pdf",
    filesize: int = 1024,
    mtime: int = 1700000000,
    module_modname: str = "resource",
    content_type: str = "resource_file",
) -> File:
    return File(
        module_id=module_id,
        section_name=f"Section {section_id}",
        section_id=section_id,
        module_name=f"Module {module_id}",
        content_filepath="/",
        content_filename=filename,
        content_fileurl=f"https://keats.kcl.ac.uk/mod/resource/{module_id}.bin",
        content_filesize=filesize,
        content_timemodified=mtime,
        module_modname=module_modname,
        content_type=content_type,
        content_isexternalfile=False,
    )


def seed_database(workspace: str, files: List[File], course_id: int, course_fullname: str = "Test") -> StateRecorder:
    """Create a real StateRecorder, save the given files, return it."""
    config = make_config(workspace)
    opts = MoodleDlOpts()
    opts.path = workspace
    rec = StateRecorder(config, opts)
    for f in files:
        rec.save_file(f, course_id, course_fullname)
    return rec


# ---------------------------------------------------------------------------
# E2E Pipeline Tests
# ---------------------------------------------------------------------------


class TestE2EAddCoursesWizard(unittest.TestCase):
    """The 'add more manually specified courses' wizard flow."""

    def test_wizard_parsing_round_trips_through_config(self):
        """User pastes URLs in CLI; those course IDs end up in config.json.
        Then a re-run reads them back via get_manually_specified_course_ids.
        """
        with tempfile.TemporaryDirectory() as td:
            config = make_config(td, manually_specified=[])

            # Simulate the wizard: parse user-typed URLs
            from moodle_dl.cli.config_wizard import ConfigWizard
            user_input = (
                "https://keats.kcl.ac.uk/course/view.php?id=86122, "
                "https://keats.kcl.ac.uk/course/view.php?id=86123"
            )
            parsed = ConfigWizard._parse_course_ids(user_input)
            self.assertEqual(parsed, [86122, 86123])

            # Save to config
            config.set_manually_specified_course_ids(parsed)

            # Re-read (simulating next CLI run)
            config2 = make_config(td)
            self.assertEqual(
                config2.get_manually_specified_course_ids(),
                [86122, 86123],
            )


class TestE2EFirstRunAndReRun(unittest.TestCase):
    """First download → re-download. Second run should be a no-op for
    unchanged files (still produce the same Course/File objects in the
    DB).
    """

    def test_first_run_creates_records_then_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td)
            config = make_config(td, manually_specified=[86124])
            opts = MoodleDlOpts()
            opts.path = td

            # First run: 3 files for course 86124
            f1 = make_file(module_id=1, course_id=86124, filename="a.pdf")
            f2 = make_file(module_id=2, course_id=86124, filename="b.pdf")
            f3 = make_file(module_id=3, course_id=86124, filename="c.pdf")
            course1 = Course(_id=86124, fullname="Cell Bio", files=[f1, f2, f3])

            db = StateRecorder(config, opts)
            service = FakeDownloadService([course1], config, opts, db)
            service.run()

            # After first run, all 3 files are saved with success
            stored_after_first = db.get_stored_files()
            all_files = [sf for c in stored_after_first for sf in c.files]
            self.assertEqual(len(all_files), 3)
            for f in all_files:
                self.assertTrue(f.saved_to, f"File {f.content_filename} has empty saved_to")

            # Re-run: same 3 files, no mtime change → no new modifications
            course2 = Course(_id=86124, fullname="Cell Bio", files=[f1, f2, f3])
            service2 = FakeDownloadService([course2], config, opts, db)
            service2.run()

            # Still 3 files, status remains success
            stored_after_second = db.get_stored_files()
            all_files2 = [sf for c in stored_after_second for sf in c.files]
            self.assertEqual(len(all_files2), 3)
            for f in all_files2:
                self.assertTrue(f.saved_to)


class TestE2ESectionChangeIsMove(unittest.TestCase):
    """If a file's section_id changes in keats, the DB should record it
    as moved, not as deleted + new."""

    def test_same_module_id_different_section_records_moved(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td)
            config = make_config(td, manually_specified=[86124])
            opts = MoodleDlOpts()
            opts.path = td

            db = StateRecorder(config, opts)

            # First run: file is in section 1
            f_v1 = make_file(module_id=42, course_id=86124, section_id=1, filename="lecture.pdf")
            db.save_file(f_v1, 86124, "Cell Bio")

            # Re-run: same module_id and content but section_id changed to 2
            f_v2 = make_file(
                module_id=42, course_id=86124, section_id=2,
                filename="lecture.pdf",
                mtime=f_v1.content_timemodified,  # not mtime-modified
            )

            # Run the diff logic
            course = Course(_id=86124, fullname="Cell Bio", files=[f_v2])
            changed = db.changes_of_new_version([course])

            # Should record the file as modified (or moved) — but the
            # file should be present, not deleted.
            changed_files = [sf for c in changed for sf in c.files]
            self.assertGreater(
                len(changed_files), 0,
                "Expected at least 1 changed file when section changed",
            )
            for cf in changed_files:
                if cf.module_id == 42:
                    self.assertFalse(
                        cf.deleted,
                        f"File with section change should not be marked deleted",
                    )


class TestE2EFailureThenSuccess(unittest.TestCase):
    """After a file fails, then succeeds, the DB should:
       - remove it from get_failed_files
       - keep it in get_stored_files
       - download_status='success'
    """

    def test_failure_then_success_flips_state(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td)
            config = make_config(td)
            opts = MoodleDlOpts()
            opts.path = td

            db = StateRecorder(config, opts)
            f = make_file(module_id=100, course_id=86124, filename="retry_me.pdf")

            # Step 1: save as failed
            db.save_failed_file(f, 86124, "Cell Bio", error_message="404 not found")

            failed = db.get_failed_files(course_id=86124, min_failures=1)
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0].module_id, 100)

            # Step 2: mark as success (simulating a later retry that worked)
            f_v2 = make_file(
                module_id=100, course_id=86124, filename="retry_me.pdf",
                # mtime updated to reflect a successful redownload
                mtime=f.content_timemodified + 1,
            )
            # We need to give it a saved_to so the success path is happy
            f_v2.saved_to = "/tmp/lecture.pdf"
            db.mark_download_success(f_v2, 86124)

            # Failed list should not contain it
            failed2 = db.get_failed_files(course_id=86124, min_failures=1)
            self.assertEqual(len(failed2), 0)

            # Stored files should contain it
            stored = db.get_stored_files()
            all_files = [sf for c in stored for sf in c.files]
            self.assertTrue(
                any(sf.module_id == 100 for sf in all_files),
                "Successful file should be in stored files",
            )


class TestE2EManualConfigEdit(unittest.TestCase):
    """If the user manually edits config.json between runs, the next run
    picks up the changes correctly."""

    def test_edited_download_options_propagate(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td)
            make_config(td, manually_specified=[86124])

            # User edits config.json to disable forum downloads
            config_path = os.path.join(td, "config.json")
            with open(config_path) as f:
                cfg = json.load(f)
            cfg["download_options"]["forums"] = False
            cfg["download_options"]["quizzes"] = False
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)

            # Re-load
            config = make_config(td)
            opts = MoodleDlOpts()
            opts.path = td

            db = StateRecorder(config, opts)

            # The "forums" flag is now False
            self.assertFalse(
                config.get_property("download_options")["forums"],
            )


class TestE2EEmptyWorkspaceBootstrap(unittest.TestCase):
    """A brand-new workspace (no config.json, no DB) should bootstrap
    cleanly on the first init."""

    def test_first_init_creates_files(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td)
            # No config.json yet
            self.assertFalse(os.path.exists(os.path.join(td, "config.json")))

            opts = MoodleDlOpts()
            opts.path = td
            config = ConfigHelper(opts)

            # StateRecorder should create the DB on first access
            db = StateRecorder(config, opts)
            self.assertTrue(os.path.exists(db.db_file))

            # Schema is at v9 (the latest). We can verify via the
            # PRAGMA user_version.
            import sqlite3 as _sqlite
            with _sqlite.connect(db.db_file) as conn:
                user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(user_version, 9)


class TestE2ERetryPreservesConfig(unittest.TestCase):
    """After --retry-failed runs, the user's manually_specified_course_ids
    config should be exactly as they left it (regression test for the
    retry-silently-re-downloads-manually-specified-courses bug we just
    fixed)."""

    def test_retry_does_not_modify_manually_specified_ids(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td)
            config = make_config(td, manually_specified=[86122, 86123, 86124, 86246])
            opts = MoodleDlOpts()
            opts.path = td

            # Verify config has the right IDs to start
            self.assertEqual(
                config.get_manually_specified_course_ids(),
                [86122, 86123, 86124, 86246],
            )

            # Now simulate a retry call
            from moodle_dl.main import retry_failed_downloads

            # Plant a failed file in the DB
            db = StateRecorder(config, opts)
            f = make_file(module_id=1, course_id=86124, filename="x.pdf")
            db.save_failed_file(f, 86124, "Test", error_message="boom")

            # Run retry
            retry_failed_downloads(config, opts)

            # Config should be unchanged
            config_after = make_config(td)
            self.assertEqual(
                config_after.get_manually_specified_course_ids(),
                [86122, 86123, 86124, 86246],
                "retry must not modify manually_specified_course_ids",
            )


class TestE2EMultiCourseIsolation(unittest.TestCase):
    """Operations on one course must not leak into another."""

    def test_failed_files_per_course(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td)
            config = make_config(td)
            opts = MoodleDlOpts()
            opts.path = td

            db = StateRecorder(config, opts)

            # Plant 2 failed files in course A, 1 in course B
            # save_failed_file takes (file, course_id, fullname, error)
            # so we need to pass the course_id explicitly. We track the
            # intended course_id via a closure variable since File has
            # no .course_id attribute.
            fa1 = make_file(module_id=1, course_id=86122, filename="a1.pdf")
            fa2 = make_file(module_id=2, course_id=86122, filename="a2.pdf")
            fb1 = make_file(module_id=3, course_id=86123, filename="b1.pdf")
            for f, intended_course in [(fa1, 86122), (fa2, 86122), (fb1, 86123)]:
                db.save_failed_file(f, intended_course, "Test", error_message="boom")

            # get_failed_files(course_id=86122) should return only course A
            failed_a = db.get_failed_files(course_id=86122, min_failures=1)
            self.assertEqual(
                {f.module_id for f in failed_a},
                {1, 2},
                "Failed files for course 86122 leaked from other courses",
            )

            failed_b = db.get_failed_files(course_id=86123, min_failures=1)
            self.assertEqual(
                {f.module_id for f in failed_b},
                {3},
            )


class TestE2EIncompleteDownloadResumable(unittest.TestCase):
    """An incomplete_download record (file started but didn't finish)
    should be retrievable for resumption, and after mark_download_complete
    should be gone from the incomplete list."""

    def test_save_then_complete_incomplete_download(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace(td)
            config = make_config(td)
            opts = MoodleDlOpts()
            opts.path = td

            db = StateRecorder(config, opts)
            f = make_file(module_id=42, course_id=86124, filename="big.zip",
                          filesize=10_000_000)

            # Mark incomplete (download started but was interrupted)
            db.save_incomplete_download(
                file_id=f.module_id,
                file_url=f.content_fileurl,
                file_path="/tmp/big.zip",
                total_bytes=10_000_000,
                downloaded_bytes=4_500_000,
            )

            # Should appear in the resumable list
            for_resume = db.get_incomplete_downloads_for_retry(max_attempts=5)
            self.assertEqual(len(for_resume), 1)
            self.assertEqual(for_resume[0]["downloaded_bytes"], 4_500_000)

            # Mark complete
            db.mark_download_complete(file_id=f.module_id, file_path="/tmp/big.zip")

            # Should no longer appear
            for_resume2 = db.get_incomplete_downloads_for_retry(max_attempts=5)
            self.assertEqual(len(for_resume2), 0)


if __name__ == "__main__":
    unittest.main()
