# -*- coding: utf-8 -*-
"""
Tests for the section-wide position_index assignment behavior.

The *NN* filename prefix that moodle-dl prepends to downloaded
files is assigned at the point of file discovery (in
``ResultBuilder._assign_positions_to_files``), and then consumed
by the downloader when the actual filename is generated (in
``task_file_ops.TaskFileOps.generate_filename_with_index``).

Position indices are assigned as the files are being added to the
download queue — i.e. "边下载边 global-section-indexing", as the
user requested. There is no opt-in flag: section-wide indexing is
the default behavior.

The scope key is ``(section_id, book_module_id)``:

  * section_id: every file in the same Moodle section shares one
    0-based counter, matching the order the Moodle server
    returns them in (the official ``get_sequence_cm_infos`` in
    ``moodle_official_repo_for_reference/public/course/classes/
    section_info.php:514`` uses ``course_sections.sequence``).
  * book_module_id: only set for the 'book' modname. Every book
    chapter is its own "booklet" and gets its own 0-based
    counter. All other modnames (page, assign, quiz, label, url,
    cookie_mod-kalvidres, cookie_mod-helixmedia, ...) share the
    section-wide counter.

The net effect on disk: within a section, the *NN* prefix runs
sequentially across all sub-folders, in server order. The only
places where the counter resets are book chapter boundaries
(preserving the per-chapter contract).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Default behavior: section-wide indexing is the ONLY behavior
# =========================================================================
class TestSectionWideIndexingIsDefault:
    """Pin the contract: section-wide indexing is the default
    behavior, with no opt-in flag. The ResultBuilder must apply
    section-wide position_index assignment on every call to
    ``_assign_positions_to_files`` — there is no alternative
    scope function, no opt-in setter, no CLI flag.

    Cross-checked against:
      - moodle_official_repo_for_reference/public/course/classes/
        section_info.php:514 (get_sequence_cm_infos)
      - moodle_official_repo_for_reference/public/course/classes/
        modinfo.php:1271 (calculate_section_weights)
    """

    def test_two_modules_in_same_section_get_sequential_indices(self):
        """Two modules in the same section, each in a different
        sub-folder, get SEQUENTIAL indices (0, 1) — not
        independent (0, 0) per sub-folder.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        f1 = _make_file(section_id=1, module_id=10, filepath='/A/',
                        filename='a.pdf', modname='resource')
        f2 = _make_file(section_id=1, module_id=11, filepath='/B/',
                        filename='b.pdf', modname='resource')

        rb = _make_result_builder()
        rb._assign_positions_to_files([f1, f2])

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

        rb = _make_result_builder()
        rb._assign_positions_to_files(files)

        assert [f.position_in_section for f in files] == [0, 1, 2]

    def test_multiple_files_in_same_module_get_sequential_indices(self):
        """A single module with 3 files gets 0, 1, 2 (the section-
        wide counter is shared across files in the same module too).
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

        rb = _make_result_builder()
        rb._assign_positions_to_files(files)

        assert [f.position_in_section for f in files] == [0, 1, 2]

    def test_sections_are_independent(self):
        """Even with section-wide indexing, DIFFERENT sections
        still get independent counters (otherwise Week 1 and
        Week 2 would both start at *01* and collide).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        s1a = _make_file(section_id=1, module_id=10, filepath='/A/',
                        filename='a.pdf', modname='resource')
        s1b = _make_file(section_id=1, module_id=11, filepath='/B/',
                        filename='b.pdf', modname='resource')
        s2a = _make_file(section_id=2, module_id=20, filepath='/X/',
                        filename='x.pdf', modname='resource')
        s2b = _make_file(section_id=2, module_id=21, filepath='/Y/',
                        filename='y.pdf', modname='resource')

        rb = _make_result_builder()
        rb._assign_positions_to_files([s1a, s1b, s2a, s2b])

        assert (s1a.position_in_section, s1b.position_in_section) == (0, 1)
        assert (s2a.position_in_section, s2b.position_in_section) == (0, 1)

    def test_system_files_remain_unindexed(self):
        """System files (metadata.json, .* hidden files, etc.) get
        position_in_section = None regardless of the indexing
        mode. Pin: system-file handling is unaffected.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        normal = _make_file(section_id=1, module_id=10, filepath='/',
                            filename='real.pdf', modname='resource')
        hidden = _make_file(section_id=1, module_id=10, filepath='/',
                            filename='.hidden_file', modname='resource')

        rb = _make_result_builder()
        rb._assign_positions_to_files([normal, hidden])

        assert normal.position_in_section == 0
        assert hidden.position_in_section is None


# =========================================================================
# Book modname is the ONE per-module exception
# =========================================================================
class TestBookModnamePerChapterException:
    """Pin the only per-module exception: book chapters each get
    their own 0-based counter. The book modname is special because
    each book chapter is a standalone "booklet" the user navigates
    as its own entity — opening a book chapter in moodle-dl should
    look the same as opening it in the Moodle web UI.
    """

    def test_two_book_chapters_stay_independent(self):
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _make_file(section_id=1, module_id=100, filepath='/Ch1/',
                        filename='p.html', modname='book'),
            _make_file(section_id=1, module_id=200, filepath='/Ch2/',
                        filename='p.html', modname='book'),
        ]

        rb = _make_result_builder()
        rb._assign_positions_to_files(files)

        # Both chapters start at 0 (each chapter is its own booklet)
        assert [f.position_in_section for f in files] == [0, 0]

    def test_book_and_page_in_same_section(self):
        """Page modules share the section-wide counter; book
        chapters keep their per-chapter counter. So a page file
        gets a non-zero position while a book file gets 0.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        page_file = _make_file(section_id=1, module_id=10, filepath='/',
                                filename='p.html', modname='page')
        book_file = _make_file(section_id=1, module_id=20, filepath='/Ch1/',
                                filename='b.html', modname='book')

        rb = _make_result_builder()
        rb._assign_positions_to_files([page_file, book_file])

        # Page is index 0 in the section. Book is also index 0
        # because book is per-chapter scoped, not section-scoped.
        assert page_file.position_in_section == 0
        assert book_file.position_in_section == 0


