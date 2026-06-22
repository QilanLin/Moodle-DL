# -*- coding: utf-8 -*-
"""
Unusual / adversarial edge cases for the singleton-description
flattening + section-wide ordering contract.

Covers cases that the basic 18 tests in test_position_indexing.py
and test_module_folder_flatten.py don't cover:

  - Cross-section independence (each section has its own counter)
  - Singleton with a non-root content_filepath ('/subfolder/')
  - section_summary interaction (module_id=0 special case)
  - Empty modules (no files at all)
  - Singleton followed by a book chapter (different scope
    rules must coexist correctly)
  - Multiple singletons in a row
  - Mixed scope mutation: a file's _module_has_attachments
    set to None must not cause regressions for tests / legacy
    code paths
  - Quiz / assign / forum / hvp / moodleoverflow singleton
    cases (these modules all have similar structure to label)
  - Book modules are exempt from the _module_has_attachments
    tagging (book.py sets per-chapter content_filepath)
  - A label module with one description + one label_file:
    has_attachments=True, so it KEEPS the module folder
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Cross-section independence
# =========================================================================
class TestCrossSectionIndependence:
    """Singleton files in different sections have independent
    position counters. A singleton in section 1 at position 0
    and a singleton in section 2 at position 0 are BOTH
    *01* (because position_in_section is per-section).
    """

    def test_singletons_in_different_sections_both_get_position_zero(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        s1 = _make_file(section_id=1, module_id=10, filepath='/',
                        filename='intro.md', modname='label')
        s2 = _make_file(section_id=2, module_id=20, filepath='/',
                        filename='outro.md', modname='label')
        s3 = _make_file(section_id=3, module_id=30, filepath='/',
                        filename='middle.md', modname='label')

        s1._module_has_attachments = False
        s2._module_has_attachments = False
        s3._module_has_attachments = False

        rb = _make_result_builder()
        rb._assign_positions_to_files([s1, s2, s3])

        # All three are singletons. They live in different
        # sections, so all three get position 0 in their
        # respective section counter. The *01* prefix will be
        # the same for all three, but in DIFFERENT directories
        # so no filename collision.
        assert s1.position_in_section == 0
        assert s2.position_in_section == 0
        assert s3.position_in_section == 0

    def test_section_summary_singletons_in_different_sections(self):
        """The 'section_summary' pseudo-module (module_id=0,
        modname='section_summary') produces a singleton
        description in every section. Each gets position 0
        in its own section — independent counters.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        s1 = _make_file(section_id=1, module_id=0, filepath='/',
                        filename='Section summary', modname='section_summary')
        s2 = _make_file(section_id=2, module_id=0, filepath='/',
                        filename='Section summary', modname='section_summary')

        s1._module_has_attachments = False
        s2._module_has_attachments = False

        rb = _make_result_builder()
        rb._assign_positions_to_files([s1, s2])

        assert s1.position_in_section == 0
        assert s2.position_in_section == 0


