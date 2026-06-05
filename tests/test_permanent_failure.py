# -*- coding: utf-8 -*-
"""Permanent-failure classification in download_service.

`_is_permanent_failure` and `PERMANENT_FAILURE_PREFIX` are the contract
between the download layer (which calls `status_callback` with a task) and
the DB / retry layer (which filters by the `[PERMANENT] ` prefix). This
file pins down that contract.
"""

import pytest

from moodle_dl.downloader.download_service import (
    PERMANENT_FAILURE_PREFIX,
    _is_permanent_failure,
)
from moodle_dl.downloader.leganto_print import LegantoPermanentFailureError


# ---------------------------------------------------------------------------
# PERMANENT_FAILURE_PREFIX constant
# ---------------------------------------------------------------------------


def test_permanent_failure_prefix_value():
    """The prefix is part of the on-disk contract: it's stored in
    StateRecorder.save_failed_file and matched in get_failed_files_*.

    Changing this string would silently invalidate all existing failure
    rows, so we pin it here.
    """
    assert PERMANENT_FAILURE_PREFIX == '[PERMANENT] '


def test_permanent_failure_prefix_ends_with_space():
    """Trailing space matters: it separates the marker from the actual
    error message when read back by humans. Without it, the first word of
    the error would be visually merged with `[PERMANENT]`.
    """
    assert PERMANENT_FAILURE_PREFIX.endswith(' ')


def test_permanent_failure_prefix_starts_with_bracket():
    """The leading `[` makes the marker greppable in log output and in the
    raw failure_reason column."""
    assert PERMANENT_FAILURE_PREFIX.startswith('[')


# ---------------------------------------------------------------------------
# _is_permanent_failure
# ---------------------------------------------------------------------------


def test_is_permanent_failure_true_for_leganto_error():
    err = LegantoPermanentFailureError('Reading list deleted')
    assert _is_permanent_failure(err) is True


def test_is_permanent_failure_false_for_plain_exception():
    assert _is_permanent_failure(Exception('boom')) is False


def test_is_permanent_failure_false_for_runtime_error():
    assert _is_permanent_failure(RuntimeError('network blip')) is False


def test_is_permanent_failure_false_for_value_error():
    assert _is_permanent_failure(ValueError('bad input')) is False


def test_is_permanent_failure_true_for_subclass():
    """Subclasses of LegantoPermanentFailureError are also permanent.

    This is how isinstance() naturally behaves, but it's the safety net
    for any future specialised error class (e.g. LegantoAccessDeniedError
    inheriting from LegantoPermanentFailureError).
    """

    class LegantoAccessDeniedError(LegantoPermanentFailureError):
        pass

    err = LegantoAccessDeniedError('no longer enrolled')
    assert _is_permanent_failure(err) is True


def test_is_permanent_failure_false_for_none():
    """None must not be treated as a permanent failure — status_callback
    guards with `if task.status else None` so this branch is exercised
    when a task has no status attached yet."""
    assert _is_permanent_failure(None) is False


def test_is_permanent_failure_false_for_string():
    """Defensive: even if a stray string slips through, we must not
    classify it as permanent. is_permanent_failure is documented as
    isinstance-checked, so a non-exception must yield False."""
    assert _is_permanent_failure('LegantoPermanentFailureError') is False


def test_is_permanent_failure_false_for_int():
    assert _is_permanent_failure(42) is False


def test_is_permanent_failure_false_for_object():
    class NotAnError:
        pass

    assert _is_permanent_failure(NotAnError()) is False


# ---------------------------------------------------------------------------
# Round-trip: prefix + reason + classifier mirror the real save_failed_file
# flow used by status_callback.
# ---------------------------------------------------------------------------


def _classify_and_format(error):
    """Reproduce the same formatting status_callback uses, without
    touching the DB or the rest of the download service.

    Mirrors download_service.py lines 459-471:
        error_message = task.status.get_error_text()
        if _is_permanent_failure(task.status.error):
            error_message = f'{PERMANENT_FAILURE_PREFIX}{error_message}'
    """
    error_message = str(error)
    if _is_permanent_failure(error):
        error_message = f'{PERMANENT_FAILURE_PREFIX}{error_message}'
    return error_message


@pytest.mark.parametrize(
    'error,expected_message',
    [
        (
            LegantoPermanentFailureError('Reading list deleted by librarian'),
            '[PERMANENT] Reading list deleted by librarian',
        ),
        (
            RuntimeError('connection reset by peer'),
            'connection reset by peer',
        ),
        (
            ValueError('bad URL'),
            'bad URL',
        ),
    ],
    ids=['leganto-permanent', 'runtime-transient', 'value-error-transient'],
)
def test_round_trip_classify_and_format(error, expected_message):
    """End-to-end of the prefix logic used in status_callback.

    A LegantoPermanentFailureError must produce a reason string that:
      1. starts with the PERMANENT_FAILURE_PREFIX marker
      2. still contains the human-readable error message after the prefix
    A non-permanent error must produce a reason string with NO marker.
    """
    assert _classify_and_format(error) == expected_message


def test_round_trip_permanent_reason_is_filterable_by_db_logic():
    """The prefix is what the DB layer uses to exclude permanent failures
    from the retry queue (see test_database_more.test_permanent_failure_marker_excluded_from_retry_queue).

    Verify the prefix appears at the start of the formatted reason so a
    simple `startswith(PERMANENT_FAILURE_PREFIX)` check in the DB layer
    works as designed.
    """
    reason = _classify_and_format(LegantoPermanentFailureError('Leganto reading list deleted'))
    assert reason.startswith(PERMANENT_FAILURE_PREFIX)


def test_round_trip_transient_reason_has_no_prefix():
    """A transient failure must NOT carry the prefix, otherwise
    --retry-failed would skip it forever (silent data loss)."""
    reason = _classify_and_format(RuntimeError('network blip'))
    assert PERMANENT_FAILURE_PREFIX not in reason
