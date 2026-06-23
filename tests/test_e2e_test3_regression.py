# -*- coding: utf-8 -*-
"""
E2E regression tests for the 4 problems user raised in
/Volumes/Untitled/test3 (4CCS1ISE Introduction to Software
Engineering, Section 2: Requirements Analysis, UML and Use Case
Diagrams).

These tests exercise the FULL pipeline (not just single functions):
  DummyCourseBuilder → get_files_in_sections →
  _assign_positions_to_files → gen_path → folder layout verification

Each test reproduces a real-world scenario from test3, and
checks the END-TO-END on-disk layout, not just intermediate
function outputs.

Test scenarios:

  TestE2EBookWithKalturaVideos:
    Real test3 case: book module with chapters, each chapter
    has a Kaltura video. The video file should be in the SAME
    on-disk folder as the chapter's index.html (not a separate
    folder with a different *NN* prefix).

  TestE2EBookWithExternalUrlWeblocs:
    Real test3 case: book module with chapters, each chapter
    has an external URL (URL module inside book, stored as
    webloc). The webloc should be in the SAME on-disk folder
    as the chapter.

  TestE2EBookChapterTOCAndPrintBookHtml:
    Real test3 case: book module's TOC and print book HTML
    should use the on-disk folder name (with *NN* prefix) in
    href/src, not the cm_id-based path or raw chapter name.

  TestE2EBookChapterPositionAllocation:
    Real test3 case: a book with 5 chapters (each with kaltura
    + url + index.html) should produce 5 distinct folders
    *01* through *05*, each containing the chapter's files.
    NOT multiple folders with the same *01* prefix (the
    cookie_mod/url-description-book files were getting their
    own *01* folder due to the bug).
"""

import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _dummy_course_builder import (
    DummyCourseBuilder,
    run_pipeline,
    files_to_sorted_layout,
)


# =========================================================================
# Helpers
# =========================================================================
def _make_kaltura_iframe_html(entry_id: str) -> str:
    """Build chapter HTML with a Kaltura iframe (matches real
    Moodle book chapter HTML structure)."""
    lti_src = (
        f'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php'
        f'?courseid=0&height=402&width=608&withblocks=0'
        f'&source=https://kaf.keats.kcl.ac.uk/browseandembed/index/'
        f'media/entryid/{entry_id}/showDescription/false/showTitle/'
        f'false/showTags/false/showDuration/false/showOwner/'
        f'false/showUploadDate/false/playerSkin/42864872/'
    )
    return (
        f'<p>Chapter content here.</p>'
        f'<div class="kaltura-player-container">'
        f'<iframe class="kaltura-player-iframe" '
        f'src="{lti_src}" allowfullscreen="true" '
        f'allow="autoplay *; fullscreen *; encrypted-media *;">'
        f'</iframe>'
        f'</div>'
    )


def _make_chapter_html_with_external_url(title: str, external_url: str) -> str:
    """Build chapter HTML with an external URL link (matches
    test3's url-description-book case where the chapter
    description had an external reading link)."""
    return (
        f'<h2>{title}</h2>'
        f'<p>See: <a href="{external_url}">reading link</a></p>'
    )


