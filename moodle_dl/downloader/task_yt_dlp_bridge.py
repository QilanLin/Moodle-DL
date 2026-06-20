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
import asyncio
import os
import re
from typing import TYPE_CHECKING, Any, Dict, Optional

import yt_dlp  # Module-level import so tests can patch
# `moodle_dl.downloader.task.yt_dlp.YoutubeDL`. The bridge uses
# `yt_dlp.YoutubeDL` here, which respects the same patch target
# (the patch operates on the imported `yt_dlp` module reference,
# which both task.py and task_yt_dlp_bridge.py share).

# Re-export logging so methods can call logging.error/debug/etc
# in a way that patches of `moodle_dl.downloader.task.logging`
# (set by tests) work via the resolver.
logging = _logging_module

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
        # 🔧 DRY: call our own report methods directly instead of
        # round-tripping through the task. Previously:
        #   self.task.report_yt_dlp_content_length(...)
        #   → task.report_yt_dlp_content_length(...)
        #   → self._yt_dlp_bridge.report_content_length(...)
        # which was a 2-hop delegation through the task.
        self.report_content_length(content_length, tmp_file_name)
        self.report_received_bytes(bytes_received_total, tmp_file_name)

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

    # ------------------------------------------------------------------
    # Full yt-dlp download (async)
    # ------------------------------------------------------------------
    async def download(
        self, dl_url: str, infos, delete_if_successful: bool,
    ) -> bool:
        """Run yt-dlp to download the URL.

        Builds the right yt-dlp options (output template, logger,
        hooks, retries, cookies), runs yt-dlp in the thread pool,
        then inspects the result to decide whether to retry via
        a generic downloader or surface a helpful error.

        @param dl_url: The URL to download.
        @param infos: HeadInfo (host, content_type, etc.).
        @param delete_if_successful: Whether to delete the
            destination file if yt-dlp succeeded.
        @return: False if the page should be downloaded anyway;
            True if yt-dlp has processed the URL and we are done.
        @raises RuntimeError: when all retries are exhausted
            and `ignore_ytdl_errors` is not set.
        """
        import asyncio as _asyncio
        import functools as _functools
        from pathlib import Path as _Path
        from io import StringIO as _StringIO
        from urllib.parse import unquote as _unquote
        from moodle_dl.utils import PathTools as _PT

        # We try to limit the filename to < 250 chars
        dl_url_decoded = _unquote(dl_url)
        is_kaltura_playlist = 'playlistAPI.kpl0Id' in dl_url_decoded
        if is_kaltura_playlist:
            base_name = os.path.splitext(self.task.filename)[0]
            safe_base_name = _PT.truncate_filename(base_name, is_file=False, max_length=120)
            filename_template = (
                f'{safe_base_name} - %(playlist_index)02d - '
                '%(title).120B (%(id).32B).%(ext)s'
            )
        elif self.task.file.content_type == 'description-url':
            filename_template = '%(title).180B (%(id).32B).%(ext)s'
        else:
            # For kalvidres and other videos, use the Moodle-provided filename
            base_name = os.path.splitext(self.task.filename)[0]
            filename_template = _PT.truncate_filename(base_name, is_file=False, max_length=240) + '.%(ext)s'
        output_template = str(_Path(self.task.destination) / filename_template)

        ydl_opts = {
            'logger': self.logger,
            'progress_hooks': [self.yt_hook],
            'post_hooks': [self.yt_hook_after_move],
            'outtmpl': output_template,
            'nocheckcertificate': self.task.opts.global_opts.skip_cert_verify,
            'retries': 10,
            'fragment_retries': 10,
            'ignoreerrors': True,
            'addmetadata': True,
            'restrictfilenames': self.task.opts.restricted_filenames,
        }

        ydl_opts.update(self.task.opts.yt_dlp_options)

        if self.task.opts.cookies_text is not None:
            ydl_opts.update({'cookiefile': _StringIO(self.task.opts.cookies_text)})

        # Use task.py's yt_dlp module reference and the
        # add_additional_extractors re-exported by task.py. Both
        # of these are set up to be patchable by tests at
        # `moodle_dl.downloader.task.yt_dlp.YoutubeDL` and
        # `moodle_dl.downloader.task.add_additional_extractors`.
        from moodle_dl.downloader import task as _task_mod
        ydl = _task_mod.yt_dlp.YoutubeDL(ydl_opts)
        _task_mod.add_additional_extractors(ydl)

        password_list = self.task.opts.video_passwords.get(infos.host, [None])
        if not isinstance(password_list, list):
            password_list = [password_list]
        if len(password_list) == 0:
            # Try at least once with no password
            password_list = [None]

        for password in password_list:
            if password is not None:
                # Don't overwrite the user's yt_dlp_options videopassword
                ydl.params['videopassword'] = password

            # We restart yt-dlp, so we need to reset the return code
            self.task.status.yt_dlp_failed_with_error = False
            self.task.status.yt_dlp_used_generic_extractor = False
            ydl._download_retcode = 0  # pylint: disable=protected-access
            try:
                loop = _asyncio.get_running_loop()
                # 🔧 Hang fix: wrap ``run_in_executor`` in
                # ``asyncio.wait_for`` so a stuck yt-dlp can't hang
                # the event loop forever. Default 10 minutes; the
                # operator can override with the env var
                # ``YT_DLP_TIMEOUT``.
                ydl_timeout = float(os.environ.get('YT_DLP_TIMEOUT', '600'))
                ydl_result = await asyncio.wait_for(
                    loop.run_in_executor(
                        self.task.thread_pool,
                        _functools.partial(ydl.download, dl_url),
                    ),
                    timeout=ydl_timeout,
                )
                if ydl_result == 0:
                    if self.task._is_index_mod_page_file():
                        # We want to download legacy moodle pages
                        return False
                    # yt-dlp has an extractor for this URL so we do not
                    # want to download the URL extra — only if yt-dlp
                    # used a generic extractor.
                    return not self.task.status.yt_dlp_used_generic_extractor
            except asyncio.TimeoutError:
                error_msg = f'yt-dlp exceeded {ydl_timeout}s timeout'
                logging.error('[%d] ❌ %s', self.task_id, error_msg)
                self._log_yt_dlp_error_diagnosis(error_msg)
            except Exception as yt_err:
                error_msg = str(yt_err)
                logging.error('[%d] ❌ yt-dlp 下载失败', self.task_id)
                # Detailed error diagnosis
                self._log_yt_dlp_error_diagnosis(error_msg)

                logging.debug('[%d] yt-dlp 完整错误: %s', self.task_id, error_msg)
                self.task.status.yt_dlp_failed_with_error = True

        if self.task.status.yt_dlp_failed_with_error and not self.task.opts.global_opts.ignore_ytdl_errors:
            if not delete_if_successful:
                _PT.remove_file(self.task.file.saved_to)
            raise RuntimeError(
                f'yt-dlp 无法下载该 URL。\n'
                f'文件: {self.task.file.content_filename}\n'
                f'URL: {dl_url[:80]}...\n\n'
                f'可能的原因:\n'
                f'  • DRM 保护 - 某些视频被版权保护\n'
                f'  • CDN 不可用 - Kaltura 服务器暂时不可用\n'
                f'  • Cookie 过期 - 认证信息已失效\n'
                f'  • 网络问题 - 连接超时或中断\n\n'
                f'建议操作:\n'
                f'  1. 刷新 Cookie: moodle-dl --refresh-cookies\n'
                f'  2. 重试下载: moodle-dl --retry-failed\n'
                f'  3. 忽略错误: moodle-dl --ignore-ytdl-errors (再运行一次)\n'
                f'  4. 查看详细日志: moodle-dl -v\n\n'
                f'详见日志了解具体错误类型和建议。'
            )

        # We want to download the URL because yt-dlp has no extractor
        return False

    def _log_yt_dlp_error_diagnosis(self, error_msg: str) -> None:
        """Log a detailed diagnosis of a yt-dlp error.

        Pattern-matches against common error categories
        (DRM, 403, 404, 503, Timeout, InvalidURL) and
        emits a Chinese-language message with the
        category, the explanation, and the suggested fix.
        """
        # IMPORTANT: keep the patterns identical to the original
        # code so tests that grep for these substrings still pass.
        if 'DRM' in error_msg or 'protected' in error_msg or 'widevine' in error_msg.lower():
            logging.error('[%d] 📋 错误类型: DRM 保护', self.task_id)
            logging.error('[%d] 说明: 该视频受 DRM 保护无法通过此方式下载', self.task_id)
            logging.error('[%d] 建议: 无法绕过 DRM 保护。请联系教师或管理员', self.task_id)
        elif '403' in error_msg or 'Forbidden' in error_msg:
            logging.error('[%d] 📋 错误类型: 403 禁止访问 (Cookie 过期)', self.task_id)
            logging.error('[%d] 说明: 没有权限访问 Kaltura CDN（通常是 Cookie 过期）', self.task_id)
            logging.error('[%d] 建议: 运行 moodle-dl --refresh-cookies 刷新 Cookie', self.task_id)
        elif '404' in error_msg or 'Not Found' in error_msg:
            logging.error('[%d] 📋 错误类型: 404 内容未找到', self.task_id)
            logging.error('[%d] 说明: Kaltura 服务器上未找到该视频', self.task_id)
            logging.error('[%d] 建议: 检查视频是否被删除或移动', self.task_id)
        elif '503' in error_msg or 'Service Unavailable' in error_msg:
            logging.error('[%d] 📋 错误类型: 503 CDN 不可用', self.task_id)
            logging.error('[%d] 说明: Kaltura CDN 服务器临时不可用', self.task_id)
            logging.error('[%d] 建议: 服务器可能在维护，请稍后重试', self.task_id)
        elif 'Timeout' in error_msg or 'timeout' in error_msg.lower():
            logging.error('[%d] 📋 错误类型: 网络超时', self.task_id)
            logging.error('[%d] 说明: 连接 yt-dlp 下载服务超过 30 秒', self.task_id)
            logging.error('[%d] 建议: 网络不稳定，请检查连接或稍后重试', self.task_id)
        elif 'InvalidURL' in error_msg or 'URL' in error_msg:
            logging.error('[%d] 📋 错误类型: URL 无效或构建错误', self.task_id)
            logging.error('[%d] 说明: yt-dlp 收到的 URL 格式不正确', self.task_id)
            logging.error('[%d] 建议: 可能是 CDN 地址错误，请提交问题报告', self.task_id)
        else:
            logging.error('[%d] 📋 错误类型: 其他错误', self.task_id)
            logging.error('[%d] 详细信息: %s', self.task_id, error_msg[:200])
            logging.error('[%d] 建议: 检查日志中的详细错误信息，或尝试以下步骤:', self.task_id)
            logging.error('[%d]   1. 刷新 Cookie: moodle-dl --refresh-cookies', self.task_id)
            logging.error('[%d]   2. 重试下载: moodle-dl --retry-failed', self.task_id)
            logging.error('[%d]   3. 查看详细日志: moodle-dl -v', self.task_id)
