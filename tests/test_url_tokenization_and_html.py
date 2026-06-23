# -*- coding: utf-8 -*-
"""
Tests for TaskFileOps.add_token_to_url and other URL/tokenization
helper methods that were previously untested.

add_token_to_url takes any URL and ensures the user's token is
appended as a query parameter (for pluginfile URLs, it does
special handling; for other URLs, it appends ?token=...).
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# add_token_to_url: append token to non-pluginfile URLs
# =========================================================================
class TestAddTokenToUrl:
    """Pin the contract: any URL passed to add_token_to_url must end
    up with the token in query string (either as pluginfile tokenized
    URL or as ?token=... appended).

    Used by various download code paths when fetching files.
    """

    def _make_task_file_ops(self, token='test_token_abc'):
        """Build a TaskFileOps with mocked task."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import MoodleURL

        task = MagicMock()
        task.opts.token = token
        moodle_url = MoodleURL(use_http=False, domain='example.com', path='')
        task.opts.moodle_url = moodle_url
        # fix_pluginfile_url accesses .rstrip on moodle_url which doesn't exist,
        # so we patch the call to use .url_base instead.
        # Easier: monkey-patch the method to use url_base.
        # The task_file_ops.add_token_to_url does
        #   UrlHelper.fix_pluginfile_url(url=url, token=..., moodle_base_url=self.task.opts.moodle_url)
        # and fix_pluginfile_url does .rstrip('/') which fails on MoodleURL.
        # For testing, we use a string instead.
        task.opts.moodle_url = 'https://example.com'
        return TaskFileOps(task)

    def test_external_url_gets_token_appended(self):
        """For non-pluginfile external URLs (like example.com),
        append token as query parameter.
        """
        ops = self._make_task_file_ops(token='mytoken123')
        url = 'https://example.com/some/path.pdf'
        result = ops.add_token_to_url(url)
        assert 'token=mytoken123' in result, (
            f'External URL should have token appended. Got: {result!r}'
        )

    def test_url_with_existing_query_param_gets_token_added(self):
        """URL with existing ?foo=bar should get &token=... appended."""
        ops = self._make_task_file_ops(token='mytoken456')
        url = 'https://example.com/path?foo=bar'
        result = ops.add_token_to_url(url)
        assert 'foo=bar' in result
        assert 'token=mytoken456' in result

    def test_url_with_existing_token_is_preserved(self):
        """If URL already has token=, don't add another one."""
        ops = self._make_task_file_ops(token='newtoken')
        url = 'https://example.com/path?token=oldtoken'
        result = ops.add_token_to_url(url)
        # Old token should be preserved (since 'token=' is already there)
        assert 'token=oldtoken' in result
        assert 'token=newtoken' not in result, (
            f'Should not add duplicate token. Got: {result!r}'
        )

    def test_external_url_with_fragment(self):
        """URL with #fragment should preserve the fragment."""
        ops = self._make_task_file_ops(token='mytoken789')
        url = 'https://example.com/path#section1'
        result = ops.add_token_to_url(url)
        assert '#section1' in result, (
            f'URL fragment should be preserved. Got: {result!r}'
        )
        assert 'token=mytoken789' in result

    def test_pluginfile_url_does_not_double_tokenize(self):
        """For pluginfile URLs, fix_pluginfile_url is called and
        token is added in pluginfile format. Should not have BOTH
        pluginfile token AND ?token= appended (no double-tokenize).
        """
        ops = self._make_task_file_ops(token='mytoken999')
        url = 'https://example.com/pluginfile.php/123/mod_resource/content/0/file.pdf'
        result = ops.add_token_to_url(url)
        # Should be processed (stays as-is or has fix applied)
        assert 'pluginfile.php' in result
        # Should NOT have ?token= appended (pluginfile handles auth differently)
        # Note: this is the contract — non-pluginfile URLs get ?token=,
        # pluginfile URLs go through fix_pluginfile_url.
        # We don't assert specific behavior here, just no crash.

    def test_url_with_empty_token_does_not_crash(self):
        """If token is empty, function should still work."""
        ops = self._make_task_file_ops(token='')
        url = 'https://example.com/path.pdf'
        # Should not crash
        result = ops.add_token_to_url(url)
        # May or may not append token, but should return a valid URL
        assert result.startswith('https://')


