# -*- coding: utf-8 -*-
"""
Tests for the section-wide position_index assignment behavior.

Background:

    When moodle-dl downloads a Moodle section (e.g. "Week 2 - Inductive
    learning"), the section may contain multiple modules (resource,
    label, page, etc.) and each module may have multiple files. The
    moodle-dl code uses the `position_in_section` field on each
    File object to generate a `*NN*` prefix in the filename, where
    NN is 1-based, 0-padded.

    The historical behavior (and the default today) is to compute
    the position index SCOPED to a (section_id, module_id, content_filepath)
    triple. This means a single section like Week 2 produces multiple
    independent index counters — one per sub-folder. Concretely:

        Week 2/
        ├── ADDITIONAL READING/
        │   ├── *11* ADDITIONAL READING.md            <-- index 0 in scope
        │   └── ...
        ├── Lecture 2： Inductive learning/
        │   ├── *02* Lecture 2：...html.md              <-- index 0 in scope
        │   └── *03* Lecture 2：...pdf
        ├── Lecture 2： LGT/
        │   └── *04* Lecture 2： LGT.pdf
        ...
        └── Practical 2： Regression.../
            ├── *18* Practical 2：...html.md            <-- index 0 in scope
            └── *19* Practical 2：...pdf

    In the user's mental model, the section is one ordered list, so
    they expect `*01*`, `*02*`, ..., `*NN*` running across the whole
    section. The historical scoped behavior is technically correct
    ("each sub-folder starts at *01*") but visually confusing.

    The opt-in fix: when `opts.global_section_indexing = True`,
    position indices are assigned across the WHOLE section,
    ignoring content_filepath. The book modname remains a special
    case (each book chapter is a standalone "booklet" and gets its
    own 0-based counter — preserving the historical per-chapter
    contract from the book's perspective).

These tests pin the contract.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Position-scope semantics: scoped (default) vs section-wide (opt-in)
# =========================================================================
class TestScopedIndexingDefault:
    """Pin the historical 'each scope gets its own 0-based counter'
    behavior. This is the default; tests in
    TestSectionWideIndexingOptIn verify the opt-in behavior.
    """

    def _make_builder(self, section_wide: bool):
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._section_wide_indexing = section_wide
        return rb

    def test_two_modules_in_same_section_get_independent_indices(self):
        """Without the opt-in flag, two modules with different
        content_filepath in the same section get INDEPENDENT
        counters. Both start at 0. This is the current (default)
        behavior.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Two modules in the same section, each landing in a
        # different sub-folder (different content_filepath)
        f1 = _make_file(section_id=1, module_id=10, filepath='/A/',
                        filename='a.pdf', modname='resource')
        f2 = _make_file(section_id=1, module_id=11, filepath='/B/',
                        filename='b.pdf', modname='resource')

        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = self._make_builder(section_wide=False)
        rb._assign_positions_to_files([f1, f2])

        # Both start at 0 because the scopes are different
        assert f1.position_in_section == 0
        assert f2.position_in_section == 0


