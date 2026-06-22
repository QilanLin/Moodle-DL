# -*- coding: utf-8 -*-
"""
Unit tests for the description_url_extractor pure-function helpers.

These tests pin the contract of three helpers extracted from
ResultBuilder._find_all_urls:

  * extract_urls_from_html(html) -> List[str]
      Pulls raw URLs out of an HTML description string.

  * should_skip_url(url, url_parts, moodle_domain, original_modname,
                    is_embedded_media) -> bool
      Encapsulates the multi-condition "should this URL be dropped?"
      decision that was previously an 11-line boolean chain in
      _find_all_urls.

  * assign_modname_for_url(url, url_parts, moodle_domain, original_modname,
                           is_kaltura, is_helixmedia) -> str
      Encapsulates the 3-branch modname dispatch in _find_all_urls
      (the /webservice/ branch, the cookie_mod branch, and the
      kaltura/helixmedia overrides).

These are pure functions. They don't depend on a ResultBuilder
instance, so tests are pin-fast (no setUp/tearDown, no DB, no
network) and the contract is easy to read in isolation.

WHY pure functions:
  * Each helper is a single decision the production code makes per URL.
  * Extracting them removes the 100+ line _find_all_urls god function.
  * Pure functions are easier to test (no fixtures, no shared state)
    and easier to reason about (no `self` to thread through).
  * Pinned tests here become the executable specification for the
    URL-handling contract.
"""
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# extract_urls_from_html
# =========================================================================
class TestExtractUrlsFromHtml:
    """Pin the contract of the HTML → List[url] extraction."""

    def test_href_attribute_extracted(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        urls = extract_urls_from_html('<a href="https://example.com/x.pdf">x</a>')
        assert 'https://example.com/x.pdf' in urls

    def test_src_attribute_extracted(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        urls = extract_urls_from_html('<img src="https://example.com/img.png">')
        assert 'https://example.com/img.png' in urls

    def test_data_attribute_extracted(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        urls = extract_urls_from_html('<object data="https://example.com/file.swf">')
        assert 'https://example.com/file.swf' in urls

    def test_anchor_text_url_extracted(self):
        """The original code has a <a ...>URL</a> regex that pulls raw
        URLs from anchor text bodies. Pin this contract — it's how
        bare-URL descriptions get picked up.
        """
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        urls = extract_urls_from_html('<a>https://example.com/bare.pdf</a>')
        assert 'https://example.com/bare.pdf' in urls

    def test_duplicate_urls_deduplicated(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        html = (
            '<a href="https://example.com/x.pdf">x</a>'
            '<img src="https://example.com/x.pdf">'  # same URL in src
            '<a href="https://example.com/x.pdf">y</a>'  # same URL again
        )
        urls = extract_urls_from_html(html)
        assert len(urls) == 1
        assert urls[0] == 'https://example.com/x.pdf'

    def test_case_insensitive_attribute_matching(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        urls = extract_urls_from_html('<A HREF="https://example.com/x.pdf">x</A>')
        assert 'https://example.com/x.pdf' in urls

    def test_single_quoted_attributes(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        urls = extract_urls_from_html("<a href='https://example.com/x.pdf'>x</a>")
        assert 'https://example.com/x.pdf' in urls

    def test_unquoted_attributes(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        urls = extract_urls_from_html('<a href=https://example.com/x.pdf>x</a>')
        assert 'https://example.com/x.pdf' in urls

    def test_empty_html_returns_empty_list(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        assert extract_urls_from_html('') == []

    def test_whitespace_only_returns_empty_list(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        for html in ['', '   ', '\n\n', '\t\t']:
            assert extract_urls_from_html(html) == [], f'Got {extract_urls_from_html(html)} for {html!r}'

    def test_html_with_no_urls_returns_empty_list(self):
        from moodle_dl.moodle.description_url_extractor import extract_urls_from_html
        html = '<p>Plain text only.</p><div>No links here.</div>'
        assert extract_urls_from_html(html) == []


# =========================================================================
# should_skip_url
# =========================================================================
class TestShouldSkipUrl:
    """Pin the contract of the multi-condition URL-skip decision.

    The original 11-line boolean chain in _find_all_urls has been
    distilled into a single function with a name that reads as a
    sentence: "should this URL be skipped?"

    The function returns True if the URL should be DROPPED
    (no File entry created), False if it should be KEPT.
    """

    def test_external_url_kept(self):
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://other.example.com/file.pdf'
        parts = urlparse(url)
        # External host, not on Moodle domain, no special flags
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='resource',
            is_embedded_media=False,
        ) is False

    def test_moodle_internal_page_link_skipped(self):
        """Internal Moodle page links (not pluginfile, not embedded
        media) must be skipped — that's the historical 'no shortcut
        files for internal links' contract.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/mod/page/view.php?id=42'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='page',
            is_embedded_media=False,
        ) is True

    def test_pluginfile_pdf_kept_for_resource_module(self):
        """The fix for the KLaSS regression: a pluginfile URL on the
        Moodle domain must be kept for non-book modnames.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/x.pdf?token=T'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='resource',
            is_embedded_media=False,
        ) is False

    def test_pluginfile_image_skipped_for_book_module(self):
        """The 'book exception': a pluginfile URL in a book chapter
        is handled by the HTML localizer, not as a standalone File.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_book/chapter/2/img.png?token=T'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='book',
            is_embedded_media=False,
        ) is True

    def test_kaltura_url_on_moodle_domain_kept(self):
        """Kaltura launch URLs (embedded media) are kept even though
        they're on the Moodle domain — that's the original
        is_embedded_media_candidate exemption.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?id=1'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='label',
            is_embedded_media=True,
        ) is False

    def test_helixmedia_url_on_moodle_domain_kept(self):
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/mod/helixmedia/view.php?id=1'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='label',
            is_embedded_media=True,
        ) is False

    def test_pluginfile_url_for_label_module_kept(self):
        """The KLaSS regression surface is mostly 'label' modnames.
        Pin that the pluginfile exemption applies.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_label/intro/file.pdf?token=T'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='label',
            is_embedded_media=False,
        ) is False

    def test_pluginfile_url_for_page_module_kept(self):
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_page/content/1/x.pdf?token=T'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='page',
            is_embedded_media=False,
        ) is False

    def test_pluginfile_url_for_url_module_kept(self):
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_url/preview.pdf?token=T'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='url',
            is_embedded_media=False,
        ) is False

    def test_external_moodle_clone_pluginfile_kept(self):
        """A pluginfile URL on a DIFFERENT Moodle host is not the
        configured Moodle domain, so the early-skip doesn't fire
        and the URL is kept. Pin the contract: the pluginfile
        exemption is for the CONFIGURED Moodle domain, not any
        host that happens to have /pluginfile.php/.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://other-moodle.example.com/pluginfile.php/1/mod_resource/content/0/x.pdf?token=T'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='resource',
            is_embedded_media=False,
        ) is False


# =========================================================================
# assign_modname_for_url
# =========================================================================
class TestAssignModnameForUrl:
    """Pin the modname dispatch contract.

    The original code had 3 nested if/elif/elif branches (lines
    577-590 in the pre-refactor result_builder.py). This function
    collapses them into a single decision point with clear precedence.
    """

    def test_kaltura_url_overrides_to_kalvidres(self):
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        url = 'https://keats.kcl.ac.uk/browseandembed/index/media/entryid/1_abc'
        parts = urlparse(url)
        result = assign_modname_for_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='label',
            is_kaltura=True,
            is_helixmedia=False,
        )
        # The kaltura branch wins over /webservice/ branch
        assert result == 'cookie_mod-kalvidres'

    def test_helixmedia_url_overrides_to_helixmedia(self):
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        url = 'https://keats.kcl.ac.uk/mod/helixmedia/view.php?id=1'
        parts = urlparse(url)
        result = assign_modname_for_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='label',
            is_kaltura=False,
            is_helixmedia=True,
        )
        assert result == 'cookie_mod-helixmedia'

    def test_pluginfile_url_on_moodle_gets_index_mod_prefix(self):
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/x.pdf'
        parts = urlparse(url)
        result = assign_modname_for_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='resource',
            is_kaltura=False,
            is_helixmedia=False,
        )
        # /webservice/ branch → index_mod-description-resource
        assert result == 'index_mod-description-resource'

    def test_other_moodle_domain_url_gets_cookie_mod_prefix(self):
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        url = 'https://keats.kcl.ac.uk/some/internal/page'
        parts = urlparse(url)
        result = assign_modname_for_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='label',
            is_kaltura=False,
            is_helixmedia=False,
        )
        # Not /webservice/ → cookie_mod-description-label
        assert result == 'cookie_mod-description-label'

    def test_external_url_gets_url_description_prefix(self):
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        url = 'https://other.example.com/file.pdf'
        parts = urlparse(url)
        result = assign_modname_for_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='label',
            is_kaltura=False,
            is_helixmedia=False,
        )
        # Not on Moodle domain → url-description-label
        assert result == 'url-description-label'

    def test_kaltura_takes_precedence_over_helixmedia(self):
        """If both flags are True (unusual but possible), Kaltura wins
        because it's checked first in the original code.
        """
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        url = 'https://keats.kcl.ac.uk/some/url'
        parts = urlparse(url)
        result = assign_modname_for_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='label',
            is_kaltura=True,
            is_helixmedia=True,
        )
        assert result == 'cookie_mod-kalvidres'

    def test_original_modname_preserved_as_suffix(self):
        """The original modname should always appear as a suffix in
        the dispatched modname, so the downloader can route the file
        to the right folder.
        """
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        for original in ('resource', 'label', 'page', 'url', 'assign'):
            url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/x.pdf'
            parts = urlparse(url)
            result = assign_modname_for_url(
                url=url,
                url_parts=parts,
                moodle_domain='keats.kcl.ac.uk',
                original_modname=original,
                is_kaltura=False,
                is_helixmedia=False,
            )
            assert result.endswith('-' + original), (
                f'Modname for original={original!r} should end with '
                f'"{original}". Got: {result!r}'
            )