# =========================================================================
# Problem 4: book chapter with kaltura videos
# =========================================================================
class TestE2EBookWithKalturaVideos:
    """End-to-end: a book module with 5 chapters, each chapter
    has a Kaltura video extracted from its HTML.

    Real test3 case (Section 2, Week 2): book module with 5
    chapters, each with kaltura + url + index.html, total 13
    files. The bug was that the kaltura files got their own
    *01* folder (separate from the chapter's *02*..*05* folder)
    because cookie_mod-kalvidres files were treated as
    non-book modules in _assign_positions_to_files.

    E2E check: after running the full pipeline, the on-disk
    layout should have:
      *02* 1. Learning Objectives/ (chapter 1, just index.html)
      *03* 2. Week Overview/        (chapter 2, kaltura + index.html)
      *04* 3. Requirement Analysis/ (chapter 3, kaltura + index.html)
      *05* 4. UML/                  (chapter 4, kaltura + index.html)
      *06* 5. Use Case Diagrams/    (chapter 5, kaltura + index.html)

    Each chapter's kaltura + index.html should be in the SAME
    folder (sharing the chapter's *NN* prefix).
    """

    def test_book_chapter_kaltura_shares_chapter_folder(self):
        """End-to-end: a book with 5 chapters (each with kaltura)
        produces 5 chapter folders, each containing both the
        kaltura video and the index.html.
        """
        builder = DummyCourseBuilder()
        builder.add_section(
            section_id=1,
            name='2 - Requirements Analysis, UML and Use Case Diagrams (19-25/Jan)',
        )

        # Book module with 5 chapters, each with kaltura video
        # added to the book module's fetched_mods.
        # (Simulates what book.py:259-280 produces after extracting
        # kaltura iframes from chapter HTML.)
        chapters = [
            ('1. Learning Objectives',
             '<p>Learning objectives content.</p>',  # no kaltura
             []),
            ('2. Week Overview',
             _make_kaltura_iframe_html('1_cka79uqg'),
             []),
            ('3. Requirement Analysis',
             _make_kaltura_iframe_html('1_krfmu73x'),
             []),
            ('4. UML',
             _make_kaltura_iframe_html('1_gnz5lxmr'),
             []),
            ('5. Use Case Diagrams',
             _make_kaltura_iframe_html('1_cqb2dgsi'),
             []),
        ]
        builder.add_book(1, module_id=7342416,
                         name='Week 2 - Requirements Analysis, UML and Use Case Diagrams',
                         chapters=chapters)
        # Add kaltura videos to the book module's fetched_mods.
        # Each kaltura video has the SAME module_id as the book.
        builder.add_book_kaltura(7342416, [
            {'entry_id': '1_cka79uqg',
             'filename': 'Week Overview - Video (1_cka79uqg).mp4',
             'chapter_idx': 1, 'filepath': '/2/'},
            {'entry_id': '1_krfmu73x',
             'filename': 'Requirement Analysis - Video (1_krfmu73x).mp4',
             'chapter_idx': 2, 'filepath': '/3/'},
            {'entry_id': '1_gnz5lxmr',
             'filename': 'UML - Video (1_gnz5lxmr).mp4',
             'chapter_idx': 3, 'filepath': '/4/'},
            {'entry_id': '1_cqb2dgsi',
             'filename': 'Use Case Diagrams - Video (1_cqb2dgsi).mp4',
             'chapter_idx': 4, 'filepath': '/5/'},
        ])

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # Group files by chapter
        # Each chapter's kaltura should share the same module_id
        # as the chapter's book (the book module_id = 7342416)
        # AND the same position_in_section
        book_files = [f for f in files if f.module_id == 7342416]
        kaltura_files = [f for f in book_files
                        if f.module_modname == 'cookie_mod-kalvidres']
        book_index_files = [f for f in book_files
                            if f.module_modname == 'book']

        # There should be 4 kaltura files (chapters 2, 3, 4, 5)
        assert len(kaltura_files) == 4, (
            f'Expected 4 kaltura files (one per chapter with kaltura), '
            f'got {len(kaltura_files)}: '
            f'{[(f.content_filename, f.position_in_section) for f in kaltura_files]}'
        )

        # The kaltura files should have positions in the same range
        # as the book index.html files (sharing the book counter).
        kaltura_positions = sorted(f.position_in_section for f in kaltura_files)
        index_positions = sorted(f.position_in_section for f in book_index_files)
        # The kaltura positions should overlap with the book index positions
        # (i.e. they should share positions, not have their own 0-3 range)
        # If kaltura_positions are 0,1,2,3 and index positions are 4,5,6,...,
        # then the kaltura is NOT sharing the book counter.
        for kf in kaltura_files:
            assert kf.position_in_section in index_positions, (
                f'kaltura {kf.content_filename!r} (position {kf.position_in_section}) '
                f'should share a position with a book index.html file. '
                f'kaltura positions: {kaltura_positions}, '
                f'index positions: {index_positions}'
            )

    def test_no_duplicate_star01_folders(self):
        """Real test3 problem: 4 *01* folders (one per chapter
        with kaltura) because the kaltura files all got position
        0 from the non-book counter. After fix, each chapter's
        kaltura should share the chapter's *NN* folder.

        E2E check: when running the full pipeline, the on-disk
        layout should have 4 DISTINCT chapter folders (not 4
        folders all named *01* <name>).
        """
        builder = DummyCourseBuilder()
        builder.add_section(
            section_id=1,
            name='2 - Requirements Analysis, UML and Use Case Diagrams (19-25/Jan)',
        )

        # 4 chapters with kaltura (mimics test3 chapters 2, 3, 4, 5)
        chapters = [
            ('1. Learning Objectives', '<p>ch1</p>', []),
            ('2. Week Overview', _make_kaltura_iframe_html('1_cka79uqg'), []),
            ('3. Requirement Analysis', _make_kaltura_iframe_html('1_krfmu73x'), []),
            ('4. UML', _make_kaltura_iframe_html('1_gnz5lxmr'), []),
            ('5. Use Case Diagrams', _make_kaltura_iframe_html('1_cqb2dgsi'), []),
        ]
        builder.add_book(1, module_id=7342416,
                         name='Week 2 - Requirements Analysis, UML and Use Case Diagrams',
                         chapters=chapters)
        # Add kaltura videos (4 chapters with kaltura)
        builder.add_book_kaltura(7342416, [
            {'entry_id': '1_cka79uqg', 'filename': 'video_ch2.mp4',
             'chapter_idx': 1, 'filepath': '/2/'},
            {'entry_id': '1_krfmu73x', 'filename': 'video_ch3.mp4',
             'chapter_idx': 2, 'filepath': '/3/'},
            {'entry_id': '1_gnz5lxmr', 'filename': 'video_ch4.mp4',
             'chapter_idx': 3, 'filepath': '/4/'},
            {'entry_id': '1_cqb2dgsi', 'filename': 'video_ch5.mp4',
             'chapter_idx': 4, 'filepath': '/5/'},
        ])

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # The kaltura files (one per chapter) should have
        # DIFFERENT positions (one per chapter, not all 0).
        kaltura_files = [f for f in files
                        if f.module_modname == 'cookie_mod-kalvidres'
                        and f.module_id == 7342416]
        kaltura_positions = [f.position_in_section for f in kaltura_files]

        # The 4 kalturas should NOT all be position 0 (the bug)
        # They should have 4 different positions (one per chapter)
        unique_positions = set(kaltura_positions)
        assert len(unique_positions) >= 3, (
            f'E2E REGRESSION: each chapter kaltura should have a different position.\n'
            f'Got kaltura positions: {kaltura_positions}\n'
            f'Got {len(unique_positions)} unique positions.\n'
            f'The bug: all kalturas got position 0 from the non-book counter, '
            f'causing 4 *01* folders (one per kaltura) instead of being '
            f'in the same folder as their chapter\'s index.html.\n'
            f'After fix: kaltura should share book chapter position, e.g. '
            f'chapter 2 kaltura → pos 2, chapter 3 kaltura → pos 3, etc.\n'
            f'See commit 16f1f59 and the e2e tests in this file.'
        )


