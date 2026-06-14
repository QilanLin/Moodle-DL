# -*- coding: utf-8 -*-
import asyncio
import copy
import functools
import html
import http.cookiejar
import logging
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import time
import traceback
import urllib
import urllib.parse as urlparse
from concurrent.futures import ThreadPoolExecutor
from email.utils import unquote
from io import StringIO
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.error import ContentTooShortError

import aiofiles
import aiohttp
import html2text
import requests
import yt_dlp  # Re-enabled for cookie_mod files (kalvidres, helixmedia, lti)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from moodle_dl.downloader.extractors import add_additional_extractors
from moodle_dl.downloader.kaltura_patterns import (
    COOKIE_MOD_MODNAMES,
    MODULE_COOKIE_KALVIDRES,
)
from moodle_dl.downloader._patterns import (  # noqa: E402
    NET_ERRORS,
    IncompleteRecord,
    ensure_dir,
    ensure_parent_dir,
    safe_remove_part_and_final,
)
from moodle_dl.downloader.task_cookie_manager import TaskCookieManager
from moodle_dl.downloader.task_file_ops import TaskFileOps
from moodle_dl.downloader.task_url_ops import TaskUrlOps
from moodle_dl.downloader.task_yt_dlp_bridge import TaskYtDlpBridge, YtLogger


# ---------------------------------------------------------------------------
# Part-file resume infrastructure
# ---------------------------------------------------------------------------
#: Suffix used for in-progress (partial) downloads. A complete file
#: never has this suffix. The atomic rename pattern means:
#:   download in progress: foo.pdf.part
#:   kill mid-download:     foo.pdf.part (on disk, no final file)
#:   resume / complete:     foo.pdf (final, atomic rename)
PART_FILE_SUFFIX = '.part'


def dest_path_to_part_path(dest_path: str) -> str:
    """Return the .part path for a given final destination path.

    Idempotent: calling on an already-`.part` path returns it
    unchanged. This avoids accidentally double-suffixing."""
    if not dest_path:
        return '.part'
    if dest_path.endswith(PART_FILE_SUFFIX):
        return dest_path
    return dest_path + PART_FILE_SUFFIX


def validate_part_file_size(part_path: str, expected_total: int) -> tuple:
    """Decide what to do with an existing .part file.

    Returns:
        (is_valid, action) where:
        - is_valid: True if part file can be reused, False if it must
          be discarded.
        - action: one of 'resume', 'rename_to_final',
          'delete_and_redownload'.

    Logic:
      - If expected_total is 0 (server didn't report size): treat any
        non-empty part as resumable. Empty part = start over.
      - If part size == expected_total: complete (rename to final).
      - If part size < expected_total: resumable via Range.
      - If part size > expected_total: corrupt (delete and
        re-download).
    """
    try:
        part_size = os.path.getsize(part_path)
    except OSError:
        return False, 'delete_and_redownload'

    if part_size == 0:
        return False, 'delete_and_redownload'

    if expected_total == 0:
        # Server didn't tell us the total size. We have some data —
        # try to resume.
        return True, 'resume'

    if part_size == expected_total:
        return True, 'rename_to_final'

    if part_size < expected_total:
        return True, 'resume'

    # part_size > expected_total (corrupt or wrong file)
    return False, 'delete_and_redownload'


def scan_for_orphan_part_files(workspace_root: str, recorder) -> List[tuple]:
    """Walk workspace_root, find ``*.part`` files, classify them.

    Returns a list of ``(part_path, expected_total, action)`` tuples
    for every ``.part`` file whose ``(file_id, file_path)`` is NOT in
    the ``incomplete_downloads`` table (i.e., orphans left by a
    previous run that died before ``save_incomplete_download`` ran).

    Files already in the table are skipped (the regular resume path
    handles them).
    """
    import sqlite3
    orphans = []
    for root, dirs, files in os.walk(workspace_root):
        # Skip macOS metadata and hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith('._') and not d.startswith('.')]
        for f in files:
            if not f.endswith(PART_FILE_SUFFIX) or f.startswith('._'):
                continue
            full = os.path.join(root, f)
            # Try to find an existing incomplete_downloads row for
            # this path. The DB stores the .part path in file_path.
            try:
                conn = sqlite3.connect(recorder.db_file)
                cur = conn.cursor()
                cur.execute(
                    """SELECT downloaded_bytes, total_bytes
                       FROM incomplete_downloads
                       WHERE file_path = ? AND status = 'pending'""",
                    (full,),
                )
                row = cur.fetchone()
                conn.close()
            except Exception:
                row = None
            if row is not None:
                # Already tracked; regular path handles it
                continue
            # Orphan: figure out expected_total from disk size
            try:
                part_size = os.path.getsize(full)
            except OSError:
                continue
            orphans.append((full, part_size, 'unknown'))
    return orphans
from moodle_dl.downloader.kaltura_url import (
    KalturaAuthenticationError,
    KalturaCDNError,
    KalturaExtractionError,
    KalturaUrlBuilder,
)
from moodle_dl.downloader.leganto_download import (
    build_leganto_download_plan,
    leganto_course_url,
    leganto_lti_launch_token_expiry,
    leganto_moodle_launch_url,
)
from moodle_dl.downloader.leganto_print import LegantoPdfPrinter, LegantoPermanentFailureError, is_leganto_reading_list_url
from moodle_dl.file_classifier import is_optional_metadata_filename
from moodle_dl.types import (
    Course,
    DlEvent,
    DownloadOptions,
    File,
    HeadInfo,
    TaskState,
    TaskStatus,
)
from moodle_dl.utils import LINK_TEMPLATES, MoodleDLCookieJar, UrlHelper
from moodle_dl.utils import PathTools as PT
from moodle_dl.utils import (
    SslHelper,
    Timer,
    clone_aiohttp_cookie_jar,
    convert_to_aiohttp_cookie_jar,
    format_bytes,
    format_seconds,
    timeconvert,
)


