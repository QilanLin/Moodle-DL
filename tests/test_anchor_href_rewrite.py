# -*- coding: utf-8 -*-
"""
Tests for EMBEDDED_RESOURCE_ATTRS coverage.

The downloader's HTML rewrite pipeline (used by both the
download_service and the repair tool) only rewrites
href/src/poster/data attributes on a fixed list of HTML
tags. This list was hardcoded to be very conservative:
'href' is only allowed on <link> tags. But page
navigation links are <a href="...">! This bug means
that clicking "Next Page" on PCR goes to a non-existent
file because the <a href> was never rewritten.

This test pins the contract that <a href> should be in
the rewrite whitelist.
"""
import os
import sys
import tempfile
import unittest

# 🔧 Portability: use __file__ to find the project root, not a
# hardcoded user-specific path. Pytest's conftest.py also adds
# the root, but having it in-file makes this test runnable in
# isolation (e.g. ``python -m unittest``).
import os.path as _path
_ROOT = _path.dirname(_path.dirname(_path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from moodle_dl.downloader.html_localizer import (
    EMBEDDED_RESOURCE_ATTRS,
    build_local_resource_map,
    rewrite_html_links_to_local_paths,
)


class DummyFile:
    def __init__(self, saved_to, content_fileurl):
        self.saved_to = saved_to
        self.content_fileurl = content_fileurl


class TestAnchorsRewritten(unittest.TestCase):
    """Pin that <a href> tags pointing to other pages
    in the workspace are rewritten to the actual disk
    path (with *NN* prefix if present)."""

    def test_anchor_href_is_rewritten(self):
        with tempfile.TemporaryDirectory() as td:
            # Two HTML files: index.html and *11* Introduction.html
            html1 = os.path.join(td, '*01* index.html')
            html2 = os.path.join(td, '*11* Introduction.html')
            with open(html1, 'w') as f:
                f.write('<a href="Introduction.html">Next</a>')
            with open(html2, 'w') as f:
                f.write('<h1>Intro</h1>')

            files = [
                DummyFile(
                    saved_to=html1,
                    content_fileurl='https://keats.kcl.ac.uk/mod_page/index.html',
                ),
                DummyFile(
                    saved_to=html2,
                    content_fileurl='https://keats.kcl.ac.uk/mod_page/Introduction.html',
                ),
            ]
            local_resources = build_local_resource_map(files)

            with open(html1, 'r', errors='replace') as f:
                html_content = f.read()
            rewritten, n = rewrite_html_links_to_local_paths(
                html_content, html1, local_resources,
            )

            self.assertEqual(n, 1,
                f'expected 1 rewrite, got {n}; '
                f'EMBEDDED_RESOURCE_ATTRS may be missing <a> '
                f'in href whitelist')
            self.assertIn('*11* Introduction.html', rewritten)
            self.assertNotIn('"Introduction.html"', rewritten)


class TestEmbeddedResourceAttrs(unittest.TestCase):
    def test_href_includes_anchor_tag(self):
        self.assertIn('a', EMBEDDED_RESOURCE_ATTRS.get('href', set()),
                      'EMBEDDED_RESOURCE_ATTRS[href] must include "a" '
                      'tag for navigation links to be rewritten')


if __name__ == '__main__':
    unittest.main()