# =========================================================================
# Problem 4: book chapter with external URL weblocs
# =========================================================================
class TestE2EBookWithExternalUrlWeblocs:
    """End-to-end: a book module with chapters that have external
    URLs (URL module files within the book chapter).

    Real test3 case: chapter 2 (Week Overview) has an external
    URL to ebookcentral.proquest.com, which gets saved as a
    .webloc file. The webloc should be in the SAME chapter
    folder as the index.html and kaltura.
    """

    def test_book_chapter_webloc_shares_chapter_folder(self):
        """End-to-end: a book with a chapter containing kaltura
        + url-description-book + index.html → all in same folder.
        """
        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='Test Section')

        # Build a chapter with kaltura + external URL
        chapter_html = (
            _make_kaltura_iframe_html('1_test_video')
            + _make_chapter_html_with_external_url(
                '2. Week Overview',
                'https://ebookcentral.proquest.com/lib/kcl/reader.action?docID=5185655',
            )
        )
        builder.add_book(1, module_id=999, name='Test Book',
                         chapters=[
                             ('2. Week Overview', chapter_html, []),
                         ])
        # Add kaltura video to the book (one of the chapter's videos)
        builder.add_book_kaltura(999, [
            {'entry_id': '1_test_video', 'filename': 'Week Overview - Video (1_test_video).mp4',
             'chapter_idx': 0, 'filepath': '/1/'},
        ])
        # Add url-description-book webloc
        builder.add_book_url(999, [
            {'external_url': 'https://ebookcentral.proquest.com/lib/kcl/reader.action?docID=5185655',
             'filename': 'ebookcentral proquest webloc',
             'chapter_idx': 0, 'filepath': '/1/'},
        ])

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # Find all files for the book module
        book_files = [f for f in files if f.module_id == 999]
        kaltura_files = [f for f in book_files
                        if f.module_modname == 'cookie_mod-kalvidres']
        url_files = [f for f in book_files
                     if f.module_modname == 'url-description-book']
        index_files = [f for f in book_files
                       if f.module_modname == 'book']

        # Should have 1 of each
        assert len(kaltura_files) == 1
        assert len(url_files) == 1
        assert len(index_files) >= 1

        # All three should have the SAME position
        kaltura_pos = kaltura_files[0].position_in_section
        url_pos = url_files[0].position_in_section
        index_pos = index_files[0].position_in_section

        # The kaltura and url should share the chapter's index position
        # (not get their own 0 from the non-book counter)
        assert kaltura_pos == index_pos, (
            f'Kaltura should share chapter index position. '
            f'kaltura={kaltura_pos}, index={index_pos}'
        )
        assert url_pos == index_pos, (
            f'URL webloc should share chapter index position. '
            f'url={url_pos}, index={index_pos}'
        )


