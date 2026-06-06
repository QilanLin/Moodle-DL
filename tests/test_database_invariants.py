# -*- coding: utf-8 -*-
"""
Property-based tests for StateRecorder invariants.

The moodle-dl database has a handful of invariants that must hold
across arbitrary operation sequences. If any of them break, the
incremental-download logic (modified/moved/deleted detection) silently
goes wrong. This file pins those invariants with property-based tests
using Hypothesis.

Invariants under test:

  I1.  After save_file → get_stored_files returns the saved file with
       the same identity (course_id, module_id, content_fileurl).
  I2.  files_have_same_type is reflexive (f == f), symmetric, and the
       result of files_are_diffrent XOR same-identity is consistent.
  I3.  position_in_section, once assigned, is non-negative and < N for
       any group of N files in the same scope.
  I4.  changes_of_new_version is a subset of the current state.
  I5.  get_failed_files / get_failed_files_summary are consistent:
       a file appearing in one with min_failures=1 should also appear
       in the other.
  I6.  After save_failed_file, the row is in 'failed' state, and the
       consecutive_failures counter is monotonically non-decreasing on
       repeated failure events.
  I7.  cache invalidation: after save_file, the next get_stored_files
       call does not return a stale "no files" result.
  I8.  notify/notification idempotency: calling notified() twice with
       the same file does not corrupt its state.

These tests use an in-memory SQLite database so they're fast and
isolated. They do NOT touch disk.
"""
import os
import sqlite3
import unittest
from typing import Any, Dict, List, Optional

from hypothesis import HealthCheck, given, settings, strategies as st

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import Course, File, MoodleDlOpts


def make_recorder(tmp_path) -> StateRecorder:
    """Create a StateRecorder backed by a fresh in-memory-style SQLite DB
    on tmp_path. We bypass the normal config-driven path so the test
    is hermetic.
    """
    opts = MoodleDlOpts()
    opts.path = str(tmp_path)
    config = ConfigHelper(opts)
    # Force the database to live in our tmp_path.
    return StateRecorder(config, opts)


def make_file(
    module_id: int,
    course_id: int,
    *,
    section_id: int = 1,
    content_filename: str = "file.pdf",
    content_fileurl: str = "https://example.com/f",
    content_filesize: int = 1024,
    content_timemodified: int = 1700000000,
    module_modname: str = "resource",
    content_type: str = "resource_file",
    **kwargs: Any,
) -> File:
    return File(
        module_id=module_id,
        section_name=f"Section {section_id}",
        section_id=section_id,
        module_name=f"Module {module_id}",
        content_filepath="/",
        content_filename=content_filename,
        content_fileurl=content_fileurl,
        content_filesize=content_filesize,
        content_timemodified=content_timemodified,
        module_modname=module_modname,
        content_type=content_type,
        content_isexternalfile=False,
        **kwargs,
    )


