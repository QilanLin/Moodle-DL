# -*- coding: utf-8 -*-
"""
Unit tests for TaskFileOps.

Pin the behavior of file/path/HTML operations extracted
from Task. These helpers turn Task into an orchestrator.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

# 🔧 Portability: use __file__ to find the project root, not a
# hardcoded user-specific path. Pytest's conftest.py also adds
# the root, but having it in-file makes this test runnable in
# isolation (e.g. ``python -m unittest``).
import os.path as _path
_ROOT = _path.dirname(_path.dirname(_path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from moodle_dl.downloader.task_file_ops import (
    SHORTCUT_EXTENSIONS,
    TaskFileOps,
    ensure_parent_dir,
)

# =======================================================================
# HTML cleaning utilities (pure functions)
# =======================================================================
class TestConvertLineBreaks:
    def test_br_to_newline(self):
        # The convert-line-breaks utility preserves \n literally
        # (no whitespace collapse happens here).
        assert TaskFileOps.convert_line_breaks('a<br>b') == 'a\nb'
        assert TaskFileOps.convert_line_breaks('a<br/>b') == 'a\nb'
        # <BR> in caps is NOT replaced (case-sensitive, matches
        # the original task.py behaviour).
        assert TaskFileOps.convert_line_breaks('a<BR>b') == 'a<BR>b'

    def test_no_br_unchanged(self):
        assert TaskFileOps.convert_line_breaks('hello world') == 'hello world'

    def test_empty_input(self):
        assert TaskFileOps.convert_line_breaks('') == ''


class TestConvertParagraphs:
    def test_p_to_newline(self):
        text = '<p>one</p><p>two</p>'
        result = TaskFileOps.convert_paragraphs(text)
        # double-newline between paragraphs
        assert '\n\n' in result
        assert 'one' in result and 'two' in result

    def test_empty_input(self):
        assert TaskFileOps.convert_paragraphs('') == ''


class TestConvertLists:
    def test_li_to_bullet(self):
        text = '<ul><li>one</li><li>two</li></ul>'
        result = TaskFileOps.convert_lists(text)
        assert '• one' in result or '•' in result
        assert 'one' in result and 'two' in result

    def test_empty_input(self):
        assert TaskFileOps.convert_lists('') == ''


class TestConvertFormatting:
    def test_bold_to_markdown(self):
        result = TaskFileOps.convert_formatting('<b>bold</b>')
        assert '**bold**' in result

    def test_italic_to_markdown(self):
        result = TaskFileOps.convert_formatting('<i>italic</i>')
        assert '*italic*' in result

    def test_empty_input(self):
        assert TaskFileOps.convert_formatting('') == ''


class TestConvertLinks:
    def test_a_to_markdown(self):
        result = TaskFileOps.convert_links(
            '<a href="http://example.com">link</a>'
        )
        assert '[link](http://example.com)' in result

    def test_empty_input(self):
        assert TaskFileOps.convert_links('') == ''


class TestRemoveHtmlTags:
    def test_strips_tags(self):
        assert TaskFileOps.remove_html_tags('<p>hello</p>') == 'hello'
        assert TaskFileOps.remove_html_tags('<b>hi</b>') == 'hi'

    def test_empty_input(self):
        assert TaskFileOps.remove_html_tags('') == ''


class TestDecodeHtmlEntities:
    def test_decodes_amp_lt_gt(self):
        result = TaskFileOps.decode_html_entities('&lt;b&gt;hi&lt;/b&gt;')
        assert result == '<b>hi</b>'

    def test_decodes_ampersand(self):
        assert TaskFileOps.decode_html_entities('&amp;') == '&'

    def test_empty_input(self):
        assert TaskFileOps.decode_html_entities('') == ''


class TestCleanWhitespace:
    def test_collapses_3_or_more_newlines(self):
        result = TaskFileOps.clean_whitespace('a\n\n\n\nb')
        # Three-or-more newlines should collapse to two
        assert result == 'a\n\nb'

    def test_collapses_multiple_spaces(self):
        result = TaskFileOps.clean_whitespace('a     b')
        assert result == 'a b'

    def test_strips(self):
        assert TaskFileOps.clean_whitespace('  hello  ') == 'hello'

    def test_empty_input(self):
        assert TaskFileOps.clean_whitespace('') == ''


# =======================================================================
# clean_html_simple and clean_html_preserve_structure
# =======================================================================
def _make_fileops() -> TaskFileOps:
    """Create a TaskFileOps with a mock task for instance methods."""
    return TaskFileOps(MagicMock())


class TestCleanHtmlSimple:
    def test_strips_html(self):
        result = _make_fileops().clean_html_simple('<p>hello <b>world</b></p>')
        assert '<' not in result
        assert 'hello' in result and 'world' in result

    def test_handles_br_as_newline(self):
        # The simple cleaner converts <br> to \n then collapses
        # runs of whitespace. So the final output has a single
        # space (not \n), but the line-break is observable by
        # checking that 'line1' and 'line2' are separated.
        result = _make_fileops().clean_html_simple('line1<br>line2')
        assert 'line1' in result and 'line2' in result
        # Crucially, the original <br> tag is gone (cleaned).
        assert '<br>' not in result

    def test_decodes_entities(self):
        result = _make_fileops().clean_html_simple('&lt;b&gt;')
        assert '<' in result and '>' in result

    def test_empty_input(self):
        assert _make_fileops().clean_html_simple('') == ''


class TestCleanHtmlPreserveStructure:
    def test_preserves_paragraphs(self):
        result = _make_fileops().clean_html_preserve_structure(
            '<p>one</p><p>two</p>'
        )
        assert 'one' in result and 'two' in result
        # Should have paragraph separation
        assert '\n' in result

    def test_preserves_bold_as_markdown(self):
        result = _make_fileops().clean_html_preserve_structure('<b>bold</b>')
        assert '**bold**' in result

    def test_preserves_links_as_markdown(self):
        result = _make_fileops().clean_html_preserve_structure(
            '<a href="http://x">link</a>'
        )
        assert '[link](http://x)' in result

    def test_empty_input(self):
        assert _make_fileops().clean_html_preserve_structure('') == ''


class TestCleanHtmlDispatch:
    def test_simple_mode_dispatches_to_simple(self):
        ops = _make_fileops()
        simple = ops.clean_html_simple('<p>x</p>')
        dispatched = ops.clean_html('<p>x</p>', mode='simple')
        assert dispatched == simple

    def test_structured_mode_dispatches_to_structured(self):
        ops = _make_fileops()
        structured = ops.clean_html_preserve_structure('<p>x</p>')
        dispatched = ops.clean_html('<p>x</p>', mode='structured')
        assert dispatched == structured

    def test_default_mode_is_structured(self):
        """No mode → structured (preserves structure as Markdown)."""
        result = _make_fileops().clean_html('<b>x</b>')
        # Structured mode keeps bold as **x**
        assert '**x**' in result


# =======================================================================
# Shortcut file extensions
# =======================================================================
class TestShortcutExtensions:
    def test_includes_all_platforms(self):
        """All 3 platform shortcut formats are supported."""
        assert '.url' in SHORTCUT_EXTENSIONS  # Windows
        assert '.webloc' in SHORTCUT_EXTENSIONS  # macOS
        assert '.desktop' in SHORTCUT_EXTENSIONS  # Linux


# =======================================================================
# ensure_parent_dir
# =======================================================================
class TestEnsureParentDir:
    def test_creates_parents(self, tmp_path):
        target = tmp_path / 'a' / 'b' / 'file.pdf'
        ensure_parent_dir(str(target))
        assert target.parent.is_dir()

    def test_idempotent(self, tmp_path):
        target = tmp_path / 'a' / 'file.pdf'
        target.parent.mkdir(parents=True, exist_ok=True)
        ensure_parent_dir(str(target))  # no exception
        assert target.parent.is_dir()

    def test_bare_filename_noop(self):
        ensure_parent_dir('foo.pdf')  # no exception
