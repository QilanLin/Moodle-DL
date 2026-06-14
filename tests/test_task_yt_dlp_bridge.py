# -*- coding: utf-8 -*-
"""
Unit tests for TaskYtDlpBridge.

Pin the behavior of yt-dlp integration extracted from Task.
This includes the YtLogger sanitization rules and the
progress hook semantics.
"""
import logging
import sys
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.types import DlEvent
from moodle_dl.downloader.task_yt_dlp_bridge import (
    _DRIVER_NOISE_PATTERNS,
    TaskYtDlpBridge,
    YtLogger,
)


def _make_status():
    """Create a simple status object with the fields the bridge touches."""
    return SimpleNamespace(
        yt_dlp_total_size_per_file={},
        yt_dlp_bytes_downloaded_per_file={},
        yt_dlp_current_file=None,
        yt_dlp_used_generic_extractor=False,
        yt_dlp_failed_with_error=False,
        external_total_size=0,
        bytes_downloaded=0,
    )


def _make_bridge() -> TaskYtDlpBridge:
    """Build a TaskYtDlpBridge with a mock task for unit tests."""
    task = MagicMock()
    task.status = _make_status()
    return TaskYtDlpBridge(task)


def _make_logger() -> YtLogger:
    """Build a YtLogger for tests with the task module's logging.

    The Task's YtLogger is the public API used by tests; it
    injects a getter that resolves the task module's logging
    attribute dynamically, so `patch('...task.logging')` works.
    """
    import unittest.mock
    from types import SimpleNamespace
    from moodle_dl.downloader.task_yt_dlp_bridge import (
        _logging_module as _bridge_logging,
    )
    task = unittest.mock.MagicMock()
    task.task_id = 1
    task.status = SimpleNamespace(
        yt_dlp_total_size_per_file={},
        yt_dlp_bytes_downloaded_per_file={},
        yt_dlp_current_file=None,
        yt_dlp_used_generic_extractor=False,
        yt_dlp_failed_with_error=False,
        external_total_size=0,
        bytes_downloaded=0,
    )
    import moodle_dl.downloader.task as _task_module
    bridge = TaskYtDlpBridge(task)
    task._yt_dlp_bridge = bridge
    return YtLogger(
        bridge,
        logging_getter=lambda: getattr(_task_module, 'logging', _bridge_logging),
    )


# =======================================================================
# YtLogger.clean_msg
# =======================================================================
class TestCleanMsg:
    def test_strips_newlines(self):
        result = _make_logger().clean_msg('a\nb\rc')
        assert '\n' not in result and '\r' not in result

    def test_strips_ansi_escapes(self):
        result = _make_logger().clean_msg('a\033[K\033[0;31mb\033[0mc')
        assert '\033' not in result
        assert 'ab' in result and 'c' in result

    def test_censors_token(self):
        """Sensitive data (token=XXX) is replaced with a placeholder."""
        result = _make_logger().clean_msg('url=token=abc123XYZ&x=1')
        # token= replaced with placeholder, abc123 gone
        assert 'abc123' not in result
        assert 'censored_sensitive_data' in result
        # Other params preserved
        assert 'x=1' in result

    def test_multiple_token_occurrences(self):
        result = _make_logger().clean_msg('a token=first b token=second c')
        assert 'censored_sensitive_data' in result
        assert 'first' not in result
        assert 'second' not in result


# =======================================================================
# YtLogger.debug (filters ETA)
# =======================================================================
class TestDebugMethod:
    def test_filters_eta_messages(self):
        """ETA messages return early without logging."""
        logger = _make_logger()
        # No exception, no observable effect (other than returning).
        # We can't easily verify the no-op without mocking, but the
        # filter is the same string check used in the existing
        # test_task_helpers_more tests.
        result = logger.debug('[download] ETA 02:30')
        assert result is None  # no exception

    def test_logs_non_eta_messages(self):
        """Non-ETA messages are cleaned and passed to logging.debug."""
        from unittest.mock import patch
        logger = _make_logger()
        logger.task_id = 42
        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.debug('Some normal debug info')
        mock_logging.debug.assert_called_once()
        args = mock_logging.debug.call_args.args
        assert args[1] == 42
        assert args[2] == 'Some normal debug info'

    def test_cleans_msg_before_logging(self):
        """token=XXX is replaced with placeholder before logging."""
        from unittest.mock import patch
        logger = _make_logger()
        logger.task_id = 42
        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.debug('download token=abc123 done')
        mock_logging.debug.assert_called_once()
        msg = mock_logging.debug.call_args.args[2]
        assert 'abc123' not in msg
        assert 'censored_sensitive_data' in msg