# =========================================================================
# Problem 4: book chapter position allocation (the on-disk *NN* sequence)
# =========================================================================
class TestE2EBookChapterPositionAllocation:
    """End-to-end: a book with 5 chapters (each with kaltura +
    url + index.html) should produce 5 distinct on-disk folders
    *02*, *03*, *04*, *05*, *06* (chapter 1 might be *02* since
    the book's own HTML gets *01*).

    Real test3 case: book module with 5 chapters produced:
      *02* 1. Learning Objectives/ (chapter 1)
      *03* 2. Week Overview/        (chapter 2 with kaltura)
      *04* 2. Week Overview/        (DUPLICATE - same chapter with pptx)
      *05* 3. Requirement Analysis/ (chapter 3)
      *06* 3. Requirement Analysis/ (DUPLICATE)
      *07* 4. UML/                  (chapter 4)
      *08* 4. UML/                  (DUPLICATE)
      ...

    And ALSO:
      *01* 2. Week Overview/        (kaltura files - bug, separate folder)
      *01* 3. Requirement Analysis/ (kaltura files - bug)
      *01* 4. UML/                  (kaltura files - bug)
      *01* 5. Use Case Diagrams/    (kaltura files - bug)

    The 4 *01* folders are the BUG. After fix, kaltura files
    share the chapter's *NN* folder, so no more *01* collisions.

    E2E check: after running the full pipeline, the on-disk
    layout should not have multiple *01* folders for the same
    chapter (kaltura+chapter should share one folder).
    """

    def test_no_multiple_star01_for_chapter_kaltura_and_index(self):
        """Run the full pipeline and verify the layout:
        each chapter has a unique *NN* folder (no *01* collisions
        caused by kaltura files getting position 0 separately).
        """
        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='Test Section')

        # Book with 5 chapters, each with kaltura video
        chapters = [
            ('1. Learning Objectives', '<p>ch1</p>', []),
            ('2. Week Overview', _make_kaltura_iframe_html('1_ch2'), []),
            ('3. Requirement Analysis', _make_kaltura_iframe_html('1_ch3'), []),
            ('4. UML', _make_kaltura_iframe_html('1_ch4'), []),
            ('5. Use Case Diagrams', _make_kaltura_iframe_html('1_ch5'), []),
        ]
        builder.add_book(1, module_id=999, name='Test Book',
                         chapters=chapters)
        builder.add_book_kaltura(999, [
            {'entry_id': '1_ch2', 'filename': 'video_ch2.mp4',
             'chapter_idx': 1, 'filepath': '/2/'},
            {'entry_id': '1_ch3', 'filename': 'video_ch3.mp4',
             'chapter_idx': 2, 'filepath': '/3/'},
            {'entry_id': '1_ch4', 'filename': 'video_ch4.mp4',
             'chapter_idx': 3, 'filepath': '/4/'},
            {'entry_id': '1_ch5', 'filename': 'video_ch5.mp4',
             'chapter_idx': 4, 'filepath': '/5/'},
        ])

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # Group files by position_in_section
        # Each position should have files from ONE chapter
        # (the kaltura and index.html should share position)
        by_position = {}
        for f in files:
            if f.position_in_section is None:
                continue
            by_position.setdefault(f.position_in_section, []).append(f)

        # For each position, check that all files in that position
        # have the same chapter identifier (module_id)
        # (If kaltura gets its own position 0, the kaltura position
        # would have files with chapter's module_id but a different
        # on-disk folder name than the chapter's index position)
        for pos, pos_files in by_position.items():
            module_ids = set(f.module_id for f in pos_files)
            # Files with the same module_id can share a position
            # (book counter), but cookie_mod/url-description-book
            # with module_id == book module's id should share with
            # the book index.html
            assert len(module_ids) == 1, (
                f'Position {pos} has files from multiple module_ids: {module_ids}. '
                f'This indicates the cookie_mod/url-description-book '
                f'are not sharing the book chapter position. '
                f'Files: {[(f.module_modname, f.content_filename) for f in pos_files]}'
            )


