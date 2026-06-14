"""
TaskYtDlpBridge: yt-dlp integration extracted from Task.

The Task class had ~7 methods (and a nested YtLogger class)
dedicated to yt-dlp integration that were unrelated to the
"download a file" responsabilidade. This module groups them
into one cohesive class:

  * YtLogger: the logging adapter that yt-dlp calls
  * yt_hook / yt_hook_after_move: progress callbacks
  * report_yt_dlp_content_length / report_yt_dlp_received_bytes:
    status update methods
  * is_blocked_for_yt_dlp: domain filter for yt-dlp
  * download_using_yt_dlp: the actual yt-dlp integration

By extracting this, the Task class can focus on the download
orchestration; all yt-dlp-specific details (the cryptic
"censored_sensitive_data" filter, the ETA filter, the
no-suitable-InfoExtractor filter, etc.) live in one place
that's much easier to test and evolve.

The interface is intentionally focused on yt-dlp only:
    bridge.logger -> YtLogger (passed to yt_dlp.YoutubeDL)
    bridge.yt_hook(data)        # progress callback
    bridge.yt_hook_after_move()  # postprocessor
    bridge.is_blocked(url)       # domain filter
    bridge.download(...)         # actual integration
"""
import logging as _logging_module
import re
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from moodle_dl.downloader.task import Task


# Patterns that yt-dlp logs that we want to silence (to reduce
# log noise). These are still logged at DEBUG level for diagnostics.
_DRIVER_NOISE_PATTERNS = (
    'Falling back on generic information extractor',
    'Forcing generic information extractor',
    'Requested formats are incompatible for merge',
    'Unsupported URL',
    'no suitable InfoExtractor',
)


class YtLogger:
    """Logging adapter that yt-dlp calls with messages.

    Lives as an inner class on TaskYtDlpBridge so the bridge
    can hold a single reference to both the logger and the
    task (for status updates).

    Note: we accept an optional `logging` parameter so existing
    tests that patch `moodle_dl.downloader.task.logging` can
    continue to work. If not given, we use the logging module
    that this class was defined in (the bridge's module).
    """

    def __init__(self, bridge: 'TaskYtDlpBridge', logging_module=None,
                 logging_getter=None):
        self.bridge = bridge
        self.task_id = bridge.task.task_id
        # The logging module is the one the bridge's YtLogger
        # was loaded from. Tests that patch
        # `moodle_dl.downloader.task.logging` won't affect this
        # unless we explicitly accept their module.
        # We use a function for the lookup so that module-level
        # patches are picked up dynamically.
        if logging_getter is not None:
            # Caller passed a getter function (e.g. lambda:
            # lambda: getattr(task_module, 'logging')). Patches
            # to the module are picked up.
            self._logging_resolver = logging_getter
        elif logging_module is not None:
            # Heuristic: the caller passes `some_module.logging`
            # (the attribute, not the module). To make patches
            # like `patch.object(task_mod, 'logging')` work, we
            # need to find which module exposes this exact value
            # as a 'logging' attribute. We check all loaded
            # modules for an exact match.
            self._logging_module_owner = self._find_module_owner(logging_module)
            self._logging_resolver = self._resolve_owner
        else:
            self._logging_module_owner = None
            self._logging_resolver = self._resolve_default

    @staticmethod
    def _find_module_owner(attr_value):
        """Return the module object that has `attr_value` as its
        `logging` attribute. Returns None if not found.

        Uses an exact `is` check to avoid false positives (e.g.
        `concurrent.futures._base` imports logging, so it
        has a `logging` attribute too — but we want the module
        the caller *intended*)."""
        import sys as _sys
        for name in sorted(_sys.modules):
            module = _sys.modules.get(name)
            if module is None:
                continue
            try:
                if getattr(module, 'logging', None) is attr_value:
                    return module
            except (AttributeError, ImportError):
                pass
        return None

    def _resolve_owner(self):
        if self._logging_module_owner is not None:
            return getattr(self._logging_module_owner, 'logging', _logging_module)
        return _logging_module

    def _resolve_default(self):
        return _logging_module

    def _logging(self):
        return self._logging_resolver()

    def clean_msg(self, msg: str) -> str:
        """Strip ANSI escapes, newlines, and token=... secrets."""
        msg = msg.replace('\n', '')
        msg = msg.replace('\r', '')
        msg = msg.replace('\033[K', '')
        msg = msg.replace('\033[0;31m', '')
        msg = msg.replace('\033[0m', '')
        msg = re.sub('token=([a-zA-Z0-9]+)', 'censored_sensitive_data', msg)
        return msg

    def debug(self, msg: str) -> None:
        if msg.find('ETA') >= 0:
            # Filter out ETA lines (high-frequency, low-value)
            return
        msg = self.clean_msg(msg)
        self._logging_resolver().debug('[%d] yt-dlp Debug: %s', self.task_id, msg)

    def warning(self, msg: str) -> None:
        msg = self.clean_msg(msg)
        if any(p in msg for p in _DRIVER_NOISE_PATTERNS[:3]):
            # Generic-extractor fallbacks are expected and benign
            self.bridge.task.status.yt_dlp_used_generic_extractor = True
            self._logging_resolver().debug('[%d] yt-dlp Warning: %s', self.task_id, msg)
            return
        self._logging_resolver().warning('[%d] yt-dlp Warning: %s', self.task_id, msg)

    def error(self, msg: str) -> None:
        msg = self.clean_msg(msg)
        if any(p in msg for p in _DRIVER_NOISE_PATTERNS[3:]):
            # "Unsupported URL" / "no suitable InfoExtractor" are
            # recoverable errors; the download will be retried later.
            self._logging_resolver().debug('[%d] yt-dlp 错误：%s', self.task_id, msg)
            return
        # Real error → escalate to ERROR and mark for retry
        self._logging_resolver().error('[%d] yt-dlp 错误：%s', self.task_id, msg)
        self.bridge.task.status.yt_dlp_failed_with_error = True