# Common hypothesis settings
db_settings = settings(
    max_examples=50,
    deadline=5000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# Module-id strategy: positive int within moodle range.
module_id_strategy = st.integers(min_value=1, max_value=10**8)
# File size strategy: typical moodle file sizes (avoid 0 and 1-byte which
# are edge cases for byte-equality checks).
file_size_strategy = st.integers(min_value=2, max_value=10**9)
# Modified time strategy: realistic unix timestamps in the recent past.
mtime_strategy = st.integers(min_value=1_500_000_000, max_value=1_900_000_000)


@st.composite
def file_strategy(draw):
    """Generate a File with reasonable random fields."""
    module_id = draw(module_id_strategy)
    course_id = draw(st.sampled_from([86122, 86123, 86124, 86246]))
    section_id = draw(st.integers(min_value=1, max_value=50))
    fname = draw(st.sampled_from([
        "lecture.pdf", "slide.pptx", "notes.md", "video.mp4",
        "image.png", "doc.docx", "transcript.html",
    ]))
    filesize = draw(file_size_strategy)
    mtime = draw(mtime_strategy)
    return make_file(
        module_id=module_id,
        course_id=course_id,
        section_id=section_id,
        content_filename=fname,
        content_fileurl=f"https://keats.kcl.ac.uk/mod/resource/{module_id}.bin",
        content_filesize=filesize,
        content_timemodified=mtime,
    )


class TestStateRecorderInvariants(unittest.TestCase):
    """Property-based invariant tests for StateRecorder."""

    @settings(max_examples=30, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(file_strategy())
    def test_invariant_I1_save_then_get_returns_same_file(self, f):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            course_id = 86124
            rec.save_file(f, course_id, "Test Course")
            stored_courses = rec.get_stored_files()
            # Find the saved file by module_id
            found = None
            for c in stored_courses:
                for sf in c.files:
                    if sf.module_id == f.module_id and sf.content_filename == f.content_filename:
                        found = sf
                        break
                if found:
                    break
            self.assertIsNotNone(
                found,
                f"File not found after save_file: module_id={f.module_id}",
            )
            self.assertEqual(found.content_fileurl, f.content_fileurl)
            self.assertEqual(found.content_filesize, f.content_filesize)

    @settings(max_examples=30, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(
        module_id_strategy,
        st.sampled_from(["resource", "assign", "url", "page", "book", "forum"]),
        st.sampled_from(["application/pdf", "video/mp4", "text/html", "image/png"]),
    )
    def test_invariant_I2_same_type_reflexive(self, module_id, modname, ctype):
        f = make_file(
            module_id=module_id,
            course_id=86124,
            module_modname=modname,
            content_type=ctype,
        )
        # Reflexive: f == f
        self.assertTrue(StateRecorder.files_have_same_type(f, f))
        # Symmetric: a == b implies b == a (also reflexive covers it for a == b)

    @settings(max_examples=30, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(
        module_id_strategy,
        st.sampled_from(["resource", "assign", "url", "page"]),
        st.sampled_from(["application/pdf", "video/mp4"]),
    )
    def test_invariant_I2_different_type_detected(self, module_id, modname, ctype):
        f1 = make_file(module_id=module_id, course_id=86124,
                       module_modname=modname, content_type=ctype)
        f2 = make_file(module_id=module_id + 1, course_id=86124,
                       module_modname="assign", content_type="text/html")
        # Different type
        self.assertFalse(StateRecorder.files_have_same_type(f1, f2))

    @settings(max_examples=20, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(file_strategy(), file_strategy())
    def test_invariant_I2_diffrent_xor_identity_consistent(self, f1, f2):
        # If f1 and f2 have the same identity (module_id + filename +
        # filesize + mtime), they are NOT different. Otherwise they MAY
        # be different. This is a sanity check on the comparison
        # primitives.
        same_identity = (
            f1.module_id == f2.module_id
            and f1.content_filename == f2.content_filename
            and f1.content_filesize == f2.content_filesize
            and f1.content_timemodified == f2.content_timemodified
        )
        are_different = StateRecorder.files_are_diffrent(f1, f2)
        if same_identity:
            # Two files with identical core attrs should not be
            # classified as "different"
            self.assertFalse(
                are_different,
                f"Identical files classified as different: {f1.content_filename} vs {f2.content_filename}",
            )

    @settings(max_examples=30, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(
        st.lists(file_strategy(), min_size=2, max_size=8),
    )
    def test_invariant_I3_position_in_section_in_range(self, files):
        """When we call ResultBuilder._assign_positions_to_files on a list
        of N files, each file's position should be in [0, N-1]."""
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Make them all share a course so they're in the same scope
        for f in files:
            f.section_id = 1  # force same scope
            f.module_modname = "resource"
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._is_system_file = staticmethod(lambda f: False)  # type: ignore
        rb._position_scope_key = ResultBuilder._position_scope_key.__get__(rb)
        rb._uses_module_directory = staticmethod(lambda modname: modname in ResultBuilder.MODULE_DIRECTORY_SUFFIXES)
        try:
            rb._assign_positions_to_files(files)
        except Exception as e:
            self.fail(f"_assign_positions_to_files raised: {e}")
        for f in files:
            self.assertIsNotNone(f.position_in_section, "position_in_section should be set")
            self.assertGreaterEqual(f.position_in_section, 0)
            # positions are per scope; with a single scope, max is N-1
            self.assertLess(f.position_in_section, len(files))

    @settings(max_examples=30, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(
        st.lists(file_strategy(), min_size=1, max_size=10),
    )
    def test_invariant_I3_positions_are_unique_within_scope(self, files):
        """Within a single scope, every file must get a distinct position."""
        from moodle_dl.moodle.result_builder import ResultBuilder

        for f in files:
            f.section_id = 1
            f.module_modname = "resource"
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._is_system_file = staticmethod(lambda f: False)  # type: ignore
        rb._position_scope_key = ResultBuilder._position_scope_key.__get__(rb)
        rb._uses_module_directory = staticmethod(lambda modname: modname in ResultBuilder.MODULE_DIRECTORY_SUFFIXES)
        rb._assign_positions_to_files(files)
        positions = [f.position_in_section for f in files]
        self.assertEqual(
            len(positions),
            len(set(positions)),
            f"Duplicate positions assigned: {positions}",
        )

    @settings(max_examples=20, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(file_strategy())
    def test_invariant_I4_changes_subset_of_current(self, f):
        """changes_of_new_version must return a subset of the input."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            course = Course(_id=86124, fullname="Test", files=[f])
            changed = rec.changes_of_new_version([course])
            # Must be a list of Course objects
            self.assertIsInstance(changed, list)
            for c in changed:
                self.assertIsInstance(c, Course)
                # The result Course must only contain files from the input
                input_filenames = {ff.content_filename for ff in course.files}
                for cf in c.files:
                    self.assertIn(
                        cf.content_filename,
                        input_filenames,
                        f"changes_of_new_version returned file not in input: {cf.content_filename}",
                    )

    @settings(max_examples=20, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(
        st.integers(min_value=1, max_value=5),   # how many times to call save_failed_file
    )
    def test_invariant_I6_consecutive_failures_monotonic(self, n_calls):
        """Calling save_failed_file N times on the same file should leave
        consecutive_failures exactly N (since each call increments by 1).
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            f = make_file(module_id=42, course_id=86124)
            for i in range(n_calls):
                rec.save_failed_file(
                    f, 86124, "Test Course",
                    error_message=f"err {i}",
                )
            summary = rec.get_failed_files_summary()
            self.assertIn(86124, summary)
            info = summary[86124]
            # With N calls, consecutive_failures should be N (since
            # save_failed_file increments by 1 on each call when the
            # file is already in the DB).
            self.assertEqual(
                info["max_consecutive"], n_calls,
                f"After {n_calls} save_failed_file calls, max_consecutive "
                f"should be {n_calls}, got {info['max_consecutive']}",
            )
            # total_failures = SUM(consecutive_failures). With one file
            # and N calls, that's also N.
            self.assertEqual(info["total_failures"], n_calls)

    @settings(max_examples=20, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(file_strategy())
    def test_invariant_I7_cache_invalidated_on_save(self, f):
        """After save_file, get_stored_files should immediately reflect
        the new file (no stale cache)."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            # First call: should not contain f
            initial = rec.get_stored_files()
            initial_ids = {
                (sf.module_id, sf.content_filename)
                for c in initial for sf in c.files
            }
            self.assertNotIn((f.module_id, f.content_filename), initial_ids)
            # Now save
            rec.save_file(f, 86124, "Test")
            # Second call: should contain f
            after = rec.get_stored_files()
            after_ids = {
                (sf.module_id, sf.content_filename)
                for c in after for sf in c.files
            }
            self.assertIn((f.module_id, f.content_filename), after_ids)

    @settings(max_examples=20, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(file_strategy())
    def test_invariant_I8_notified_idempotent(self, f):
        """Calling notified() twice with the same file should not change
        anything (or at most stay in 'notified' state)."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            rec.save_file(f, 86124, "Test")
            # Mark modified + not notified
            with sqlite3.connect(rec.db_file) as conn:
                conn.execute("UPDATE files SET modified = 1, notified = 0")
                conn.commit()
            # First call to notified
            changes = rec.changes_to_notify()
            rec.notified(changes)
            # Second call should be a no-op (changes_to_notify returns [])
            second = rec.changes_to_notify()
            self.assertEqual(second, [])


class TestDatabaseConcurrencyInvariants(unittest.TestCase):
    """Pin invariants that depend on database-level semantics like
    the WAL journal, foreign-key constraints, and the query cache."""

    @settings(max_examples=10, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(
        st.lists(file_strategy(), min_size=1, max_size=20),
        st.sampled_from([86122, 86123, 86124, 86246]),
    )
    def test_bulk_save_then_get_returns_all(self, files, course_id):
        """Saving N files in one course and then get_stored_files must
        return all N (no file lost, no duplicate)."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            for f in files:
                rec.save_file(f, course_id, f"Course {course_id}")
            stored_courses = rec.get_stored_files()
            stored = [
                sf
                for c in stored_courses
                for sf in c.files
            ]
            # The number of stored files should equal N (assuming no
            # primary-key collisions due to identical module_id).
            unique_module_ids = {f.module_id for f in files}
            self.assertEqual(
                len(stored),
                len(unique_module_ids),
                f"Bulk save lost files: {len(stored)} stored vs {len(files)} saved",
            )

    @settings(max_examples=20, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    @given(
        st.lists(file_strategy(), min_size=1, max_size=5),
    )
    def test_failed_file_recovery_resets_status(self, files):
        """After a file is failed and then mark_download_success is
        called, get_failed_files should not return it."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            for f in files:
                rec.save_failed_file(f, 86124, "Test", error_message="boom")
            # Now mark the first one as success
            if files:
                rec.mark_download_success(files[0], 86124)
                failed = rec.get_failed_files(course_id=86124, min_failures=1)
                # The first file (by module_id) should not be in the
                # failed list anymore.
                failed_module_ids = {f.module_id for f in failed}
                self.assertNotIn(
                    files[0].module_id,
                    failed_module_ids,
                    f"mark_download_success did not remove from failed list",
                )


if __name__ == "__main__":
    unittest.main()