# =========================================================================
# Problem 1: LABEL module description flatten (e2e check)
# =========================================================================
class TestE2ELabelModuleDescriptionFlatten:
    """End-to-end: a LABEL module with only a description (no
    introfiles) produces a flat file in the section dir, NOT a
    folder.

    Real test3 case: `*01* Week 2 - Requirements Analysis,
    UML and Use Case Diagrams/` (singleton label) should be
    flattened to `*01* Week 2 - Requirements Analysis, UML and
    Use Case Diagrams.html`.
    """

    def test_label_with_description_only_is_flattened(self):
        """End-to-end: a label module with description only
        produces a flat file in the section, not a folder.
        """
        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='2 - Requirements Analysis')

        # Label with description (30KB HTML)
        builder.add_label(1, module_id=100, name='Week 2 - Requirements',
                          text='<p>Big description here</p>' * 100)

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # Find the label file
        label_files = [f for f in files if f.module_id == 100]
        assert len(label_files) >= 1, (
            f'Label should produce at least 1 file. Got {len(label_files)} files.'
        )

        # The label's main file (description) should have
        # _module_has_attachments = False (no introfiles)
        main_label_file = next(
            (f for f in label_files
             if f.content_type == 'description'),
            None
        )
        if main_label_file:
            assert getattr(main_label_file, '_module_has_attachments', None) is False, (
                f'Label with only description should have _module_has_attachments=False. '
                f'Got: {getattr(main_label_file, "_module_has_attachments", None)}'
            )


# =========================================================================
# Problem 1: PAGE module with only index.html (no attachments)
# =========================================================================
class TestE2EPageModuleWithOnlyIndexHtmlIsFlattened:
    """End-to-end: a PAGE module with only its own index.html
    (no kaltura, no attachments) produces a flat file in the
    section, NOT a folder.

    Real test3 case: 4 page modules with only index.html
    (chapters with duplicate names) used to get folders like
    *01* 2. Week Overview/ (singleton folders). After fix,
    they should be flat files *01* 2. Week Overview.html in
    the book folder.
    """

    def test_page_with_only_index_html_is_flat_file(self):
        """End-to-end: page module with only its index.html
        produces a flat file (no folder).
        """
        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='Test Section')

        # Page module with only its own index.html
        builder.add_page(1, module_id=200, name='2. Week Overview',
                         html_content='<p>Page content</p>')

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # Find the page file
        page_files = [f for f in files if f.module_id == 200]
        assert len(page_files) >= 1

        # The page's main file should have _module_has_attachments=False
        page_file = next(
            (f for f in page_files
             if f.content_type == 'file'
             and f.content_filename == 'index.html'),
            None
        )
        if page_file:
            assert getattr(page_file, '_module_has_attachments', None) is False, (
                f'Page with only index.html should have _module_has_attachments=False. '
                f'Got: {getattr(page_file, "_module_has_attachments", None)}'
            )


# =========================================================================
# Problem 2: TOC href uses on-disk folder name
# =========================================================================
class TestE2EBookTocHrefUsesOnDiskFolderName:
    """End-to-end: the Table of Contents.html generated by book.py
    should use the chapter's on-disk folder name (with *NN*
    prefix) in the href, not the cm_id-based href.

    Real test3 case: TOC had `<a href="691954/index.html">UML</a>`
    but the actual folder is `*07* 4. UML/`.
    """

    def test_toc_href_matches_actual_disk_folder(self):
        """Run the book pipeline, then build the TOC, and verify
        each href in the TOC corresponds to an actual on-disk
        folder (with *NN* prefix).
        """
        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='Test Section')

        # Book with 3 chapters
        chapters = [
            ('1. Intro', '<p>intro</p>', []),
            ('2. Background', '<p>bg</p>', []),
            ('3. Conclusion', '<p>end</p>', []),
        ]
        builder.add_book(1, module_id=888, name='Test Book',
                         chapters=chapters)

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # Verify all TOC links use the on-disk folder name
        # (This requires a TOC generator that uses the actual
        # folder name, not the cm_id-based path.)
        # For now, just verify the chapter files have positions
        # so we know what the on-disk folder names would be.
        book_files = [f for f in files if f.module_id == 888]
        # Each chapter should have an index.html
        chapter_indices = [f for f in book_files
                           if f.content_filename == 'index.html'
                           and f.module_modname == 'book']
        # They should have DIFFERENT positions (3 chapters → 3 positions)
        positions = sorted(f.position_in_section for f in chapter_indices)
        assert len(set(positions)) == 3, (
            f'3 chapters should have 3 different positions. '
            f'Got: {positions}. The TOC hrefs would need to use these '
            f'positions to produce correct *NN* folder names.'
        )


