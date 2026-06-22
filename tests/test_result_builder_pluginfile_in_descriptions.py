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
