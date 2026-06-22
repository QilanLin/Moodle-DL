# -*- coding: utf-8 -*-
"""
Adversarial / unusual tests for book chapter + off-main-page
edge cases.

Covers scenarios not yet tested by
tests/test_off_page_and_book_attachments.py (12 tests):

  - url-description webloc files inside book chapters
    (CS2 has many of these — see test_off_page_url_webloc_in_book_chapter)
  - cookie_mod videos embedded in book chapter HTML
  - On-page + off-page books in the same course (mixed)
  - Double-download risk: same fileurl appears in chapter
    HTML and as attachment
  - Book chapter with embedded images (label_file content_type)
  - Off-page singleton label gets section-wide *NN* prefix
    (its module_id 0 is special)
  - Empty fetched_mods (no modules)
  - fetched_mods with only on-main-page modules (skip all)
  - Book chapter order in fetched_mods differs from server order
    (does moodle-dl preserve order or sort?)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Book chapter with embedded URL files (url-description modname)
# =========================================================================
class TestBookChapterWithEmbeddedUrls:
    """A book chapter's HTML may contain <a href="..."> tags
    pointing to external resources. The chapter HTML content
    is processed by `_handle_files` (or by book.py's HTML
    extraction). The embedded URLs become File objects with
    modname 'url-description-...' (from
    description_url_extractor.py).

    These embedded URL files share the chapter's
    content_filepath (because they're extracted from the
    chapter HTML), and the chapter folder contains them.
    """

    def test_book_chapter_url_extraction_files_have_chapter_filepath(self):
        """A book chapter with an embedded URL (e.g. a YouTube
        link extracted from the chapter HTML) — the resulting
        File object should have the same content_filepath as
        the chapter (so it lands in the chapter folder).
        """
        # book.py produces files with content_filepath set
        # to /<chapter_folder_name>/. The url-description
        # extraction (via _find_all_urls) inherits this.
        # We simulate by creating a File with the matching
        # content_filepath.
        from moodle_dl.types import File

        f = File(
            module_id=100, section_name='Analyse data',
            section_id=1,
            module_name='My Book',
            content_filepath='/1. Introduction/',
            content_filename='youtube_link.webloc',
            content_fileurl='https://youtube.com/watch?v=abc',
            content_filesize=100, content_timemodified=0,
            module_modname='url-description-label',
            content_type='description-url',
            content_isexternalfile=True,
        )
        # The url-description file shares the chapter's folder
        assert f.content_filepath == '/1. Introduction/'

    def test_off_page_book_chapter_url_extraction_reaches_download(self):
        """An off-page book's chapter URL extraction files
        still reach the download pipeline via
        _get_files_not_on_main_page."""
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books'}
        fetched_mods = {
            'book': {
                100: {
                    'id': 100,
                    'name': 'Off Page Book',
                    'files': [
                        {'type': 'file', 'filename': 'ch1.html',
                         'filepath': '/1. Introduction/',
                         'fileurl': 'x', 'filesize': 100,
                         'timemodified': 0, 'type': 'file'},
                        {'type': 'description-url',
                         'filename': 'link.webloc',
                         'filepath': '/1. Introduction/',
                         'fileurl': 'x', 'filesize': 0,
                         'timemodified': 0, 'type': 'description-url'},
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # Both files reach download
        assert len(files) == 2
        filenames = {f.content_filename for f in files}
        assert 'ch1.html' in filenames
        assert 'link.webloc' in filenames


# =========================================================================
# Book chapter with cookie_mod video (Kaltura)
# =========================================================================
class TestBookChapterWithCookieMod:
    """A book chapter's HTML may embed a Kaltura/Helixmedia
    video. book.py's iframe extraction converts these into
    File objects with module_modname 'cookie_mod-kalvidres'
    and content_filepath = /<chapter_folder>/.

    These video files go through the video download path
    (yt-dlp) rather than the file download path.
    """

    def test_book_chapter_cookie_mod_video_path(self):
        """A book chapter with an embedded Kaltura video —
        the video File has module_modname='cookie_mod-kalvidres'
        and content_filepath = /<chapter_folder>/.
        """
        from moodle_dl.types import File

        f = File(
            module_id=100, section_name='Analyse data',
            section_id=1,
            module_name='My Book',
            content_filepath='/2. Coding and uncoding/',
            content_filename='Lecture.mp4',
            content_fileurl='https://kaltura.example.com/embed/123',
            content_filesize=0, content_timemodified=0,
            module_modname='cookie_mod-kalvidres',
            content_type='cookie_mod',
            content_isexternalfile=True,
        )
        # Video inherits the chapter's folder
        assert f.content_filepath == '/2. Coding and uncoding/'

    def test_off_page_book_chapter_kalvidres_video_downloads(self):
        """An off-page book with an embedded Kaltura video —
        the video File object reaches the download pipeline.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books'}
        fetched_mods = {
            'book': {
                100: {
                    'id': 100,
                    'name': 'Book With Videos',
                    'files': [
                        {'type': 'kalvidres_embedded',
                         'filename': 'Video 1.mp4',
                         'filepath': '/1. Chapter 1/',
                         'fileurl': 'x', 'filesize': 0,
                         'timemodified': 0, 'type': 'kalvidres_embedded'},
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        assert len(files) == 1
        assert files[0].module_modname == 'cookie_mod-kalvidres'


# =========================================================================
# Off-page label: singleton flattening
# =========================================================================
class TestOffPageLabelSingleton:
    """An off-page label module (e.g. a hidden label) with
    only its description HTML is a singleton. It gets
    flattened into the synthetic 'Labels not on main page'
    section directory, with the section-wide *NN* prefix.
    """

    def test_off_page_label_singleton_gets_nn_prefix(self):
        """A label module off the main page with only its
        description HTML gets _module_has_attachments=False
        (set by _get_files_not_on_main_page's tagging) and
        a *NN* prefix in its filename.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='Labels not on main page',
            section_id=-1,
            module_name='Hidden Label',
            content_filepath='/',
            content_filename='Hidden Label',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='label',
            content_type='description',
            content_isexternalfile=False,
        )
        # _get_files_not_on_main_page would set this for non-book
        f._module_has_attachments = False
        f.position_in_section = 0
        f._in_module_folder = False

        ops = TaskFileOps(MagicMock())
        filename = ops.generate_filename_with_index(f)
        # Singleton flat: gets *01* prefix (singleton's only
        # file, position 0)
        assert filename == '*01* Hidden Label'


# =========================================================================
# On-page + off-page books: don't double-download
# =========================================================================
class TestNoDoubleDownload:
    """A course may have a book that appears BOTH on the main
    page AND in the off-main-page list (if the same cm_id
    is referenced twice, e.g. via different access modes).

    Pin: only one of the two code paths produces the files.
    The 'on_main_page' flag is set by _get_files_in_modules
    after processing the book; _get_files_not_on_main_page
    then SKIPS the book via 'if on_main_page in module: continue'.
    """

    def test_book_processed_once_via_on_main_page_skip(self):
        """A book that was processed via _get_files_in_modules
        (because it appeared on the course main page) gets
        the on_main_page flag set. _get_files_not_on_main_page
        skips it.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books'}
        fetched_mods = {
            'book': {
                # Already on main page — on_main_page=True
                100: {
                    'id': 100,
                    'name': 'Visible Book',
                    'files': [
                        {'type': 'file', 'filename': 'ch1.html',
                         'filepath': '/1/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'},
                    ],
                    'on_main_page': True,  # set by get_files_in_modules
                },
            },
        }
        # _get_files_not_on_main_page should skip this
        files = rb._get_files_not_on_main_page(fetched_mods)
        assert len(files) == 0

    def test_multiple_books_mixed_on_and_off_page(self):
        """A course with 3 books: 2 visible (on main page),
        1 hidden (off main page). Only the hidden one
        contributes files to the 'Books not on main page'
        section.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books'}
        fetched_mods = {
            'book': {
                100: {'id': 100, 'name': 'Visible 1',
                      'files': [{'type': 'file', 'filename': 'a.html',
                                'filepath': '/1/', 'fileurl': 'x',
                                'filesize': 100, 'timemodified': 0,
                                'type': 'file'}],
                      'on_main_page': True},
                200: {'id': 200, 'name': 'Visible 2',
                      'files': [{'type': 'file', 'filename': 'b.html',
                                'filepath': '/1/', 'fileurl': 'x',
                                'filesize': 100, 'timemodified': 0,
                                'type': 'file'}],
                      'on_main_page': True},
                300: {'id': 300, 'name': 'Hidden',
                      'files': [{'type': 'file', 'filename': 'c.html',
                                'filepath': '/1/', 'fileurl': 'x',
                                'filesize': 100, 'timemodified': 0,
                                'type': 'file'}]},
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # Only the hidden book (300) produces files
        assert len(files) == 1
        assert files[0].module_id == 300


# =========================================================================
# Empty fetched_mods
# =========================================================================
class TestEmptyFetchedMods:
    """Edge case: no modules at all (empty fetched_mods)."""

    def test_empty_fetched_mods_returns_no_files(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books'}
        files = rb._get_files_not_on_main_page({})
        assert files == []

    def test_fetched_mods_with_only_empty_modules(self):
        """All modules have empty files arrays."""
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books', 'assign': 'Assignments'}
        fetched_mods = {
            'book': {100: {'id': 100, 'name': 'Empty Book', 'files': []}},
            'assign': {200: {'id': 200, 'name': 'Empty Assign', 'files': []}},
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        assert files == []


# =========================================================================
# Module name appears in multiple courses
# =========================================================================
class TestModuleAcrossCourses:
    """fetched_mods is structured as {mod_name: {course_id:
    {module_id: {...}}}. Different courses have different
    course_id keys. We only test within a single course in
    _get_files_not_on_main_page (course.files only contains
    one course at a time). Pin that modules from OTHER
    courses don't leak into this course's files.
    """

    def test_other_course_modules_not_in_current_course(self):
        """_get_files_not_on_main_page is called per-course.
        Only modules for the CURRENT course should be in the
        output. (In production, add_files_to_courses filters
        by course.id before calling _get_files_not_on_main_page.)
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books'}
        # Simulate fetched_mods with one course's books
        fetched_mods = {
            'book': {
                # Course 1
                100: {'id': 100, 'name': 'Book A',
                      'files': [{'type': 'file', 'filename': 'a.html',
                                'filepath': '/', 'fileurl': 'x',
                                'filesize': 100, 'timemodified': 0,
                                'type': 'file'}]},
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # Only 1 book in this course
        assert len(files) == 1


# =========================================================================
# Book chapter with both attachment AND embedded URL
# =========================================================================
class TestBookChapterMixedAttachmentAndUrl:
    """A book chapter has:
      - chapter HTML (type='file')
      - attachment PPT (type='file')
      - embedded webloc URL (type='description-url')
    All three must reach the download pipeline with their
    chapter content_filepath preserved.
    """

    def test_three_file_types_same_chapter(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books'}
        fetched_mods = {
            'book': {
                100: {
                    'id': 100,
                    'name': 'Mixed Chapter Book',
                    'files': [
                        {'type': 'file', 'filename': 'index.html',
                         'filepath': '/3. Mixed/',
                         'fileurl': 'x', 'filesize': 100,
                         'timemodified': 0, 'type': 'file'},
                        {'type': 'file', 'filename': 'slides.pptx',
                         'filepath': '/3. Mixed/',
                         'fileurl': 'x', 'filesize': 100,
                         'timemodified': 0, 'type': 'file'},
                        {'type': 'description-url',
                         'filename': 'reference.webloc',
                         'filepath': '/3. Mixed/',
                         'fileurl': 'x', 'filesize': 0,
                         'timemodified': 0, 'type': 'description-url'},
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # All 3 files reach download
        assert len(files) == 3
        # All share the same chapter folder
        for f in files:
            assert f.content_filepath == '/3. Mixed/'


# =========================================================================
# Book chapter content_filepath is normalized (trailing / stripped)
# =========================================================================
class TestBookChapterContentFilepathNormalize:
    """book.py sets content_filepath = f'/{chapter_folder_name}/'
    (with trailing slash). gen_path uses PT.sanitize_path which
    strips trailing slashes. The chapter folder name is the
    SAME regardless of trailing-slash normalization.
    """

    def test_chapter_filepath_normalization_in_gen_path(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        # Two files: one with trailing /, one without
        f1 = File(
            module_id=100, section_name='S', section_id=1,
            module_name='My Book',
            content_filepath='/1. Chapter 1/',  # with trailing /
            content_filename='a.html',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='book', content_type='file',
            content_isexternalfile=False,
        )
        f2 = File(
            module_id=100, section_name='S', section_id=1,
            module_name='My Book',
            content_filepath='/1. Chapter 1',  # without trailing /
            content_filename='b.html',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='book', content_type='file',
            content_isexternalfile=False,
        )
        ops = TaskFileOps(MagicMock())
        d1 = ops.gen_path('/storage', course, f1)
        d2 = ops.gen_path('/storage', course, f2)
        # Both paths should be equivalent after normalization
        assert d1 == d2, (
            f'Trailing slash should be normalized: '
            f'{d1!r} != {d2!r}'
        )


# =========================================================================
# On-page book: section_summary interaction
# =========================================================================
class TestBookWithSectionSummary:
    """An on-page book module's section has both the book
    (processed via fetched_mods) AND a section summary (HTML
    description of the section). The section summary is added
    by get_files_in_sections after _get_files_in_modules.
    """

    def test_section_summary_added_alongside_book_files(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books'}
        # Simulate a section with one book module + a summary
        course_sections = [
            {
                'id': 1,
                'name': 'Analyse data',
                'summary': '<p>Section intro text</p>',
                'modules': [
                    {
                        'id': 100,
                        'name': 'My Book',
                        'modname': 'book',
                        'contents': [],
                    },
                ],
            },
        ]
        # The book is in fetched_mods
        fetched_mods = {
            'book': {
                100: {
                    'id': 100,
                    'name': 'My Book',
                    'files': [
                        {'type': 'file', 'filename': 'ch1.html',
                         'filepath': '/1/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'},
                    ],
                },
            },
        }
        files = rb.get_files_in_sections(course_sections, fetched_mods)
        # The book file + section summary file should both appear
        assert len(files) >= 2
        # Section summary has modname='section_summary'
        # Book file has modname='book'
        modnames = {f.module_modname for f in files}
        assert 'book' in modnames
        assert 'section_summary' in modnames


# =========================================================================
# Off-page assign with assign_file attachment
# =========================================================================
class TestOffPageAssignWithAssignmentFile:
    """An assignment (off main page) with its actual
    submission_file attachment. The attachment (type='file')
    is NOT a singleton — the module folder is kept.
    """

    def test_off_page_assign_with_file_attachment_keeps_folder(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'assign': 'Assignments'}
        fetched_mods = {
            'assign': {
                100: {
                    'id': 100,
                    'name': 'Lab 1',
                    'files': [
                        {'type': 'file', 'filename': 'intro.html',
                         'filepath': '/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'},
                        {'type': 'file', 'filename': 'template.docx',
                         'filepath': '/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'},
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # Both files reach download
        assert len(files) == 2
        # Both should have has_attachments=True (multi-file module)
        for f in files:
            assert f._module_has_attachments is True

    def test_off_page_assign_singleton_description_only(self):
        """An assignment with only its description HTML
        (no submission_file, no attachments) is a
        singleton — gets flattened.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'assign': 'Assignments'}
        fetched_mods = {
            'assign': {
                100: {
                    'id': 100,
                    'name': 'Lab 1',
                    'files': [
                        # Note: 'description' (not 'file') — the
                        # assign module's intro HTML. Real CS2
                        # data shows assign intros are type='description'.
                        {'type': 'description', 'filename': 'intro.html',
                         'filepath': '/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'description'},
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # Singleton description → has_attachments=False
        assert len(files) == 1
        assert files[0]._module_has_attachments is False


# =========================================================================
# gen_path for cookie_mod video in book chapter
# =========================================================================
class TestCookieModVideoInBookChapterGenPath:
    """A Kaltura video embedded in a book chapter has
    module_modname='cookie_mod-kalvidres' (overridden by
    _handle_files from book.py's iframe extraction). gen_path
    hits the cookie_mod branch → module folder + chapter folder.
    """

    def test_cookie_mod_in_book_chapter_uses_module_folder(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='S', section_id=1,
            module_name='My Book',
            content_filepath='/1. Chapter 1/',
            content_filename='Lecture.mp4',
            content_fileurl='https://kaltura.example.com/embed/123',
            content_filesize=0, content_timemodified=0,
            module_modname='cookie_mod-kalvidres',
            content_type='cookie_mod',
            content_isexternalfile=True,
        )
        # cookie_mod-kalvidres is not in
        # MODULE_DIRECTORY_SUFFIXES (which is module_module_dir
        # ending strings). It IS in MODULE_DIRECTORY_MODNAMES.
        # Both branches hit path_of_file_in_module. Verify the
        # cookie_mod branch.
        ops = TaskFileOps(MagicMock())
        dest = ops.gen_path('/storage', course, f)
        # Module folder + chapter folder
        assert '/My Book/' in dest
        assert '/1. Chapter 1' in dest  # trailing / stripped
        # _in_module_folder set
        assert f._in_module_folder is True


# =========================================================================
# Multiple off-page modules in different modnames
# =========================================================================
class TestMultipleOffPageModuleTypes:
    """A course can have off-page modules of different
    modnames (book + assign + lti + h5pactivity + forum).
    Each gets its own '<plural> not on main page' section.
    """

    def test_off_page_different_modnames_separate_sections(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {
            'book': 'Books', 'assign': 'Assignments',
            'lti': 'Ltis', 'h5pactivity': 'H5pactivities',
            'forum': 'Forums',
        }
        fetched_mods = {
            'book': {100: {'id': 100, 'name': 'B',
                            'files': [{'type': 'file', 'filename': 'b.html',
                                      'filepath': '/', 'fileurl': 'x',
                                      'filesize': 100, 'timemodified': 0,
                                      'type': 'file'}]}},
            'assign': {200: {'id': 200, 'name': 'A',
                              'files': [{'type': 'file', 'filename': 'a.html',
                                        'filepath': '/', 'fileurl': 'x',
                                        'filesize': 100, 'timemodified': 0,
                                        'type': 'file'}]}},
            'lti': {300: {'id': 300, 'name': 'L',
                          'files': [{'type': 'file', 'filename': 'l.html',
                                    'filepath': '/', 'fileurl': 'x',
                                    'filesize': 100, 'timemodified': 0,
                                    'type': 'file'}]}},
            'h5pactivity': {400: {'id': 400, 'name': 'H',
                                  'files': [{'type': 'file', 'filename': 'h.html',
                                            'filepath': '/', 'fileurl': 'x',
                                            'filesize': 100, 'timemodified': 0,
                                            'type': 'file'}]}},
            'forum': {500: {'id': 500, 'name': 'F',
                            'files': [{'type': 'file', 'filename': 'f.html',
                                      'filepath': '/', 'fileurl': 'x',
                                      'filesize': 100, 'timemodified': 0,
                                      'type': 'file'}]}},
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # All 5 files reach download
        assert len(files) == 5
        # Each module_id appears exactly once
        module_ids = {f.module_id for f in files}
        assert module_ids == {100, 200, 300, 400, 500}
        # Each has its own synthetic section_name based on mod_plural
        section_names = {f.section_name for f in files}
        # The section_name is set per modname iteration, so all
        # 5 files have 5 different section names
        assert len(section_names) == 5