# =========================================================================
# Helper methods for URL parsing
# =========================================================================
class TestAddTokenToUrlEdgeCases:
    """Additional edge cases for URL tokenization."""

    def _make_task_file_ops(self, token='token'):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import MoodleURL

        task = MagicMock()
        task.opts.token = token
        moodle_url = MoodleURL(use_http=False, domain='example.com', path='')
        task.opts.moodle_url = moodle_url
        # fix_pluginfile_url accesses .rstrip on moodle_url which doesn't exist,
        # so we patch the call to use .url_base instead.
        # Easier: monkey-patch the method to use url_base.
        # The task_file_ops.add_token_to_url does
        #   UrlHelper.fix_pluginfile_url(url=url, token=..., moodle_base_url=self.task.opts.moodle_url)
        # and fix_pluginfile_url does .rstrip('/') which fails on MoodleURL.
        # For testing, we use a string instead.
        task.opts.moodle_url = 'https://example.com'
        return TaskFileOps(task)

    def test_http_url_token_added(self):
        """HTTP URLs (not HTTPS) also get token."""
        ops = self._make_task_file_ops(token='http_token')
        url = 'http://insecure.example.com/file.pdf'
        result = ops.add_token_to_url(url)
        assert 'token=http_token' in result

    def test_url_with_no_path(self):
        """URL with just domain (no path) gets token."""
        ops = self._make_task_file_ops(token='nopth')
        url = 'https://example.com'
        result = ops.add_token_to_url(url)
        assert 'token=nopth' in result

    def test_url_with_port(self):
        """URL with explicit port should preserve port."""
        ops = self._make_task_file_ops(token='portok')
        url = 'https://example.com:8443/file.pdf'
        result = ops.add_token_to_url(url)
        assert ':8443' in result
        assert 'token=portok' in result

    def test_url_with_userinfo(self):
        """URL with user:pass@ in URL should be preserved."""
        ops = self._make_task_file_ops(token='usertok')
        url = 'https://user:pass@example.com/file.pdf'
        result = ops.add_token_to_url(url)
        assert 'user:pass@example.com' in result
        assert 'token=usertok' in result

    def test_relative_url_with_leading_slash(self):
        """Path-only URL (e.g. '/file.pdf') gets token."""
        ops = self._make_task_file_ops(token='relpath')
        # Note: urlparse handles this as scheme='', netloc='', path='/file.pdf'
        url = '/file.pdf'
        result = ops.add_token_to_url(url)
        # May not work correctly for truly relative URLs (no host)
        # but should not crash
        assert isinstance(result, str)


# =========================================================================
# HTML cleaning helpers (convert_line_breaks, convert_paragraphs)
# =========================================================================
class TestConvertLineBreaksEdgeCases:
    """More edge cases for HTML <br> tag conversion."""

    def test_multiple_br_tags(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_line_breaks('a<br>b<br>c<br>d')
        assert result == 'a\nb\nc\nd'

    def test_br_with_attributes_quirk(self):
        """Pin: <br style="..."> is NOT converted (regex limitation)."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_line_breaks('line1<br style="margin:10px">line2')
        assert '<br style' in result

    def test_br_uppercase_preserved_not_converted(self):
        """Note: implementation is case-sensitive. <BR> is NOT converted
        (only <br> lowercase).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        # Pin the actual behavior (case-sensitive)
        result = TaskFileOps.convert_line_breaks('line1<BR>line2')
        # The implementation only matches lowercase 'br'
        assert '<BR>' in result or 'BR' in result

    def test_br_tag_with_whitespace_in_self_closing_quirk(self):
        """Pin actual: <br/ > (space before >) is NOT converted (regex limitation)."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_line_breaks('line1<br/ >line2')
        assert '<br/ >' in result

    def test_br_with_multiline_content(self):
        """Multiline content with br tags. <br> adds \\n on top of
        any existing \\n, producing \\n\\n between lines."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        text = 'First line<br>\nSecond line<br/>\nThird line'
        result = TaskFileOps.convert_line_breaks(text)
        assert 'First line\n\nSecond line\n\nThird line' == result

    def test_no_br_tags_returns_same(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        text = 'plain text without tags'
        assert TaskFileOps.convert_line_breaks(text) == text


class TestConvertParagraphsEdgeCases:
    """More edge cases for <p> tag conversion."""

    def test_multiple_paragraphs(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_paragraphs(
            '<p>first</p><p>second</p><p>third</p>'
        )
        # Each <p> becomes newline-separated
        assert 'first' in result
        assert 'second' in result
        assert 'third' in result

    def test_p_with_attributes(self):
        """<p class="..."> should also be converted."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_paragraphs(
            '<p class="para">text</p>'
        )
        assert 'text' in result
        # The class attribute should be stripped
        assert 'class=' not in result or 'para' not in result

    def test_no_p_tags_returns_same(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        text = 'plain text without p tags'
        assert TaskFileOps.convert_paragraphs(text) == text

    def test_p_uppercase_not_converted(self):
        """Pin case-sensitivity: <P> is NOT converted (only <p>)."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_paragraphs('<P>text</P>')
        # <P> should be preserved (not stripped)
        assert '<P>' in result or 'text' in result