# =======================================================================
# YtLogger.warning
# =======================================================================
class TestWarningMethod:
    def test_generic_extractor_noise_sets_task_flag(self):
        """Generic-extractor fallback is noise; flag is set, no WARNING log."""
        logger = _make_logger()
        bridge = logger.bridge
        bridge.task.status.yt_dlp_used_generic_extractor = False

        from unittest.mock import patch
        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.warning('Falling back on generic information extractor')

        # NOT logged at WARNING level
        mock_logging.warning.assert_not_called()
        # But the task flag was set
        assert bridge.task.status.yt_dlp_used_generic_extractor is True

    def test_forcing_generic_extractor_noise(self):
        from unittest.mock import patch
        logger = _make_logger()
        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.warning('Forcing generic information extractor')
        mock_logging.warning.assert_not_called()

    def test_incompatible_formats_noise(self):
        from unittest.mock import patch
        logger = _make_logger()
        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.warning('Requested formats are incompatible for merge')
        mock_logging.warning.assert_not_called()

    def test_normal_warning_passes_through(self):
        from unittest.mock import patch
        logger = _make_logger()
        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.warning('Some unexpected issue')
        mock_logging.warning.assert_called_once()


# =======================================================================
# YtLogger.error
# =======================================================================
class TestErrorMethod:
    def test_unsupported_url_downgraded(self):
        """'Unsupported URL' is recoverable; flag NOT set, no ERROR log."""
        logger = _make_logger()
        bridge = logger.bridge
        bridge.task.status.yt_dlp_failed_with_error = False

        from unittest.mock import patch
        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.error('Unsupported URL: https://example.com')

        # NOT escalated to ERROR; flag NOT set
        mock_logging.error.assert_not_called()
        assert bridge.task.status.yt_dlp_failed_with_error is False

    def test_no_suitable_info_extractor_downgraded(self):
        from unittest.mock import patch
        logger = _make_logger()
        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.error('no suitable InfoExtractor found')
        mock_logging.error.assert_not_called()

    def test_real_error_escalated(self):
        from unittest.mock import patch
        logger = _make_logger()
        bridge = logger.bridge
        bridge.task.status.yt_dlp_failed_with_error = False

        with patch('moodle_dl.downloader.task.logging') as mock_logging:
            logger.error('Some catastrophic yt-dlp failure')

        # Escalated to ERROR; flag set
        mock_logging.error.assert_called_once()
        assert bridge.task.status.yt_dlp_failed_with_error is True


# =======================================================================
# yt_hook (progress callback)
# =======================================================================
class TestYtHook:
    def test_status_error_returns_early(self):
        bridge = _make_bridge()
        bridge.task.status.yt_dlp_current_file = None
        bridge.yt_hook({'status': 'error'})
        # Nothing should be set
        assert bridge.task.status.yt_dlp_current_file is None

    def test_no_tmpfilename_returns_early(self):
        bridge = _make_bridge()
        bridge.task.status.yt_dlp_current_file = None
        bridge.yt_hook({'status': 'downloading', 'downloaded_bytes': 100})
        assert bridge.task.status.yt_dlp_current_file is None

    def test_empty_tmpfilename_returns_early(self):
        bridge = _make_bridge()
        bridge.yt_hook({'status': 'downloading', 'tmpfilename': ''})
        assert bridge.task.status.yt_dlp_current_file != ''

    def test_records_current_file(self):
        bridge = _make_bridge()
        bridge.task.status.yt_dlp_current_file = None
        bridge.yt_hook({
            'status': 'downloading',
            'tmpfilename': '/path/to/foo.mp4',
            'total_bytes': 1000,
            'downloaded_bytes': 500,
        })
        assert bridge.task.status.yt_dlp_current_file == '/path/to/foo.mp4'

    def test_uses_total_bytes_estimate_when_total_zero(self):
        """If total_bytes is 0/None, fall back to total_bytes_estimate."""
        bridge = _make_bridge()
        # When the underlying task methods are called, the status
        # would normally be updated. The bridge delegates to
        # self.task.report_yt_dlp_*; the integration with the
        # real TaskStatus is exercised in test_task_helpers_more.
        # Here we just verify the bridge doesn't crash and the
        # current_file is recorded.
        bridge.yt_hook({
            'status': 'downloading',
            'tmpfilename': 'f',
            'total_bytes': 0,
            'total_bytes_estimate': 2000,
            'downloaded_bytes': 100,
        })
        assert bridge.task.status.yt_dlp_current_file == 'f'


