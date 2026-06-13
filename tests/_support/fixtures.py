"""
Shared test fixtures for moodle-dl tests.

This module provides reusable pytest fixtures and helper functions
to eliminate the boilerplate that was previously duplicated across
many test files. Import via:

    from tests._support.fixtures import (
        range_http_server,    # Real local HTTP server with Range support
        tmp_db,                # tmp dir + StateRecorder
        task_factory,          # Build a Task with mocked deps
        tmp_db_recorder,       # Just a (tmp_path, recorder) tuple
    )

Each fixture is intentionally self-contained: it owns its own
lifecycle (start/stop, connect/disconnect, create/drop) so
callers can compose them freely.
"""
import asyncio
import http.server
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import urllib.parse
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# Make moodle_dl importable from any test that uses these fixtures
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import DownloadOptions, File, MoodleDlOpts


# ========================================================================
# HTTP server with Range support
# ========================================================================
class RangeHTTPServer:
    """A local HTTP server that supports Range requests.

    This replaces three near-identical handler classes that were
    previously defined in test_e2e_resume.py,
    test_resume_kill_recovery.py, and test_kill_resilience.py.

    Usage:
        with range_http_server(content) as (url, server):
            # server.bytes_served, server.request_log available
            ...

    Attributes:
        bytes_served: total bytes sent across all requests
        request_log: list of (kind, start, length) tuples
        serve_mode: 'normal' (with Range) or 'no_range' (rejects Range)
        chunk_delay_seconds: simulate slow downloads
    """

    def __init__(self, content: bytes, mode: str = 'normal', chunk_delay: float = 0.0, etag: str = ''):
        self.file_content = content
        self.file_size = len(content)
        self.request_log = []
        self.bytes_served = 0
        self.serve_mode = mode
        self.chunk_delay_seconds = chunk_delay
        self.etag = etag
        self._server = None
        self._thread = None

    def _make_handler(self):
        """Build a handler class bound to this server instance.

        Note: a fresh class per server is needed because BaseHTTPRequestHandler
        reads handler-class attributes at request time. If multiple
        server instances shared one class, their state would be racy.
        """
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server._handle_GET(self)

            def log_message(self, format, *args):
                pass

        return Handler

    def _handle_GET(self, handler):
        start, end = 0, self.file_size - 1
        range_header = handler.headers.get('Range', '')
        if self.serve_mode == 'no_range' or not range_header.startswith('bytes='):
            self.request_log.append(('full', None, self.file_size))
            handler.send_response(200)
            handler.send_header('Content-Length', str(self.file_size))
            handler.send_header(
                'Accept-Ranges',
                'none' if self.serve_mode == 'no_range' else 'bytes',
            )
            handler.end_headers()
            handler.wfile.write(self.file_content)
            self.bytes_served += self.file_size
            return
        # Parse the Range spec
        spec = range_header[len('bytes='):]
        if '-' in spec:
            start_s, end_s = spec.split('-', 1)
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else (self.file_size - 1)
        end = min(end, self.file_size - 1)
        length = end - start + 1
        self.request_log.append(('range', start, length))
        handler.send_response(206)
        handler.send_header('Content-Range', f'bytes {start}-{end}/{self.file_size}')
        handler.send_header('Content-Length', str(length))
        handler.send_header('Accept-Ranges', 'bytes')
        # Optional: support ETag if etag is set on the server
        if self.etag:
            handler.send_header('ETag', self.etag)
        handler.end_headers()
        if self.chunk_delay_seconds > 0:
            # Slow send: write byte-by-byte so a cancel can fire
            import time as _time
            for i in range(start, end + 1):
                handler.wfile.write(self.file_content[i:i + 1])
                self.bytes_served += 1
                if i % 100 == 0:
                    _time.sleep(self.chunk_delay_seconds)
        else:
            handler.wfile.write(self.file_content[start:end + 1])
            self.bytes_served += length

    def start(self):
        port = self._find_free_port()
        self._server = http.server.HTTPServer(('127.0.0.1', port), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f'http://127.0.0.1:{port}'

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=2)
            self._server = None
            self._thread = None

    @staticmethod
    def _find_free_port():
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]


@contextmanager
def range_http_server(content: bytes, mode: str = 'normal', chunk_delay: float = 0.0, etag: str = ''):
    """Context manager: yield (base_url, server) for a real local
    HTTP server with Range support.

    Example:
        with range_http_server(b'hello world') as (base_url, srv):
            assert srv.file_size == 11
            # Use base_url in tests
    """
    server = RangeHTTPServer(content, mode=mode, chunk_delay=chunk_delay, etag=etag)
    base_url = server.start()
    try:
        yield base_url, server
    finally:
        server.stop()


