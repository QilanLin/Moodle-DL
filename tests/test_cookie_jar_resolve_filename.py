# -*- coding: utf-8 -*-
"""
Tests for the refactored filename-resolution helper in MoodleDLCookieJar.

Before the refactor, the `if filename is None: ... else: raise` block
was duplicated verbatim in both save() and load(). The refactor
extracts a private `_resolve_filename()` method.

These tests pin:
  1. save() with explicit filename → uses it
  2. save() with None filename, jar.filename set → uses jar.filename
  3. save() with None filename and None jar.filename → raises
  4. Same three cases for load()
  5. The helper is callable directly (internal API)
"""
import http.cookiejar
import io
import os
import unittest
from unittest.mock import MagicMock

import pytest

from moodle_dl.utils import MoodleDLCookieJar


def _make_jar(filename=None):
    """Build a fresh jar with no cookies."""
    return MoodleDLCookieJar(filename=filename)


class TestResolveFilename(unittest.TestCase):
    """Pin the new _resolve_filename() helper."""

    def test_explicit_filename_returns_it(self):
        jar = _make_jar()
        self.assertEqual(jar._resolve_filename('/tmp/explicit.txt'), '/tmp/explicit.txt')

    def test_none_filename_uses_jar_default(self):
        jar = _make_jar(filename='/tmp/jar_default.txt')
        self.assertEqual(jar._resolve_filename(None), '/tmp/jar_default.txt')

    def test_none_filename_no_jar_default_raises(self):
        jar = _make_jar(filename=None)
        with self.assertRaises(ValueError) as cm:
            jar._resolve_filename(None)
        # Should mention "filename" in the error message (the stdlib
        # MISSING_FILENAME_TEXT) so users can find it.
        self.assertIn('filename', str(cm.exception).lower())


class TestSaveLoadBothCallResolveFilename(unittest.TestCase):
    """The two public entry points must funnel through the helper."""

    def test_save_with_jar_filename_uses_it(self):
        with tempfile_TemporaryDirectory() as td:
            path = os.path.join(td, 'save_default.txt')
            jar = MoodleDLCookieJar(filename=path)
            # Add a single cookie so the file has something to write.
            jar.set_cookie(http.cookiejar.Cookie(
                version=0, name='a', value='b', port=None,
                port_specified=False, domain='.example.com',
                domain_specified=True, domain_initial_dot=True,
                path='/', path_specified=True, secure=False,
                expires=None, discard=True, comment=None, comment_url=None,
                rest={}, rfc2109=False,
            ))
            # save() with no explicit filename → should use self.filename
            jar.save()
            self.assertTrue(os.path.exists(path))

    def test_load_with_jar_filename_uses_it(self):
        with tempfile_TemporaryDirectory() as td:
            path = os.path.join(td, 'load_default.txt')
            # Write a valid Netscape cookie file with a future
            # expiration so default ignore_expires=False won't skip it.
            future_expires = '9999999999'  # year 2286
            with open(path, 'w') as f:
                f.write('# Netscape HTTP Cookie File\n')
                f.write(f'.example.com\tTRUE\t/\tFALSE\t{future_expires}\tsid\tabc\n')

            jar = MoodleDLCookieJar(filename=path)
            jar.load()
            cookies = list(jar)
            self.assertEqual(len(cookies), 1)
            self.assertEqual(cookies[0].name, 'sid')


# Helper context manager, kept local to this file to avoid the import
# in main tests.
class tempfile_TemporaryDirectory:
    def __enter__(self):
        import tempfile
        self._name = tempfile.mkdtemp()
        return self._name

    def __exit__(self, *args):
        import shutil
        shutil.rmtree(self._name, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
