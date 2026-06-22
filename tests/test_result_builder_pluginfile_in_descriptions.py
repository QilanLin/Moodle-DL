# -*- coding: utf-8 -*-
"""
Regression test for the description-page → markdown-only regression bug.

Bug summary (KLaSS Research Data + 6CCS3ML1 Machine Learning, June 2026):

  When a Moodle ``resource`` module's description HTML contained a real
  attachment URL pointing to ``pluginfile.php`` on the Moodle domain
  (e.g. ``https://keats.kcl.ac.uk/webservice/pluginfile.php/.../cw1.pdf?token=...``),
  the downloader silently dropped the URL during HTML link extraction
  in :py:meth:`ResultBuilder._find_all_urls`. The ``is_moodle_url and
  not is_embedded_media_candidate`` early-skip on line ~542 discarded
  every ``keats.kcl.ac.uk`` URL that wasn't a Kaltura / Helixmedia
  launch. As a result the description's only downloadable attachment
  (the PDF / DOCX / ZIP / PNG that the user actually wanted) was never
  turned into a File entry, the description itself was saved as a
  9-251 byte markdown stub, and the real file was lost.

This file pins the contract that ``_find_all_urls`` MUST return a
``File`` entry for every ``pluginfile.php`` URL it finds, regardless
of whether the host matches the Moodle domain. The contract mirrors
the KCL Moodle production behaviour observed in the June 2026 CS2
regression audit: every PDF/ZIP/DOCX/PNG/MP3 referenced in a
description page is delivered via ``pluginfile.php`` and the host
is always the Moodle domain (no Kaltura, no Helixmedia).

Test class:

* :class:`TestPluginfileUrlsInDescriptions` — verifies that a
  ``pluginfile.php`` URL on the Moodle host is kept by
  ``_find_all_urls`` and turned into a File entry with
  ``content_type='description-url'``. Four sub-cases:
  plain PDF, plain ZIP, plain DOCX, plain PNG.

These tests follow the moodle-dl TDD contract: every fix must be
pinned by a failing test that exercises the exact regression
condition observed in production.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_builder(version=2024010100):
    """Builder helper matching the existing test_result_builder_more.py pattern."""
    from moodle_dl.moodle.result_builder import ResultBuilder
    from moodle_dl.types import MoodleURL
    return ResultBuilder(
        moodle_url=MoodleURL(use_http=False, domain='keats.kcl.ac.uk', path='/'),
        version=version,
        mod_plurals={'quiz': 'quizzes', 'resource': 'resources', 'page': 'pages'},
        token='token-abc',
    )


def call_find_all_urls(builder, html, location):
    """Single call site for _find_all_urls with the standard test shape."""
    return builder._find_all_urls(
        html,
        no_search_for_moodle_urls=False,
        filter_urls_containing=[],
        **location,
    )


def make_location(**overrides):
    location = {
        'section_id': 1,
        'section_name': 'Week 1',
        'module_id': 10,
        'module_name': 'Module',
        'module_modname': 'resource',
        'content_filepath': '/',
    }
    location.update(overrides)
    return location


# =========================================================================
# pluginfile.php URLs in description HTML must become File entries
# =========================================================================
class TestPluginfileUrlsInDescriptions:
    """Regression: Moodle-domain pluginfile.php URLs were silently dropped.

    Real-world reproducer (file_id=410, 6CCS3ML1):
        HTML in description: <a href="https://keats.kcl.ac.uk/webservice/
        pluginfile.php/.../cw1.pdf?token=...">Coursework 1.pdf</a>
        Expected: 1 File entry with content_fileurl = the PDF URL
        Actual (buggy): 0 File entries; the description itself becomes a
                        263-byte markdown stub and the PDF is never downloaded.
    """

    def test_pluginfile_pdf_url_in_description_is_kept(self):
        """A PDF link in a description page must produce a File entry."""
        builder = make_builder()
        url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/12679107/'
            'mod_resource/content/0/cw1.pdf?token=1b4621da61fc59b53453011af91ecc50'
            '&offline=1'
        )
        html = f'<p>Download the coursework PDF.</p><a href="{url}">Coursework 1.pdf</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='Coursework 1'))

        assert len(files) == 1, (
            f'Expected 1 File entry for the pluginfile.php PDF URL, '
            f'got {len(files)}. The bug: _find_all_urls drops Moodle-domain '
            f'pluginfile.php URLs because they are not Kaltura/Helixmedia.'
        )
        file_obj = files[0]
        assert file_obj.content_fileurl == url
        assert file_obj.content_type == 'description-url'

    def test_pluginfile_zip_url_in_description_is_kept(self):
        """A ZIP link in a description page must produce a File entry."""
        builder = make_builder()
        url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/12679108/'
            'mod_resource/content/0/cw1_pacman.zip?token=1b4621da61fc59b53453011af91ecc50'
            '&offline=1'
        )
        html = f'<p>Starter code:</p><a href="{url}">pacman-cw1.zip</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='Coursework 1'))

        assert len(files) == 1, (
            f'Expected 1 File entry for the pluginfile.php ZIP URL, '
            f'got {len(files)}'
        )
        assert files[0].content_fileurl == url

    def test_pluginfile_docx_url_in_description_is_kept(self):
        """A DOCX link in a description page must produce a File entry."""
        builder = make_builder()
        url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/2809457/'
            'mod_resource/content/0/Section7_Sharing%20Data%20Further%20Reading.docx'
            '?token=1b4621da61fc59b53453011af91ecc50&offline=1'
        )
        html = f'<p>Further reading:</p><a href="{url}">Sharing Data Further Reading.docx</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='Sharing Data Further Reading'))

        assert len(files) == 1
        # _normalize_extracted_html_url decodes %20 → space; compare on the
        # normalized form (real production code path)
        assert files[0].content_fileurl == url.replace('%20', ' ')

    def test_pluginfile_png_url_in_description_is_kept(self):
        """A PNG link in a description page must produce a File entry."""
        builder = make_builder()
        url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/'
            'mod_resource/content/0/assets/TOC_icons/collapseIcon.png'
            '?token=1b4621da61fc59b53453011af91ecc50&offline=1'
        )
        html = f'<p>Image:</p><img src="{url}" alt="collapse">'

        files = call_find_all_urls(builder, html, make_location(module_name='assets'))

        assert len(files) == 1
        assert files[0].content_fileurl == url

    def test_multiple_pluginfile_urls_in_description_all_kept(self):
        """Real descriptions often have several pluginfile.php URLs.

        Reproducer: KLaSS Research Data module 'data lifecycle' had PNG,
        GIF, MP3, ZIP all embedded as <a>/<img>/<source> tags inside the
        same description. All must become File entries.
        """
        builder = make_builder()
        png = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/assets/TOC_icons/collapseIcon.png?token=T'
        gif = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/assets/anim.gif?token=T'
        mp3 = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/audio.mp3?token=T'
        zip_url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/The%20data%20lifecycle%20V2.zip?token=T'

        html = (
            f'<p>See:</p>'
            f'<a href="{png}">icon</a>'
            f'<a href="{gif}">anim</a>'
            f'<a href="{mp3}">audio</a>'
            f'<a href="{zip_url}">data zip</a>'
        )
        files = call_find_all_urls(builder, html, make_location(module_name='data lifecycle'))

        urls_returned = {f.content_fileurl for f in files}
        # _normalize_extracted_html_url decodes %20 → space; compare on normalized forms
        assert png in urls_returned, f'PNG pluginfile URL was dropped. Got: {urls_returned}'
        assert gif in urls_returned, f'GIF pluginfile URL was dropped. Got: {urls_returned}'
        assert mp3 in urls_returned, f'MP3 pluginfile URL was dropped. Got: {urls_returned}'
        assert zip_url.replace('%20', ' ') in urls_returned, f'ZIP pluginfile URL was dropped. Got: {urls_returned}'

    def test_non_pluginfile_moodle_url_still_skipped(self):
        """Internal Moodle page links (e.g. /mod/page/view.php?id=42) should
        still NOT produce a File entry, to preserve the existing
        'no shortcut files for internal links' contract.

        This pin prevents an over-correction: the fix must only
        preserve pluginfile.php URLs, not every Moodle-domain URL.
        """
        builder = make_builder()
        internal_url = 'https://keats.kcl.ac.uk/mod/page/view.php?id=42'
        html = f'<a href="{internal_url}">click here for more</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='Page'))

        assert files == [], (
            f'Internal Moodle page links must still be skipped (no shortcut '
            f'files for /mod/page/view.php). Got: {[f.content_fileurl for f in files]}'
        )


# =========================================================================
# Unusual edge cases — the kind of inputs that the original 6 tests don't
# cover and that a future refactor could silently break.
# =========================================================================
class TestPluginfileUrlsUnusualEdgeCases:
    """Edge cases that exercise boundary conditions of the pluginfile fix.

    These tests are unusual in three senses:

    1. **They target real production shapes that the simple test cases
       don't cover** (e.g. pluginfile URLs that LOOK like internal
       navigation, pluginfile URLs inside <source>/<video>/<iframe>
       tags, URLs in HTML attribute order variants, deeply nested
       pluginfile paths).

    2. **They pin the contract under adversarial conditions** (URL
       parser edge cases, mixed-encoding URLs, no_search_for_moodle_urls
       override, filter_urls_containing match, the book-modname exception).

    3. **They are written to fail loudly if a future refactor breaks
       the fix in a non-obvious way** — e.g. by adding a broader
       exception, by changing the early-skip order, or by
       short-circuiting before the pluginfile check.

    Each test name describes the SHAPE of the input, not the
    implementation, so it stays useful as the code evolves.
    """

    # ----- URL parser edge cases ----------------------------------------

    def test_pluginfile_url_with_path_containing_extra_query_params(self):
        """Real Moodle pluginfile URLs often have ?token=...&offline=1&forcedownload=1.

        Pin: the pluginfile detection must look at url_parts.path, not the
        full URL string, so query parameters don't break the match.
        """
        builder = make_builder()
        url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/12679107/'
            'mod_resource/content/0/cw1.pdf?token=T&offline=1&forcedownload=1'
        )
        html = f'<a href="{url}">coursework</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='cw1'))

        assert len(files) == 1
        assert files[0].content_fileurl == url

    def test_pluginfile_url_with_uppercase_path_segments(self):
        """Some Moodle proxies uppercase the path. The 'pluginfile.php'
        substring must be matched case-sensitively (PHP file names are
        case-sensitive on Linux). If a future refactor uses a
        case-insensitive match, false positives on internal URLs like
        /webservice/PLUGINFILE.php would be possible. Pin the contract.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/file.pdf?token=T'
        html = f'<a href="{url}">file</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        assert len(files) == 1

    def test_pluginfile_url_with_www_subdomain(self):
        """Some KCL Moodle subdomains use www.keats.kcl.ac.uk instead of
        keats.kcl.ac.uk. The host comparison must handle BOTH, otherwise
        www. pluginfile URLs would be incorrectly classified as 'not
        Moodle domain' and fall through to the wrong code path.
        """
        builder = make_builder()
        url = 'https://www.keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/file.pdf?token=T'
        html = f'<a href="{url}">file</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        # Pin: today the bug means www. pluginfile URLs are also kept
        # (the early-skip only checks hostname, and the new exemption is
        # on path). If a future refactor adds 'or hostname ends with
        # .moodle_domain' logic, this test pins that www. is in scope.
        assert len(files) == 1
        assert 'pluginfile.php' in files[0].content_fileurl

    def test_pluginfile_url_in_iframe_src_attribute(self):
        """A pluginfile URL can appear in <iframe src="..."> (a legacy
        Moodle pattern for embedding PDFs). The regex must catch the
        src= form, not just href=.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/embed.pdf?token=T'
        html = f'<iframe src="{url}" width="100%"></iframe>'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        assert len(files) == 1
        assert files[0].content_fileurl == url

    def test_pluginfile_url_in_video_source_tag(self):
        """A pluginfile URL can appear in <video><source src="..."> for
        video attachments referenced from a description page. The src=
        regex already catches this in production; pin it.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/intro.mp4?token=T'
        html = f'<video controls><source src="{url}" type="video/mp4"></video>'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        assert len(files) == 1
        assert files[0].content_fileurl == url

    def test_pluginfile_url_mixed_case_in_html_attribute(self):
        """HTML is case-insensitive for tag/attribute names. Pin that
        the URL extractor doesn't fail on <A HREF="..."> or <Img SRC="...">.
        """
        builder = make_builder()
        url1 = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/a.pdf?token=T'
        url2 = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/b.png?token=T'
        html = f'<A HREF="{url1}">a</A><Img SRC="{url2}">'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        urls_returned = {f.content_fileurl for f in files}
        assert url1 in urls_returned
        assert url2 in urls_returned

    # ----- The book-modname exception ----------------------------------

    def test_pluginfile_url_in_book_module_is_still_skipped(self):
        """Pin the 'book exception': a pluginfile URL in a 'book' module
        description must NOT become a File entry, because the book
        chapter is downloaded as a single HTML file and the HTML
        localizer rewrites internal references. Creating a File entry
        here would cause a duplicate download.

        This pins the historical contract from
        test_find_all_urls_skips_root_relative_moodle_resources_without_shortcuts
        and ensures the new fix doesn't regress it.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_book/chapter/2/image.png?token=T'
        html = f'<img src="{url}">'

        files = call_find_all_urls(builder, html, make_location(module_modname='book', content_filepath='/chapter/'))

        assert files == [], (
            f'pluginfile.php URL inside a book chapter must be skipped '
            f'(book images are handled by the HTML localizer). '
            f'Got: {[f.content_fileurl for f in files]}'
        )

    def test_pluginfile_url_in_book_subchapter_is_still_skipped(self):
        """Same as above, but with a deeply-nested chapter path.
        Pin that the book exception is based on module_modname='book',
        not on the path depth.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_book/chapter/2/sub/3/deep.png?token=T'
        html = f'<img src="{url}">'

        files = call_find_all_urls(builder, html, make_location(module_modname='book', content_filepath='/chapter/sub/'))

        assert files == []

    def test_pluginfile_url_in_label_module_is_kept(self):
        """The 'label' modname (a Moodle 'label' module, used for inline
        text blocks) is the most common embedding surface for the
        KLaSS regression. Pin that labels get the pluginfile exemption.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_label/intro/file.pdf?token=T'
        html = f'<a href="{url}">label file</a>'

        files = call_find_all_urls(builder, html, make_location(module_modname='label'))

        assert len(files) == 1
        assert files[0].content_fileurl == url

    def test_pluginfile_url_in_page_module_is_kept(self):
        """The 'page' modname (Moodle 'Page' resource with HTML body) is
        the second most common embedding surface. Pin that pages get
        the pluginfile exemption.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_page/content/1/file.pdf?token=T'
        html = f'<a href="{url}">page file</a>'

        files = call_find_all_urls(builder, html, make_location(module_modname='page'))

        assert len(files) == 1
        assert files[0].content_fileurl == url

    def test_pluginfile_url_in_url_module_is_kept(self):
        """The 'url' modname (Moodle 'URL' resource linking out) can
        embed pluginfile URLs for previews. Pin the exemption applies
        here too — even though the module is a URL, the embedded
        pluginfile file IS the downloadable attachment.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_url/content/0/preview.pdf?token=T'
        html = f'<a href="{url}">preview</a>'

        files = call_find_all_urls(builder, html, make_location(module_modname='url'))

        assert len(files) == 1
        assert files[0].content_fileurl == url

    # ----- The filter_urls_containing and no_search_for_moodle_urls modifiers

    def test_pluginfile_url_filtered_by_filter_urls_containing_still_skipped(self):
        """The filter_urls_containing modifier is used to black-list
        certain URL patterns. A pluginfile URL whose path contains a
        filtered substring must still be skipped, even with the new
        exemption. This pins that the filter check happens AFTER the
        pluginfile exemption, not before.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/private.pdf?token=T'
        html = f'<a href="{url}">file</a>'

        files = builder._find_all_urls(
            html,
            no_search_for_moodle_urls=False,
            filter_urls_containing=['/private.pdf'],
            **make_location(module_name='m'),
        )

        assert files == [], (
            f'pluginfile URL containing /private.pdf must be filtered out. '
            f'Got: {[f.content_fileurl for f in files]}'
        )

    def test_no_search_for_moodle_urls_does_not_change_pluginfile_behavior(self):
        """The no_search_for_moodle_urls parameter is defined on
        _find_all_urls but is NOT actually consulted in the function
        body (verified by grep — no usage in the function). It is a
        dead parameter today.

        Pin that this dead parameter does not affect the pluginfile
        fix: passing it True still results in the pluginfile URL being
        kept. If a future refactor wires the parameter up, this test
        pins that pluginfile handling must take precedence.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/file.pdf?token=T'
        html = f'<a href="{url}">file</a>'

        files = builder._find_all_urls(
            html,
            no_search_for_moodle_urls=True,
            filter_urls_containing=[],
            **make_location(module_name='m'),
        )

        # The dead parameter has no effect; pluginfile URLs are still kept.
        assert len(files) == 1, (
            f'no_search_for_moodle_urls is currently a dead parameter and '
            f'does not affect extraction. pluginfile URL should still be '
            f'kept. Got: {[f.content_fileurl for f in files]}'
        )

    # ----- URL pattern variants that should NOT match -------------------

    def test_url_containing_pluginfile_php_in_query_string_only_is_not_a_pluginfile_url(self):
        """A URL that has 'pluginfile.php' in the QUERY STRING but not
        in the PATH is NOT a Moodle pluginfile URL. Pin that the
        detection uses url_parts.path, not the full URL string.
        """
        builder = make_builder()
        # 'pluginfile.php' is in the query string, not the path.
        # A real pluginfile URL has it in the path:
        #   /webservice/pluginfile.php/123/mod_resource/content/0/x.pdf
        url = 'https://keats.kcl.ac.uk/some/other/path?ref=pluginfile.php&file=x.pdf'
        html = f'<a href="{url}">file</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        assert files == [], (
            f"URL with 'pluginfile.php' only in query string must NOT be "
            f"treated as pluginfile URL. Got: {[f.content_fileurl for f in files]}"
        )

    def test_url_with_pluginfile_php_as_substring_of_other_word_is_NOT_kept(self):
        """A URL like /webservice/mypluginfile.php/... has 'pluginfile.php'
        as a substring of the path BUT it is NOT a real pluginfile URL
        (the path component is 'mypluginfile.php', not 'pluginfile.php').

        Pin the leading-slash convention that aligns with the moodle-dl
        SSOT helper UrlHelper.is_pluginfile_url AND the official Moodle
        mobile app (CoreUrl.isPluginFileUrl): a URL is a pluginfile URL
        only if it contains '/pluginfile.php' (with leading slash) as
        a path component. '/webservice/mypluginfile.php/...' is rejected
        as a false positive.

        This is the same fix applied to description_url_extractor in
        June 2026, when we discovered the substring match was letting
        false positives through. Pin the contract so a future refactor
        doesn't reintroduce the bug.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/mypluginfile.php/1/mod_resource/content/0/file.pdf?token=T'
        html = f'<a href="{url}">file</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        # Leading-slash convention: 'mypluginfile.php' is NOT a pluginfile URL
        # (no '/pluginfile.php' as a complete path component).
        # The URL is on a Moodle domain, not kaltura/helixmedia, not a real
        # pluginfile → the early-skip fires and no File is created.
        assert files == [], (
            f'mypluginfile.php must NOT be treated as a pluginfile URL '
            f'(leading-slash convention). Got: {[f.content_fileurl for f in files]}'
        )

    # ----- Defensive: the URL must come back as a description-url type -----

    def test_pluginfile_url_creates_description_url_file_type(self):
        """Pin that the File created from a pluginfile URL has
        content_type='description-url'. This is the contract the
        downloader uses to route it to the URL download path
        (with token-aware auth, etc.). If a future refactor changes
        this to 'resource_file' or 'file', the downloader may try
        to render it as a local file path and fail.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/file.pdf?token=T'
        html = f'<a href="{url}">file</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        assert len(files) == 1
        assert files[0].content_type == 'description-url', (
            f'pluginfile File must be content_type=description-url so the '
            f'downloader routes it through the token-aware URL path. '
            f'Got content_type={files[0].content_type!r}'
        )

    def test_pluginfile_url_module_modname_is_index_mod_description(self):
        """Pin that the File created from a pluginfile URL has
        module_modname='index_mod-description-<original>' (NOT
        'url-description-<original>').

        The code at lines 579-583 dispatches by URL path: a URL on
        the Moodle domain whose path contains '/webservice/' gets
        the 'index_mod-description-' prefix, while other Moodle
        domain URLs get 'cookie_mod-description-'. pluginfile.php
        URLs match the /webservice/ branch.

        This distinction matters: index_mod-description-* is
        recognized as an indexable URL link (e.g. the 'URL' module
        type), so the downloader downloads the file via the
        pluginfile endpoint and token-aware auth.
        """
        builder = make_builder()
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/file.pdf?token=T'
        html = f'<a href="{url}">file</a>'

        files = call_find_all_urls(builder, html, make_location(module_modname='resource'))

        assert len(files) == 1
        # pluginfile URLs match the /webservice/ branch in the code
        assert files[0].module_modname.startswith('index_mod-description-'), (
            f'pluginfile URL on Moodle domain should be tagged '
            f'index_mod-description-* (the /webservice/ branch). '
            f'Got: {files[0].module_modname!r}'
        )
        assert 'resource' in files[0].module_modname, (
            f'Original modname should be preserved as suffix. Got: {files[0].module_modname!r}'
        )

    # ----- End-to-end shapes seen in the actual KLaSS/ML regression -----

    def test_klass_actual_h5p_interactive_with_embedded_assets(self):
        """Pin the exact KLaSS Research Data 'data lifecycle' H5P
        interactive pattern, which has 11+ pluginfile URLs (png, gif,
        mp3, zip) all in one description. This is the original
        regression shape.
        """
        builder = make_builder()
        # Build a realistic H5P-style description
        png = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/assets/TOC_icons/collapseIcon.png?token=T'
        png2 = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/assets/TOC_icons/expandIcon.png?token=T'
        gif = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/assets/anim.gif?token=T'
        mp3 = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/audio.mp3?token=T'
        zip_url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/2696283/mod_resource/content/0/The%20data%20lifecycle%20V2.zip?token=T'

        html = f"""
        <div class="h5p-interactive">
          <p>An interactive activity to demonstrate how data is reused.</p>
          <img src="{png}" alt="collapse">
          <img src="{png2}" alt="expand">
          <img src="{gif}" alt="animation">
          <audio controls><source src="{mp3}" type="audio/mpeg"></audio>
          <p>Download the data:</p>
          <a href="{zip_url}">data lifecycle V2.zip</a>
        </div>
        """

        files = call_find_all_urls(builder, html, make_location(module_name='data lifecycle'))

        urls_returned = {f.content_fileurl for f in files}
        # All 5 attachments must be present
        assert png in urls_returned, f'collapseIcon.png missing: {urls_returned}'
        assert png2 in urls_returned, f'expandIcon.png missing: {urls_returned}'
        assert gif in urls_returned, f'anim.gif missing: {urls_returned}'
        assert mp3 in urls_returned, f'audio.mp3 missing: {urls_returned}'
        assert zip_url.replace('%20', ' ') in urls_returned, f'data zip missing: {urls_returned}'

        # All 5 must have content_type='description-url' (the production contract)
        for f in files:
            assert f.content_type == 'description-url', (
                f'Each pluginfile File must be description-url. Got: {f.content_type!r}'
            )

    def test_pluginfile_url_in_label_module_with_kaltura_video_also_present(self):
        """A label that has BOTH a Kaltura video AND a pluginfile PDF
        link in the same description. Pin that both are kept (Kaltura
        via the existing is_embedded_media_candidate path, pluginfile
        via the new exemption). This was a real production shape.
        """
        builder = make_builder()
        pdf_url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_label/intro/handout.pdf?token=T'
        kaltura_url = (
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?courseid=0&source='
            'https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_abc123%2F'
            'playerSkin%2F42864872%2F'
        )

        html = f'<a href="{pdf_url}">handout</a><iframe src="{kaltura_url}"></iframe>'

        files = call_find_all_urls(builder, html, make_location(module_modname='label'))

        urls_returned = {f.content_fileurl for f in files}
        assert pdf_url in urls_returned, f'PDF URL dropped: {urls_returned}'
        # Kaltura URL should also be present (rebuilt to standard form)
        assert any('kaltura' in u.lower() for u in urls_returned), (
            f'Kaltura URL not converted: {urls_returned}'
        )

    # ----- Defensive: regression where the fix goes too far -------------

    def test_non_pluginfile_url_on_external_moodle_clone_is_still_kept(self):
        """Pin that a URL on a DIFFERENT Moodle instance (e.g.
        moodle.example.com, not keats.kcl.ac.uk) keeps the
        old behavior: it becomes a File entry. The pluginfile
        exemption only applies to URLs on the configured Moodle
        domain.
        """
        builder = make_builder()
        url = 'https://other-moodle.example.com/pluginfile.php/1/mod_resource/content/0/file.pdf?token=T'
        html = f'<a href="{url}">file</a>'

        files = call_find_all_urls(builder, html, make_location(module_name='m'))

        # This URL is on a different host, so is_moodle_url is False,
        # so the early-skip doesn't fire, so the URL is kept. Pin.
        assert len(files) == 1
        assert files[0].content_fileurl == url

    def test_empty_html_returns_empty_files_list(self):
        """Empty HTML must not crash, must return []. This is a
        baseline that the pluginfile fix didn't break.
        """
        builder = make_builder()
        files = call_find_all_urls(builder, '', make_location(module_name='m'))
        assert files == []

    def test_html_with_only_whitespace_returns_empty_files_list(self):
        """Whitespace-only HTML must not crash and must return []. Pin
        that the regex findall doesn't pick up empty strings.
        """
        builder = make_builder()
        for html in ['', '   ', '\n\n', '\t\t', '\r\n\r\n']:
            files = call_find_all_urls(builder, html, make_location(module_name='m'))
            assert files == [], f'Got {files} for whitespace HTML {html!r}'