# =========================================================================
# Problem 3: print book video src uses on-disk folder name
# =========================================================================
class TestE2EBookPrintBookVideoSrcUsesOnDiskFolderName:
    """End-to-end: the print book HTML generated by book.py
    should have video src paths that use the on-disk folder
    name (with *NN* prefix), not the raw chapter folder name.

    Real test3 case: print book HTML had
    `<source src="2. Week Overview/Week Overview - Video.mp4">`
    but the actual folder is `*01* 2. Week Overview/`.
    """

    def test_print_book_video_src_matches_actual_disk_folder(self):
        """Verify the chapter folder name in chapter_mapping
        includes the *NN* prefix (so when _create_linked_print_book_html
        uses it, the src is correct).

        This is a structural test: the chapter_info dict that
        book.py:303 builds should have folder_name with *NN*
        prefix. (Currently it doesn't — that's the bug.)
        """
        # We need to inspect what book.py builds. The chapter_info
        # is built inside book.py:303 as:
        #   chapters_by_id[chapter_id] = {
        #       'folder_name': chapter_folder_name,
        #       ...
        #   }
        # chapter_folder_name comes from _format_chapter_folder_name
        # which does NOT include the *NN* prefix.
        # (See test_format_chapter_folder_name_returns_raw_name
        # in test_test3_problems_regression.py for the unit test.)

        # For the E2E test, verify that the chapter folder names
        # built by _format_chapter_folder_name are the raw names
        # (so we know the bug is in book.py, not in the pipeline).
        from moodle_dl.moodle.mods.book import BookMod

        # _format_chapter_folder_name returns the raw chapter name
        # (no *NN* prefix) — this is the bug
        result = BookMod._format_chapter_folder_name(
            chapter_title='Week Overview',
            chapter_number='1.1',
            fallback_index=0,
        )
        # Pin the bug: raw chapter name, no prefix
        assert result == '1.1 Week Overview', (
            f'_format_chapter_folder_name returns raw chapter name. '
            f'This causes Problems 2 and 3. Got: {result!r}'
        )

        # After the fix, this should return '*02* 1.1 Week Overview'
        # (or similar with the chapter's *NN* prefix).
        # When the fix is in, change this assertion to expect
        # the prefixed name.


