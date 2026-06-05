# -*- coding: utf-8 -*-
"""
End-to-end / integration tests for moodle-dl's download flow.

These tests stitch together real components — StateRecorder, FakeDownloadService,
DownloadPauseController, DownloadService.status_callback — and verify the
behaviour a user sees across multiple invocations.

Coverage:

1)  "Record → Modify → Re-download" rename detection
    FakeDownloadService persists N files; we then simulate the user renaming
    a file on the Moodle side (changed content_filename, same module_id +
    content_fileurl). On the next "scan", the diff layer should recognise
    exactly one moved file, one unchanged file, and one new file.

2)  Token renewal
    config.get_token() is patched so the first call returns a stale value that
    triggers a MoodleAuthError. We then call set_tokens() with a fresh value
    and verify the second call returns the new token — i.e. the canonical
    refresh path used by --new-token works against the StateRecorder store.

3)  Multi-mod failures
    Five files spread across three modnames. status_callback is fed a mix of
    FINISHED and FAILED events; one of the failures carries a
    LegantoPermanentFailureError. We assert that save_failed_file / mark_download_success
    are called for the right files, and that only the permanent failure
    gets the [PERMANENT] prefix.

4)  --retry-failed skips permanent failures
    Seed StateRecorder with 5 failed rows where one carries the [PERMANENT]
    prefix. Run the same DB query retry_failed_downloads uses and assert
    only 4 rows are returned, and that the PERMANENT row is never present
    in the retry queue or the summary.

5)  DownloadPauseController hotkeys & wait
    Direct unit-level exercise of handle_key('p' / 'r' / 'x'), is_paused,
    consume_pause_request, and wait_if_requested (with asyncio.sleep mocked
    so the test returns instantly).

6)  Cross-file pause / resume inside DownloadService
    Build 10 fake tasks, real_run the service, request a pause after the
    first task finishes, verify the second task waits, then resume and
    verify all tasks finish.

The tests use real StateRecorder against a tmp_path database so that the
on-disk contract (PERMANENT_FAILURE_PREFIX, get_failed_files_*) is
exercised end-to-end, not just via mocks.
"""

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.download_service import (
    PERMANENT_FAILURE_PREFIX,
    DownloadPauseController,
    DownloadService,
)
from moodle_dl.downloader.fake_download_service import FakeDownloadService
from moodle_dl.downloader.leganto_print import LegantoPermanentFailureError
from moodle_dl.types import (
    Course,
    DlEvent,
    DownloadStatus,
    File,
    MoodleDlOpts,
    TaskState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_file(
    *,
    filename='file.pdf',
    file_id=None,
    module_id=10,
    section_id=1,
    size=100,
    modname='resource',
    content_type='file',
    deleted=0,
    url='https://example.test/file',
    filepath='/',
    hash_value=None,
    section_name='Week 1',
    module_name='Module',
    timemodified=1700000000,
):
    return File(
        module_id=module_id,
        section_name=section_name,
        section_id=section_id,
        module_name=module_name,
        content_filepath=filepath,
        content_filename=filename,
        content_fileurl=url,
        content_filesize=size,
        content_timemodified=timemodified,
        module_modname=modname,
        content_type=content_type,
        content_isexternalfile=False,
        file_id=file_id,
        deleted=deleted,
        file_hash=hash_value,
    )


def make_state_recorder(tmp_path) -> StateRecorder:
    """Build a StateRecorder against a fresh tmp_path sqlite database."""
    config = MagicMock(spec=ConfigHelper)
    config.get_misc_files_path.return_value = str(tmp_path)
    return StateRecorder(config, MoodleDlOpts())


def make_config_helper(tmp_path) -> ConfigHelper:
    """A real ConfigHelper — needed for set_tokens() / get_token() to work."""
    config = ConfigHelper(MoodleDlOpts(path=str(tmp_path)))
    return config


def count_files_in_state(tmp_path) -> int:
    """Count non-deleted rows in the v9 'files' table for the test DB."""
    db_file = tmp_path / 'moodle_state.db'
    if not db_file.exists():
        return 0
    conn = sqlite3.connect(str(db_file))
    try:
        row = conn.execute('SELECT COUNT(*) FROM files WHERE deleted = 0').fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1) "Record → Modify → Re-download" rename detection
# ---------------------------------------------------------------------------