# =========================================================================
# tokenpluginfile.php (Moodle 4.5+) — official mobile app convention
# =========================================================================
class TestTokenPluginfileUrl:
    """Pin the contract for the third file-serving endpoint.

    Moodle 4.5+ adds /tokenpluginfile.php/<accessKey>/<contextid>/...
    as a token-based alternative to the cookie-based /webservice/
    pluginfile.php. The official Moodle mobile app treats it as a
    pluginfile URL (it has the same /pluginfile.php structure with
    an accessKey prefix), and moodle-dl's own UrlHelper.is_pluginfile_url
    recognizes all three forms.

    These tests pin that:
      1. should_skip_url() keeps tokenpluginfile URLs (they are
         downloadable resources, not internal page links)
      2. assign_modname_for_url() dispatches them to the
         index_mod-description-* branch (same as /webservice/pluginfile)
    """

    def test_tokenpluginfile_url_kept_for_resource_module(self):
        """tokenpluginfile.php URL on the Moodle domain must be kept,
        same as webservice/pluginfile.php — it's a downloadable
        resource attachment.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/tokenpluginfile.php/abc123def/2696283/mod_resource/content/0/cw1.pdf'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='resource',
            is_embedded_media=False,
        ) is False

    def test_tokenpluginfile_url_dispatches_to_index_mod(self):
        """tokenpluginfile URL should dispatch to index_mod-description-*
        just like /webservice/pluginfile.php does (the file is
        downloaded via the webservice endpoint with token-aware auth).
        """
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        url = 'https://keats.kcl.ac.uk/tokenpluginfile.php/abc123def/2696283/mod_resource/content/0/cw1.pdf'
        parts = urlparse(url)
        result = assign_modname_for_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='resource',
            is_kaltura=False,
            is_helixmedia=False,
        )
        assert result == 'index_mod-description-resource'

    def test_tokenpluginfile_url_skipped_for_book_module(self):
        """The book-modname exception applies to tokenpluginfile URLs
        too — book chapter images are handled by the HTML localizer.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        url = 'https://keats.kcl.ac.uk/tokenpluginfile.php/abc123def/1/mod_book/chapter/2/img.png'
        parts = urlparse(url)
        assert should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='book',
            is_embedded_media=False,
        ) is True


