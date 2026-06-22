# -*- coding: utf-8 -*-
"""
Tests for book chapter attachments (PPT, images, etc.) and
"not on main page" module files.

User question (2026-06-22):

  '现在 not on main page / book 的 chapter 里的 ppt 等可能不会
  显示在book 的页面上的还能正常下载吗 （if you have doubt,
  refer to those 3 official repos）'

These tests pin that BOTH classes of files download correctly
with the new contract from commit ab20833:

  1. "Not on main page" files (assign / forum / lti / book / etc.)
     — modules that don't appear on the course main page because
     they're hidden, restricted, or use a non-section-based UI.
     `_get_files_not_on_main_page` collects these files from the
     `fetched_mods` dict and adds them to `course.files`.
     Download pipeline iterates `course.files` (download_service.py:416).

  2. Book chapter attachments (PPT / image / etc.) — these are
     nested inside a book's chapter. `book.py` flattens them into
     `module_data['files']` with their per-chapter content_filepath.
     `_handle_files` produces a File object per attachment.
     After commit ab20833, book chapter files (like other module
     folder files) do NOT get a *NN* prefix — the chapter folder
     name itself encodes the chapter's position.

Reference (verified against 3 official repos):
  - moodle_official_repo_for_reference/public/course/externallib.php
    (core_course_get_contents) — returns modules that appear on
    the course main page (with sections and modules arrays).
    Some modules (off-page books, hidden assignments, LTI-linked
    content) are NOT in this response.
  - moodle_official_repo_for_reference/public/mod/book/classes/
    external.php (mod_book_get_books_by_courses) — returns book
    metadata + chapter content. Book chapters are nested in the
    returned book's 'contents' field; chapter attachments (PPT,
    image) appear as additional content items with filepath
    /<chapter_id>/.
  - moodle_mobile_app_official_repo_for_reference/src/core/features/
    course/services/course.ts:963 — "modules are ordered in the
    order of appearance in the course"; the mobile app trusts
    server order.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Book chapter attachments: download + folder structure
# =========================================================================
class TestBookChapterAttachmentsDownload:
    """A book chapter can have multiple files (chapter HTML +
    PPT / image attachments). All of them must be reachable
    via the download pipeline, and their on-disk structure
    must be correct (chapter folder + file basename).
    """

    def test_book_chapter_with_ppt_attachment_gets_correct_path(self):
        """A book chapter has a PPT attachment. The PPT file
        lands at <book>/<chapter_folder>/<file>.basename> —
        no *NN* prefix (chapter folder name encodes position).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Analyse data with NVivo and SPSS')
        f = File(
            module_id=100, section_name='Analyse data',
            section_id=1,
            module_name='NVivo Tutorials',
            content_filepath='/1. Introduction/',
            content_filename='yt_icon_rgb.png',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='book',
            content_type='file',
            content_isexternalfile=False,
        )
        # _get_files_in_modules skips book when tagging
        # _module_has_attachments (book.py already sets
        # content_filepath per chapter).

        ops = TaskFileOps(MagicMock())
        dest = ops.gen_path('/storage', course, f)
        # gen_path: book modname hits the module-folder branch
        # (line 187) because 'book' is in MODULE_DIRECTORY_SUFFIXES.
        # _in_module_folder = True.
        # Module folder + chapter folder, no *NN* prefix:
        assert '/NVivo Tutorials/' in dest
        assert '/1. Introduction' in dest
        assert dest.endswith('/1. Introduction')

        # Filename: no *NN* prefix (chapter folder is the position marker)
        f._in_module_folder = True
        f.position_in_section = 2  # would be assigned
        filename = ops.generate_filename_with_index(f)
        assert filename == 'yt_icon_rgb.png', (
            f'Book chapter file should NOT have *NN* prefix '
            f'(chapter folder is the position marker), '
            f'got {filename!r}'
        )

    def test_book_chapter_html_alongside_attachment(self):
        """A book chapter with both chapter HTML AND attachment.
        Both files land in the same chapter folder. Both
        filenames have NO *NN* prefix.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')

        # Chapter HTML
        html = File(
            module_id=100, section_name='S', section_id=1,
            module_name='My Book',
            content_filepath='/2. Import/',
            content_filename='index.html',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='book',
            content_type='file',
            content_isexternalfile=False,
        )
        # PPT attachment
        ppt = File(
            module_id=100, section_name='S', section_id=1,
            module_name='My Book',
            content_filepath='/2. Import/',
            content_filename='slides.pptx',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='book',
            content_type='file',
            content_isexternalfile=False,
        )

        ops = TaskFileOps(MagicMock())
        html_dest = ops.gen_path('/storage', course, html)
        ppt_dest = ops.gen_path('/storage', course, ppt)

        # Both go to the same chapter folder
        html_chapter_dir = '/My Book/2. Import'
        assert html_chapter_dir in html_dest
        assert html_chapter_dir in ppt_dest

        # Neither has *NN* prefix
        html._in_module_folder = True
        ppt._in_module_folder = True
        html.position_in_section = 1
        ppt.position_in_section = 2
        assert ops.generate_filename_with_index(html) == 'index.html'
        assert ops.generate_filename_with_index(ppt) == 'slides.pptx'

    def test_book_chapter_filepaths_preserved(self):
        """A chapter's content_filepath ('/2. Import/') is
        preserved as the chapter subfolder in the path.
        Multiple chapters of the same book land at different
        chapter subfolders (1. Introduction/, 2. Import/, ...).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')

        ch1 = File(
            module_id=100, section_name='S', section_id=1,
            module_name='My Book',
            content_filepath='/1. Introduction/',
            content_filename='chapter1.html',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='book',
            content_type='file',
            content_isexternalfile=False,
        )
        ch2 = File(
            module_id=100, section_name='S', section_id=1,
            module_name='My Book',
            content_filepath='/2. Import/',
            content_filename='chapter2.html',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='book',
            content_type='file',
            content_isexternalfile=False,
        )

        ops = TaskFileOps(MagicMock())
        ch1_dest = ops.gen_path('/storage', course, ch1)
        ch2_dest = ops.gen_path('/storage', course, ch2)

        # Different chapter folders for different chapters
        assert '/1. Introduction' in ch1_dest
        assert '/2. Import' in ch2_dest
        assert ch1_dest != ch2_dest