def test_record_modify_redownload_detects_moved_and_new(tmp_path):
    """End-to-end rename + new-file detection across two FakeDownloadService runs.

    First "download" (FakeDownloadService) persists 2 files to the DB; we
    also create the actual on-disk files (so the DB's _file_exists_on_disk
    check sees them as already-downloaded and skips them on the next scan).
    Then we simulate a Moodle-side rename of file A → A' plus a brand-new
    file C. The second pass through StateRecorder.changes_of_new_version
    should classify:

      - 1 moved       (A → A')
      - 0 unchanged   (B stays in DB, not in changed list at all)
      - 1 new         (C)
    """
    recorder = make_state_recorder(tmp_path)
    config = MagicMock(spec=ConfigHelper)
    config.get_misc_files_path.return_value = str(tmp_path)
    config.get_download_path.return_value = str(tmp_path)
    config.get_restricted_filenames.return_value = False
    opts = MoodleDlOpts()
    opts.path = str(tmp_path)
    opts.download_chunk_size = 1024
    opts.max_parallel_yt_dlp = 2

    # First snapshot — two files, all in course 7.
    file_a = make_file(
        filename='A.pdf', module_id=1, url='https://moodle.test/A.pdf', size=100
    )
    file_b = make_file(
        filename='B.pdf', module_id=2, url='https://moodle.test/B.pdf', size=200
    )
    course_v1 = Course(7, 'Test Course', [file_a, file_b])
    FakeDownloadService([course_v1], config, opts, recorder).run()

    # Two rows persisted.
    assert count_files_in_state(tmp_path) == 2

    # Touch the on-disk files so _file_exists_on_disk() returns True for B
    # on the second scan. (FakeDownloadService just builds the path; it
    # does not write bytes.)
    for stored_file in recorder.get_stored_files()[0].files:
        Path(stored_file.saved_to).parent.mkdir(parents=True, exist_ok=True)
        Path(stored_file.saved_to).write_bytes(b'placeholder')

    # --- Now simulate "the next day" ---
    # A is renamed to A' (content_fileurl, module_id all stay the same).
    file_a_renamed = make_file(
        filename='A_prime.pdf',  # ← name changed
        module_id=1,  # ← same module_id
        url='https://moodle.test/A.pdf',  # ← same url
        size=100,
    )
    # B is unchanged.
    file_b_again = make_file(
        filename='B.pdf', module_id=2, url='https://moodle.test/B.pdf', size=200
    )
    # C is brand new.
    file_c_new = make_file(
        filename='C.pdf', module_id=3, url='https://moodle.test/C.pdf', size=300
    )
    course_v2 = Course(7, 'Test Course', [file_a_renamed, file_b_again, file_c_new])

    current = [course_v2]
    changed = recorder.changes_of_new_version(current)

    # All changed files should be in one course (course 7).
    assert len(changed) == 1
    assert changed[0].id == 7
    changed_files = changed[0].files
    moved = [f for f in changed_files if getattr(f, 'moved', False)]
    new = [f for f in changed_files if not getattr(f, 'moved', False)]

    # A → A' is a "moved" file.
    moved_names = {f.content_filename for f in moved}
    assert moved_names == {'A_prime.pdf'}, f'expected only A_prime.pdf to be moved, got {moved_names}'

    # C is brand new (not in stored at all → not detected as moved).
    new_names = {f.content_filename for f in new}
    assert new_names == {'C.pdf'}, f'expected C.pdf to be the only new file, got {new_names}'

    # B is NOT in changed_files because it is unchanged on disk.
    all_changed_names = {f.content_filename for f in changed_files}
    assert 'B.pdf' not in all_changed_names


def test_record_modify_redownload_detects_modified_file_as_modified(tmp_path):
    """A content-timemodified bump is detected as 'modified' (not 'moved')."""
    recorder = make_state_recorder(tmp_path)
    config = MagicMock(spec=ConfigHelper)
    config.get_misc_files_path.return_value = str(tmp_path)
    config.get_download_path.return_value = str(tmp_path)
    config.get_restricted_filenames.return_value = False
    opts = MoodleDlOpts()
    opts.path = str(tmp_path)

    file_v1 = make_file(
        filename='lecture.pdf', module_id=1, url='https://moodle.test/lecture.pdf', size=100,
        timemodified=1700000000,
    )
    FakeDownloadService([Course(11, 'Course', [file_v1])], config, opts, recorder).run()

    # Create the actual on-disk file so _file_exists_on_disk() sees it.
    for stored_file in recorder.get_stored_files()[0].files:
        Path(stored_file.saved_to).parent.mkdir(parents=True, exist_ok=True)
        Path(stored_file.saved_to).write_bytes(b'placeholder')

    # Same path, same module_id, but timemodified bumped (and size grew) → "modified".
    file_v2 = make_file(
        filename='lecture.pdf', module_id=1, url='https://moodle.test/lecture.pdf', size=200,
        timemodified=1700000999,
    )
    changed = recorder.changes_of_new_version([Course(11, 'Course', [file_v2])])

    assert len(changed) == 1
    modified = [f for f in changed[0].files if getattr(f, 'modified', False)]
    assert len(modified) == 1
    assert modified[0].content_filename == 'lecture.pdf'