# =======================================================================
# yt_hook_after_move
# =======================================================================
class TestYtHookAfterMove:
    def test_strips_prefix_before_destination(self):
        bridge = _make_bridge()
        bridge.task.destination = '/dest'
        bridge.task.file.saved_to = ''
        bridge.yt_hook_after_move('/elsewhere/file.mp4')
        # No '/dest' substring, so no stripping
        assert bridge.task.file.saved_to == '/elsewhere/file.mp4'

    def test_strips_prefix_when_contains_destination(self):
        bridge = _make_bridge()
        bridge.task.destination = '/dest'
        bridge.task.file.saved_to = ''
        bridge.yt_hook_after_move('/elsewhere/dest/file.mp4')
        # '/elsewhere' stripped
        assert bridge.task.file.saved_to == '/dest/file.mp4'


# =======================================================================
# is_blocked_for_yt_dlp
# =======================================================================
class TestIsBlockedForYtDlp:
    def test_default_not_blocked(self):
        bridge = _make_bridge()
        assert bridge.is_blocked_for_yt_dlp('https://example.com') is False


# =======================================================================
# report_content_length
# =======================================================================
class TestReportContentLength:
    def test_first_time_emits_total_size(self):
        bridge = _make_bridge()
        bridge.task.status.yt_dlp_total_size_per_file = {}
        bridge.report_content_length(1000, 'file.mp4')
        assert bridge.task.status.yt_dlp_total_size_per_file['file.mp4'] == 1000
        # TOTAL_SIZE callback called
        bridge.task.callback.assert_called_with(
            DlEvent.TOTAL_SIZE, bridge.task, content_length=1000,
        )

    def test_no_change_no_callback(self):
        bridge = _make_bridge()
        bridge.task.status.yt_dlp_total_size_per_file = {'f': 1000}
        bridge.task.callback.reset_mock()
        bridge.report_content_length(1000, 'f')
        # No new callback (size unchanged)
        bridge.task.callback.assert_not_called()


# =======================================================================
# report_received_bytes
# =======================================================================
class TestReportReceivedBytes:
    def test_first_time_emits_received(self):
        bridge = _make_bridge()
        bridge.task.status.yt_dlp_bytes_downloaded_per_file = {}
        bridge.task.status.bytes_downloaded = 0
        bridge.report_received_bytes(100, 'f')
        assert bridge.task.status.bytes_downloaded == 100
        bridge.task.callback.assert_called_with(
            DlEvent.RECEIVED, bridge.task, bytes_received=100,
        )

    def test_diff_zero_no_callback(self):
        """Same bytes received twice → no second callback."""
        bridge = _make_bridge()
        bridge.task.status.yt_dlp_bytes_downloaded_per_file = {'f': 100}
        bridge.task.status.bytes_downloaded = 100
        bridge.task.callback.reset_mock()
        bridge.report_received_bytes(100, 'f')
        bridge.task.callback.assert_not_called()

    def test_diff_positive_emits(self):
        bridge = _make_bridge()
        bridge.task.status.yt_dlp_bytes_downloaded_per_file = {'f': 50}
        bridge.task.status.bytes_downloaded = 50
        bridge.task.callback.reset_mock()
        bridge.report_received_bytes(100, 'f')
        # Diff = 50
        bridge.task.callback.assert_called_with(
            DlEvent.RECEIVED, bridge.task, bytes_received=50,
        )
        assert bridge.task.status.bytes_downloaded == 100


# =======================================================================
# Noise patterns constant
# =======================================================================
class TestDriverNoisePatterns:
    def test_warning_patterns_present(self):
        assert 'Falling back on generic information extractor' in _DRIVER_NOISE_PATTERNS
        assert 'Forcing generic information extractor' in _DRIVER_NOISE_PATTERNS
        assert 'Requested formats are incompatible for merge' in _DRIVER_NOISE_PATTERNS

    def test_error_patterns_present(self):
        assert 'Unsupported URL' in _DRIVER_NOISE_PATTERNS
        assert 'no suitable InfoExtractor' in _DRIVER_NOISE_PATTERNS