# =========================================================================
# Integration with ResultBuilder public API
# =========================================================================
class TestNoOptInSetter:
    """Pin: there is no opt-in setter for section-wide indexing.
    The section-wide behavior is hard-coded; you cannot toggle it
    off. This is the contract the user requested: no CLI flag,
    no env var, no setter — just section-wide indexing, always.
    """

    def test_no_set_section_wide_indexing_method(self):
        """ResultBuilder must not expose a set_section_wide_indexing
        method (it was removed when the opt-in was deleted).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert not hasattr(ResultBuilder, 'set_section_wide_indexing'), (
            'ResultBuilder.set_section_wide_indexing must not exist — '
            'section-wide indexing is the only behavior, no opt-in.'
        )

    def test_no_section_wide_indexing_instance_attr(self):
        """ResultBuilder instances must not have a _section_wide_indexing
        attribute (it was removed when the opt-in was deleted).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        assert not hasattr(rb, '_section_wide_indexing'), (
            'ResultBuilder must not have _section_wide_indexing attribute.'
        )


# =========================================================================
# Position-scope key: must be (section_id, book_module_id) only
# =========================================================================
class TestPositionScopeKey:
    """Pin the scope key for a file's position index. Returns
    ``(section_id, book_module_id)``: section_id groups all files
    in the same Moodle section; book_module_id resets the counter
    for each book chapter. Every other modname (page, label, url,
    cookie_mod, ...) shares the section-wide counter.
    """

    def test_page_module_uses_section_scope(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        f = _make_file(section_id=5, module_id=100, filepath='/A/',
                       filename='a.html', modname='page')
        sk = ResultBuilder._position_scope_key(f)
        # (section_id, None) — page shares the section-wide counter
        assert sk == (5, None)

    def test_label_module_uses_section_scope(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        f = _make_file(section_id=5, module_id=100, filepath='/A/',
                       filename='a.md', modname='label')
        sk = ResultBuilder._position_scope_key(f)
        assert sk == (5, None)

    def test_url_module_uses_section_scope(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        f = _make_file(section_id=5, module_id=100, filepath='/A/',
                       filename='a.url', modname='url')
        sk = ResultBuilder._position_scope_key(f)
        assert sk == (5, None)

    def test_cookie_mod_kalvidres_uses_section_scope(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        f = _make_file(section_id=5, module_id=100, filepath='/A/',
                       filename='a.mp4', modname='cookie_mod-kalvidres')
        sk = ResultBuilder._position_scope_key(f)
        # cookie_mod-kalvidres is NOT a book, so it shares the section counter
        assert sk == (5, None)

    def test_assign_module_uses_section_scope(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        f = _make_file(section_id=5, module_id=100, filepath='/A/',
                       filename='a.pdf', modname='assign')
        sk = ResultBuilder._position_scope_key(f)
        assert sk == (5, None)

    def test_book_module_uses_per_chapter_scope(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        f = _make_file(section_id=5, module_id=100, filepath='/Ch1/',
                       filename='b.html', modname='book')
        sk = ResultBuilder._position_scope_key(f)
        # Book modname uses its own module_id as the second key,
        # giving each chapter its own 0-based counter.
        assert sk == (5, 100)

    def test_two_book_chapters_have_different_scope_keys(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        ch1 = _make_file(section_id=5, module_id=100, filepath='/Ch1/',
                         filename='b.html', modname='book')
        ch2 = _make_file(section_id=5, module_id=200, filepath='/Ch2/',
                         filename='b.html', modname='book')
        # Different module_ids → different scope keys → independent counters
        assert ResultBuilder._position_scope_key(ch1) != ResultBuilder._position_scope_key(ch2)


# =========================================================================
# Helpers
# =========================================================================
def _make_result_builder():
    """Build a ResultBuilder with the minimum init needed to call
    ``_assign_positions_to_files``. We bypass __init__ and set the
    required attributes directly because __init__ requires a
    real MoodleURL/version/...).
    """
    from moodle_dl.moodle.result_builder import ResultBuilder
    rb = ResultBuilder.__new__(ResultBuilder)
    return rb


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