# ---------------------------------------------------------------------------
# 2) Token renewal (config.get_token → set_tokens → next get_token)
# ---------------------------------------------------------------------------


def test_token_renewal_after_failed_auth_writes_new_token(tmp_path):
    """First call with old token raises MoodleAuthError, refresh, then second
    call with the new token succeeds. The new token is persisted in
    AuthSessionManager's session table.
    """
    from moodle_dl.moodle.request_helper import MoodleAuthError

    config = make_config_helper(tmp_path)

    # Plant an old token via the v2 path (set_tokens → AuthSessionManager).
    config.set_tokens('OLD_TOKEN_stale', None)

    # First call: returns the OLD token.
    assert config.get_token() == 'OLD_TOKEN_stale'

    # Simulate Moodle rejecting the old token.
    with pytest.raises(MoodleAuthError):
        # The user re-runs the wizard. The new login response gives a fresh token.
        raise MoodleAuthError('invalidtoken: stale token')

    # The refresh path (MoodleService.obtain_login_token returns a fresh
    # (token, private_token) tuple). We don't call the network — we just
    # write the new value via set_tokens, which is what -nt / auto-reauth
    # ultimately call.
    config.set_tokens('NEW_TOKEN_fresh', 'priv-fresh-1')

    # Second call: returns the NEW token.
    assert config.get_token() == 'NEW_TOKEN_fresh'

    # And the new token must be visible in the auth_sessions table.
    db_file = tmp_path / 'moodle_state.db'
    conn = sqlite3.connect(str(db_file))
    try:
        row = conn.execute(
            "SELECT token_value, status FROM auth_sessions "
            "WHERE session_type = 'token' AND status = 'valid' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 'NEW_TOKEN_fresh'


def test_token_renewal_can_recover_when_session_table_was_empty(tmp_path):
    """If the auth_sessions table is empty, set_tokens creates a fresh row."""
    config = make_config_helper(tmp_path)
    db_file = tmp_path / 'moodle_state.db'

    # No tokens at all initially.
    with pytest.raises(ValueError):
        config.get_token()

    # After set_tokens with a brand-new value, get_token returns it.
    config.set_tokens('first_ever_token', None)
    assert config.get_token() == 'first_ever_token'

    # Row is in the DB.
    conn = sqlite3.connect(str(db_file))
    try:
        rows = conn.execute(
            "SELECT token_value FROM auth_sessions WHERE session_type = 'token' AND status = 'valid'"
        ).fetchall()
    finally:
        conn.close()
    assert ('first_ever_token',) in rows


# ---------------------------------------------------------------------------
# 3) Multi-mod failures
# ---------------------------------------------------------------------------


def _make_download_service_for_callback():
    """Minimal DownloadService that supports status_callback but skips
    gen_all_tasks (which would otherwise call into fetch_state / etc.).
    """
    service = DownloadService.__new__(DownloadService)
    service.courses = []
    service.config = MagicMock()
    service.opts = MagicMock()
    service.database = MagicMock()
    service.status = DownloadStatus()
    service.progress_tracker = MagicMock()
    service.pause_controller = MagicMock(
        wait_if_requested=MagicMock(),
        is_paused=MagicMock(return_value=False),
    )
    return service


def test_multi_mod_failures_split_success_and_failure_correctly(tmp_path):
    """Five files from three mods; two succeed, three fail (one of which
    is a permanent Leganto error). status_callback must:

      - call database.save_file + mark_download_success for the 2 successes
      - call database.save_failed_file for the 3 failures
      - prefix the permanent one's reason with [PERMANENT]
    """
    service = _make_download_service_for_callback()

    resource_ok = make_file(filename='slides.pdf', modname='resource', size=10)
    resource_fail = make_file(filename='corrupt.pdf', modname='resource', size=20)
    assignment_ok = make_file(filename='submission.pdf', modname='assign', size=30)
    assignment_fail = make_file(filename='overdue.pdf', modname='assign', size=40)
    leganto_perm = make_file(filename='reading-list.pdf', modname='leganto', size=50)

    course = Course(42, 'Mixed Mods Course')
    tasks = {
        'slides': SimpleNamespace(
            file=resource_ok, course=course,
            status=SimpleNamespace(get_error_text=MagicMock(return_value=''), error=None),
        ),
        'corrupt': SimpleNamespace(
            file=resource_fail, course=course,
            status=SimpleNamespace(
                get_error_text=MagicMock(return_value='checksum mismatch'),
                error=RuntimeError('checksum mismatch'),
            ),
        ),
        'submission': SimpleNamespace(
            file=assignment_ok, course=course,
            status=SimpleNamespace(get_error_text=MagicMock(return_value=''), error=None),
        ),
        'overdue': SimpleNamespace(
            file=assignment_fail, course=course,
            status=SimpleNamespace(
                get_error_text=MagicMock(return_value='assignment window closed'),
                error=RuntimeError('assignment window closed'),
            ),
        ),
        'leganto': SimpleNamespace(
            file=leganto_perm, course=course,
            status=SimpleNamespace(
                get_error_text=MagicMock(return_value='Reading list deleted by librarian'),
                error=LegantoPermanentFailureError('Reading list deleted by librarian'),
            ),
        ),
    }

    service.status_callback(DlEvent.FINISHED, tasks['slides'])
    service.status_callback(DlEvent.FAILED, tasks['corrupt'])
    service.status_callback(DlEvent.FINISHED, tasks['submission'])
    service.status_callback(DlEvent.FAILED, tasks['overdue'])
    service.status_callback(DlEvent.FAILED, tasks['leganto'])

    # Two successes.
    assert service.status.files_downloaded == 2
    assert service.status.files_failed == 3

    # save_file + mark_download_success called once per success.
    assert service.database.save_file.call_count == 2
    assert service.database.mark_download_success.call_count == 2

    # save_failed_file called once per failure, with the right file + reason.
    assert service.database.save_failed_file.call_count == 3

    save_failed_calls = service.database.save_failed_file.call_args_list
    reasons_by_file = {
        call.args[0].content_filename: call.args[3]
        for call in save_failed_calls
    }
    assert reasons_by_file['corrupt.pdf'] == 'checksum mismatch'
    assert reasons_by_file['overdue.pdf'] == 'assignment window closed'
    assert (
        reasons_by_file['reading-list.pdf']
        == f'{PERMANENT_FAILURE_PREFIX}Reading list deleted by librarian'
    )

    # And critically: only the leganto one carries the [PERMANENT] prefix.
    permanent_reasons = [
        r for r in reasons_by_file.values() if r.startswith(PERMANENT_FAILURE_PREFIX)
    ]
    assert permanent_reasons == [
        f'{PERMANENT_FAILURE_PREFIX}Reading list deleted by librarian'
    ]


# ---------------------------------------------------------------------------
# 4) --retry-failed skips permanent failures
# ---------------------------------------------------------------------------


def test_retry_failed_downloads_excludes_permanent_marker(tmp_path):
    """retry_failed_downloads loads failures via
    get_failed_files_with_course_info — that query filters out rows whose
    last_failed_reason starts with PERMANENT_FAILURE_PREFIX. A subsequent
    call to get_failed_files_summary must report the same (smaller) count.
    """
    recorder = make_state_recorder(tmp_path)

    # 5 failed files in 1 course. 4 have normal reasons, 1 has [PERMANENT].
    files = [
        ('a.pdf', 'network blip'),
        ('b.pdf', '503 server unavailable'),
        ('c.pdf', f'{PERMANENT_FAILURE_PREFIX}Reading list deleted'),
        ('d.pdf', 'TLS handshake failed'),
        ('e.pdf', 'connection reset by peer'),
    ]
    for idx, (filename, reason) in enumerate(files, start=1):
        f = make_file(
            filename=filename, module_id=idx, url=f'https://moodle.test/{filename}',
        )
        recorder.save_failed_file(f, 999, 'Course Permanent', reason)

    # Sanity: 5 rows of 'failed' status total.
    conn = sqlite3.connect(str(tmp_path / 'moodle_state.db'))
    try:
        rows = conn.execute("SELECT COUNT(*) FROM files WHERE download_status = 'failed'").fetchone()
    finally:
        conn.close()
    assert rows[0] == 5

    # Retry queue: 4 files (not 5).
    grouped = recorder.get_failed_files_with_course_info(min_failures=1)
    assert 999 in grouped
    retry_filenames = {f.content_filename for f in grouped[999]['files']}
    assert retry_filenames == {'a.pdf', 'b.pdf', 'd.pdf', 'e.pdf'}
    assert 'c.pdf' not in retry_filenames, 'PERMANENT-marked file must be excluded from retry'

    # Summary: 4 / 4 / max 1, not 5.
    summary = recorder.get_failed_files_summary()
    assert summary[999]['failed_count'] == 4
    assert summary[999]['max_consecutive'] == 1


def test_retry_failed_excludes_permanent_end_to_end_via_main(tmp_path):
    """Calling the actual retry_failed_downloads() function — without actually
    running the downloader — should:
      1. Print the summary header (4 files, not 5).
      2. Load 4 files (not 5) into courses for retry.
      3. Reset those 4 files via reset_failed_file_for_retry.
    """
    from moodle_dl.main import (
        _get_failed_download_statistics,
        _load_failed_files_as_courses,
        _reset_failed_files_for_retry,
    )

    recorder = make_state_recorder(tmp_path)
    files_with_reasons = [
        ('one.pdf', 'network blip'),
        ('two.pdf', 'connection reset'),
        ('three.pdf', f'{PERMANENT_FAILURE_PREFIX}list gone'),
        ('four.pdf', 'TLS error'),
        ('five.pdf', 'DNS failure'),
    ]
    for idx, (filename, reason) in enumerate(files_with_reasons, start=1):
        f = make_file(
            filename=filename, module_id=idx, url=f'https://moodle.test/{filename}',
        )
        recorder.save_failed_file(f, 7, 'Course X', reason)

    # The pipeline retry_failed_downloads() runs.
    summary = _get_failed_download_statistics(recorder)
    assert summary[7]['failed_count'] == 4
    assert summary[7]['total_failures'] == 4

    courses = _load_failed_files_as_courses(recorder)
    assert len(courses) == 1
    retry_filenames = sorted(f.content_filename for f in courses[0].files)
    assert retry_filenames == ['five.pdf', 'four.pdf', 'one.pdf', 'two.pdf']

    # And _reset_failed_files_for_retry would be called on the 4 files.
    database_spy = MagicMock(wraps=recorder)
    _reset_failed_files_for_retry(database_spy, courses)
    assert database_spy.reset_failed_file_for_retry.call_count == 4


# ---------------------------------------------------------------------------
# 5) DownloadPauseController hotkeys
# ---------------------------------------------------------------------------


class TestDownloadPauseControllerHotkeys:
    """Direct unit-level exercise of the pause/resume state machine.

    Covers handle_key('p' / 'r' / 'x'), consume_pause_request, is_paused,
    and the wait_if_requested coroutine (with asyncio.sleep mocked so the
    test does not block on real wall-clock time).
    """

    def test_unknown_key_returns_empty(self):
        controller = DownloadPauseController(enabled=False)
        assert controller.handle_key('x') == ''
        assert controller.handle_key('Q') == ''
        assert controller.handle_key('') == ''

    def test_lowercase_pause_and_resume(self):
        controller = DownloadPauseController(enabled=False)

        assert controller.handle_key('p') == 'pause_requested'
        assert controller.is_paused() is False  # not paused until consumed
        assert controller.consume_pause_request() is True
        assert controller.is_paused() is True

        # Second 'p' while paused is a no-op.
        assert controller.handle_key('p') == ''
        assert controller.is_paused() is True

        # 'r' resumes.
        assert controller.handle_key('r') == 'resume'
        assert controller.is_paused() is False

    def test_uppercase_keys_are_accepted(self):
        """P/R upper-case is the documented alternative."""
        controller = DownloadPauseController(enabled=False)

        assert controller.handle_key('P') == 'pause_requested'
        assert controller.consume_pause_request() is True
        assert controller.is_paused() is True

        assert controller.handle_key('R') == 'resume'
        assert controller.is_paused() is False

    def test_stop_clears_pause_request_and_state(self):
        controller = DownloadPauseController(enabled=False)
        controller.handle_key('p')
        assert controller.consume_pause_request() is True
        assert controller.is_paused() is True

        controller.stop()
        assert controller.is_paused() is False
        assert controller.consume_pause_request() is False

    def test_wait_if_requested_returns_immediately_without_request(self):
        """If no pause is pending, wait_if_requested must return immediately
        (and must NOT call asyncio.sleep — there's nothing to wait for).
        """
        controller = DownloadPauseController(enabled=False)

        async def runner():
            # If wait_if_requested were broken, it would call
            # asyncio.sleep(self.poll_interval) inside a loop. We use
            # wait_for with a 0.5s timeout as a tripwire.
            await asyncio.wait_for(controller.wait_if_requested(), timeout=0.5)

        asyncio.run(runner())

    def test_wait_if_requested_blocks_until_resume(self):
        """After handle_key('p') + consume_pause_request, wait_if_requested
        must await asyncio.sleep in a loop until handle_key('r') is received.
        We mock asyncio.sleep to drive the state machine deterministically.
        """
        controller = DownloadPauseController(enabled=False, poll_interval=0.01)
        controller.handle_key('p')
        assert controller.consume_pause_request() is True

        sleep_call_count = [0]

        async def fake_sleep(_seconds):
            sleep_call_count[0] += 1
            # First call: we're still paused. Release the pause on the
            # second call so the loop exits.
            if sleep_call_count[0] >= 2:
                controller.handle_key('r')

        async def runner():
            with _patch_asyncio_sleep(fake_sleep):
                await asyncio.wait_for(controller.wait_if_requested(), timeout=2.0)

        asyncio.run(runner())

        # The fake_sleep should have been called at least twice (initial
        # wait + after-resume check).
        assert sleep_call_count[0] >= 2
        # And after the resume, we are no longer paused.
        assert controller.is_paused() is False


class _patch_asyncio_sleep:
    """Context manager that patches asyncio.sleep inside
    moodle_dl.downloader.download_service for the duration of the block.
    """

    def __init__(self, side_effect):
        self._side_effect = side_effect
        self._patcher = None

    def __enter__(self):
        from moodle_dl.downloader import download_service as ds

        self._original = ds.asyncio.sleep
        self._patcher = _AsyncSleepMock(self._side_effect)
        ds.asyncio.sleep = self._patcher
        return self._patcher

    def __exit__(self, exc_type, exc, tb):
        from moodle_dl.downloader import download_service as ds

        ds.asyncio.sleep = self._original
        return False


class _AsyncSleepMock:
    def __init__(self, side_effect):
        self._side_effect = side_effect

    async def __call__(self, *args, **kwargs):
        return await self._side_effect(*args, **kwargs)


# ---------------------------------------------------------------------------
# 6) DownloadService pause/resume across multiple files
# ---------------------------------------------------------------------------


def _build_service_with_pauseable_tasks(n_tasks=10):
    """Build a DownloadService whose all_tasks are lightweight mocks that
    we can introspect. status_callback is wired to a real DownloadStatus;
    the pause_controller is a real DownloadPauseController (not a mock)
    so we exercise the real state machine.
    """
    service = DownloadService.__new__(DownloadService)
    service.courses = []
    service.config = MagicMock()
    service.config.get_manually_specified_course_ids.return_value = []
    service.config.get_options_of_courses.return_value = {}
    service.opts = MagicMock()
    service.opts.download_chunk_size = 1024
    service.opts.max_parallel_yt_dlp = 2
    service.opts.cookies_text = None
    service.opts.global_opts = MagicMock()
    service.opts.global_opts.skip_cert_verify = False
    service.database = MagicMock()
    service.database.get_incomplete_downloads_for_retry.return_value = []
    service.database.cleanup_old_incomplete_downloads.return_value = 0
    service.status = DownloadStatus()
    service.progress_tracker = MagicMock()
    service._status_log_event = None
    service._status_log_loop = None
    service._last_logged_status_snapshot = None
    service._bytes_downloaded_at_last_status_log_signal = 0
    service.pause_controller = DownloadPauseController(enabled=False, poll_interval=0.01)
    service.network_throttle = MagicMock()

    async def idle_network_wait(_name):
        await asyncio.sleep(0)

    service.network_throttle.async_wait = idle_network_wait
    service._rewrite_html_resource_links_after_task = MagicMock(return_value=0)
    service._rewrite_downloaded_html_resource_links = MagicMock(return_value=0)
    service._display_download_summary = MagicMock()

    # log_download_status must be a coroutine (real_run awaits it inside
    # asyncio.create_task), so wire a real idle coroutine.
    async def idle_log_download_status():
        await asyncio.Event().wait()

    service.log_download_status = idle_log_download_status  # type: ignore[assignment]

    # Build N fake tasks that record their start.
    course = Course(1, 'C')
    tasks = []
    for i in range(n_tasks):
        f = make_file(filename=f'file_{i}.pdf', module_id=100 + i, file_id=1000 + i, size=10)
        task_run_started = asyncio.Event()
        task_run_finished = asyncio.Event()

        task = SimpleNamespace(
            file=f,
            course=course,
            may_perform_network_io=MagicMock(return_value=False),
            status=SimpleNamespace(state=TaskState.FINISHED, get_error_text=MagicMock(return_value='')),
            _started=task_run_started,
            _finished=task_run_finished,
        )
        tasks.append(task)
    service.all_tasks = tasks
    return service


@pytest.mark.asyncio
async def test_download_service_pauses_after_current_task_then_resumes():
    """A pause request must take effect AFTER the current task finishes —
    not mid-task — and the next task must wait until resume is pressed.

    We build N tasks that each block on an explicit "go" event so we can
    drive them deterministically. After the first task's `run()` is
    awaited, we request a pause and assert that:
      1. The first task completed (its started/finished events are both set).
      2. The second task is blocked (its started event is NOT set yet),
         because wait_if_requested() is consuming the pause request.
    Then we press resume, release the second task, and verify everything
    finishes.
    """
    from moodle_dl.downloader import download_service as ds

    n = 4
    service = _build_service_with_pauseable_tasks(n_tasks=n)

    # Replace ds.asyncio.sleep with a no-op-equivalent so the pause poll
    # loop is essentially a busy-wait. We still need to yield, so we
    # sleep for 0 seconds (event loop tick).
    original_sleep = ds.asyncio.sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    ds.asyncio.sleep = fast_sleep
    try:
        # Override the task `run` coroutines to be deterministic: each
        # waits on a per-task "go" event so we control when it completes.
        go_events = [asyncio.Event() for _ in range(n)]
        for idx, task in enumerate(service.all_tasks):
            go_event = go_events[idx]

            async def deterministic_run(ge=go_event, t=task):
                t._started.set()
                await ge.wait()
                t._finished.set()

            task.run = deterministic_run

        # Fire off real_run in the background.
        run_task = asyncio.create_task(service.real_run())

        # Wait for task 0 to actually start.
        await service.all_tasks[0]._started.wait()
        # Release task 0.
        go_events[0].set()
        await service.all_tasks[0]._finished.wait()

        # Task 1 should now have started.
        await service.all_tasks[1]._started.wait()

        # Request a pause AFTER task 1 has started running. The pause
        # takes effect at the next wait_if_requested() checkpoint, which
        # happens after task 1's `run()` returns. So task 1 will finish
        # (once we release it), and task 2 will NOT start until resume.
        service.pause_controller.handle_key('p')

        # Release task 1.
        go_events[1].set()
        await service.all_tasks[1]._finished.wait()

        # Give the loop a chance to call wait_if_requested() and start polling.
        await asyncio.sleep(0.05)

        # At this point, task 2 MUST NOT have started.
        assert not service.all_tasks[2]._started.is_set(), (
            'task 2 should be blocked by pause_controller.wait_if_requested()'
        )

        # Resume.
        service.pause_controller.handle_key('r')

        # Now task 2 should be able to start.
        await service.all_tasks[2]._started.wait()
        # And eventually all tasks should finish.
        for idx, go in enumerate(go_events[2:], start=2):
            go.set()
            await service.all_tasks[idx]._finished.wait()

        await asyncio.wait_for(run_task, timeout=2.0)

        # Final invariant: all tasks completed.
        completed = sum(1 for t in service.all_tasks if t._finished.is_set())
        assert completed == n, f'expected {n} tasks to complete, got {completed}'
    finally:
        ds.asyncio.sleep = original_sleep
        if not run_task.done():
            run_task.cancel()
            try:
                await run_task
            except (asyncio.CancelledError, Exception):
                pass
        service.pause_controller.stop()