# ========================================================================
# Database + recorder setup
# ========================================================================
@pytest.fixture
def tmp_db():
    """Yield a (tmp_path, recorder) pair with a fully-initialized DB.

    The DB is created in a fresh temp dir; tests can read/write/modify
    it without affecting other tests. StateRecorder.__init__ creates
    the v9 schema.
    """
    with tempfile.TemporaryDirectory() as td:
        config = MagicMock(spec=ConfigHelper)
        config.get_misc_files_path.return_value = td
        opts = MoodleDlOpts()
        recorder = StateRecorder(config, opts)
        yield td, recorder


@pytest.fixture
def tmp_db_recorder():
    """Yield only the recorder (some tests don't need the tmp dir)."""
    with tempfile.TemporaryDirectory() as td:
        config = MagicMock(spec=ConfigHelper)
        config.get_misc_files_path.return_value = td
        opts = MoodleDlOpts()
        recorder = StateRecorder(config, opts)
        yield td, recorder


# ========================================================================
# Task factory (real Task, mocked deps)
# ========================================================================
def make_task_for_tests(
    recorder,
    file_id: int = 1,
    module_id: int = 1,
    content_filesize: int = 1024,
    content_fileurl: str = 'http://x/test.pdf',
    content_filename: str = 'test.pdf',
):
    """Build a Task with the minimum viable state for unit tests.

    The Task is constructed via __new__ to bypass its heavy __init__
    (which would try to fetch from Moodle). All dependencies are
    mocked except the recorder (which the Task uses to persist
    incomplete downloads). The task is ready to call
    _save_incomplete_on_kill, download_url, etc.

    Returns:
        (task, file) where file is the mocked File object.
    """
    from moodle_dl.downloader.task import Task
    from moodle_dl.types import File, TaskState, TaskStatus

    task = Task.__new__(Task)
    task.opts = MagicMock(spec=DownloadOptions)
    task.config = MagicMock(spec=ConfigHelper)
    task.database = recorder
    task._open_file_handle = None

    file_obj = File(
        module_id=module_id,
        module_name='Test Module',
        module_modname='resource',
        section_name='Section 1',
        section_id=100,
        content_filename=content_filename,
        content_filepath='/',
        content_fileurl=content_fileurl,
        content_filesize=content_filesize,
        content_timemodified=0,
        content_type='resource_file',
        content_isexternalfile=False,
        saved_to='',
        time_stamp=0,
        modified=False,
        moved=False,
        deleted=False,
        notified=False,
        file_id=file_id,
        old_file_id=0,
    )
    task.file = file_obj
    task.task_id = 1
    status = TaskStatus()
    status.state = TaskState.INIT
    status.bytes_downloaded = 0
    task.status = status
    task.destination = ''
    return task, file_obj


# ========================================================================
# SQLite inspection helpers (used by many tests)
# ========================================================================
def query_count(recorder, table: str, where_clause: str = '', params: tuple = ()) -> int:
    """Generic count query helper.

    Args:
        recorder: a StateRecorder
        table: table name
        where_clause: e.g. 'file_id = ?' (no WHERE keyword)
        params: bound parameters
    """
    conn = sqlite3.connect(recorder.db_file)
    try:
        if where_clause:
            sql = f'SELECT COUNT(*) FROM {table} WHERE {where_clause}'
        else:
            sql = f'SELECT COUNT(*) FROM {table}'
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def query_one(recorder, sql: str, params: tuple = ()):
    """Return one row (or None)."""
    conn = sqlite3.connect(recorder.db_file)
    try:
        cur = conn.execute(sql, params)
        return cur.fetchone()
    finally:
        conn.close()


# ========================================================================
# File-utility helpers
# ========================================================================
def write_part_file(td: str, size: int, name: str = 'test.pdf') -> str:
    """Write a fake .part file of `size` bytes in td. Returns the path."""
    part_path = os.path.join(td, name + '.part')
    with open(part_path, 'wb') as f:
        f.write(b'x' * size)
    return part_path


# ========================================================================
# Auto-discovery for pytest
# ========================================================================
# pytest looks for conftest.py at every directory level. We make the
# fixtures above available by re-exporting them in tests/conftest.py.
# This file should not have its own conftest; tests/_support/ contains
# helpers that need to be explicitly imported.