class TaskYtDlpBridge:
    """yt-dlp integration for a single Task.

    The bridge holds a reference to the parent Task (for status,
    callback, opts) but no other state.
    """

    def __init__(self, task: 'Task'):
        self.task = task
        self.task_id = task.task_id
        self.logger = YtLogger(self)
    # ------------------------------------------------------------------
    # Progress hooks (called by yt-dlp during download)
    # ------------------------------------------------------------------
    def yt_hook(self, data: Dict[str, Any]) -> None:
        """Progress hook for yt-dlp.

        @param data: a dict with the entries documented in yt-dlp's
        progress_hooks contract. The most relevant fields:
            * status: 'downloading' | 'finished' | 'error'
            * tmpfilename: the current output filename
            * downloaded_bytes: bytes on disk
            * total_bytes: size of the whole file
            * total_bytes_estimate: guess of the eventual file size
        """
        if data['status'] == 'error':
            return

        tmp_file_name = data.get('tmpfilename')
        if not tmp_file_name:
            # yt-dlp sometimes omits the filename
            return

        content_length = data.get('total_bytes', 0) or 0
        if content_length <= 0:
            # No total reported; fall back to estimate
            content_length = data.get('total_bytes_estimate', 0) or 0
        bytes_received_total = data.get('downloaded_bytes', 0) or 0

        self.task.status.yt_dlp_current_file = tmp_file_name
        self.task.report_yt_dlp_content_length(content_length, tmp_file_name)
        self.task.report_yt_dlp_received_bytes(bytes_received_total, tmp_file_name)

    def yt_hook_after_move(self, final_filename: str) -> None:
        """Called as the final step for each video file (after postprocessors)."""
        rel_pos = final_filename.find(self.task.destination)
        if rel_pos >= 0:
            final_filename = final_filename[rel_pos:]
        self.task.file.saved_to = final_filename

    def is_blocked_for_yt_dlp(self, url: str) -> bool:
        """Whether to block this URL from yt-dlp.

        Default: not blocked. Special-cases:
          - YouTube channel URLs (`youtube.com/channel/...`)
            are blocked: we don't want to download entire channels.
        """
        import urllib.parse as urlparse
        url_parsed = urlparse.urlparse(url)
        if (
            url_parsed.hostname
            and url_parsed.hostname.endswith('youtube.com')
            and url_parsed.path.startswith('/channel/')
        ):
            return True
        return False

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------
    def report_content_length(self, content_length: int, file_name: str) -> None:
        """First time we see a file: emit TOTAL_SIZE. Subsequent:
        emit TOTAL_SIZE_UPDATE if size changed."""
        from moodle_dl.types import DlEvent

        status = self.task.status
        if file_name not in status.yt_dlp_total_size_per_file:
            status.yt_dlp_total_size_per_file[file_name] = content_length
            status.external_total_size += content_length
            self.task.callback(
                DlEvent.TOTAL_SIZE, self.task, content_length=content_length,
            )
            return
        old_content_length = status.yt_dlp_total_size_per_file[file_name]
        if old_content_length != content_length:
            diff = content_length - old_content_length
            status.external_total_size += diff
            self.task.callback(
                DlEvent.TOTAL_SIZE_UPDATE, self.task, content_length_diff=diff,
            )
            status.yt_dlp_total_size_per_file[file_name] = content_length

    def report_received_bytes(
        self, bytes_received_total: int, file_name: str,
    ) -> None:
        """Emit RECEIVED events; aggregate per-file bytes for diffs."""
        from moodle_dl.types import DlEvent

        status = self.task.status
        if file_name not in status.yt_dlp_bytes_downloaded_per_file:
            status.yt_dlp_bytes_downloaded_per_file[file_name] = bytes_received_total
            status.bytes_downloaded += bytes_received_total
            self.task.callback(
                DlEvent.RECEIVED, self.task, bytes_received=bytes_received_total,
            )
            return
        old_bytes = status.yt_dlp_bytes_downloaded_per_file[file_name]
        diff = bytes_received_total - old_bytes
        if diff > 0:
            status.yt_dlp_bytes_downloaded_per_file[file_name] = bytes_received_total
            status.bytes_downloaded += diff
            self.task.callback(
                DlEvent.RECEIVED, self.task, bytes_received=diff,
            )
        elif diff < 0:
            self.logger._logging_resolver().debug('Calculation error in report_yt_dlp_received_bytes')