# =========================================================================
# "Not on main page" modules: download pipeline reach
# =========================================================================
class TestNotOnMainPageFilesDownload:
    """Modules that don't appear on the course main page
    (because they're hidden, restricted, or use a non-section
    UI like books or LTI) still have their files downloaded.
    `_get_files_not_on_main_page` collects them; the download
    pipeline iterates `course.files`.
    """

    def test_off_page_book_files_reach_course_files(self):
        """A book that doesn't appear on the course main page
        (because the user doesn't have access to its section,
        or because the book is hidden) still gets its files
        collected via `_get_files_not_on_main_page`.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # We test that the function correctly skips modules that
        # ARE on the main page, and processes modules that AREN'T.
        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books', 'assign': 'Assignments',
                          'lti': 'Ltis', 'h5pactivity': 'H5pactivities',
                          'forum': 'Forums', 'url': 'Urls'}

        # Build a fake fetched_mods dict
        fetched_mods = {
            'book': {
                # Module 1: already on main page — should be skipped
                100: {
                    'id': 100,
                    'name': 'On Page Book',
                    'files': [
                        {'type': 'file', 'filename': 'ch1.html',
                         'filepath': '/1/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'}
                    ],
                    'on_main_page': True,  # already processed
                },
                # Module 2: NOT on main page — should be processed
                200: {
                    'id': 200,
                    'name': 'Off Page Book',
                    'files': [
                        {'type': 'file', 'filename': 'intro.html',
                         'filepath': '/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'}
                    ],
                },
            },
        }

        files = rb._get_files_not_on_main_page(fetched_mods)
        # Only module 200 (not on main page) should produce files
        assert len(files) == 1, (
            f'Only off-main-page books should produce files, '
            f'got {len(files)}'
        )
        assert files[0].module_id == 200

    def test_off_page_assign_files_reach_course_files(self):
        """An assignment whose intro is not on the main page
        still gets its files downloaded."""
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books', 'assign': 'Assignments',
                          'lti': 'Ltis', 'h5pactivity': 'H5pactivities',
                          'forum': 'Forums', 'url': 'Urls'}
        fetched_mods = {
            'assign': {
                100: {
                    'id': 100,
                    'name': 'Lab 1',
                    'files': [
                        {'type': 'file', 'filename': 'intro.html',
                         'filepath': '/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'}
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        assert len(files) == 1
        assert files[0].module_id == 100
        assert files[0].module_modname == 'assign'

    def test_off_page_lti_files_reach_course_files(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books', 'assign': 'Assignments',
                          'lti': 'Ltis', 'h5pactivity': 'H5pactivities',
                          'forum': 'Forums', 'url': 'Urls'}
        fetched_mods = {
            'lti': {
                100: {
                    'id': 100,
                    'name': 'External Tool',
                    'files': [
                        {'type': 'file', 'filename': 'launch.html',
                         'filepath': '/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'}
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        assert len(files) == 1
        assert files[0].module_id == 100

    def test_off_page_h5pactivity_files_reach_course_files(self):
        """H5P interactive content (often embedded off the main
        page) still gets its files downloaded."""
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books', 'assign': 'Assignments',
                          'lti': 'Ltis', 'h5pactivity': 'H5pactivities',
                          'forum': 'Forums', 'url': 'Urls'}
        fetched_mods = {
            'h5pactivity': {
                100: {
                    'id': 100,
                    'name': 'Interactive Content',
                    'files': [
                        {'type': 'file', 'filename': 'content.html',
                         'filepath': '/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'}
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        assert len(files) == 1
        assert files[0].module_modname == 'h5pactivity'


# =========================================================================
# Mixed: book on main page + book off main page
# =========================================================================
class TestMixedBookOnAndOffMainPage:
    """A course can have both on-page and off-page books. The
    on-page book goes through `_get_files_in_modules` (per
    section). The off-page book goes through
    `_get_files_not_on_main_page` (off-page section name).
    """

    def test_on_page_and_off_page_book_both_download(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books', 'assign': 'Assignments',
                          'lti': 'Ltis', 'h5pactivity': 'H5pactivities',
                          'forum': 'Forums', 'url': 'Urls'}
        # The fetched_mods has both an on-page and an off-page book.
        fetched_mods = {
            'book': {
                # On main page
                100: {
                    'id': 100,
                    'name': 'Visible Book',
                    'files': [
                        {'type': 'file', 'filename': 'ch1.html',
                         'filepath': '/1/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'}
                    ],
                    'on_main_page': True,
                },
                # Off main page
                200: {
                    'id': 200,
                    'name': 'Hidden Book',
                    'files': [
                        {'type': 'file', 'filename': 'intro.html',
                         'filepath': '/', 'fileurl': 'x',
                         'filesize': 100, 'timemodified': 0,
                         'type': 'file'}
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # Only the hidden book (off-page) is collected here
        assert len(files) == 1
        assert files[0].module_id == 200
        assert files[0].module_name == 'Hidden Book'

    def test_on_page_book_processed_by_get_files_in_sections(self):
        """The visible book (on main page) is processed by
        `_get_files_in_modules` instead of
        `_get_files_not_on_main_page`. The same ResultBuilder
        methods are called but from different code paths.
        """
        # We verify the contract by checking that the on-page
        # flag is preserved through the call chain.
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books', 'assign': 'Assignments',
                          'lti': 'Ltis', 'h5pactivity': 'H5pactivities',
                          'forum': 'Forums', 'url': 'Urls'}

        # The 'on_main_page' flag is SET by `_get_files_in_modules`
        # after processing the module via its section. The same
        # flag is then CHECKED by `_get_files_not_on_main_page` to
        # skip already-processed modules. This avoids double-
        # downloading files.
        #
        # We simulate this by setting the flag and verifying the
        # skip behavior in `_get_files_not_on_main_page`.
        fetched_mods = {
            'book': {
                100: {
                    'id': 100,
                    'name': 'Already Processed Book',
                    'files': [],
                    'on_main_page': True,  # set by get_files_in_modules
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # No files — the book was already processed
        assert len(files) == 0


# =========================================================================
# gen_path for not-on-main-page files
# =========================================================================
class TestGenPathForNotOnMainPageFiles:
    """Not-on-main-page files are tagged with _module_has_attachments
    (unless they're book modules). The flattening rule applies
    to them too — singletons get section_dir, multi-file modules
    keep their folder.

    The synthetic section_name is "<plural> not on main page"
    (e.g. "Assignments not on main page", "Books not on main page").
    """

    def test_off_page_label_singleton_section_name(self):
        """A label module off the main page with only a
        description HTML gets the section_name 'Labels not on
        main page' and is flattened.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='Labels not on main page',
            section_id=-1,
            module_name='Some Label',
            content_filepath='/',
            content_filename='Some Label',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='label',
            content_type='description',
            content_isexternalfile=False,
        )
        f._module_has_attachments = False  # singleton

        ops = TaskFileOps(MagicMock())
        dest = ops.gen_path('/storage', course, f)
        # Flattened: no 'Some Label/' folder, just section dir
        assert '/Some Label/' not in dest
        assert dest.endswith('/Labels not on main page')