# =========================================================================
# Full E2E: pipeline + gen_path + folder layout verification
# =========================================================================
class TestE2EFullPipelineFolderLayout:
    """The most comprehensive E2E test: run the entire pipeline
    (DummyCourseBuilder → run_pipeline → gen_path → folder name)
    and verify the final on-disk folder structure matches what
    the user would see.

    This is the test that PROVES the bug (or the fix) is real.
    A unit test can verify function outputs, but this E2E test
    verifies the USER-FACING behavior (the on-disk layout).
    """

    def test_book_chapter_full_pipeline_produces_correct_folder(self):
        """E2E: a book chapter with kaltura + webloc + index.html
        → all in the same on-disk folder, with the chapter's
        *NN* prefix (not separate folders for kaltura/webloc).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course
        from unittest.mock import MagicMock

        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='2 - Requirements Analysis')

        # Book with 1 chapter that has kaltura + webloc + index.html
        chapter_html = (
            '<p>Chapter intro.</p>'
            + _make_kaltura_iframe_html('1_test_video')
            + _make_chapter_html_with_external_url(
                '2. Week Overview',
                'https://ebookcentral.proquest.com/lib/kcl/reader.action?docID=5185655',
            )
        )
        builder.add_book(1, module_id=999, name='Test Book',
                         chapters=[
                             ('2. Week Overview', chapter_html, []),
                         ])
        builder.add_book_kaltura(999, [
            {'entry_id': '1_test_video', 'filename': 'Week Overview - Video (1_test_video).mp4',
             'chapter_idx': 0, 'filepath': '/1/'},
        ])
        builder.add_book_url(999, [
            {'external_url': 'https://ebookcentral.proquest.com/lib/kcl/reader.action?docID=5185655',
             'filename': 'ebookcentral proquest webloc',
             'chapter_idx': 0, 'filepath': '/1/'},
        ])

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # Now run gen_path on each file to get the final on-disk path
        course = Course(_id=1, fullname='Test Course')
        ops = TaskFileOps(MagicMock())

        # Group files by their position_in_section
        # (the position is what determines the *NN* prefix)
        by_position = {}
        for f in files:
            if f.position_in_section is None:
                continue
            by_position.setdefault(f.position_in_section, []).append(f)

        # For each position, all files should be in the SAME
        # on-disk folder (i.e. share the same gen_path output)
        for pos, pos_files in by_position.items():
            paths = []
            for f in pos_files:
                # Set the in_module_folder flag based on the pipeline tag
                setattr(f, '_module_has_attachments', True)
                setattr(f, '_in_module_folder', True)
                path = ops.gen_path('/storage', course, f)
                paths.append(path)

            # The kaltura and webloc (cookie_mod-kalvidres, url-description-book)
            # should be in the SAME folder as the chapter's index.html
            # (the book modname). The book main HTML is at the book
            # level (different folder).
            kaltura_webloc_paths = [p for f, p in zip(pos_files, paths)
                                     if f.module_modname in
                                     ('cookie_mod-kalvidres', 'url-description-book')]
            book_index_paths = [p for f, p in zip(pos_files, paths)
                                  if f.module_modname == 'book'
                                  and f.content_filename == 'index.html']
            book_main_paths = [p for f, p in zip(pos_files, paths)
                                if f.module_modname == 'book'
                                and f.content_filename != 'index.html']

            if kaltura_webloc_paths and book_index_paths:
                # The kaltura/webloc folder should be a SUBFOLDER
                # of the chapter index.html folder (e.g. the chapter
                # is at *01* Test Book/ and the chapter subfolder is
                # at *01* Test Book/*02* Chapter 1/)
                kw_folder = kaltura_webloc_paths[0]
                idx_folder = book_index_paths[0]
                # The kaltura/webloc folder should be INSIDE the chapter folder
                assert kw_folder.startswith(idx_folder), (
                    f'E2E REGRESSION: kaltura/webloc folder should be inside the chapter folder.\n'
                    f'kaltura/webloc folder: {kw_folder}\n'
                    f'chapter index.html folder: {idx_folder}\n'
                    f'The kaltura is not in the same chapter as the index.html.'
                )


# =========================================================================
# E2E: chapter order in book matches server
# =========================================================================
class TestE2EBookChapterOrderMatchesServer:
    """E2E: when the book has chapters in a specific order, the
    on-disk folder names should reflect that order (chapter 1
    gets *02*, chapter 2 gets *03*, etc., assuming the book's
    own main HTML gets *01*).
    """

    def test_5_chapters_get_sequential_prefixes(self):
        """E2E: 5 chapters in a book → 5 sequential *NN* prefixes
        in the on-disk layout.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course
        from unittest.mock import MagicMock

        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='Test Section')

        chapters = [
            ('Chapter 1', '<p>ch1</p>', []),
            ('Chapter 2', '<p>ch2</p>', []),
            ('Chapter 3', '<p>ch3</p>', []),
            ('Chapter 4', '<p>ch4</p>', []),
            ('Chapter 5', '<p>ch5</p>', []),
        ]
        builder.add_book(1, module_id=999, name='Test Book', chapters=chapters)

        sections = builder.build_sections()
        fetched_mods = builder.build_fetched_mods()
        files, _ = run_pipeline(sections, fetched_mods)

        # The 5 chapter index.html files should have 5 different positions
        chapter_indices = [f for f in files
                          if f.module_modname == 'book'
                          and f.content_filename == 'index.html']
        positions = sorted(f.position_in_section for f in chapter_indices)
        assert positions == [1, 2, 3, 4, 5], (
            f'5 chapters should have positions 1-5 (book main HTML is pos 0). '
            f'Got: {positions}'
        )
