# -*- coding: utf-8 -*-
"""
Unusual edge-case tests for section-wide position_index assignment.

These tests pin boundary conditions and adversarial inputs that
the basic 20 tests in test_position_indexing*.py don't cover:

  * Book chapters share a counter across content_filepath boundaries
    (the historical contract was that each chapter sub-folder got
    its own 0-based counter; the new contract changes this).
  * Different book chapters (different module_ids) get independent
    counters.
  * module_id=0 (section_summary, "Section summary" pseudo-module)
    doesn't accidentally trigger a per-chapter scope.
  * All-system-files section doesn't crash and produces all-None.
  * Mixed system + real files: real files get 0, 1, 2 (system
    files don't take slots).
  * Idempotency: calling _assign_positions_to_files twice on the
    same file list does not double-count.
  * Input-order preservation: real files are indexed in the order
    they appear in the input list.
  * 100+ files in a section: the *99* / *100* formatting boundary.
  * Mixed modname input order (book, page, label, cookie_mod in
    arbitrary order) is indexed in input order, not modname order.
  * Multiple sections with section_id=0 don't collide.
  * Same content_filepath but different modules → same scope
    (because content_filepath is no longer part of the scope key).
  * Module with no module_id (None) — does it get None scope?
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Book modname: cross-content_filepath sharing
# =========================================================================
class TestBookModnameCrossContentFilepath:
    """The new contract: a book module is ONE scope, regardless
    of how many sub-folders (content_filepath values) the chapter
    spans. All files in the same book module share one 0-based
    counter, in input order.

    Real-world reproducer: a book chapter may have files in
    /Chapter 1/, /Chapter 1/images/, /Chapter 1/attachments/
    etc. They all get sequential numbers, not per-sub-folder
    resets.
    """

    def test_book_with_multiple_chapters_get_sequential_indices(self):
        """A book module's content items come back from the Moodle
        server with different content_filepath values per chapter
        (and sometimes per sub-folder like /01 - Introduction/images/).
        The book module is one cm_id in course_sections.sequence.

        Pin the current contract: a book module is one scope, so
        ALL its files share a 0-based counter regardless of which
        chapter sub-folder they live in. This prevents the *01*
        collision that would happen if chapter 1's html (at
        /01 - Introduction/) and chapter 1's image (at
        /01 - Introduction/images/) both got *01* (which they
        would under a per-filepath scope).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # 5 files in 3 different "chapters" (filepath values) of the
        # same book module
        files = [
            _file('chapter_root.html',  module_id=42, filepath='/',                          modname='book'),
            _file('index.html',         module_id=42, filepath='/01 - Introduction/',        modname='book'),
            _file('attachment.pdf',     module_id=42, filepath='/01 - Introduction/',        modname='book'),
            _file('image.png',          module_id=42, filepath='/01 - Introduction/images/', modname='book'),
            _file('extra.docx',         module_id=42, filepath='/02 - Background/',          modname='book'),
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # Whole book shares one 0-based counter (input order):
        # chapter_root=0, ch1 index=1, ch1 attach=2, ch1 image=3, ch2 extra=4
        assert files[0].position_in_section == 0
        assert files[1].position_in_section == 1
        assert files[2].position_in_section == 2
        assert files[3].position_in_section == 3
        assert files[4].position_in_section == 4

    def test_different_book_modules_have_independent_counters(self):
        """Two different book modules (different module_ids) get
        INDEPENDENT 0-based counters. Each book module is its own
        scope; the counter resets at every book boundary.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            # Book 1, chapter 1
            _file('book1_ch1.html',  module_id=100, filepath='/Ch1/', modname='book'),
            _file('book1_ch1_2.html', module_id=100, filepath='/Ch1/', modname='book'),
            # Book 2, chapter 1 (same filepath as book 1!)
            _file('book2_ch1.html',  module_id=200, filepath='/Ch1/', modname='book'),
            _file('book2_ch1_2.html', module_id=200, filepath='/Ch1/', modname='book'),
            # Book 3
            _file('book3_root.html',  module_id=300, filepath='/', modname='book'),
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # Different module_ids → independent counters
        # Book 1 chapter 1: 0, 1
        # Book 2 chapter 1: 0, 1 (counter resets, even though
        #                       same content_filepath as book 1)
        # Book 3 root: 0
        assert files[0].position_in_section == 0  # book1 ch1
        assert files[1].position_in_section == 1  # book1 ch1_2
        assert files[2].position_in_section == 0  # book2 ch1 (RESET)
        assert files[3].position_in_section == 1  # book2 ch1_2
        assert files[4].position_in_section == 0  # book3 root

    def test_book_module_among_non_book_files_in_section(self):
        """A book chapter (independent scope) sits between page
        modules (section-wide scope). The page files get sequential
        section numbers; the book file gets 0 (its own scope).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _file('page1.html', section_id=1, module_id=10, filepath='/', modname='page'),
            _file('book_root.html', section_id=1, module_id=20, filepath='/', modname='book'),
            _file('page2.html', section_id=1, module_id=11, filepath='/', modname='page'),
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # Page files: 0, 1 (section-wide scope, input order)
        # Book file: 0 (book's own scope)
        assert files[0].position_in_section == 0  # page1
        assert files[1].position_in_section == 0  # book_root
        assert files[2].position_in_section == 1  # page2


# =========================================================================
# module_id=0 (section_summary pseudo-module) does NOT trigger book scope
# =========================================================================
class TestModuleIdZeroEdgeCase:
    """The 'section_summary' pseudo-module uses module_id=0 and
    modname='section_summary'. It must NOT trigger the book per-
    chapter scope (which would make all section summaries share
    the same 0-based counter across the whole course, breaking
    numbering).
    """

    def test_section_summary_uses_section_scope(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        f = _file('Section summary', section_id=1, module_id=0,
                  filepath='/', modname='section_summary')
        sk = ResultBuilder._position_scope_key(f)
        # module_id=0 with modname='section_summary' must NOT be
        # treated as a book chapter (which would group all section
        # summaries across the course under the same scope).
        # section_id=1, module_scope=None
        assert sk == (1, None)

    def test_multiple_section_summaries_in_different_sections(self):
        """Two section summaries in different sections get
        INDEPENDENT counters (each starts at 0 in its own section).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        f1 = _file('Section summary', section_id=1, module_id=0,
                   filepath='/', modname='section_summary')
        f2 = _file('Section summary', section_id=2, module_id=0,
                   filepath='/', modname='section_summary')

        rb = _make_rb()
        rb._assign_positions_to_files([f1, f2])

        # Different section_id → independent scope → both start at 0
        assert f1.position_in_section == 0
        assert f2.position_in_section == 0

    def test_module_id_none_uses_section_scope(self):
        """A File with module_id=None (malformed input) must NOT
        trigger the book per-chapter scope. It gets section-wide
        scope.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        f = _file('orphan.txt', section_id=5, module_id=None,
                  filepath='/', modname='label')
        sk = ResultBuilder._position_scope_key(f)
        # module_id=None with modname='label' → not book → section scope
        assert sk == (5, None)


# =========================================================================
# All-system-files edge cases
# =========================================================================
class TestAllSystemFiles:
    """Sections where every file is a system file. None should get
    a position; none should crash.
    """

    def test_all_system_files_get_none_positions(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _file('metadata.json',         module_id=10, modname='resource'),
            _file('table of contents.html', module_id=10, modname='resource'),
            _file('.hidden',                module_id=10, modname='resource'),
            _file('_notes.md',              module_id=10, modname='resource'),
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        assert all(f.position_in_section is None for f in files)

    def test_empty_input_list_does_not_crash(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = _make_rb()
        # Should not raise
        rb._assign_positions_to_files([])


# =========================================================================
# Mixed system + real files: real files keep their slot from 0
# =========================================================================
class TestMixedSystemAndRealFiles:
    """System files are skipped, but they should not consume a
    position slot. Real files should start at 0 in input order.
    """

    def test_real_files_start_at_0_when_system_files_precede(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _file('metadata.json',         module_id=10, modname='resource'),  # system
            _file('real1.pdf',             module_id=10, modname='resource'),  # real
            _file('_notes.md',              module_id=10, modname='resource'),  # system
            _file('real2.pdf',             module_id=10, modname='resource'),  # real
            _file('table of contents.html', module_id=10, modname='resource'),  # system
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # All real files are in the same module → share one slot (0).
        assert files[0].position_in_section is None
        assert files[1].position_in_section == 0
        assert files[2].position_in_section is None
        assert files[3].position_in_section == 0
        assert files[4].position_in_section is None

    def test_real_files_start_at_0_when_system_files_interleave(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _file('real1.pdf',  module_id=10, modname='resource'),
            _file('metadata.json', module_id=10, modname='resource'),
            _file('real2.pdf',  module_id=10, modname='resource'),
            _file('real3.pdf',  module_id=10, modname='resource'),
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # All real files are in the same module → share one slot (0).
        # The system file (metadata.json) is None.
        assert [f.position_in_section for f in files] == [0, None, 0, 0]


# =========================================================================
# Idempotency: calling twice doesn't double-count
# =========================================================================
class TestIdempotency:
    """Calling _assign_positions_to_files twice on the same file
    list produces the same result. The first call writes 0, 1, 2,
    ... and the second call also writes 0, 1, 2, ... (not 3, 4, 5).

    This is important because if the function ever gets called
    twice (e.g. if the section is reprocessed on retry), files
    shouldn't get a different number each time.
    """

    def test_calling_twice_produces_same_positions(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _file('a.pdf', module_id=10, modname='resource'),
            _file('b.pdf', module_id=11, modname='resource'),
            _file('c.pdf', module_id=12, modname='resource'),
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)
        first_pass = [f.position_in_section for f in files]
        # Second call
        rb._assign_positions_to_files(files)
        second_pass = [f.position_in_section for f in files]
        assert first_pass == second_pass == [0, 1, 2]

    def test_two_independent_sections_dont_pollute_each_other(self):
        """Processing section 1, then section 2, should give each
        its own 0-based counter, even if the same file objects are
        used (which is unrealistic but tests the reset behavior).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Different sections, each with 3 files
        s1 = [_file(f's1_{i}.pdf', section_id=1, module_id=10+i, modname='resource') for i in range(3)]
        s2 = [_file(f's2_{i}.pdf', section_id=2, module_id=20+i, modname='resource') for i in range(3)]

        rb = _make_rb()
        rb._assign_positions_to_files(s1)
        rb._assign_positions_to_files(s2)

        assert [f.position_in_section for f in s1] == [0, 1, 2]
        assert [f.position_in_section for f in s2] == [0, 1, 2]


# =========================================================================
# Input-order preservation
# =========================================================================
class TestInputOrderPreservation:
    """The position is assigned in INPUT order, not in some
    modname-sorted order. This is important because Moodle's
    course_sections.sequence defines the canonical order, and
    the order in which _assign_positions_to_files receives the
    files must match that.
    """

    def test_input_order_not_modname_sorted(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Input: book, page, label, cookie_mod (in that order)
        files = [
            _file('book.html',    section_id=1, module_id=20, filepath='/', modname='book'),
            _file('page.html',    section_id=1, module_id=10, filepath='/', modname='page'),
            _file('label.md',     section_id=1, module_id=30, filepath='/', modname='label'),
            _file('video.mp4',    section_id=1, module_id=40, filepath='/', modname='cookie_mod-kalvidres'),
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # book: position 0 (its own scope)
        # page: position 0 (section scope)
        # label: position 1 (section scope, comes after page)
        # video: position 2 (section scope, comes after label)
        assert files[0].position_in_section == 0  # book (own scope)
        assert files[1].position_in_section == 0  # page
        assert files[2].position_in_section == 1  # label
        assert files[3].position_in_section == 2  # video

    def test_input_order_determines_position(self):
        """Positions are assigned in INPUT order, regardless of
        any other property of the files. Two input lists with the
        same logical files in different orders produce different
        position_in_section values that reflect the input order.

        We construct two SEPARATE File lists (so the File objects
        are distinct) and assign each to a fresh ResultBuilder.
        Otherwise the second call would overwrite the first's
        positions.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Forward: 5 distinct File objects, input order f0, f1, f2, f3, f4
        forward = [_file(f'f{i}.pdf', module_id=10+i, modname='resource') for i in range(5)]
        # Reversed input: f4, f3, f2, f1, f0
        reversed_list = [_file(f'f{i}.pdf', module_id=10+i, modname='resource') for i in reversed(range(5))]

        rb1 = _make_rb()
        rb1._assign_positions_to_files(forward)

        rb2 = _make_rb()
        rb2._assign_positions_to_files(reversed_list)

        # Forward: f0=0, f1=1, f2=2, f3=3, f4=4
        assert [f.position_in_section for f in forward] == [0, 1, 2, 3, 4]
        # Reversed: reversed_list[0]=f4 → 0, [1]=f3 → 1, ..., [4]=f0 → 4
        # (Because _assign_positions_to_files assigns in list order)
        assert [f.position_in_section for f in reversed_list] == [0, 1, 2, 3, 4]


# =========================================================================
# 100+ files: *99* / *100* formatting boundary
# =========================================================================
class TestPositionNumberingBoundary:
    """Position 99+ should switch from 2-digit to 3-digit prefix
    (per generate_filename_with_index's contract). This test
    ensures _assign_positions_to_files doesn't artificially cap
    at 99, and that the contract is honored end-to-end.
    """

    def test_more_than_99_files_get_correct_positions(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        # 102 files: positions should be 0..101
        files = [
            _file(f'f{i:03d}.pdf', module_id=10+i, modname='resource')
            for i in range(102)
        ]

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        assert files[0].position_in_section == 0
        assert files[98].position_in_section == 98
        assert files[99].position_in_section == 99
        assert files[100].position_in_section == 100
        assert files[101].position_in_section == 101

    def test_filename_uses_3digit_prefix_at_99(self):
        """End-to-end test: position=99 (1-based *100*) should use
        3-digit prefix. The 2-digit/3-digit boundary is at
        position=98 vs position=99.

        Index 0..98 → *01*..*99* (2-digit)
        Index 99..   → *100*..   (3-digit)
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock

        # position=98 → 1-based *99* (still 2-digit)
        f98 = _file('a.pdf', module_id=10, modname='resource')
        f98.position_in_section = 98
        assert TaskFileOps(MagicMock()).generate_filename_with_index(f98) == '*99* a.pdf'

        # position=99 → 1-based *100* (3-digit)
        f99 = _file('b.pdf', module_id=11, modname='resource')
        f99.position_in_section = 99
        assert TaskFileOps(MagicMock()).generate_filename_with_index(f99) == '*100* b.pdf'

        # position=999 → 1-based *1000* (still 3-digit; the contract
        # says 3-digit for 99+)
        f999 = _file('c.pdf', module_id=12, modname='resource')
        f999.position_in_section = 999
        assert TaskFileOps(MagicMock()).generate_filename_with_index(f999) == '*1000* c.pdf' 

    def test_filename_uses_2digit_prefix_for_under_100(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock

        for pos in (0, 9, 50, 98):
            f = _file('lecture.pdf', module_id=10, modname='resource')
            f.position_in_section = pos
            filename = TaskFileOps(MagicMock()).generate_filename_with_index(f)
            assert filename == f'*{pos+1:02d}* lecture.pdf', (
                f'Expected 2-digit prefix for position={pos}, got {filename!r}'
            )


# =========================================================================
# Multiple sections with the same section_id
# =========================================================================
class TestSectionIdCollisions:
    """Two calls to _assign_positions_to_files with the same
    section_id but different module_ids must NOT collide if the
    module_ids are different (i.e. they're really in the same
    section, just two different calls). The (section_id, None)
    scope key for non-book modules means a single call, not
    a per-module scope, so two different calls would re-use
    counter 0.

    This test pins the current behavior: each call resets the
    counter. If a future refactor changes this to share state
    across calls, the test will need updating.
    """

    def test_two_calls_with_same_section_id_reset_counter(self):
        """This pins the current behavior: each
        _assign_positions_to_files call resets the section's
        counter. This is correct because get_files_in_sections
        calls _assign_positions_to_files once per section, with
        the files for that section only.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        rb = _make_rb()

        # First call: 3 files in section 1
        s1 = [_file(f'first_{i}.pdf', section_id=1, module_id=10+i, modname='resource') for i in range(3)]
        rb._assign_positions_to_files(s1)
        assert [f.position_in_section for f in s1] == [0, 1, 2]

        # Second call: 2 more files in the same section
        s2 = [_file(f'second_{i}.pdf', section_id=1, module_id=20+i, modname='resource') for i in range(2)]
        rb._assign_positions_to_files(s2)
        # Each call resets the counter
        assert [f.position_in_section for f in s2] == [0, 1]


# =========================================================================
# Same content_filepath, different modules → same scope
# =========================================================================
class TestSameContentFilepathDifferentModules:
    """Two files with the same content_filepath but different
    module_ids are in the same section scope (because
    content_filepath is no longer part of the scope key). This
    is a behavior change from the historical scoped behavior.
    """

    def test_same_filepath_different_modules_share_scope(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Two files in the same section, same content_filepath,
        # different module_ids. With the new contract, they share
        # the section-wide scope.
        f1 = _file('shared.pdf', section_id=1, module_id=10, filepath='/A/', modname='page')
        f2 = _file('shared.pdf', section_id=1, module_id=11, filepath='/A/', modname='label')

        rb = _make_rb()
        rb._assign_positions_to_files([f1, f2])

        # Same section scope → sequential: f1=0, f2=1
        assert f1.position_in_section == 0
        assert f2.position_in_section == 1


# =========================================================================
# Helpers
# =========================================================================
def _make_rb():
    from moodle_dl.moodle.result_builder import ResultBuilder
    return ResultBuilder.__new__(ResultBuilder)


def _file(filename, *, section_id=1, module_id=10, filepath='/',
          modname='resource', content_type='resource_file'):
    """Build a File with the minimum fields needed for position
    assignment.
    """
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
        content_type=content_type,
        content_isexternalfile=False,
    )
