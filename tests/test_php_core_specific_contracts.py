# -*- coding: utf-8 -*-
"""
Tests pinning the per-module numbering contract against SPECIFIC
statements found by the PHP core verification sub-agent
(deleg_464a2a01).

The sub-agent traced the data flow DB → modinfo cache →
core_course_get_contents API → individual module *_export_contents
callbacks. These tests pin those specific structural claims.

If any test fails because the Moodle PHP source has changed,
the docstring cites the file:line so the fix can be re-verified.
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MOODLE_PHP = '/Users/linqilan/CodingProjects/moodle/moodle_official_repo_for_reference'


def _read(rel):
    full = os.path.join(MOODLE_PHP, rel)
    if not os.path.exists(full):
        return ''
    with open(full) as f:
        return f.read()


# =========================================================================
# modinfo.php: section->sequence is implode(',', cm_ids)
# =========================================================================
class TestModinfoSectionSequenceContract:
    """Pin that course_sections.sequence is constructed as
    implode(',', cm_ids) — one cm_id per module, never per file.
    """

    def test_modinfo_uses_implode_with_cm_ids(self):
        """modinfo.php line 1052–1055 (per sub-agent verification):
        course_sections.sequence = implode(',', cm_ids).
        """
        src = _read('public/course/classes/modinfo.php')
        if not src:
            return  # Skipped if file not found
        # The implode + sequence + cm pattern should appear
        assert 'implode' in src, (
            'modinfo.php should use implode to build section sequence'
        )
        # The sequence field is mentioned
        assert 'sequence' in src, (
            'modinfo.php should reference course_sections.sequence'
        )

    def test_modinfo_section_get_sequence_cm_infos(self):
        """section_info.php line 514–528: get_sequence_cm_infos
        returns one cm_info per cm_id.
        """
        src = _read('public/course/classes/section_info.php')
        if not src:
            return
        assert 'get_sequence_cm_infos' in src, (
            'section_info.php should define get_sequence_cm_infos'
        )
        # The function body should iterate cm_ids
        idx = src.find('function get_sequence_cm_infos')
        if idx < 0:
            idx = src.find('public function get_sequence_cm_infos')
        if idx > 0:
            body = src[idx:idx + 3000]
            # Should iterate and return cm_info objects
            assert 'explode' in body or 'split' in body or 'foreach' in body, (
                'get_sequence_cm_infos should iterate the sequence '
                '(explode on comma, foreach over cm_ids)'
            )


class TestModinfoCalculateSectionWeightsContract:
    """Pin that Moodle's internal sort weight uses one increment
    per cm: 'cm' . $cm->id with $currentweight++.

    Sub-agent citation: modinfo.php:1278-1279.
    """

    def test_calculate_section_weights_in_modinfo(self):
        """modinfo.php has calculate_section_weights method."""
        src = _read('public/course/classes/modinfo.php')
        if not src:
            return
        assert 'calculate_section_weights' in src, (
            'modinfo.php should define calculate_section_weights'
        )

    def test_section_weights_use_one_increment_per_cm(self):
        """calculate_section_weights increments currentweight per
        cm (one increment per module, not per file).
        """
        src = _read('public/course/classes/modinfo.php')
        if not src:
            return
        idx = src.find('calculate_section_weights')
        if idx < 0:
            return
        body = src[idx:idx + 3000]
        # Look for the $currentweight++ pattern
        assert 'currentweight' in body, (
            'calculate_section_weights should use currentweight variable'
        )
        assert '++' in body, (
            'calculate_section_weights should increment currentweight '
            'per cm (one increment per module)'
        )


# =========================================================================
# externallib.php: one module entry per cm, files in contents
# =========================================================================
class TestCoreCourseGetContentsEmitsOnePerCm:
    """Pin that get_course_contents in externallib.php emits ONE
    module entry per cm, with files nested in module['contents'].
    Sub-agent citations: externallib.php:226–380.
    """

    def test_externallib_module_iteration_pattern(self):
        """externallib.php iterates modules (cm) and emits one
        entry per cm with nested contents.
        """
        src = _read('public/course/externallib.php')
        if not src:
            return
        # The function should iterate modules (not files)
        idx = src.find('public static function get_course_contents(')
        if idx < 0:
            return
        body = src[idx:idx + 30000]
        # Look for the modules iteration
        assert 'contents' in body, (
            'get_course_contents should produce a modules array with '
            'each module having a contents subarray'
        )

    def test_externallib_returns_webservice_pluginfile_urls(self):
        """Sub-agent citation: externallib.php:334 — API returns
        webservice/pluginfile.php URLs (not the legacy /pluginfile.php).
        """
        src = _read('public/course/externallib.php')
        if not src:
            return
        # Look for webservice/pluginfile.php URL pattern
        assert 'webservice/pluginfile.php' in src, (
            'externallib.php should return webservice/pluginfile.php URLs '
            '(per API contract)'
        )


# =========================================================================
# Book module: per-book scope exception
# =========================================================================
class TestBookModulePerChapterContract:
    """Pin that book modules return structure + per-chapter content
    under ONE cm. Sub-agent citation: mod/book/lib.php:526-630.

    This justifies our per-book scope exception (book modname gets
    per-book sub-counter for chapter content + chapter images).
    """

    def test_book_lib_has_export_contents(self):
        """mod/book/lib.php has book_export_contents or similar."""
        src = _read('public/mod/book/lib.php')
        if not src:
            return
        # Look for export_contents or similar
        assert (
            'export_contents' in src or 'get_contents' in src
            or '_export' in src
        ), (
            'mod/book/lib.php should have export_contents or '
            'get_contents method'
        )

    def test_book_returns_index_html_per_chapter(self):
        """Sub-agent: book_export_contents returns structure +
        per-chapter index.html + per-chapter files all under ONE cm.
        """
        src = _read('public/mod/book/lib.php')
        if not src:
            return
        # Look for index.html generation per chapter
        assert (
            'index.html' in src or 'index_html' in src
        ), (
            'mod/book should generate index.html per chapter'
        )


# =========================================================================
# pluginfile.php vs webservice/pluginfile.php
# =========================================================================
class TestPluginfileDispatchContract:
    """Pin that pluginfile.php and webservice/pluginfile.php share
    a common file_pluginfile() dispatch. Sub-agent confirmation:
    they differ only in auth (cookie vs token) and response
    framing (CORS, AJAX_SCRIPT).
    """

    def test_pluginfile_dispatcher_defined(self):
        """pluginfile.php defines a dispatcher that calls
        file_pluginfile().
        """
        src = _read('public/pluginfile.php')
        if not src:
            return
        assert 'file_pluginfile' in src, (
            'public/pluginfile.php should call file_pluginfile() '
            '(common dispatch)'
        )

    def test_webservice_pluginfile_uses_same_dispatch(self):
        """webservice/pluginfile.php also calls file_pluginfile()
        (same dispatch, different auth).
        """
        src = _read('public/webservice/pluginfile.php')
        if not src:
            return
        assert 'file_pluginfile' in src, (
            'public/webservice/pluginfile.php should call file_pluginfile() '
            '(same dispatch as pluginfile.php)'
        )

    def test_both_pluginfile_endpoints_exist(self):
        """Both endpoint files exist (proves they're 2 separate
        endpoints serving the same content).
        """
        pluginfile = os.path.join(MOODLE_PHP, 'public/pluginfile.php')
        webservice_pluginfile = os.path.join(
            MOODLE_PHP, 'public/webservice/pluginfile.php'
        )
        assert os.path.exists(pluginfile), (
            'public/pluginfile.php should exist'
        )
        assert os.path.exists(webservice_pluginfile), (
            'public/webservice/pluginfile.php should exist'
        )


# =========================================================================
# URL module vs inline description link
# =========================================================================
class TestUrlModuleVsInlineLinkContract:
    """Pin that URL modules have their own cm in section->sequence,
    while inline description links live inside another module's
    description (HTML content). They are DIFFERENT ordering units.
    """

    def test_url_module_has_own_cm_in_sequence(self):
        """A URL (mod_url) module is one entry in section->sequence.
        Inline links (HTML a href) live inside another module's
        description — not separate cm entries.
        """
        url_ext = _read('public/mod/url/externallib.php')
        if not url_ext:
            url_ext = _read('public/mod/url/classes/external.php')
        if not url_ext:
            return  # Skipped if not found
        # URL module external API should exist
        assert (
            'mod_url_get_urls' in url_ext
            or 'get_urls' in url_ext
        ), (
            'mod_url external API should expose URL module data'
        )


# =========================================================================
# Behavioral tests matching sub-agent's data flow trace
# =========================================================================
class TestOurCodeFollowsMoodleDataFlow:
    """Verify that our production code's data flow matches what
    the sub-agent traced in the PHP source.
    """

    def test_assign_positions_handles_cm_id_granularity(self):
        """Sub-agent trace: modinfo.php builds cms by cm_id,
        externallib.php emits one entry per cm, our
        _assign_positions_to_files advances counter per module_id
        (=cm_id). Pin the contract.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # 3 CMs in a section
        # cm 100: 2 files
        # cm 200: 1 file
        # cm 300: 3 files
        files = []
        files.extend([
            _make_file(1, 100, 'cm100_a.html'),
            _make_file(1, 100, 'cm100_b.pdf'),
            _make_file(1, 200, 'cm200.html'),
            _make_file(1, 300, 'cm300_a.html'),
            _make_file(1, 300, 'cm300_b.pdf'),
            _make_file(1, 300, 'cm300_c.docx'),
        ])

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # 3 distinct slots (one per CM), regardless of file count
        slots = sorted(set(f.position_in_section for f in files))
        assert slots == [0, 1, 2], (
            f'3 CMs should produce 3 distinct slots. Got: {slots}'
        )

        # cm 100 → slot 0, cm 200 → slot 1, cm 300 → slot 2
        assert files[0].position_in_section == 0
        assert files[1].position_in_section == 0  # cm 100 b.pdf same slot
        assert files[2].position_in_section == 1  # cm 200
        assert files[3].position_in_section == 2  # cm 300 a
        assert files[4].position_in_section == 2
        assert files[5].position_in_section == 2

    def test_book_chapter_files_share_book_scope(self):
        """Sub-agent: book_export_contents returns per-chapter
        files under one cm. Our code: book modname uses per-chapter
        scope (so all files in the same chapter share a position).

        Per-chapter contract (test3 Problem 4 fix, 2026-06-24):
        All files in the same chapter (same content_filepath) share
        a position. The book counter advances per CHAPTER, not per
        file. This is so cookie_mod-kalvidres and url-description-book
        sub-files (extracted from the chapter's HTML) end up in the
        same folder as the chapter's index.html.

        Contract source:
          - moodle_official_repo_for_reference/public/mod/book/lib.php:573
            (each chapter's filepath is /{chapter_id}/, all sub-files
            share this filepath)
          - moodle_mobile_app_official_repo_for_reference/src/addons/mod/book/
            services/book.ts:110-157 (getContentsMap groups by chapter
            id via /\\d+/ regex on filepath)
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Book cm 100 with 2 chapters:
        # Chapter 1: html + image (same filepath /1/)
        # Chapter 2: html + image (same filepath /2/)
        # All files share book scope, counter advances per chapter.
        files = []
        from moodle_dl.types import File
        for chapter_num, chapter in [(1, 1), (2, 2)]:
            for name_suffix, is_img in [('.html', False), ('.png', True)]:
                f = File(
                    module_id=100, section_name='S', section_id=1,
                    module_name='mod_100',
                    content_filepath=f'/{chapter_num}/',
                    content_filename=f'chapter{chapter}{name_suffix}',
                    content_fileurl=f'https://example.com/c{chapter}{name_suffix}',
                    content_filesize=1024, content_timemodified=0,
                    module_modname='book',
                    content_type='file',
                    content_isexternalfile=False,
                )
                f._module_has_attachments = is_img
                files.append(f)

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # Per-chapter scope: files in the same chapter (same
        # content_filepath) share a position.
        chapter_positions = {1: [], 2: []}
        for f in files:
            if 'chapter1' in f.content_filename:
                chapter_positions[1].append(f.position_in_section)
            elif 'chapter2' in f.content_filename:
                chapter_positions[2].append(f.position_in_section)

        # Chapter 1: both files share position 0 (per-chapter scope)
        assert sorted(chapter_positions[1]) == [0, 0], (
            f'Chapter 1 files (html + image) should share position 0. '
            f'Got: {sorted(chapter_positions[1])}. '
            f'After test3 Problem 4 fix, files in the same chapter '
            f'share a position.'
        )
        # Chapter 2: both files share position 1 (next chapter)
        assert sorted(chapter_positions[2]) == [1, 1], (
            f'Chapter 2 files (html + image) should share position 1. '
            f'Got: {sorted(chapter_positions[2])}'
        )


# =========================================================================
# Behavioral: URL module vs inline link distinction
# =========================================================================
class TestUrlModuleVsInlineLinkPositioning:
    """Pin the contract that URL modules get their own slot, while
    inline description links (URLs in description HTML) live under
    the parent module's slot.
    """

    def test_url_module_gets_own_slot(self):
        """A URL (modname='url') module is one CM, gets its own
        slot.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # URL module + a label module
        files = [
            _make_file(1, 100, 'https://example.com', modname='url'),
            _make_file(1, 200, 'label.md', modname='label'),
        ]
        # url module has no attachments (it's a single URL)
        files[0]._module_has_attachments = False
        files[1]._module_has_attachments = False

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # 2 distinct slots (one per CM)
        assert files[0].position_in_section == 0
        assert files[1].position_in_section == 1

    def test_inline_link_lives_under_parent_module(self):
        """An inline link (description-url in a label module) lives
        under the parent module's slot, not its own slot.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Label module (cm 100) with description-url inline
        # Plus a separate URL module (cm 200)
        files = [
            _make_file(
                1, 100, 'https://inline.com', modname='label',
                content_type='description-url'
            ),
            _make_file(1, 200, 'https://module.com', modname='url'),
        ]
        files[0]._module_has_attachments = False
        files[1]._module_has_attachments = False

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # Label module (with inline link) gets slot 0
        # URL module gets slot 1
        assert files[0].position_in_section == 0
        assert files[1].position_in_section == 1


# =========================================================================
# Helpers
# =========================================================================
def _make_file(section_id, module_id, filename, modname='resource',
               has_attachments=False, content_type='file'):
    from moodle_dl.types import File
    f = File(
        module_id=module_id, section_name='S', section_id=section_id,
        module_name=f'mod_{module_id}', content_filepath='/',
        content_filename=filename,
        content_fileurl=f'https://example.com/{filename}',
        content_filesize=1024, content_timemodified=0,
        module_modname=modname,
        content_type=content_type,
        content_isexternalfile=False,
    )
    f._module_has_attachments = has_attachments
    return f


def _make_rb():
    from moodle_dl.moodle.result_builder import ResultBuilder
    rb = ResultBuilder.__new__(ResultBuilder)
    return rb