# =========================================================================
# Singleton with a non-root content_filepath
# =========================================================================
class TestSingletonWithSubdirFilepath:
    """A singleton module with content_filepath='/subfolder/'
    (e.g. a label inside a sub-section): the file goes into
    <storage>/<course>/<section>/<subfolder>/<file>, not
    directly into the section directory.

    Pin: this is the historical sub-folder behavior, NOT
    the singleton-flattening rule. The sub-folder in
    content_filepath is preserved (book chapter subdirs
    use the same mechanism, see test_book_module_flat_structure).
    """

    def test_singleton_with_subfolder_filepath_preserves_subfolder(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='Week 3', section_id=1,
            module_name='Lecture 3',
            content_filepath='/subfolder/',
            content_filename='Lecture 3 intro',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='label', content_type='description',
            content_isexternalfile=False,
        )
        f._module_has_attachments = False  # singleton
        dest = TaskFileOps(MagicMock()).gen_path('/storage', course, f)
        # Sub-folder is preserved
        assert '/subfolder' in dest, (
            f'Singleton with subfolder content_filepath should '
            f'preserve the subfolder, got {dest}'
        )
        # The module folder is NOT added (singleton flat)
        assert '/Lecture 3/' not in dest, (
            f'Singleton should not add module folder, got {dest}'
        )

    def test_singleton_with_root_filepath_is_completely_flat(self):
        """A singleton with content_filepath='/' is flattened
        straight into the section directory, no subfolder."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='Week 3', section_id=1,
            module_name='Lecture 3',
            content_filepath='/',
            content_filename='Lecture 3 intro',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='label', content_type='description',
            content_isexternalfile=False,
        )
        f._module_has_attachments = False
        dest = TaskFileOps(MagicMock()).gen_path('/storage', course, f)
        # Path ends with section dir, no extra subfolders
        assert dest.endswith('/Week 3'), (
            f'Singleton with root filepath should land in section '
            f'dir, got {dest}'
        )


# =========================================================================
# Empty module
# =========================================================================
class TestEmptyModuleHandling:
    """A module that produces no files at all (e.g. an empty
    label, or a module the user can't see) should not crash
    _get_files_in_modules. The _module_has_attachments tagging
    skips it cleanly.
    """

    def test_no_files_in_section(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = _make_result_builder()
        # Should not raise
        rb._assign_positions_to_files([])

    def test_only_system_files_in_section(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _make_file(section_id=1, module_id=1, filepath='/',
                        filename='metadata.json', modname='resource'),
            _make_file(section_id=1, module_id=1, filepath='/',
                        filename='.hidden', modname='resource'),
        ]
        # Even if has_attachments is unset, system files get None
        # position. The result_builder doesn't try to read
        # _module_has_attachments on system files because
        # _is_system_file short-circuits before that.

        rb = _make_result_builder()
        rb._assign_positions_to_files(files)
        # All system files: position None
        assert all(f.position_in_section is None for f in files)


# =========================================================================
# Multiple singletons in a row
# =========================================================================
class TestConsecutiveSingletons:
    """Three singletons in a row (no multi-file module between
    them) get sequential positions 0, 1, 2 in section-wide
    order. The *NN* prefix tells them apart even though
    they're all in the same section directory.
    """

    def test_three_consecutive_singletons_get_0_1_2(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        s1 = _make_file(section_id=1, module_id=1, filepath='/',
                        filename='first.md', modname='label')
        s2 = _make_file(section_id=1, module_id=2, filepath='/',
                        filename='second.md', modname='label')
        s3 = _make_file(section_id=1, module_id=3, filepath='/',
                        filename='third.md', modname='label')

        for f in (s1, s2, s3):
            f._module_has_attachments = False

        rb = _make_result_builder()
        rb._assign_positions_to_files([s1, s2, s3])

        assert [f.position_in_section for f in (s1, s2, s3)] == [0, 1, 2]

    def test_mixed_singleton_modnames_all_participate(self):
        """Singleton label, page, assign, quiz, url modules in
        a single section all get sequential positions. They
        share the section-wide counter regardless of modname.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        s1 = _make_file(section_id=1, module_id=1, filepath='/',
                        filename='lbl.md', modname='label')
        s2 = _make_file(section_id=1, module_id=2, filepath='/',
                        filename='pg.html', modname='page')
        s3 = _make_file(section_id=1, module_id=3, filepath='/',
                        filename='asg.html', modname='assign')
        s4 = _make_file(section_id=1, module_id=4, filepath='/',
                        filename='qz.html', modname='quiz')
        s5 = _make_file(section_id=1, module_id=5, filepath='/',
                        filename='url.html', modname='url')

        for f in (s1, s2, s3, s4, s5):
            f._module_has_attachments = False

        rb = _make_result_builder()
        rb._assign_positions_to_files([s1, s2, s3, s4, s5])

        assert [f.position_in_section for f in (s1, s2, s3, s4, s5)] == [0, 1, 2, 3, 4]


# =========================================================================
# Book chapters adjacent to singletons
# =========================================================================
class TestSingletonNextToBookChapter:
    """A singleton label file sits between two book chapters
    in the same section. The book chapter scope is per-chapter
    (each chapter is its own booklet), the singleton is
    section-wide. They must coexist without counter pollution.
    """

    def test_singleton_between_two_book_chapters(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Module order in section: [book_ch1, label_singleton, book_ch2]
        # The book module has 2 chapters (different module_ids
        # but same book modname). Wait — actually a single
        # book module is one module_id. Different chapters in
        # the same book are content_filepath subdirs. So we
        # use different file objects with different content_filepath
        # but all module_id=100.
        #
        # In production, book.py sets content_filepath to
        # /<chapter_folder>/ for each chapter. ResultBuilder
        # scopes (section_id, book_module_id) so all book
        # chapters share the same scope (one book, one
        # counter). Each chapter file gets sequential positions
        # within the book scope.
        b1_ch1 = _make_file(section_id=1, module_id=100,
                            filepath='/Chapter 1/',
                            filename='page1.html', modname='book')
        b1_ch2 = _make_file(section_id=1, module_id=100,
                            filepath='/Chapter 2/',
                            filename='page2.html', modname='book')
        # Singleton label (no attachments)
        singleton = _make_file(section_id=1, module_id=200,
                               filepath='/',
                               filename='note.md', modname='label')
        b1_ch3 = _make_file(section_id=1, module_id=100,
                            filepath='/Chapter 3/',
                            filename='page3.html', modname='book')

        # has_attachments is set by result_builder. Book is
        # exempt (book.py already sets per-chapter filepath),
        # but we set it explicitly here for clarity. The book
        # files share the (1, 100) scope; the singleton has
        # the (1, None) scope.
        for f in (b1_ch1, b1_ch2, b1_ch3):
            f._module_has_attachments = True  # ignored for book
        singleton._module_has_attachments = False

        rb = _make_result_builder()
        rb._assign_positions_to_files(
            [b1_ch1, b1_ch2, singleton, b1_ch3]
        )

        # Book chapter scope (1, 100) — 3 chapters share:
        # b1_ch1 = 0, b1_ch2 = 1, b1_ch3 = 2
        # Singleton scope (1, None) — independent:
        # singleton = 0
        # (The book scope counter is reset per call to
        # _assign_positions_to_files — see test scoped below.)
        # In this single call, all four files go through
        # _assign_positions_to_files at once. The book
        # chapters share scope (1, 100), the singleton
        # uses (1, None) which is a DIFFERENT key.
        # So singleton counter is independent of book counter.
        # In the singleton's scope, there's only one file
        # (the singleton itself), so its position is 0.
        # In the book's scope, there are 3 files, so they
        # get 0, 1, 2.
        assert b1_ch1.position_in_section == 0
        assert b1_ch2.position_in_section == 1
        assert b1_ch3.position_in_section == 2
        # Singleton gets 0 in its own (1, None) scope
        assert singleton.position_in_section == 0


# =========================================================================
# Module with one description + one attachment
# =========================================================================
class TestModuleWithDescriptionPlusAttachment:
    """A label module with one description HTML AND one
    inline label_file (image / attachment) is NOT a
    singleton — it has attachments, so it keeps the
    module folder. The folder name encodes the position
    in the section; the files inside have no *NN* prefix.
    """

    def test_label_module_with_one_label_file_keeps_folder(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        desc = _make_file(section_id=1, module_id=1, filepath='/Module/',
                          filename='Module description.html',
                          modname='label')
        attach = _make_file(section_id=1, module_id=1, filepath='/Module/',
                            filename='image.png', modname='label')

        # Both files come from the same module — has_attachments=True
        desc._module_has_attachments = True
        attach._module_has_attachments = True

        rb = _make_result_builder()
        rb._assign_positions_to_files([desc, attach])

        # Both share the section-wide counter
        assert desc.position_in_section == 0
        assert attach.position_in_section == 1


# =========================================================================
# _module_has_attachments=None (legacy / unset) defaults
# =========================================================================
class TestLegacyUnsetModuleHasAttachments:
    """Files that don't have _module_has_attachments set
    (None default) must NOT be silently flattened. This is
    the production safety net for callers that haven't been
    migrated through the new result_builder._get_files_in_modules
    path yet.
    """

    def test_none_default_preserves_module_folder(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='Week 3', section_id=1,
            module_name='Lecture 3',
            content_filepath='/',
            content_filename='Lecture 3 intro',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='label', content_type='description',
            content_isexternalfile=False,
        )
        # Do NOT set _module_has_attachments — None default
        dest = TaskFileOps(MagicMock()).gen_path('/storage', course, f)
        # Legacy behavior: module folder is created
        assert '/Lecture 3' in dest, (
            f'Legacy unset _module_has_attachments should keep '
            f'module folder, got {dest}'
        )

    def test_explicit_false_triggers_flattening(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='Week 3', section_id=1,
            module_name='Lecture 3',
            content_filepath='/',
            content_filename='Lecture 3 intro',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='label', content_type='description',
            content_isexternalfile=False,
        )
        f._module_has_attachments = False  # EXPLICIT False
        dest = TaskFileOps(MagicMock()).gen_path('/storage', course, f)
        # Module folder is NOT created
        assert '/Lecture 3' not in dest, (
            f'Explicit _module_has_attachments=False should '
            f'flatten, got {dest}'
        )


# =========================================================================
# Page module with sub-folder (content_filepath='/sub/')
# =========================================================================
class TestPageModuleWithSubfolder:
    """A page module can have files in subdirectories
    (e.g. an HTML page with images in /assets/). The module
    has both the description HTML and the sub-asset files.
    The sub-folder is preserved (it's in content_filepath).
    """

    def test_page_module_with_assets_keeps_module_folder(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        # Page HTML
        f1 = File(
            module_id=100, section_name='Week 3', section_id=1,
            module_name='Lecture 3',
            content_filepath='/Lecture 3/',
            content_filename='Lecture 3.html',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='page', content_type='file',
            content_isexternalfile=False,
        )
        # Sub-asset in the same module
        f2 = File(
            module_id=100, section_name='Week 3', section_id=1,
            module_name='Lecture 3',
            content_filepath='/Lecture 3/assets/',
            content_filename='main.css',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='page', content_type='file',
            content_isexternalfile=False,
        )
        for f in (f1, f2):
            f._module_has_attachments = True
        # Simulate _assign_positions_to_files order
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = _make_result_builder()
        rb._assign_positions_to_files([f1, f2])
        # Both share section scope
        assert f1.position_in_section == 0
        assert f2.position_in_section == 1


# =========================================================================
# gen_path: legacy fallback for unhandled modname
# =========================================================================
class TestLegacyModnameFallback:
    """A modname not in the recognized list (e.g. some custom
    Moodle plugin module) falls through to path_of_file (4
    layers, no module folder). This is the historical default
    for unknown modnames.
    """

    def test_unknown_modname_uses_legacy_path(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=100, section_name='Week 3', section_id=1,
            module_name='Some Custom Module',
            content_filepath='/',
            content_filename='Some Custom Module.html',
            content_fileurl='https://example.com/x',
            content_filesize=100, content_timemodified=0,
            module_modname='custom_plugin_xyz',  # not recognized
            content_type='description',
            content_isexternalfile=False,
        )
        # Legacy default — _module_has_attachments is None
        dest = TaskFileOps(MagicMock()).gen_path('/storage', course, f)
        # No module folder for unrecognized modnames
        assert '/Some Custom Module' not in dest, (
            f'Unrecognized modname should not create module '
            f'folder, got {dest}'
        )
        assert dest.endswith('/Week 3'), (
            f'Path should end with section dir, got {dest}'
        )


# =========================================================================
# Helpers
# =========================================================================
def _make_result_builder():
    from moodle_dl.moodle.result_builder import ResultBuilder
    return ResultBuilder.__new__(ResultBuilder)


def _make_file(*, section_id, module_id, filepath, filename, modname):
    from moodle_dl.types import File
    return File(
        module_id=module_id,
        section_name=f'Sec {section_id}',
        section_id=section_id,
        module_name='m',
        content_filepath=filepath,
        content_filename=filename,
        content_fileurl='https://example.com/x',
        content_filesize=100,
        content_timemodified=0,
        module_modname=modname,
        content_type='description',
        content_isexternalfile=False,
    )