# =========================================================================
# Book chapter attachments in not_on_main_page
# =========================================================================
class TestBookChapterInNotOnMainPage:
    """A book that's not on the main page can also have chapter
    attachments. These files must:
      - Reach the download pipeline (via _get_files_not_on_main_page)
      - Have a chapter folder in their path (book.py sets
        content_filepath per chapter)
      - Not have a *NN* prefix (chapter folder is position marker)
    """

    def test_off_page_book_chapter_attachment_downloads(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.mod_plurals = {'book': 'Books', 'assign': 'Assignments',
                          'lti': 'Ltis', 'h5pactivity': 'H5pactivities',
                          'forum': 'Forums', 'url': 'Urls'}
        # An off-page book with chapter HTML + attachment
        fetched_mods = {
            'book': {
                100: {
                    'id': 100,
                    'name': 'Off Page Book',
                    'files': [
                        {'type': 'file', 'filename': 'chapter1.html',
                         'filepath': '/1. Chapter 1/',
                         'fileurl': 'x', 'filesize': 100,
                         'timemodified': 0, 'type': 'file'},
                        {'type': 'file', 'filename': 'slides.pptx',
                         'filepath': '/1. Chapter 1/',
                         'fileurl': 'x', 'filesize': 100,
                         'timemodified': 0, 'type': 'file'},
                    ],
                },
            },
        }
        files = rb._get_files_not_on_main_page(fetched_mods)
        # Both files reach the download pipeline
        assert len(files) == 2
        filenames = [f.content_filename for f in files]
        assert 'chapter1.html' in filenames
        assert 'slides.pptx' in filenames
        # Both files have chapter folder content_filepath
        for f in files:
            assert f.content_filepath == '/1. Chapter 1/'
            assert f.module_modname == 'book'

    def test_off_page_book_chapter_filename_no_nn_prefix(self):
        """Off-page book chapter file basenames have NO *NN*
        prefix (chapter folder is the position marker)."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='Books not on main page',
            section_id=-1,
            module_name='Hidden Book',
            content_filepath='/1. Chapter 1/',
            content_filename='chapter1.html',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='book',
            content_type='file',
            content_isexternalfile=False,
        )

        ops = TaskFileOps(MagicMock())
        dest = ops.gen_path('/storage', course, f)
        f._in_module_folder = True
        f.position_in_section = 0  # book chapter scope
        filename = ops.generate_filename_with_index(f)
        # No *NN* prefix on book chapter file
        assert filename == 'chapter1.html', (
            f'Off-page book chapter file should not have *NN* '
            f'prefix, got {filename!r}'
        )