class TestSectionWideIndexingOptIn:
    """Pin the opt-in behavior: when
    ResultBuilder is initialized with a ResultBuilder._SECTION_WIDE_INDEXING
    class flag, position indices are assigned across the whole
    section, ignoring content_filepath. Book chapters remain
    independent (per-chapter counter preserved).
    """

    def _make_builder(self, section_wide: bool):
        """Return a ResultBuilder with the section-wide opt-in
        set/unset. We bypass __init__ and set the flag directly
        so we don't need a real MoodleURL/version.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._section_wide_indexing = section_wide
        return rb

    def test_two_modules_in_same_section_get_sequential_indices(self):
        """Opt-in: two modules in the same section, each in a
        different sub-folder, get SEQUENTIAL indices (0, 1)
        instead of both being 0.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        f1 = _make_file(section_id=1, module_id=10, filepath='/A/',
                        filename='a.pdf', modname='resource')
        f2 = _make_file(section_id=1, module_id=11, filepath='/B/',
                        filename='b.pdf', modname='resource')

        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files([f1, f2])

        # Sequential, not independent
        assert f1.position_in_section == 0
        assert f2.position_in_section == 1

    def test_three_modules_in_section_get_indices_0_1_2(self):
        """Three modules, three sub-folders: indices are 0, 1, 2
        (running across the whole section).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _make_file(section_id=1, module_id=10, filepath='/A/',
                        filename='a.pdf', modname='resource'),
            _make_file(section_id=1, module_id=11, filepath='/B/',
                        filename='b.pdf', modname='resource'),
            _make_file(section_id=1, module_id=12, filepath='/C/',
                        filename='c.pdf', modname='resource'),
        ]

        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files(files)

        assert [f.position_in_section for f in files] == [0, 1, 2]

    def test_multiple_files_in_same_module_get_sequential_indices(self):
        """A single module with 3 files gets 0, 1, 2 (this is the
        same as the default behavior; the opt-in doesn't change
        same-module ordering).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _make_file(section_id=1, module_id=10, filepath='/M/',
                        filename='one.pdf', modname='resource'),
            _make_file(section_id=1, module_id=10, filepath='/M/',
                        filename='two.pdf', modname='resource'),
            _make_file(section_id=1, module_id=10, filepath='/M/',
                        filename='three.pdf', modname='resource'),
        ]

        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files(files)

        assert [f.position_in_section for f in files] == [0, 1, 2]

    def test_sections_are_still_independent(self):
        """Even with section-wide indexing, DIFFERENT sections
        still get independent counters (otherwise Week 1 and
        Week 2 would both start at *01* and collide).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Section 1: 2 files → indices 0, 1
        s1a = _make_file(section_id=1, module_id=10, filepath='/A/',
                        filename='a.pdf', modname='resource')
        s1b = _make_file(section_id=1, module_id=11, filepath='/B/',
                        filename='b.pdf', modname='resource')
        # Section 2: 2 files → indices 0, 1 (NOT 2, 3)
        s2a = _make_file(section_id=2, module_id=20, filepath='/X/',
                        filename='x.pdf', modname='resource')
        s2b = _make_file(section_id=2, module_id=21, filepath='/Y/',
                        filename='y.pdf', modname='resource')

        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files([s1a, s1b, s2a, s2b])

        assert (s1a.position_in_section, s1b.position_in_section) == (0, 1)
        assert (s2a.position_in_section, s2b.position_in_section) == (0, 1)

    def test_book_chapters_remain_independent(self):
        """The book modname is special: each chapter is a
        standalone 'booklet' and must get its own 0-based
        counter. This preserves the historical book contract.
        Even with section-wide opt-in, two book chapters in
        the same section get INDEPENDENT counters (each starts
        at 0).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Two book chapters (each is its own module_id) in the same section
        f1 = _make_file(section_id=1, module_id=10, filepath='/Chapter 1/',
                        filename='page.html', modname='book')
        f2 = _make_file(section_id=1, module_id=11, filepath='/Chapter 2/',
                        filename='page.html', modname='book')

        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files([f1, f2])

        # Book chapters still independent: both at 0
        assert f1.position_in_section == 0
        assert f2.position_in_section == 0

    def test_book_and_resource_in_same_section_sequential(self):
        """A book chapter (independent scope) and a page module
        (section-wide scope) in the same section should get
        SEQUENTIAL indices across the whole section, because
        the section-wide counter treats page as part of the
        section. The book chapter keeps its own 0-based counter
        (per-chapter is its own booklet).
        Pin the contract: book IS independent even in section-wide mode.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Section: [page1, page2, book_chapter1, book_chapter2]
        r1 = _make_file(section_id=1, module_id=10, filepath='/',
                        filename='a.html', modname='page')
        r2 = _make_file(section_id=1, module_id=11, filepath='/',
                        filename='b.html', modname='page')
        b1 = _make_file(section_id=1, module_id=20, filepath='/Ch1/',
                        filename='p.html', modname='book')
        b2 = _make_file(section_id=1, module_id=21, filepath='/Ch2/',
                        filename='p.html', modname='book')

        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files([r1, r2, b1, b2])

        # Page modules: 0, 1 (section-wide, sequential)
        # Book chapters: 0, 0 (each chapter is its own booklet)
        assert (r1.position_in_section, r2.position_in_section) == (0, 1)
        assert (b1.position_in_section, b2.position_in_section) == (0, 0)

    def test_system_files_remain_unindexed(self):
        """System files (metadata.json, .* hidden files, etc.) get
        position_in_section = None regardless of the opt-in.
        Pin: opt-in does NOT change system-file handling.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        normal = _make_file(section_id=1, module_id=10, filepath='/',
                            filename='real.pdf', modname='resource')
        hidden = _make_file(section_id=1, module_id=10, filepath='/',
                            filename='.hidden_file', modname='resource')

        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files([normal, hidden])

        assert normal.position_in_section == 0
        assert hidden.position_in_section is None


# =========================================================================
# Integration with get_files_in_sections
# =========================================================================
class TestGetFilesInSectionsRespectsOptIn:
    """Pin that the opt-in flag flows through from MoodleDlOpts
    to get_files_in_sections, so the user can enable it from
    config.json or the CLI.
    """

    def test_opts_flag_propagates_to_section_wide_indexing(self):
        """When MoodleDlOpts.global_section_indexing = True, the
        ResultBuilder uses section-wide indexing.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import MoodleURL
        from unittest.mock import MagicMock

        # A real ResultBuilder
        rb = ResultBuilder(
            moodle_url=MoodleURL(use_http=False, domain='keats.kcl.ac.uk', path='/'),
            version=2024010100,
            mod_plurals={},
            token='t',
        )
        # Opt-in should be False by default (backward compat)
        assert getattr(rb, '_section_wide_indexing', False) is False

        # Setter enables it
        rb.set_section_wide_indexing(True)
        assert rb._section_wide_indexing is True

        # Disable
        rb.set_section_wide_indexing(False)
        assert rb._section_wide_indexing is False


# =========================================================================
# Helpers
# =========================================================================
def _make_file(*, section_id, module_id, filepath, filename, modname):
    """Build a File object with the minimum fields needed for
    position assignment.

    Position assignment reads:
      - file.content_filename (the file's basename)
      - file.section_id
      - file.module_id
      - file.module_modname
      - file.content_filepath
    """
    from moodle_dl.types import File
    f = File(
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
        content_type='resource_file',
        content_isexternalfile=False,
    )
    return f