# =========================================================================
# SSOT alignment with UrlHelper.is_pluginfile_url
# =========================================================================
class TestSsotAlignment:
    """Pin that description_url_extractor agrees with the
    moodle-dl-wide SSOT UrlHelper.is_pluginfile_url() for what
    counts as a 'pluginfile URL'.

    Why: the existing helper in moodle_dl/utils.py accepts 3
    endpoint forms (pluginfile.php, webservice/pluginfile.php,
    tokenpluginfile.php), and the official Moodle mobile app
    uses the same 3 forms. The description_url_extractor must
    not silently disagree with either — that would create
    routes that look right in one layer but fail in another.

    These tests run the same URL set through both
    is_pluginfile_url() and should_skip_url() to verify they
    reach the same answer for the same input shape.
    """

    def test_ssot_pluginfile_url_kept_under_all_three_forms(self):
        """All three official pluginfile forms (standard, webservice,
        token) must be treated identically: kept for non-book modnames.
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        from moodle_dl.utils import UrlHelper

        urls = [
            'https://keats.kcl.ac.uk/pluginfile.php/1/mod_resource/content/0/x.pdf',
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/x.pdf?token=T',
            'https://keats.kcl.ac.uk/tokenpluginfile.php/abc/1/mod_resource/content/0/x.pdf',
        ]
        for url in urls:
            # Both helpers must agree: this IS a pluginfile URL
            assert UrlHelper.is_pluginfile_url(url), f'UrlHelper says NO to {url}'
            # ... and description_url_extractor must KEEP it (for non-book modname)
            parts = urlparse(url)
            kept = not should_skip_url(
                url=url,
                url_parts=parts,
                moodle_domain='keats.kcl.ac.uk',
                original_modname='resource',
                is_embedded_media=False,
            )
            assert kept, f'should_skip_url says SKIP to {url}'

    def test_ssot_assign_modname_agrees_with_webservice_branch(self):
        """All three pluginfile forms must dispatch to the same
        modname prefix (index_mod-description-*) because they all
        go through the webservice endpoint with token-aware auth.
        """
        from moodle_dl.moodle.description_url_extractor import assign_modname_for_url
        modnames = []
        for url in [
            'https://keats.kcl.ac.uk/pluginfile.php/1/mod_resource/content/0/x.pdf',
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/x.pdf?token=T',
            'https://keats.kcl.ac.uk/tokenpluginfile.php/abc/1/mod_resource/content/0/x.pdf',
        ]:
            parts = urlparse(url)
            modnames.append(assign_modname_for_url(
                url=url,
                url_parts=parts,
                moodle_domain='keats.kcl.ac.uk',
                original_modname='resource',
                is_kaltura=False,
                is_helixmedia=False,
            ))
        # All three should be the same modname (they all route to
        # the same download path)
        assert len(set(modnames)) == 1, f'Expected all 3 forms to share modname, got: {modnames}'


# =========================================================================
# Defensive: /pluginfile.php (leading slash) vs pluginfile.php (no slash)
# =========================================================================
class TestLeadingSlashConvention:
    """Pin the moodle-dl-internal convention: pluginfile.php is
    always checked as a path-segment substring, never as a bare
    substring.

    Rationale (per UrlHelper.is_pluginfile_url in moodle_dl/utils.py
    and CoreUrl.isPluginFileUrl in the official Moodle mobile app):
    the URL is only a pluginfile URL if it contains '/pluginfile.php'
    or '/tokenpluginfile.php' as a complete path component. A URL
    like 'https://evil.com/foo-pluginfile.php' (where 'pluginfile.php'
    is a substring of a different path component) must NOT match.

    This convention is the one used by the SSOT helper, and the
    description_url_extractor must agree with it.

    The leading-slash convention closes a real false-positive:
    a URL like /webservice/mypluginfile.php/... contains the substring
    'pluginfile.php' but is NOT a real pluginfile URL.
    """

    def test_pluginfile_php_substring_of_other_word_not_a_pluginfile_url(self):
        """A URL like /webservice/mypluginfile.php/... contains the
        substring 'pluginfile.php' (the file-extension part of
        'mypluginfile.php') but is NOT a real pluginfile URL —
        the leading-slash convention requires '/pluginfile.php' to
        be a complete path component.

        Pin that both the SSOT and the description_url_extractor
        agree on this case: both reject 'mypluginfile.php' as a
        false positive. (The previous substring-match behavior was
        a real bug — it would have let '/webservice/mypluginfile.php/'
        slip through as if it were a real pluginfile URL.)
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        from moodle_dl.utils import UrlHelper
        url = 'https://keats.kcl.ac.uk/webservice/mypluginfile.php/1/x.pdf?token=T'
        # SSOT says NO (leading-slash convention)
        assert not UrlHelper.is_pluginfile_url(url), (
            'SSOT should reject mypluginfile.php (no leading slash '
            'before pluginfile.php)'
        )
        # description_url_extractor must agree
        parts = urlparse(url)
        kept = not should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='resource',
            is_embedded_media=False,
        )
        assert not kept, (
            'description_url_extractor should skip mypluginfile.php '
            '(the leading-slash convention rules it out as a real '
            'pluginfile URL)'
        )

    def test_pluginfile_php_uppercase_not_a_pluginfile_url(self):
        """Pin the case-sensitive convention: /PLUGINFILE.PHP/ is NOT
        a pluginfile URL (PHP file names are case-sensitive on Linux,
        and the official mobile app uses case-sensitive matching).
        """
        from moodle_dl.moodle.description_url_extractor import should_skip_url
        from moodle_dl.utils import UrlHelper
        url = 'https://keats.kcl.ac.uk/PLUGINFILE.PHP/1/mod_resource/content/0/x.pdf'
        assert not UrlHelper.is_pluginfile_url(url), (
            'SSOT must reject uppercase pluginfile URL (case-sensitive)'
        )
        parts = urlparse(url)
        # description_url_extractor must agree: skip the URL
        kept = not should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain='keats.kcl.ac.uk',
            original_modname='resource',
            is_embedded_media=False,
        )
        assert not kept, 'description_url_extractor disagrees with is_pluginfile_url SSOT'