class Task:
    """
    Task is responsible to download or create a file.
    
    📥 DOWNLOAD IMPLEMENTATION FEATURES:
    
    1. RESUME/RESUMABLE DOWNLOAD (断点续传):
       - Supports HTTP Range requests for resumable downloads
       - Checks server's Accept-Ranges header
       - Stores incomplete download state in database (incomplete_downloads table)
       - Verifies file integrity using ETag and Last-Modified headers
       - Automatically resumes from last checkpoint on network interruption
       
       Flow:
         1. Check if server supports Range requests (Accept-Ranges: bytes)
         2. On download failure, save progress to incomplete_downloads table:
            - downloaded_bytes: How many bytes were downloaded
            - total_bytes: Expected file size
            - etag/last_modified: For integrity verification
         3. On retry, check incomplete_downloads and resume if conditions met:
            - Server still supports Range requests
            - File hasn't been modified (check ETag/Last-Modified)
            - Retry attempts < limit
       
    2. MULTI-LAYER ERROR HANDLING:
       - Graceful handling of network failures
       - Automatic retry with exponential backoff
       - Fallback to alternative download methods (external downloader, yt-dlp)
       - Smart status tracking for debugging
    
    3. SPECIAL FILE TYPES:
       - HTML files: Saved as HTML content
       - Description files: Converted to Markdown
       - External URLs: Created as .desktop/.URL shortcuts
       - Kaltura videos: yt-dlp based extraction
       - Book modules: Playwright-based rendering
    
    4. OPTIMIZATION:
       - Chunked download with configurable chunk size
       - Concurrent downloads with semaphore control
       - Progress tracking per file
       - Proper resource cleanup on error
    
    Reference: RFC 7233 HTTP Range Requests specification
    """
    CHUNK_SIZE = 102400  # default: 1024 * 100 = 100kb; will be overwritten with download_chunk_size
    MAX_DL_RETRIES = 3

    # ======================== Session warm-up 节流 ========================
    # 当 kalvidres 请求落到 enrol/login 重定向页时，Moodle 的 session 可能只是
    # 进入了"降级"状态——cookie 还在但服务端不认。一次便宜的主页 GET 就能让
    # session 重新被识别。但要节流：避免大批量重定向风暴时每个任务都打主页。
    # 5 分钟窗口是经验值——比 sessiontimeout (8h) 短得多，比单批下载的
    # 短突发 (秒级) 长得多。
    SESSION_WARMUP_MIN_INTERVAL_S = 5 * 60
    _last_session_warmup_at: float = 0.0
    # asyncio.Lock 必须惰性创建，否则会绑到 import 时的 event loop（通常是 None）
    _session_warmup_lock: Optional[asyncio.Lock] = None

    # ======================== Kaltura 提取常量 ========================
    # HTTP 请求配置
    REQUEST_TIMEOUT = 30
    REQUEST_RETRY_ATTEMPTS = 3
    REQUEST_BACKOFF_FACTOR = 1

    # Kaltura CDN 列表（按优先级排序）
    KALTURA_CDN_FALLBACKS = KalturaUrlBuilder.CDN_FALLBACKS
    KALTURA_PARTNER_FALLBACKS_BY_HOST = KalturaUrlBuilder.PARTNER_FALLBACKS_BY_HOST
    KALTURA_UICONF_FALLBACKS_BY_HOST = KalturaUrlBuilder.UICONF_FALLBACKS_BY_HOST

    # 正则表达式模式（预编译）
    REGEX_ENTRY_ID = KalturaUrlBuilder.REGEX_ENTRY_ID
    REGEX_UICONF_ID = KalturaUrlBuilder.REGEX_UICONF_ID
    REGEX_KALTURA_PLAYLIST = KalturaUrlBuilder.REGEX_KALTURA_PLAYLIST
    REGEX_PARTNER_ID = KalturaUrlBuilder.REGEX_PARTNER_ID
    REGEX_KALTURA_CDN = KalturaUrlBuilder.REGEX_KALTURA_CDN
    REGEX_LTI_IFRAME = re.compile(r'<iframe[^>]+src="([^"]*lti_launch\.php[^"]*)"')
    REGEX_TARGET_LINK_URI = re.compile(r'name="target_link_uri"\s+value="([^"]+)"')

    # DRM 检测关键词
    DRM_KEYWORDS = [
        'DRM',
        'protected',
        'widevine',
        'encrypted',
        'drm-protected',
        'WidevineDecryptor',
    ]
    RQ_HEADER = {
        'User-Agent': (
            'Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36'
            + ' (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile'
        ),
        'Content-Type': 'application/x-www-form-urlencoded',
    }

    def _kaltura_urls(self) -> KalturaUrlBuilder:
        return KalturaUrlBuilder(self.task_id)

    def __init__(
        self,
        task_id: int,
        file: File,
        course: Course,
        options: DownloadOptions,
        thread_pool: ThreadPoolExecutor,
        callback: Callable[[], None],
        database: Optional["StateRecorder"] = None,
    ):
        self.task_id = task_id
        self.file = file
        self.course = course
        self.opts = options
        self.thread_pool = thread_pool
        self.callback = callback
        # The main flow may inject a pre-built StateRecorder. If
        # not provided, the Task falls back to creating its own
        # (preserving backward compatibility). The main flow's
        # use of database= avoids the per-completion schema
        # validation that the legacy code triggered, and ensures
        # that completion writes land in the correct DB when
        # global opts is being reused across workspaces.
        self.database = database

        # 🔧 Refactor: cookie/session management is delegated to
        # TaskCookieManager, which encapsulates the cookie cache
        # and requests.Session factory. This reduces the Task
        # surface area and groups all cookie concerns in one place.
        self._cookie_mgr = TaskCookieManager(
            options,
            retry_attempts=self.REQUEST_RETRY_ATTEMPTS,
            backoff_factor=self.REQUEST_BACKOFF_FACTOR,
        )

        # 🔧 Refactor: file/path/HTML operations are delegated
        # to TaskFileOps, which groups filename generation, path
        # construction, HTML cleaning, and shortcut management in
        # one class. Keeps Task focused on download orchestration.
        self._file_ops = TaskFileOps(self)

        # 🔧 Refactor: yt-dlp integration is delegated to
        # TaskYtDlpBridge, which owns YtLogger, yt_hook,
        # yt_hook_after_move, report_yt_dlp_*, is_blocked_*, and
        # download_using_yt_dlp. The bridge keeps the cryptic
        # "censored_sensitive_data" filter, ETA filter, and
        # yt-dlp noise-pattern detection in one place.
        self._yt_dlp_bridge = TaskYtDlpBridge(self)

        # 🔧 Refactor: URL operations are delegated to TaskUrlOps,
        # which owns add_token_to_url, is_filtered_domain, and
        # is_drm_error. The class is stateless so it's used as
        # a module-level utility, but it's exposed via self._url_ops
        # for consistency with the other helpers.
        self._url_ops = TaskUrlOps()

        # API 来源标记 ('mobile' 或 'web')，用于 Fallback 策略
        self.api_source = 'mobile'  # 默认为 mobile API

        self.destination = self.gen_path(options.download_path, course, file)
        self.filename = self._file_ops.generate_filename_with_index(file)
        self.status = TaskStatus()

    def _get_or_create_database(self) -> "StateRecorder":
        """Return the injected database, or build a one-off from
        global_opts. The injection path is preferred (avoids
        per-completion schema validation and ensures workspace
        isolation); the fallback path preserves compatibility with
        callers that haven't been refactored yet.

        When constructing the fallback ConfigHelper, we pass
        validate_db=False to skip the full StateRecorder init
        in ConfigHelper.__init__ — we want a StateRecorder of
        our own choosing, not a side-effect-init one.
        """
        if self.database is not None:
            return self.database
        from moodle_dl.config import ConfigHelper
        from moodle_dl.database import StateRecorder
        config = ConfigHelper(self.opts.global_opts, validate_db=False)
        return StateRecorder(config, self.opts)

    def _extract_entry_id(self, url: str) -> str:
        """
        从 browseandembed URL 中提取 entry ID。
        
        @param url: browseandembed URL
        @return: entry ID
        @raise: KalturaExtractionError 如果提取失败
        """
        return self._kaltura_urls().extract_entry_id(url)

    def _extract_uiconf_id(self, url: str) -> str:
        """
        从 browseandembed URL 中提取 uiconf_id。
        
        @param url: browseandembed URL
        @return: uiconf_id
        @raise: KalturaExtractionError 如果提取失败
        """
        return self._kaltura_urls().extract_uiconf_id(url)

    def _extract_partner_id(self, html_content: str) -> str:
        """
        从 browseandembed 页面中提取 partner ID。
        
        @param html_content: browseandembed 页面 HTML 内容
        @return: partner ID
        @raise: KalturaExtractionError 如果提取失败
        """
        return self._kaltura_urls().extract_partner_id(html_content)

    def _infer_partner_id_from_browse_url(self, browseandembed_url: str) -> Optional[str]:
        """
        Infer a Kaltura partner ID from known institutional KAF hosts.

        This is only used after parsing the browseandembed HTML failed. The
        entry ID and uiconf ID still come from the signed LTI launch response.
        """
        return self._kaltura_urls().infer_partner_id_from_browse_url(browseandembed_url)

    def _infer_uiconf_id_from_browse_url(self, browseandembed_url: str) -> Optional[str]:
        """Infer a Kaltura uiconf ID for known KCL hosts when the URL omits it."""
        return self._kaltura_urls().infer_uiconf_id_from_browse_url(browseandembed_url)

    @staticmethod
    def _source_url_from_kaltura_lti_launch(url: str) -> Optional[str]:
        """Return the Kaltura source URL embedded in Moodle's lti_launch.php URL."""
        return KalturaUrlBuilder.source_url_from_lti_launch(url)

    def _build_kaltura_url_from_known_embed_url(self, url: str) -> Optional[str]:
        """
        Build a yt-dlp-friendly Kaltura URL from URLs that already contain the
        entry id and player skin.

        Moodle descriptions sometimes contain KCL Kaltura embed URLs directly,
        or Moodle's lti_launch.php wrapper with the real KAF URL in the source
        query parameter. Fetching those pages is unnecessary and can fail on
        local certificate stores, so derive the stable player URL directly.
        """
        return self._kaltura_urls().build_from_known_embed_url(url)

    def _log_browseandembed_url(self, browseandembed_url: str) -> None:
        """Log the non-sensitive parts of a Kaltura browseandembed URL."""
        self._kaltura_urls().log_browseandembed_url(browseandembed_url)

    def _detect_kaltura_cdn(self, html_content: str) -> Optional[str]:
        """
        从页面中检测 Kaltura CDN 地址。
        
        @param html_content: 页面 HTML 内容
        @return: CDN 地址或 None
        """
        return self._kaltura_urls().detect_cdn(html_content)

    def _build_kaltura_url(self, partner_id: str, uiconf_id: str, entry_id: str, cdn: str) -> str:
        """
        构建 Kaltura 播放器 URL。
        
        @param partner_id: Partner ID
        @param uiconf_id: UI 配置 ID
        @param entry_id: Entry ID
        @param cdn: CDN 地址
        @return: 完整的 Kaltura URL
        """
        return self._kaltura_urls().build_url(partner_id, uiconf_id, entry_id, cdn)

    def _build_kaltura_playlist_url(
        self,
        partner_id: str,
        uiconf_id: str,
        playlist_id: str,
        cdn: str,
        source_url: str,
    ) -> str:
        """Build a yt-dlp-friendly Kaltura URL for KAF playlist embeds."""
        return self._kaltura_urls().build_playlist_url(
            partner_id,
            uiconf_id,
            playlist_id,
            cdn,
            source_url,
        )

    @staticmethod
    def gen_path(storage_path: str, course: Course, file: File):
        # 🔧 Delegated to TaskFileOps. Behavior preserved.
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        import unittest.mock as _mock
        return TaskFileOps(_mock.MagicMock()).gen_path(storage_path, course, file)
    def add_token_to_url(self, url: str) -> str:
        """
        Adds the Moodle token to a URL (使用改进的 URL 处理)

        基于官方 Moodle Mobile App 的 fixPluginfileURL 实现
        参考：moodleapp/src/core/singletons/url.ts

        改进点：
        - 处理 HTML 转义 (&amp; → &)
        - 避免重复添加 token
        - 自动转换 pluginfile.php → webservice/pluginfile.php
        - 添加 offline=1 参数（外部仓库必需）

        @param url: The URL to that the token should be added.
        @return: The URL with the token.
        """
        # 🔧 Delegated to TaskUrlOps. Behavior preserved.
        return self._url_ops.add_token_to_url(
            url=url,
            token=self.opts.token,
            moodle_base_url=self.opts.moodle_url,
        )

    def create_target_file(self, target_path: str) -> str:
        """
        Rename target_path if necessary to an unused filename and touch the target_path.
        @param target_path: The desired path for the target file
        @return: Path to the touched target file (may differ from input if renamed)
        """
        target_path = PT.get_unused_file_path(target_path)
        PT.touch_file(target_path)
        return target_path

    def rename_old_file(self) -> bool:
        """
        Try to rename an existing modified file. Add the extension '_old' to the filename if possible.
        @return: True on success
        """
        if self.file.old_file is None:
            return False

        old_path = self.file.old_file.saved_to
        if not os.path.exists(old_path):
            return False

        logging.debug('[%d] Renaming old file', self.task_id)

        destination, filename, file_extension = PT.get_path_parts(old_path)
        new_filename = f'{filename}_old.{file_extension}'
        new_path = PT.get_unused_file_path(PT.make_path(destination, new_filename))

        try:
            shutil.move(old_path, new_path)
            self.file.old_file.saved_to = new_path
        except OSError:
            logging.warning('[%d] Failed to renaming old file %r to %r', self.task_id, old_path, new_path)
            return False

        return True

    # 🔧 Yt-dlp integration is delegated to TaskYtDlpBridge.
    # See moodle_dl/downloader/task_yt_dlp_bridge.py for the
    # actual implementations. Task keeps the thin delegators
    # so the existing call sites (including legacy tests that
    # patch `moodle_dl.downloader.task.YtLogger` etc.) keep
    # working.
    class YtLogger:
        """logger for yt-dlp (kept as a Task class attribute for
        backward compatibility with `Task.YtLogger(task)` and
        existing tests that patch `moodle_dl.downloader.task.logging`).
        Delegates all real work to TaskYtDlpBridge's YtLogger."""

        def __init__(self, task):
            # Pass a getter for the task module's logging so
            # existing tests patching `moodle_dl.downloader.task.logging`
            # continue to work. The closure captures the module
            # OBJECT (not the attribute value), so patches are
            # picked up dynamically.
            import moodle_dl.downloader.task as _task_module
            from moodle_dl.downloader.task_yt_dlp_bridge import (
                _logging_module as _bridge_logging,
            )
            self.bridge = task._yt_dlp_bridge
            self.task = task
            self.task_id = task.task_id
            # Re-build the bridge's logger using the task module's
            # logging so the test patches hit it.
            self.bridge.logger = YtLogger(
                task._yt_dlp_bridge,
                logging_getter=lambda: getattr(_task_module, 'logging', _bridge_logging),
            )

        def clean_msg(self, msg: str) -> str:
            return self.bridge.logger.clean_msg(msg)

        def debug(self, msg):
            # Inline the ETA filter so the message flow is preserved
            # without round-tripping through the bridge (which would
            # re-construct the logger).
            if msg.find('ETA') >= 0:
                return
            self.bridge.logger.debug(msg)

        def warning(self, msg):
            self.bridge.logger.warning(msg)

        def error(self, msg):
            self.bridge.logger.error(msg)

    def yt_hook(self, data: Dict):
        self._yt_dlp_bridge.yt_hook(data)

    def report_yt_dlp_content_length(self, content_length: int, file_name: str):
        self._yt_dlp_bridge.report_content_length(content_length, file_name)

    def report_yt_dlp_received_bytes(self, bytes_received_total: int, file_name: str):
        self._yt_dlp_bridge.report_received_bytes(bytes_received_total, file_name)

    def yt_hook_after_move(self, final_filename: str):
        self._yt_dlp_bridge.yt_hook_after_move(final_filename)

    def is_blocked_for_yt_dlp(self, url: str):
        return self._yt_dlp_bridge.is_blocked_for_yt_dlp(url)

    def set_utime(self, last_modified_header: str = None):
        """
        Sets the last modified time of the downloaded file
        Modified time will be set based on the given last_modified value or the moodle file attribute timemodified
        Access time will always be set to now

        @param last_modified_header: The last_modified header from the Webpage. Defaults to None.
        """
        if not os.path.isfile(self.file.saved_to):
            return
        try:
            if last_modified_header is not None:
                server_modified_time = timeconvert(last_modified_header)
                if server_modified_time is not None and server_modified_time > 0:
                    os.utime(self.file.saved_to, (time.time(), server_modified_time))
                    return

            if self.file.content_timemodified is not None and self.file.content_timemodified > 0:
                os.utime(self.file.saved_to, (time.time(), self.file.content_timemodified))

        except OSError:
            logging.debug(
                '[%d] Access time and modification time of the downloaded file could not be set', self.task_id
            )

    async def get_head_infos(self, dl_url: str) -> HeadInfo:
        """
        Do a Head request to collect some information about the URL
        @return: If download should be aborted then None; else HeadInfo
        """
        ssl_context = SslHelper.get_ssl_context(
            self.opts.global_opts.skip_cert_verify,
            self.opts.global_opts.allow_insecure_ssl,
            self.opts.global_opts.use_all_ciphers,
        )
        async with aiohttp.ClientSession(cookie_jar=self.get_cookie_jar(), raise_for_status=True) as session:
            try:
                async with session.request("HEAD", dl_url, headers=self.RQ_HEADER, ssl=ssl_context, timeout=20) as resp:
                    if resp.url != dl_url:
                        if resp.history and len(resp.history) > 0:
                            logging.debug('[%d] URL was %s time(s) redirected', self.task_id, len(resp.history))
                        else:
                            logging.debug('[%d] URL has changed after information retrieval', self.task_id)

                    guessed_file_name = posixpath.basename(resp.url.path)
                    if "Content-Disposition" in resp.headers.keys():
                        # Exp: Content-Disposition: attachment; filename="filename.jpg"
                        found_names = re.findall("filename=(.+)", resp.headers["Content-Disposition"])
                        if len(found_names) > 0:
                            guessed_file_name = unquote(found_names[0])

                    return HeadInfo(
                        # Exp: Content-Type: text/html; charset=utf-8
                        content_type=resp.headers.get('Content-Type', 'text/html').split(';')[0],
                        content_length=int(resp.headers.get('Content-Length', -1)),
                        # Exp: Last-Modified: Wed, 21 Oct 2015 07:28:00 GMT
                        last_modified=resp.headers.get('Last-Modified', None),
                        final_url=str(resp.url),
                        guessed_file_name=guessed_file_name,
                        host=resp.url.host,
                    )

            except aiohttp.InvalidURL:
                # don't download urls like 'mailto:name@provider.com'
                logging.debug(
                    '[%d] 外部文件下载已取消，因为 URL 格式无效',
                    self.task_id,
                )
                return None
            except aiohttp.ClientResponseError as head_err:
                if head_err.status in [408, 409, 429]:
                    # 408 (timeout) or 409 (conflict) and 429 (too many requests)
                    logging.warning(
                        '[%d] Head 请求失败，状态：%s %s', self.task_id, head_err.status, head_err.message
                    )
                    raise head_err from None

                logging.warning(
                    '[%d] 外部文件下载已取消，因为 HTTP 错误：%s %s',
                    self.task_id,
                    head_err.status,
                    head_err.message,
                )
                return None

            except (aiohttp.ClientError, OSError, ValueError, ContentRangeError) as head_err:
                logging.warning('[%d] Head request for external file failed with unexpected error', self.task_id)
                raise head_err from None

    async def download_using_yt_dlp(self, dl_url: str, infos: HeadInfo, delete_if_successful: bool):
        # 🔧 Delegated to TaskYtDlpBridge. Behavior preserved.
        return await self._yt_dlp_bridge.download(
            dl_url, infos, delete_if_successful,
        )

    async def download_using_external_downloader(self, dl_url: str, external_dl_cmd: str, delete_if_successful: bool):
        cmd = external_dl_cmd.replace('%U', dl_url)
        logging.debug(
            '[%d] Run external downloader using the following command: `%s`',
            self.task_id,
            cmd,
        )
        external_dl_failed_with_error = False
        try:
            proc = await asyncio.create_subprocess_exec(
                shlex.split(cmd),
                cwd=str(self.destination),
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )

            for lines in await proc.stdout.readline():
                # line = line.decode('utf-8', 'replace')
                logging.debug('[%d] Ext-Dl: %s', self.task_id, lines.splitlines()[-1])

            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                external_dl_failed_with_error = True
        except (subprocess.SubprocessError, ValueError, TypeError) as e:
            stderr = str(e)
            external_dl_failed_with_error = True

        if external_dl_failed_with_error:
            logging.error('[%d] External downloader error: %s', self.task_id, stderr)
            if not delete_if_successful:
                # cleanup the url-link file
                PT.remove_file(self.file.saved_to)
            raise RuntimeError('The external downloader could not download the URL')

        self.file.saved_to = str(Path(self.destination) / self.filename)

    async def external_download_url(self, add_token: bool, delete_if_successful: bool, needs_moodle_cookies: bool):
        """
        Use only for "external" shortcut/URL files.
        It tests whether a URL refers to a file, that is not an HTML web page then downloads it.
        Otherwise an attempt will be made to download it using yt-dlp.

        @param add_token: Adds the ws-token to the url
        @param delete_if_successful: Deletes the tmp file if download was successful
        @param needs_moodle_cookies: For this URL moodle cookies are required
        In case of an failure an exception will be raised
        """
        url_to_download = self.file.content_fileurl
        logging.debug('[%d] Try to download external file %s', self.task_id, url_to_download)

        if add_token:
            url_to_download = self.add_token_to_url(url_to_download)

        if delete_if_successful:
            # If temporary file is not needed delete it as soon as possible
            PT.remove_file(self.file.saved_to)

        if needs_moodle_cookies and self.opts.cookies_text is None:
            # Without Moodle cookies we should not continue
            # We assume that there are Moodle cookies in the cookie file, if one exists.
            # TODO: Perhaps explicitly check if Moodle cookies are set
            raise ValueError(
                'Moodle cookies are missing. Set a private token so that moodle-dl can obtain moodle cookies'
            )

        infos = await self.get_head_infos(url_to_download)
        if infos is None:
            # Head request failed (e.g., 404)
            # Raise exception so shortcut will be created as fallback
            raise ValueError('无法获取外部链接信息（可能返回 404 或其他 HTTP 错误）')

        external_dl_cmd = self.opts.external_file_downloaders.get(infos.host, "")
        if infos.is_html and external_dl_cmd != "":
            await self.download_using_external_downloader(
                dl_url=url_to_download,
                external_dl_cmd=external_dl_cmd,
                delete_if_successful=delete_if_successful,
            )
            return
        # For cookie_mod files (kalvidres, helixmedia, lti), always try yt-dlp
        # These are Moodle-integrated video platforms that need special handling
        if needs_moodle_cookies and infos.is_html and not self.is_blocked_for_yt_dlp(url_to_download):
            yt_dlp_processed = await self.download_using_yt_dlp(
                dl_url=url_to_download,
                infos=infos,
                delete_if_successful=delete_if_successful,
            )
            if yt_dlp_processed:
                return

        # 对于其他 HTML 页面（如 YouTube/Tumblr 等公开平台），不下载网页源码，只创建快捷方式
        if infos.is_html:
            logging.debug(
                '[%d] 检测到 HTML 页面（%s），跳过下载，将保留/重新创建快捷方式',
                self.task_id,
                urlparse.urlparse(url_to_download).hostname,
            )
            # 如果之前删除了快捷方式文件（delete_if_successful=True），需要重新创建
            # 如果没有删除（delete_if_successful=False），快捷方式文件已存在，无需操作
            # 由于我们不知道调用者是否删除了文件，统一重新创建快捷方式
            if delete_if_successful:
                # 快捷方式已被删除，需要重新创建
                await self.create_shortcut()
            # 正常返回，不抛出异常
            return

        logging.debug('[%d] Downloading URL directly', self.task_id)

        # Generate file name for external file
        # 走到这里说明不是 HTML 页面，而是实际文件（PDF、MP4、ZIP 等）
        new_name, new_extension = os.path.splitext(infos.guessed_file_name)
        if new_extension == '':
            # 无法识别文件扩展名，使用默认扩展名
            new_extension = '.bin'

        if self.file.content_type == 'description-url' and new_name != '':
            self.filename = new_name + new_extension

        _old_name, old_extension = os.path.splitext(self.filename)
        if old_extension != new_extension:
            self.filename = self.filename + new_extension

        self.set_path(True)

        await self.download_url(url_to_download, self.file.saved_to)

    def is_filtered_external_domain(self):
        """
        Filter external linked files.
        Check if the domain of the download link is on the blacklist or is not on the whitelist.

        @return: True if the domain is filtered.
        """
        # 🔧 Delegated to TaskUrlOps. The URL parsing (extracting
        # the hostname) stays here because it depends on the
        # task's file attribute; the whitelist/blacklist matching
        # lives in the helper.
        domain = urlparse.urlparse(self.file.content_fileurl).hostname
        return self._url_ops.is_filtered_domain(
            domain,
            blacklist=self.opts.download_domains_blacklist,
            whitelist=self.opts.download_domains_whitelist,
        )

    async def create_shortcut(self):
        "Create a Shortcut to a URL"
        logging.debug('[%d] Creating a shortcut', self.task_id)
        PT.remove_file(self.file.saved_to)
        for link_type, should_write in self.opts.write_links.items():
            if should_write:
                self.set_path(True, link_type)
                async with aiofiles.open(
                    self.file.saved_to, 'w+', encoding='utf-8', newline='\r\n' if link_type == 'url' else '\n'
                ) as shortcut:
                    template_vars = {'url': self.file.content_fileurl}
                    if link_type == 'desktop':
                        template_vars['filename'] = self.file.saved_to[: -(len(link_type) + 1)]
                    await shortcut.write(LINK_TEMPLATES[link_type] % template_vars)

    def set_path(self, ignore_attributes: bool = False, force_file_extension=None):
        """Set the path where a file should be created. The file type is used to set the needed file extension.
        An empty target file is created which may need to be cleaned up.

        @param ignore_attributes: If the file attributes should be ignored.
        """
        if self.file.content_type == 'description' and not ignore_attributes:
            # 🔧 避免双重.md扩展名
            filename = self.filename if self.filename.endswith('.md') else (self.filename + '.md')
            self.file.saved_to = str(Path(self.destination) / filename)

        elif self.file.content_type == 'html' and not ignore_attributes:
            # 🔧 避免双重.html扩展名
            filename = self.filename if self.filename.endswith('.html') else (self.filename + '.html')
            self.file.saved_to = str(Path(self.destination) / filename)

        elif force_file_extension is not None:
            self.file.saved_to = str(Path(self.destination) / (self.filename + f'.{force_file_extension}'))

        else:  # normal path
            self.file.saved_to = str(Path(self.destination) / self.filename)

        self.file.saved_to = self.create_target_file(self.file.saved_to)

    async def create_description(self):
        "Create a description file"
        logging.debug('[%d] Creating a description file', self.task_id)

        md_content = ''
        if self.file.text_content is not None:
            h2t_handler = html2text.HTML2Text()
            md_content = h2t_handler.handle(self.file.text_content).strip()
            # we could run html.unescape() over to_save, but this could destroy the md file

        if md_content == '':
            logging.debug('[%d] Remove target file because description file would be empty', self.task_id)
            PT.remove_file(self.file.saved_to)
            return

        async with aiofiles.open(self.file.saved_to, 'w+', encoding='utf-8') as md_file:
            await md_file.write(md_content)

    async def create_html_file(self):
        "Create a HTML file and optionally a Markdown version"
        logging.debug('[%d] Creating a html file', self.task_id)

        html_content = ''
        if self.file.html_content is not None:
            html_content = self.file.html_content

        if html_content == '':
            logging.debug('[%d] Remove target file because html file would be empty', self.task_id)
            PT.remove_file(self.file.saved_to)
            return

        # Save HTML version
        async with aiofiles.open(self.file.saved_to, 'w+', encoding='utf-8') as html_file:
            await html_file.write(html_content)

        # Also create a Markdown version for easier reading (especially for blocks)
        if self.file.module_modname.startswith('block_'):
            try:
                from html2text import HTML2Text
                h2t_handler = HTML2Text()
                h2t_handler.ignore_links = False
                h2t_handler.ignore_images = False
                h2t_handler.body_width = 0  # Don't wrap lines

                md_content = h2t_handler.handle(html_content).strip()

                if md_content:
                    md_path = self.file.saved_to.replace('.html', '.md')
                    async with aiofiles.open(md_path, 'w+', encoding='utf-8') as md_file:
                        await md_file.write(md_content)
                    logging.debug('[%d] Created Markdown version: %s', self.task_id, md_path)
            except Exception as e:
                logging.debug('[%d] Failed to create Markdown version: %s', self.task_id, e)
                # Continue even if Markdown conversion fails

    async def create_content_file(self):
        "Create a content file (e.g., metadata.json)"
        logging.debug('[%d] Creating a content file', self.task_id)

        content = ''
        if self.file.content is not None:
            content = self.file.content

        if content == '':
            logging.debug('[%d] Remove target file because content file would be empty', self.task_id)
            PT.remove_file(self.file.saved_to)
            return

        async with aiofiles.open(self.file.saved_to, 'w+', encoding='utf-8') as content_file:
            await content_file.write(content)

    async def _download_index_mod_page(self):
        """Download a legacy Moodle page as Markdown instead of a browser shortcut."""
        url_to_download = self.file.content_fileurl
        if not url_to_download:
            logging.warning('[%d] 没有可用的 Moodle 页面 URL，跳过文件: %s', self.task_id, self.file.content_filename)
            return

        url_to_download = self.add_token_to_url(url_to_download)

        PT.remove_file(self.file.saved_to)
        PT.make_dirs(self.destination)
        if self.filename.lower().endswith(('.html', '.htm')):
            self.filename = os.path.splitext(self.filename)[0]
        self.set_path(ignore_attributes=True, force_file_extension='md')

        ssl_context = SslHelper.get_ssl_context(
            self.opts.global_opts.skip_cert_verify,
            self.opts.global_opts.allow_insecure_ssl,
            self.opts.global_opts.use_all_ciphers,
        )

        async with aiohttp.ClientSession(cookie_jar=self.get_cookie_jar(), raise_for_status=True) as session:
            async with session.request(
                "GET",
                url_to_download,
                headers=self.RQ_HEADER,
                ssl=ssl_context,
                timeout=20,
            ) as resp:
                html_content = await resp.text()

        h2t_handler = html2text.HTML2Text()
        h2t_handler.ignore_links = False
        h2t_handler.ignore_images = False
        h2t_handler.body_width = 0
        md_content = h2t_handler.handle(html_content).strip()

        if md_content == '':
            logging.debug('[%d] Remove target file because Moodle page would be empty', self.task_id)
            PT.remove_file(self.file.saved_to)
            return

        async with aiofiles.open(self.file.saved_to, 'w+', encoding='utf-8') as md_file:
            await md_file.write(md_content)

    def move_old_file(self) -> bool:
        """
        Try to move the old file to the new location.
        @return: True if successful. Else the file needs to be re-downloaded.
        """

        if self.file.old_file is None:
            return False

        old_path = self.file.old_file.saved_to
        if not os.path.exists(old_path):
            return False

        logging.debug('[%d] Moving old file "%s" to new target location', self.task_id, old_path)
        try:
            # On Windows, the temporary file must be deleted first.
            PT.remove_file(self.file.saved_to)
            shutil.move(old_path, self.file.saved_to)
            return True
        except OSError as e:
            logging.warning('[%d] Moving the old file %s failed unexpectedly!  Error: %s', self.task_id, old_path, e)
        return False

    async def create_data_url_file(self):
        url_to_download = self.file.content_fileurl
        logging.debug('[%d] Creating a Data-URL file', self.task_id)

        # 🔒 安全验证：只允许 HTTP/HTTPS 协议
        parsed_url = urlparse.urlparse(url_to_download)
        if parsed_url.scheme not in ['http', 'https']:
            logging.warning('[%d] ❌ 不支持的 URL 协议: %s (只允许 http/https)', self.task_id, parsed_url.scheme)
            return False

        PT.remove_file(self.file.saved_to)
        self.set_path(True)
        with urllib.request.urlopen(url_to_download) as response:
            data = response.read()

        async with aiofiles.open(self.file.saved_to, "wb") as target_file:
            await target_file.write(data)

    @classmethod
    def _get_session_warmup_lock(cls) -> asyncio.Lock:
        """惰性创建跨任务共享的 asyncio.Lock。

        必须在 await 上下文里第一次访问；不能放到类定义里——那样会绑定到
        import 时（通常 None）的 event loop，并发任务上各自的 loop 看不到它。
        """
        if cls._session_warmup_lock is None:
            cls._session_warmup_lock = asyncio.Lock()
        return cls._session_warmup_lock

    async def _try_warmup_session(self, moodle_domain: str) -> bool:
        """主动 GET Moodle 主页，把 session 从'降级'状态拉回来。

        触发场景：kalvidres 请求被重定向到 enrol/index.php 或 login/index.php。
        Moodle 服务端 session 偶尔会进入这种状态——cookie 还在浏览器里、用户也
        真的注册了课程，但服务端这次请求里没认出来。一次便宜的 GET / 通常就
        能让后续请求恢复正常。

        @param moodle_domain: 形如 "keats.kcl.ac.uk"
        @return: True 表示这次调用真的发起了 warm-up（不保证 session 恢复了，
                 调用方应该接着重试原请求；返回 False 表示被节流跳过了）。
        """
        lock = self._get_session_warmup_lock()
        async with lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - Task._last_session_warmup_at
            if elapsed < self.SESSION_WARMUP_MIN_INTERVAL_S:
                logging.debug(
                    '[%d] Session warm-up 已在 %.0fs 前执行过，跳过（最小间隔 %ds）',
                    self.task_id, elapsed, self.SESSION_WARMUP_MIN_INTERVAL_S,
                )
                return False

            # 真正发起 warm-up GET。用 requests + 现有 cookie，跟随重定向；
            # 失败也吞掉——这只是个保底机制，真有问题让调用方的重试自己暴露。
            try:
                warmup_url = f'https://{moodle_domain}/'
                logging.info(
                    '[%d] 🔄 Session 似乎降级，访问 %s 尝试唤醒...',
                    self.task_id, warmup_url,
                )
                session = requests.Session()
                if self.opts.cookies_text is not None:
                    session.cookies = self._cookie_mgr.get_requests_jar()
                verify_ssl = not self.opts.global_opts.skip_cert_verify
                resp = await asyncio.to_thread(
                    session.get, warmup_url,
                    headers=self.RQ_HEADER, verify=verify_ssl,
                    timeout=self.REQUEST_TIMEOUT, allow_redirects=True,
                )
                # 注意：这里不判断结果是 my/ 还是 login/——成败让调用方的重试
                # 决定。我们只关心"warm-up 这个动作已经执行了"。
                logging.debug(
                    '[%d] Warm-up 完成 (status=%d, final_url=%s)',
                    self.task_id, resp.status_code, resp.url,
                )
            except Exception as e:
                logging.debug('[%d] Warm-up 请求失败: %s', self.task_id, e)
                # 故意吞——warm-up 失败不应阻塞重试

            Task._last_session_warmup_at = now
            return True

    async def extract_kalvidres_text(self, url: str, save_path: str) -> bool:
        """
        Extract text content from a kalvidres page and save as Markdown.
        Uses generic DOM-based extraction (not hardcoded keywords).

        @param url: The kalvidres page URL
        @param save_path: Path to save the extracted text
        @return: True if successful, False otherwise
        """
        try:
            import re
            import html as html_module
            import requests
            from urllib.parse import urlparse

            logging.debug('[%d] Extracting text from kalvidres URL: %s', self.task_id, url)

            verify_ssl = not self.opts.global_opts.skip_cert_verify
            original_domain = urlparse(url).netloc

            def _fetch():
                # 每次重试都用新 session，避免上一轮的状态污染
                sess = requests.Session()
                if self.opts.cookies_text is not None:
                    sess.cookies = self._cookie_mgr.get_requests_jar()
                return sess.get(url, headers=self.RQ_HEADER, verify=verify_ssl, timeout=30)

            response = _fetch()
            final_url = response.url
            final_domain = urlparse(final_url).netloc

            # session 降级检测：cookie 还在但服务端没认出来。enrol/login 重定向
            # 且仍在同一域名 → 试一次主页 warm-up，让 Moodle 服务端把 session
            # 状态拉回来，再重试一次。warm-up 自带 5 分钟节流。
            redirect_to_auth = (
                response.status_code == 200
                and ('login/index.php' in final_url or 'enrol/index.php' in final_url)
                and final_domain == original_domain
            )
            if redirect_to_auth:
                logging.info(
                    '[%d] Kalvidres 被重定向到 %s，尝试 session warm-up 后重试',
                    self.task_id, final_url,
                )
                warmed = await self._try_warmup_session(original_domain)
                if warmed:
                    response = _fetch()
                    final_url = response.url
                    final_domain = urlparse(final_url).netloc

            if response.status_code != 200:
                logging.warning('[%d] Failed to fetch kalvidres page: %d', self.task_id, response.status_code)
                return False

            logging.debug('[%d] Kalvidres page URL: %s', self.task_id, final_url)

            # 重试后仍在 enrol/login → cookie 真死了（或用户真没注册）
            if ('login/index.php' in final_url or 'enrol/index.php' in final_url) and final_domain == original_domain:
                logging.warning(
                    '[%d] Warm-up 后仍被重定向到 %s，cookies 可能已失效或用户未注册该课程',
                    self.task_id, final_url,
                )
                return False

            html_content = response.text

            # Extract text content using generic DOM-based method
            text_data = {}

            # 1. Extract page title
            title_match = re.search(r'<title>([^<]+)</title>', html_content)
            if title_match:
                text_data['page_title'] = html_module.unescape(title_match.group(1).strip())

            # 2. Extract module name (H1)
            h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL)
            if h1_match:
                h1_text = self._file_ops.clean_html_simple(h1_match.group(1))
                if h1_text:
                    text_data['module_name'] = h1_text

            # 3. Extract activity-description (core content - generic!)
            activity_pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
            activity_match = re.search(activity_pattern, html_content, re.DOTALL)
            if activity_match:
                content_html = activity_match.group(1)
                text_data['activity_description'] = self._file_ops.clean_html_preserve_structure(content_html)

            # Save as Markdown if we have content
            if text_data:
                await self._save_kalvidres_text(text_data, save_path)
                logging.info('[%d] Saved kalvidres text to: %s', self.task_id, save_path)
                return True
            else:
                logging.debug('[%d] No text content found in kalvidres page', self.task_id)
                return False

        except Exception as e:
            logging.warning('[%d] Failed to extract kalvidres text: %s', self.task_id, e)
            return False

    async def extract_kalvidres_video_url(self, url: str) -> Optional[str]:
        """
        从 kalvidres 页面提取 Kaltura 视频播放器 URL。
        
        处理流程:
        1. 获取 kalvidres 页面
        2. 提取 lti_launch URL
        3. 从 lti_launch.php 获取 browseandembed URL
        4. 从 browseandembed 页面提取 partner ID、entry ID、uiconf_id
        5. 尝试使用检测到的或备用 CDN 构建 Kaltura URL

        @param url: kalvidres 页面 URL
        @return: Kaltura 播放器 URL，失败返回 None
        """
        try:
            logging.debug('[%d] 🔍 开始提取 Kaltura 视频 URL: %s', self.task_id, url[:80] + '...')

            known_embed_url = self._build_kaltura_url_from_known_embed_url(url)
            if known_embed_url:
                logging.info('[%d] ✅ 从已知 Kaltura 嵌入 URL 构建视频 URL', self.task_id)
                return known_embed_url
            
            # 创建带重试机制的 session
            session = self._cookie_mgr.create_session()
            verify_ssl = not self.opts.global_opts.skip_cert_verify

            # ====== 阶段 1: 获取 kalvidres 页面 ======
            try:
                response = session.get(url, headers=self.RQ_HEADER, verify=verify_ssl, 
                                     timeout=self.REQUEST_TIMEOUT)
            except requests.Timeout:
                logging.error('[%d] ❌ 超时 (kalvidres 页面): 无法在 %d 秒内获取页面',
                            self.task_id, self.REQUEST_TIMEOUT)
                return None
            except requests.ConnectionError as e:
                logging.error('[%d] ❌ 连接错误: 无法连接到服务器', self.task_id)
                return None

            # 检查 HTTP 状态码
            if response.status_code == 403:
                raise KalturaAuthenticationError('Cookie 过期或权限不足 (HTTP 403)')
            elif response.status_code == 404:
                logging.error('[%d] ❌ HTTP 404: 页面未找到', self.task_id)
                return None
            elif response.status_code == 503:
                raise KalturaCDNError('服务器不可用 (HTTP 503)')
            elif response.status_code != 200:
                logging.error('[%d] ❌ HTTP %d: 无法获取 kalvidres 页面', 
                            self.task_id, response.status_code)
                return None

            html_content = response.text
            logging.debug('[%d] ✓ 成功获取 kalvidres 页面 (%d 字节)', 
                        self.task_id, len(html_content))

            # ====== 阶段 2: 提取 lti_launch 页面 URL ======
            iframe_match = self.REGEX_LTI_IFRAME.search(html_content)
            if not iframe_match:
                raise KalturaExtractionError('无法在 kalvidres 页面中找到 lti_launch iframe')

            lti_launch_url = iframe_match.group(1).replace('&amp;', '&')
            logging.debug('[%d] ✓ 找到 LTI 启动 URL', self.task_id)

            # ====== 阶段 3: 获取 lti_launch.php 页面 ======
            try:
                lti_response = session.get(lti_launch_url, headers=self.RQ_HEADER, 
                                         verify=verify_ssl, timeout=self.REQUEST_TIMEOUT)
            except requests.Timeout:
                logging.error('[%d] ❌ 超时 (LTI 启动): 无法在 %d 秒内获取页面',
                            self.task_id, self.REQUEST_TIMEOUT)
                return None

            if lti_response.status_code == 403:
                raise KalturaAuthenticationError('Cookie 过期或权限不足 (LTI 页面 HTTP 403)')
            elif lti_response.status_code == 503:
                raise KalturaCDNError('Kaltura 服务器不可用 (LTI 页面 HTTP 503)')
            elif lti_response.status_code != 200:
                logging.error('[%d] ❌ HTTP %d: 无法获取 lti_launch.php', 
                            self.task_id, lti_response.status_code)
                return None

            lti_html = lti_response.text
            logging.debug('[%d] ✓ 成功获取 LTI 启动页面', self.task_id)

            # ====== 阶段 4: 从 LTI 页面提取 browseandembed URL ======
            target_uri_match = self.REGEX_TARGET_LINK_URI.search(lti_html)
            if not target_uri_match:
                raise KalturaExtractionError('无法在 lti_launch 页面中找到 target_link_uri')

            browseandembed_url = html.unescape(target_uri_match.group(1))
            logging.debug('[%d] ✓ 找到 browseandembed URL', self.task_id)
            self._log_browseandembed_url(browseandembed_url)

            # ====== 阶段 5: 从 browseandembed URL 提取信息 ======
            try:
                entry_id = self._extract_entry_id(browseandembed_url)
                uiconf_id = self._extract_uiconf_id(browseandembed_url)
            except KalturaExtractionError as e:
                logging.error('[%d] ❌ 解析失败: %s', self.task_id, e)
                return None

            # ====== 阶段 6: 获取 browseandembed 页面 ======
            try:
                browseandembed_response = session.get(browseandembed_url, headers=self.RQ_HEADER,
                                                     verify=verify_ssl, timeout=self.REQUEST_TIMEOUT)
            except requests.Timeout:
                logging.error('[%d] ❌ 超时 (browseandembed): 无法在 %d 秒内获取页面',
                            self.task_id, self.REQUEST_TIMEOUT)
                return None

            if browseandembed_response.status_code == 403:
                raise KalturaAuthenticationError('Cookie 过期或无权限访问 (browseandembed 页面)')
            elif browseandembed_response.status_code == 503:
                raise KalturaCDNError('Kaltura CDN 不可用 (browseandembed 页面 HTTP 503)')
            elif browseandembed_response.status_code != 200:
                logging.error('[%d] ❌ HTTP %d: 无法获取 browseandembed 页面',
                            self.task_id, browseandembed_response.status_code)
                return None

            logging.debug('[%d] ✓ 成功获取 browseandembed 页面', self.task_id)

            # ====== 阶段 7: 提取 partner ID ======
            try:
                partner_id = self._extract_partner_id(browseandembed_response.text)
            except KalturaExtractionError as e:
                partner_id = self._infer_partner_id_from_browse_url(browseandembed_url)
                if not partner_id:
                    logging.error('[%d] ❌ 解析失败: %s', self.task_id, e)
                    return None

            # ====== 阶段 8: 检测或使用备用 CDN ======
            detected_cdn = self._detect_kaltura_cdn(browseandembed_response.text)
            cdns_to_try: List[str] = []
            if detected_cdn:
                cdns_to_try.append(detected_cdn)
            cdns_to_try.extend(self.KALTURA_CDN_FALLBACKS)

            # ====== 阶段 9: 构建 Kaltura URL 并返回 ======
            kaltura_url = self._build_kaltura_url(partner_id, uiconf_id, entry_id, cdns_to_try[0])
            logging.info('[%d] ✅ 成功提取 Kaltura 视频 URL (CDN: %s)', 
                       self.task_id, cdns_to_try[0])
            return kaltura_url

        except KalturaAuthenticationError as e:
            logging.error('[%d] ❌ 认证失败: %s', self.task_id, e)
            logging.error('[%d] 💡 建议: 运行 moodle-dl --refresh-cookies 刷新 Cookie', self.task_id)
            return None
        except KalturaCDNError as e:
            logging.error('[%d] ❌ CDN 错误: %s', self.task_id, e)
            logging.error('[%d] 💡 建议: CDN 服务器暂时不可用，请稍后重试', self.task_id)
            return None
        except KalturaExtractionError as e:
            logging.error('[%d] ❌ 提取失败: %s', self.task_id, e)
            return None
        except requests.RequestException as e:
            logging.error('[%d] ❌ 网络请求错误: %s', self.task_id, e)
            return None
        except Exception as e:
            error_msg = str(e)
            logging.error('[%d] ❌ 未知错误: %s', self.task_id, e)
            
            # 尝试诊断错误原因
            if 'cookie' in error_msg.lower() or 'auth' in error_msg.lower():
                logging.error('[%d] 💡 可能原因: Cookie 过期或认证失败', self.task_id)
            elif 'ssl' in error_msg.lower() or 'certificate' in error_msg.lower():
                logging.error('[%d] 💡 可能原因: SSL 证书问题', self.task_id)
            elif 'timeout' in error_msg.lower():
                logging.error('[%d] 💡 可能原因: 网络超时', self.task_id)
            
            logging.debug('[%d] 详细错误堆栈: %s', self.task_id, traceback.format_exc())
            return None

    async def _save_kalvidres_text(self, text_data: dict, save_path: str):
        """Save extracted text as Markdown file"""
        lines = []

        if text_data.get('page_title'):
            lines.append(f"# {text_data['page_title']}")
            lines.append("")

        if text_data.get('module_name'):
            lines.append(f"## {text_data['module_name']}")
            lines.append("")

        if text_data.get('activity_description'):
            lines.append(text_data['activity_description'])
            lines.append("")

        content = '\n'.join(lines)

        # Create directory if needed
        ensure_parent_dir(save_path)

        async with aiofiles.open(save_path, 'w', encoding='utf-8') as f:
            await f.write(content)

    async def run(self):
        if self.status.state != TaskState.INIT:
            logging.debug('[%d] Task was already started', self.task_id)
            return
        self.status.state = TaskState.STARTED

        success = await self.real_run()

        if success:
            self.set_utime()
            self.file.time_stamp = int(time.time())

    def _is_metadata_file(self) -> bool:
        """
        Check if this file is a metadata file that can be optionally skipped
        
        Metadata files include:
        - JSON files from Resource module (corresponding to PDFs/videos)
        - _metadata.json files
        - _info files
        - _notes.md files
        """
        return is_optional_metadata_filename(self.file.content_filename)

    def may_perform_network_io(self) -> bool:
        """Return whether this task may open a network connection during download."""
        if self._is_metadata_file() and not self.opts.download_metadata_files:
            return False

        if self.file.content_type in ('description', 'html', 'content', 'directory_placeholder'):
            return False

        if self.file.content_fileurl and self.file.content_fileurl.startswith('data:'):
            return False

        if self.file.content_type == 'leganto_pdf':
            return True

        if self.file.module_modname.startswith('index_mod'):
            return True

        if self.file.module_modname.startswith('cookie_mod'):
            return True

        if self.file.module_modname.startswith('url'):
            if not self.file.content_fileurl:
                return False
            if is_leganto_reading_list_url(self.file.content_fileurl):
                return True
            return self.opts.download_linked_files and not self.is_filtered_external_domain()

        return bool(self.file.content_fileurl)

    def _is_index_mod_page_file(self) -> bool:
        """Return whether an index_mod entry is the page HTML, not an embedded asset."""
        if (
            not self.file.module_modname.startswith('index_mod')
            and getattr(self.file, 'module_name', '') != 'index_mod-page'
        ):
            return False

        content_type = (getattr(self.file, 'content_type', '') or '').lower()
        if content_type in ('html', 'description'):
            return True

        filename = (getattr(self.file, 'content_filename', '') or '').lower()
        if filename.endswith(('.html', '.htm')):
            return True

        file_url = getattr(self.file, 'content_fileurl', '') or ''
        file_url_path = urlparse.urlsplit(file_url).path.lower()
        if file_url_path.endswith(('.html', '.htm')):
            return True

        return False

    async def real_run(self) -> bool:
        """
        主下载流程的原子化编排器。

        职责：协调下载流程的 4 个阶段
        1. 元数据文件检查（可跳过）
        2. 文件准备（目录/重命名/移动）
        3. 类型特定的下载执行
        4. 错误处理和清理

        每个阶段都由独立的原子函数处理，便于测试和复用。
        """
        try:
            logging.debug('[%d] Starting Task: %s', self.task_id, self)

            # 🔧 修复和验证 pluginfile URL（双重保障）
            # 虽然 result_builder 已经修复过，但这里作为下载前的最后防线
            if self.file.content_fileurl and 'pluginfile.php' in self.file.content_fileurl:
                try:
                    original_url = self.file.content_fileurl
                    fixed_url = UrlHelper.fix_pluginfile_url(
                        self.file.content_fileurl,
                        token=self.opts.token,
                        moodle_base_url=self.opts.moodle_url
                    )
                    if original_url != fixed_url:
                        logging.info(f'[{self.task_id}] 🔧 Fixed URL before download: {original_url[:80]}...')
                        logging.debug(f'[{self.task_id}]    Fixed to: {fixed_url[:80]}...')
                        self.file.content_fileurl = fixed_url
                except Exception as e:
                    logging.warning(f'[{self.task_id}] ⚠️ URL fix attempt failed: {e}')

            # 阶段 1: 检查元数据文件
            if await self._handle_metadata_file():
                return True

            # 目录占位符：只需要确保目录存在，无需下载文件
            if self.file.content_type == 'directory_placeholder':
                await self._handle_directory_placeholder()
                return True

            # 阶段 2: 准备下载环境
            if not await self._prepare_download():
                return False
            
            # 阶段 3: 执行实际下载
            await self._execute_download()
            
            logging.debug('[%d] Download finished', self.task_id)
            self.report_success()
            return True
            
        except Exception as dl_err:
            # 阶段 4: 错误处理
            await self._handle_error(dl_err)
            return False

    async def _handle_directory_placeholder(self):
        """
        处理目录占位符：仅创建对应的章节文件夹，不生成任何文件。
        """
        PT.make_dirs(self.destination)
        self.file.saved_to = self.destination
        logging.debug('[%d] Created empty chapter folder placeholder at %s', self.task_id, self.destination)
        self.report_success()
    
    async def _handle_metadata_file(self) -> bool:
        """
        检查并处理元数据文件的跳过逻辑。
        
        返回: True 如果文件被跳过，False 继续处理
        """
        if self._is_metadata_file() and not self.opts.download_metadata_files:
            logging.debug(
                '[%d] Skipping metadata file (download_metadata_files is disabled): %s',
                self.task_id,
                self.file.content_filename
            )
            self.status.total_size = 0
            self.status.bytes_downloaded = 0
            self.report_success()
            return True  # 已处理，停止进一步处理
        return False  # 继续处理
    
    async def _prepare_download(self) -> bool:
        """
        准备下载环境：创建目录、处理已修改文件、检查移动状态。
        
        返回: True 如果准备成功，False 如果需要停止
        """
        PT.make_dirs(self.destination)
        self._saved_to_before_prepare = self.file.saved_to

        # 如果文件已修改，重命名旧文件
        if self.file.modified:
            self.rename_old_file()

        # 创建空的目标文件
        self.set_path()
        logging.debug('[%d] Starting downloading of: %s', self.task_id, self.file.saved_to)

        # 尝试移动旧文件（如果存在）
        if self.file.moved:
            if self.move_old_file():
                return False  # 文件已移动，停止处理
        
        return True
    
    async def _execute_download(self):
        """
        根据文件类型执行不同的下载策略。
        
        支持的类型：
        - description: 从描述创建文件
        - html: 从 HTML 内容创建文件
        - content: 元数据文件
        - index_mod: Moodle 索引模块
        - cookie_mod: 需要 cookies 的模块（视频等）
        - url: 外部链接（带快捷方式备选）
        - data: 数据 URL
        - 默认: 常规 HTTP 下载
        """
        if self._is_index_mod_page_file():
            await self._download_index_mod_page()

        elif self.file.content_type == 'description':
            await self.create_description()
        
        elif self.file.content_type == 'html':
            await self.create_html_file()
        
        elif self.file.content_type == 'content':
            await self.create_content_file()

        elif self.file.content_type == 'leganto_pdf':
            await self._download_leganto_reading_list_pdf()
        
        elif self.file.module_modname.startswith('cookie_mod'):
            await self._download_cookie_mod_file()
        
        elif self.file.module_modname.startswith('url') and (not self.file.content_fileurl or not self.file.content_fileurl.startswith('data:')):
            await self._download_external_url_with_fallback()

        elif self.file.content_fileurl and self.file.content_fileurl.startswith('data:'):
            await self.create_data_url_file()

        else:
            # 常规 HTTP 下载
            if self.file.content_fileurl:
                url_to_download = self.add_token_to_url(self.file.content_fileurl)
                logging.debug('[%d] Downloading %s', self.task_id, url_to_download)
                await self.download_url(url_to_download, self.file.saved_to)
            else:
                # 没有 URL，跳过下载
                logging.warning('[%d] 没有可用的下载 URL，跳过文件: %s', self.task_id, self.file.content_filename)
                self.status.set_error('No URL available for download')
    
    async def _download_cookie_mod_file(self):
        """
        处理需要 cookies 的模块文件（如 Kaltura 视频）。
        
        对于 kalvidres：先提取文本内容和视频 URL，然后下载视频。
        """
        if self.file.module_modname == MODULE_COOKIE_KALVIDRES:
            await self._handle_kalvidres_download()
        else:
            # 其他 cookie_mod 类型的视频
            await self.external_download_url(add_token=False, delete_if_successful=True, needs_moodle_cookies=True)
    
    async def _handle_kalvidres_download(self):
        """
        处理 Kalvidres（Kaltura 视频）的特殊下载流程。
        
        步骤：
        1. 提取页面文本内容
        2. 提取真实的 Kaltura 视频 URL
        3. 使用提取的 URL 下载，如失败则回退到原始 URL
        """
        video_path = str(self.file.saved_to)
        text_path = os.path.splitext(video_path)[0] + '_notes.md'

        kaltura_url = self._build_kaltura_url_from_known_embed_url(self.file.content_fileurl)
        if kaltura_url:
            logging.info(
                '[%d] Direct Kaltura embed URL detected; skipping text extraction',
                self.task_id,
            )
        else:
            # 提取文本内容
            logging.info('[%d] Extracting kalvidres text content...', self.task_id)
            await self.extract_kalvidres_text(self.file.content_fileurl, text_path)

            # 提取 Kaltura 视频 URL
            logging.info('[%d] Extracting Kaltura video URL for yt-dlp...', self.task_id)
            kaltura_url = await self.extract_kalvidres_video_url(self.file.content_fileurl)

        if kaltura_url:
            # 使用提取的 URL 下载
            original_url = self.file.content_fileurl
            self.file.content_fileurl = kaltura_url
            logging.info('[%d] Using extracted Kaltura URL: %s', self.task_id, kaltura_url)
            
            try:
                await self.external_download_url(add_token=False, delete_if_successful=True, needs_moodle_cookies=True)
            finally:
                # 恢复原始 URL
                self.file.content_fileurl = original_url
        else:
            # 回退到原始 URL
            logging.warning('[%d] Failed to extract Kaltura URL, trying original URL', self.task_id)
            await self.external_download_url(add_token=False, delete_if_successful=True, needs_moodle_cookies=True)
    
    async def _download_external_url_with_fallback(self):
        """
        下载外部链接，如果下载失败则创建快捷方式作为备选。
        
        流程：
        1. 检查是否应该下载外部链接
        2. 尝试下载
        3. 如果失败或不满足条件，创建快捷方式
        """
        download_success = False

        if is_leganto_reading_list_url(self.file.content_fileurl):
            await self._download_leganto_reading_list_pdf()
            logging.debug('[%d] Leganto reading list saved as PDF', self.task_id)
            return
        
        if self.opts.download_linked_files and not self.is_filtered_external_domain():
            try:
                await self.external_download_url(
                    add_token=False, delete_if_successful=True, needs_moodle_cookies=False
                )
                download_success = True
                logging.debug('[%d] 外部链接下载成功，跳过快捷方式创建', self.task_id)
            except Exception as e:
                logging.debug('[%d] 外部链接下载失败：%r，将创建快捷方式作为备选', self.task_id, e)
                download_success = False

        # 如果下载失败或不满足条件，创建快捷方式
        if not download_success:
            await self.create_shortcut()

    async def _download_leganto_reading_list_pdf(self):
        """Save a Leganto reading list through the page's Print list action.

        三级 fallback 链（任一成功即返回）：
          1. stored_lti  —— 用 Moodle 返回的 LTI id_token 直接 POST 到 Leganto
          2. moodle_lti  —— 从 Moodle 的 mod/lti/view.php 重新发起 launch
          3. course_url  —— 打开课程主页，找 Reading List 链接点过去

        实测三级最稳：当 Moodle 的 LTI launch 也跳回主页时（如 session 半失效），
        从课程页点链接通常能拿到一份新鲜的 launch context。

        遇到 LegantoPermanentFailureError（Reading List 已被学校删除等）会立即
        短路——后面的 fallback 也救不回，重试只会浪费 wall-clock budget。
        """
        plan = build_leganto_download_plan(
            content_type=self.file.content_type,
            file_url=self.file.content_fileurl,
            file_content=self.file.content,
            moodle_url=self.opts.moodle_url,
            course_id=getattr(self.course, 'id', None),
            module_id=getattr(self.file, 'module_id', None),
        )
        if plan.parse_error:
            logging.debug('[%d] Could not parse Leganto launch payload: %s', self.task_id, plan.parse_error)

        if plan.token_expired:
            logging.info(
                '[%d] Leganto LTI launch token has expired; refreshing from Moodle module',
                self.task_id,
            )

        if not plan.has_launch_data():
            raise RuntimeError('Leganto launch data is unavailable')

        self._prepare_leganto_pdf_target()
        # 默认有头：Leganto 偶尔会跳 SSO，需要让用户能看到登录界面；
        # 与 mid-download cookie refresh 行为一致。
        # MOODLE_DL_HEADLESS=1 可强制无头（适合 CI/无人值守）。
        from moodle_dl.cli.authenticators import _should_use_headless_sso
        printer = LegantoPdfPrinter(
            self.opts.cookies_text,
            skip_cert_verify=self.opts.global_opts.skip_cert_verify,
            headless=_should_use_headless_sso(),
        )

        # 按优先级排列要尝试的 fallback 阶段。每阶段都是一组 print_to_pdf 参数。
        attempts = []
        if plan.launch_parameters is not None:
            attempts.append((
                'stored LTI launch',
                {
                    'url': plan.target_url(),
                    **plan.print_kwargs(),
                },
            ))
        if plan.moodle_launch_url:
            attempts.append((
                'Moodle LTI launch',
                {
                    'url': plan.moodle_launch_url,
                    'launch_parameters': None,
                    'moodle_launch_url': plan.moodle_launch_url,
                    'course_url': None,
                },
            ))
        if plan.course_url:
            attempts.append((
                'Moodle course page',
                {
                    'url': plan.course_url,
                    'launch_parameters': None,
                    'moodle_launch_url': None,
                    'course_url': plan.course_url,
                },
            ))

        last_exc: Optional[BaseException] = None
        for stage, kwargs in attempts:
            url = kwargs.pop('url')
            try:
                await printer.print_to_pdf(url, self.file.saved_to, **kwargs)
                return
            except LegantoPermanentFailureError:
                # 不可恢复——传播给 status_callback，最终标记为 permanent，不再
                # 进入 --retry-failed 队列。
                raise
            except RuntimeError as exc:
                last_exc = exc
                logging.warning(
                    '[%d] Leganto fallback "%s" failed (%s); trying next stage',
                    self.task_id, stage, exc,
                )

        # 所有 fallback 都失败——抛最后一次的真错而不是新错，方便诊断。
        if last_exc is not None:
            raise last_exc
        raise RuntimeError('Leganto reading list download had no usable fallback')

    def _leganto_lti_launch_token_expiry(self, launch_parameters) -> Optional[int]:
        """Return the expiry timestamp of a Leganto LTI id_token, if readable."""
        return leganto_lti_launch_token_expiry(launch_parameters)

    def _leganto_course_url(self) -> Optional[str]:
        """Return the Moodle course page used to launch Leganto with course context."""
        return leganto_course_url(
            self.file.content_type,
            self.file.content_fileurl,
            self.opts.moodle_url,
            getattr(self.course, 'id', None),
        )

    def _leganto_moodle_launch_url(self) -> Optional[str]:
        """Return the Moodle LTI module URL used to refresh a Leganto launch."""
        return leganto_moodle_launch_url(
            self.file.content_type,
            self.opts.moodle_url,
            getattr(self.file, 'module_id', None),
        )

    def _prepare_leganto_pdf_target(self):
        """Ensure the current task writes to a PDF file instead of a shortcut-like filename."""
        previous_saved_to = getattr(self, '_saved_to_before_prepare', None)

        source_name = self.file.module_name or self.file.content_filename or 'Reading List'
        if self.file.content_filename and not self.file.content_filename.lower().startswith(('http://', 'https://')):
            source_name = self.file.content_filename

        source_name = PT.to_valid_name(source_name, is_file=True)
        base, extension = os.path.splitext(source_name)
        self.filename = source_name if extension.lower() == '.pdf' else f'{base}.pdf'

        if self.file.saved_to:
            PT.remove_file(self.file.saved_to)
        self._remove_leganto_shortcut_fallbacks(previous_saved_to)
        self.set_path(True)
        self._remove_leganto_shortcut_fallbacks()

    def _remove_leganto_shortcut_fallbacks(self, target_path: str = None):
        """Remove shortcut files left by older Leganto fallback behavior."""
        target_path = target_path or self.file.saved_to
        if not target_path:
            return
        base_path, extension = os.path.splitext(target_path)
        if extension.lower() not in ('.pdf', '.url', '.webloc', '.desktop'):
            return
        for link_extension in ('.url', '.webloc', '.desktop'):
            self._remove_path_and_appledouble(base_path + link_extension)

    @staticmethod
    def _remove_path_and_appledouble(path: str) -> None:
        PT.remove_file(path)
        try:
            path_obj = Path(path)
            PT.remove_file(str(path_obj.with_name(f'._{path_obj.name}')))
        except (OSError, ValueError):
            pass
    
    async def _handle_error(self, dl_err: Exception):
        """
        统一的错误处理和清理。
        
        处理：
        1. 记录错误和追踪信息
        2. 分析文件状态
        3. 清理失败的文件和统计
        4. 报告失败
        """
        self.status.error = dl_err
        logging.error('[%d] %r', self.task_id, dl_err)
        logging.error('[%d] 尝试下载文件时出错：%s', self.task_id, dl_err)

        # 分析文件状态
        if os.path.isfile(self.file.saved_to):
            file_size = 0
            try:
                file_size = os.path.getsize(self.file.saved_to)
            except OSError:
                pass
            logging.debug(
                '[%d] file size: %d; downloaded: %d',
                self.task_id,
                file_size,
                self.status.bytes_downloaded,
            )

        logging.debug('[%d] Traceback:\n%s', self.task_id, traceback.format_exc())

        # 清理失败的文件
        # 注意: download_url 中已经实现了智能的文件保留逻辑（断点续传）
        # 🔧 Part-file resume: also remove the .part file
        safe_remove_part_and_final(
            self.file.saved_to or '',
            pt_remove_file=PT.remove_file,
        )
        self.report_received_bytes(-self.status.bytes_downloaded)
        self.report_failure()

    def get_cookie_jar(self) -> aiohttp.CookieJar:
        if self.opts.cookies_text is not None:
            cached_aiohttp_cookie_jar = getattr(self.opts, '_moodle_dl_aiohttp_cookie_jar_cache', None)
            if cached_aiohttp_cookie_jar is None:
                cached_aiohttp_cookie_jar = convert_to_aiohttp_cookie_jar(self._cookie_mgr.get_mozilla_jar())
                setattr(self.opts, '_moodle_dl_aiohttp_cookie_jar_cache', cached_aiohttp_cookie_jar)
            return clone_aiohttp_cookie_jar(cached_aiohttp_cookie_jar)
        return None

    async def check_range_download_opt(self, url, session):
        try:
            headers = self.RQ_HEADER.copy()
            headers['Range'] = 'bytes=0-4'
            resp = await session.request("GET", url, headers=headers)
            return resp.headers.get('Content-Range') is not None and resp.status == 206
        except Exception as err:
            logging.debug("Failed to check if download can be continued on fail: %s", err)
        return False

    def report_success(self):
        self.status.state = TaskState.FINISHED
        self.callback(DlEvent.FINISHED, self)

    def report_failure(self):
        self.status.state = TaskState.FAILED
        self.callback(DlEvent.FAILED, self)

    def report_received_bytes(self, bytes_received: int):
        self.status.bytes_downloaded += bytes_received
        self.callback(DlEvent.RECEIVED, self, bytes_received=bytes_received)

    def report_content_length(self, content_length: int, save_in_status: bool = True):
        if content_length is not None and content_length != 0:
            if self.file.content_filesize is None or self.file.content_filesize <= 0:
                if save_in_status:
                    self.status.external_total_size = content_length
                self.callback(DlEvent.TOTAL_SIZE, self, content_length=content_length)

    async def _perform_download_request(
        self,
        session: aiohttp.ClientSession,
        dl_url: str,
        dest_path: str,
        headers: dict,
        ssl_context,
        timeout: int,
        file_obj,
        total_bytes_received: int,
        disable_compression: bool = False,
    ):
        """
        执行单个下载请求，并处理编码问题的智能降级
        
        Args:
            session: aiohttp ClientSession
            dl_url: 下载 URL
            dest_path: 目标文件路径
            headers: 请求头
            ssl_context: SSL 上下文
            timeout: 超时时间
            file_obj: 文件对象
            total_bytes_received: 已接收字节数
            disable_compression: 是否禁用压缩（智能降级标记）
        
        Returns:
            (file_obj, total_bytes_received, content_length, content_range)
        """
        # 如果需要禁用压缩，添加相应的请求头
        req_headers = headers.copy()
        if disable_compression:
            req_headers['Accept-Encoding'] = 'identity'
            logging.debug(
                '[%d] 禁用压缩重试：已设置 Accept-Encoding: identity',
                self.task_id,
            )
        
        async with session.request(
            "GET", dl_url, headers=req_headers, ssl=ssl_context, timeout=timeout
        ) as resp:
            content_length = int(resp.headers.get("Content-Length", 0))
            self.report_content_length(content_length)
            content_range = resp.headers.get("Content-Range")  # Exp: bytes 200-1000/67589

            if resp.status not in [200, 206]:
                logging.debug('[%d] Warning got status %s', self.task_id, resp.status)

            # 使用传入的 dest_path 参数而不是 self.destination
            # 🔧 Part-file resume: 用 .part 后缀写, 完成时 atomically rename
            part_path = dest_path_to_part_path(dest_path)
            file_obj = file_obj or await aiofiles.open(part_path, "wb")
            # 🔧 Ctrl-C resilience: track the open file handle so the
            # OUTER download_url's finally block can close it on kill.
            self._open_file_handle = file_obj
            
            try:
                async for chunk in resp.content.iter_chunked(self.CHUNK_SIZE):
                    bytes_received = len(chunk)
                    total_bytes_received += bytes_received
                    self.report_received_bytes(bytes_received)
                    await file_obj.write(chunk)
            except aiohttp.ClientPayloadError as payload_err:
                # 检查是否是编码相关的错误（如 gzip）
                if 'gzip' in str(payload_err) or 'content-encoding' in str(payload_err).lower():
                    if not disable_compression:
                        # 标记需要禁用压缩重试
                        logging.warning(
                            '[%d] 检测到编码错误，将禁用压缩重试：%s',
                            self.task_id,
                            payload_err,
                        )
                        raise ValueError('需要禁用压缩重试')
                # 重新抛出非编码相关的错误
                raise

            return file_obj, total_bytes_received, content_length, content_range

    async def download_url(self, dl_url: str, dest_path: str, timeout: int = None):
        # 🔧 Ctrl-C / kill resilience:
        # Wrap the entire download in a try/except that catches BOTH
        # asyncio.CancelledError (raised when the event loop is being
        # torn down, e.g. by signal handlers) AND BaseException
        # (KeyboardInterrupt at the top of the loop). On cancellation
        # we MUST save the partial .part file to disk + record an
        # incomplete_downloads row so the next run can resume.
        self._open_file_handle = None  # 🔧 tracked for finally cleanup
        try:
            return await self._download_url_impl(dl_url, dest_path, timeout)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            # Reached on Ctrl-C / SIGTERM / task.cancel(). The file
            # is at .part, partial. Save the resume record and re-raise.
            await self._save_incomplete_on_kill(dl_url, dest_path)
            raise
        finally:
            # 🔧 Ctrl-C resilience: ensure open file handle is closed
            # so the .part on disk is fully flushed before the
            # exception propagates. Without this, partial writes may
            # be lost when the event loop tears down.
            fh = getattr(self, '_open_file_handle', None)
            if fh is not None and not getattr(fh, 'closed', True):
                self._open_file_handle = None
                try:
                    await fh.close()
                except Exception:
                    pass

    async def _save_incomplete_on_kill(self, dl_url: str, dest_path: str):
        """Best-effort: record the .part file in incomplete_downloads.

        Called from download_url's except block when the download was
        killed (Ctrl-C, SIGTERM, asyncio cancellation, OOM kill -9).
        The .part file is left on disk; this method records its size
        so the next run can resume from byte N.

        Must be tolerant of secondary failures: if the DB is also
        locked, we still want to keep the .part on disk for the
        scan_for_orphan_part_files() sweep on next startup.
        """
        try:
            part_path = dest_path_to_part_path(dest_path)
            if not os.path.exists(part_path):
                # Nothing on disk to save
                return
            part_size = os.path.getsize(part_path)
            if part_size == 0:
                # Empty .part file: nothing downloaded, don't record
                return
            self._save_incomplete_download(
                part_path, dl_url, part_size, 0  # 0 = unknown total
            )
            logging.warning(
                '[%d] 🔧 Kill detected — saved %s bytes to %s for resume',
                self.task_id, part_size, part_path,
            )
        except Exception as save_err:
            # Don't let a save failure mask the original cancellation
            logging.error(
                '[%d] Failed to save incomplete record on kill: %s',
                self.task_id, save_err,
            )

    async def _download_url_impl(self, dl_url: str, dest_path: str, timeout: int = None):
        # 🔧 Ctrl-C resilience: if this function is interrupted by
        # asyncio.CancelledError (from task.cancel() / signal handler),
        # we want to leave the .part file on disk for resume, NOT
        # delete it. We do NOT need to add a try/except here — the
        # OUTER `download_url` already catches CancelledError and
        # calls _save_incomplete_on_kill.
        total_bytes_received = 0
        content_length = 0
        done_tries = 0
        can_continue_on_fail = False
        file_obj = None
        disable_compression = False
        headers = self.RQ_HEADER.copy()
        ssl_context = SslHelper.get_ssl_context(
            self.opts.global_opts.skip_cert_verify,
            self.opts.global_opts.allow_insecure_ssl,
            self.opts.global_opts.use_all_ciphers,
        )

        # 🆕 尝试恢复之前中断的下载
        resume_attempted = False

        with Timer() as watch:
            async with aiohttp.ClientSession(cookie_jar=self.get_cookie_jar(), raise_for_status=True) as session:
                # 尝试恢复未完成的下载（仅在第一次尝试时）
                # 🔧 Part-file resume: the part file lives at .part suffix;
                # the DB row recorded dest_path.part. Pass the part
                # path explicitly to avoid the size mismatch.
                if not resume_attempted:
                    part_path = dest_path_to_part_path(dest_path)
                    if os.path.exists(part_path):
                        resume_attempted = True
                        try:
                            resumed_bytes, resumed_file_obj = await self._resume_incomplete_download(
                                part_path, dl_url, session, headers, ssl_context
                            )
                            if resumed_file_obj is not None and resumed_bytes > 0:
                                total_bytes_received = resumed_bytes
                                file_obj = resumed_file_obj
                                can_continue_on_fail = True
                                headers['Range'] = f'bytes={total_bytes_received}-'
                        except Exception as resume_err:
                            logging.debug('[%d] 尝试恢复下载时出错: %s', self.task_id, resume_err)

                while done_tries < self.MAX_DL_RETRIES:
                    try:
                        if done_tries > 0:
                            logging.debug(
                                '[%d] Start downloading (Try %d of %d)',
                                self.task_id,
                                done_tries + 1,
                                self.MAX_DL_RETRIES,
                            )

                        if done_tries > 0 and can_continue_on_fail:
                            headers['Range'] = f'bytes={total_bytes_received}-'
                        elif not can_continue_on_fail and 'Range' in headers:
                            del headers['Range']

                        try:
                            file_obj, total_bytes_received, content_length, content_range = await self._perform_download_request(
                                session,
                                dl_url,
                                dest_path,
                                headers,
                                ssl_context,
                                timeout,
                                file_obj,
                                total_bytes_received,
                                disable_compression=disable_compression,
                            )
                        except ValueError as val_err:
                            # 这是编码错误的标记，需要禁用压缩重试
                            if str(val_err) == '需要禁用压缩重试':
                                if not disable_compression:
                                    disable_compression = True
                                    # 重置状态重新尝试
                                    if file_obj is not None and not file_obj.closed:
                                        await file_obj.close()
                                    file_obj = None
                                    PT.remove_file(dest_path)
                                    self.report_received_bytes(-total_bytes_received)
                                    total_bytes_received = 0
                                    # 继续下一次尝试（不增加 done_tries）
                                    continue
                            raise

                        if done_tries > 0 and can_continue_on_fail and not content_range and content_length != 206:
                            raise ContentRangeError(
                                f"[{self.task_id}] Server did not response with requested range data"
                            )

                        if file_obj is not None and not file_obj.closed:
                            await file_obj.close()

                        # 🔧 Part-file resume: atomic rename .part → final
                        part_path = dest_path_to_part_path(dest_path)
                        if part_path != dest_path and os.path.exists(part_path):
                            try:
                                os.replace(part_path, dest_path)
                                logging.debug(
                                    '[%d] Atomically renamed %s → %s',
                                    self.task_id, part_path, dest_path,
                                )
                            except OSError as rename_err:
                                logging.warning(
                                    '[%d] Failed to rename %s to %s: %s',
                                    self.task_id, part_path, dest_path, rename_err,
                                )

                        if content_length >= 0 and total_bytes_received < content_length:
                            raise ContentTooShortError(
                                f'[{self.task_id}] Download incomplete: Got only {format_bytes(total_bytes_received)}'
                                + f' out of {format_bytes(content_length)} bytes',
                                dest_path,
                            )

                        logging.debug('[%d] Successfully downloaded %s', self.task_id, dest_path)
                        
                        # 🆕 清理完成的下载记录
                        try:
                            if self.file.file_id is not None:
                                database = self._get_or_create_database()
                                database.mark_download_complete(self.file.file_id, dest_path)
                        except Exception as cleanup_err:
                            logging.debug('[%d] 清理下载记录时出错: %s', self.task_id, cleanup_err)

                        break

                    except (aiohttp.ClientError, OSError, ValueError, ContentRangeError) as err:
                        if done_tries == 0:
                            can_continue_on_fail = await self.check_range_download_opt(dl_url, session)

                        done_tries += 1
                        if (
                            (not can_continue_on_fail and total_bytes_received > 0)
                            or isinstance(err, ContentRangeError)
                            or (done_tries >= self.MAX_DL_RETRIES)
                        ):
                            should_save_incomplete = _should_save_incomplete(
                                can_continue_on_fail,
                                total_bytes_received,
                                content_length or 0,
                                err,
                            )
                            can_continue_on_fail = False
                            # Clean up failed file because we can not recover
                            if file_obj is not None and not file_obj.closed:
                                await file_obj.close()
                            file_obj = None

                            # ✅ 断点续传改进：保留未完成的文件，记录到数据库
                            # If download can be continued and size > 0,
                            # remember that the file started downloading, and continue downloading
                            # on next run instead of deleting it.
                            if should_save_incomplete:
                                # 저장到数据库用于下次续传
                                # 🔧 Part-file resume: record the .part path so
                                # the next run can find and resume it
                                part_path = dest_path_to_part_path(dest_path)
                                try:
                                    self._save_incomplete_download(
                                        part_path, dl_url, total_bytes_received, content_length or 0
                                    )
                                    logging.warning(
                                        '[%d] 下载中断，已保存 %s 字节到 %s，将在下次重试时继续下载',
                                        self.task_id, 
                                        format_bytes(total_bytes_received),
                                        part_path,
                                    )
                                except Exception as save_err:
                                    logging.error('[%d] 저장中断下载记录失败: %s', self.task_id, save_err)
                                    # 如果保存失败，删除文件（回退到原来的行为）
                                    PT.remove_file(part_path)
                                    self.report_received_bytes(-total_bytes_received)
                                    total_bytes_received = 0
                            else:
                                # 如果无法续传或没有部分下载，删除文件
                                # 🔧 Part-file resume: delete the .part file, not
                                # the final path (the final path may not exist
                                # if we were writing to .part the whole time)
                                safe_remove_part_and_final(
                                    dest_path,
                                    pt_remove_file=PT.remove_file,
                                )
                                self.report_received_bytes(-total_bytes_received)
                                total_bytes_received = 0

                        if isinstance(err, aiohttp.ClientResponseError):
                            if err.status not in [408, 409, 429]:  # pylint: disable=no-member
                                # 408 (timeout) or 409 (conflict) and 429 (too many requests)
                                logging.warning(
                                    '[%d] Download failed with status: %s %s', self.task_id, err.status, err.message
                                )
                                raise err from None

                        if done_tries < self.MAX_DL_RETRIES:
                            logging.debug('[%d] Download error occurred: %s', self.task_id, err)
                            await asyncio.sleep(1)
                            continue

                        # No more tries
                        raise err from None
        logging.debug(
            '[%d] Download of %s finished in %s',
            self.task_id,
            format_bytes(total_bytes_received),
            format_seconds(watch.duration),
        )

    def _save_incomplete_download(self, file_path: str, file_url: str,
                                   downloaded_bytes: int, total_bytes: int):
        """
        저장未完成的下载信息到数据库，用于断点续传

        @param file_path: 文件保存路径
        @param file_url: 文件 URL
        @param downloaded_bytes: 已下载的字节数
        @param total_bytes: 文件总字节数
        """
        try:
            # 获取数据库连接
            database = self._get_or_create_database()

            # 获取或创建 file_id
            file_id = self.file.file_id
            if file_id is None:
                # 如果 file_id 不存在，需要先保存文件信息到数据库
                database.new_file(self.file, self.course.id, self.course.fullname)
                # 从数据库获取新的 file_id
                import sqlite3
                conn = sqlite3.connect(database.db_file)
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT file_id FROM files
                    WHERE course_id = ? AND module_id = ? AND content_fileurl = ?""",
                    (self.course.id, self.file.module_id, self.file.content_fileurl)
                )
                result = cursor.fetchone()
                conn.close()
                file_id = result[0] if result else None

                if file_id is None:
                    logging.warning('[%d] 无法获取 file_id，跳过保存中断下载记录', self.task_id)
                    return

            # 🔧 IncompleteRecord: typed value object replacing the
            # 6-arg cursor.execute boilerplate. The .save() method
            # delegates to recorder.save_incomplete_download.
            IncompleteRecord(
                file_id=file_id,
                file_url=file_url,
                file_path=file_path,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
            ).save(database)

            logging.debug(
                '[%d] 已保存未完成下载记录: file_id=%d, 进度=%d/%d',
                self.task_id, file_id, downloaded_bytes, total_bytes
            )
        except Exception as e:
            logging.error('[%d] 保存未完成下载记录时出错: %s', self.task_id, e)
            raise

    async def _resume_incomplete_download(self, file_path: str, dl_url: str, 
                                           session: aiohttp.ClientSession,
                                           headers: dict, ssl_context) -> tuple:
        """
        尝试恢复未完成的下载
        
        @param file_path: 文件路径
        @param dl_url: 下载 URL
        @param session: aiohttp 会话
        @param headers: HTTP 头
        @param ssl_context: SSL 上下文
        @return: (已下载字节数, 文件对象) 或 (0, None) 如果无法恢复
        """
        try:
            from moodle_dl.database import StateRecorder
            from moodle_dl.config import ConfigHelper
            
            database = self._get_or_create_database()

            file_id = self.file.file_id
            if file_id is None:
                return 0, None
            
            # 获取未完成的下载信息
            incomplete_info = database.get_incomplete_download(file_id, file_path)
            if not incomplete_info:
                return 0, None
            
            downloaded_bytes = incomplete_info['downloaded_bytes']
            
            # 检查文件是否存在且大小匹配
            if not os.path.exists(file_path) or os.path.getsize(file_path) != downloaded_bytes:
                logging.warning(
                    '[%d] 本地文件大小不匹配，跳过续传 (本地: %d, 数据库: %d)',
                    self.task_id,
                    os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                    downloaded_bytes
                )
                database.increment_incomplete_download_attempt(incomplete_info['download_id'], '文件大小不匹配')
                return 0, None
            
            # 尝试发送 HEAD 请求检查服务器是否支持 Range
            if not incomplete_info['server_supports_range']:
                logging.debug('[%d] 服务器不支持 Range 请求，无法续传', self.task_id)
                return 0, None
            
            try:
                async with session.head(dl_url, headers={'User-Agent': headers.get('User-Agent', '')}, 
                                       ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status not in [200, 206]:
                        logging.warning('[%d] HEAD 请求返回 %d，无法续传', self.task_id, resp.status)
                        return 0, None
                    
                    # 检查 Content-Length
                    content_length = resp.content_length
                    if content_length and downloaded_bytes >= content_length:
                        logging.warning('[%d] 已下载字节数大于等于文件大小，删除中断记录', self.task_id)
                        database.mark_download_complete(file_id, file_path)
                        return 0, None
            except Exception as head_err:
                logging.debug('[%d] HEAD 请求失败，无法续传: %s', self.task_id, head_err)
                return 0, None

            # 打开文件用于追加写入
            file_obj = await aiofiles.open(file_path, 'a+b')

            try:
                logging.info(
                    '[%d] 恢复未完成下载，从 %s 处继续',
                    self.task_id,
                    format_bytes(downloaded_bytes)
                )

                return downloaded_bytes, file_obj
            except Exception as log_err:
                # 日志记录失败时关闭文件
                await file_obj.close()
                logging.debug('[%d] 日志记录失败: %s', self.task_id, log_err)
                return 0, None
        except Exception as e:
            logging.debug('[%d] 恢复下载失败: %s', self.task_id, e)
            return 0, None

    def __str__(self):
        return 'Task (%(task_id)s, %(file)s, %(course)s, %(status)s)' % {
            'task_id': self.task_id,
            'file': self.file,
            'course': self.course,
            'status': self.status,
        }


class ContentRangeError(ConnectionError):
    pass


def _should_save_incomplete(
    can_continue_on_fail: bool,
    total_bytes_received: int,
    content_length: int,
    err: Exception,
) -> bool:
    """The decision: should we save this download as incomplete (for resume)?

    Pinned in tests/test_resume_subsystem.py. Do not change without
    updating those tests.

    Returns True only when:
      - server supports Range (can_continue_on_fail is True)
      - we have some data (> 0 bytes)
      - we're not done (< content_length)
      - the error wasn't a Range header failure
    """
    return (
        can_continue_on_fail
        and total_bytes_received > 0
        and total_bytes_received < (content_length or 0)
        and not isinstance(err, ContentRangeError)
    )
