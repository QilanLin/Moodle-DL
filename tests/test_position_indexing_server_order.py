# -*- coding: utf-8 -*-
"""
Tests that pin the section-wide position-indexing behavior to MATCH
the Moodle server's `course_sections.sequence` order.

Reference: moodle_official_repo_for_reference/public/course/classes/
section_info.php:514 (`get_sequence_cm_infos`) and modinfo.php:1271
(`calculate_section_weights`). The Moodle server returns modules
within a section in the order defined by `course_sections.sequence`,
a comma-separated list of course_module ids. The mobile app and
moodle-dl consume this order directly from the API response.

These tests pin the position_index assignment so that, when the
section-wide opt-in is enabled, the resulting *NN* numbers run
sequentially across the section in the SAME order as the Moodle
server's course_sections.sequence.

Two important properties:
  1. cookie_mod-kalvidres / cookie_mod-helixmedia videos are part
     of the section's sequential order (they live in the same
     course page, shown after the previous module). Each lecture
     part module is ONE module in the sequence, so the *NN*
     numbering should NOT restart per module.
  2. Book modname is special: each chapter is a standalone
     "booklet" and should keep per-chapter counters (the historical
     contract from the book modname's perspective).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Server-order alignment: cookie_mod videos should be sequential
# =========================================================================
class TestCookieModVideosInSectionWideOrder:
    """Pin that cookie_mod videos (Kaltura / Helixmedia) get
    sequential *NN* numbers within the section, NOT per-module
    per-chapter counters.

    Real-world reproducer: a "Week 2" section may have 6 lecture-
    part modules, each containing one Kaltura video. With the
    opt-in, those 6 videos should be *05*, *06*, *07*, *08*,
    *09*, *10* (continuing from previous modules), NOT *01* × 6
    (which is what the historical scoped behavior produces because
    cookie_mod-kalvidres is in MODULE_DIRECTORY_MODNAMES, giving
    each video its own module_scope).
    """

    def _make_builder(self, section_wide: bool):
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._section_wide_indexing = section_wide
        return rb

    def test_six_kaltura_videos_in_same_section_get_sequential_numbers(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File

        # 3 label modules (each its own scope) followed by 6
        # cookie_mod-kalvidres modules, each with one file.
        # With section-wide opt-in, the 6 videos should get
        # numbers 3, 4, 5, 6, 7, 8 (continuous), NOT 0, 0, 0, 0, 0, 0.
        files = []
        # 3 label files (server-sorted)
        for i, modid in enumerate([101, 102, 103]):
            files.append(File(
                module_id=modid, section_name='Week 2', section_id=1,
                module_name=f'label {i}', content_filepath='/',
                content_filename=f'label{i}.md',
                content_fileurl='https://example.com/x',
                content_filesize=100, content_timemodified=0,
                module_modname='label', content_type='description',
                content_isexternalfile=False,
            ))
        # 6 Kaltura video modules (server-sorted, each its own module)
        for i, modid in enumerate([201, 202, 203, 204, 205, 206]):
            files.append(File(
                module_id=modid, section_name='Week 2', section_id=1,
                module_name=f'Lecture {i+1}', content_filepath='/',
                content_filename=f'lecture_{i+1}.mp4',
                content_fileurl='https://example.com/x',
                content_filesize=0, content_timemodified=0,
                module_modname='cookie_mod-kalvidres',
                content_type='cookie_mod',
                content_isexternalfile=True,
            ))

        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files(files)

        # 3 labels: 0, 1, 2
        # 6 videos (each unique module_id, so each gets unique scope
        # UNLESS section-wide opt-in collapses the scope for
        # cookie_mod modules): 3, 4, 5, 6, 7, 8
        positions = [f.position_in_section for f in files]
        assert positions == [0, 1, 2, 3, 4, 5, 6, 7, 8], (
            f'Section-wide opt-in should make cookie_mod videos '
            f'continue from previous modules. Expected [0..8], got '
            f'{positions}'
        )

    def test_label_then_video_continue_numbering(self):
        """The simplest case: a label (no module dir) followed by
        a cookie_mod video. The video should be index 1, not 0.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File

        files = [
            File(module_id=100, section_name='S', section_id=1,
                 module_name='label', content_filepath='/',
                 content_filename='label.md',
                 content_fileurl='x', content_filesize=100,
                 content_timemodified=0,
                 module_modname='label', content_type='description',
                 content_isexternalfile=False),
            File(module_id=200, section_name='S', section_id=1,
                 module_name='video', content_filepath='/',
                 content_filename='video.mp4',
                 content_fileurl='x', content_filesize=0,
                 content_timemodified=0,
                 module_modname='cookie_mod-kalvidres',
                 content_type='cookie_mod',
                 content_isexternalfile=True),
        ]
        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files(files)
        assert [f.position_in_section for f in files] == [0, 1]


# =========================================================================
# Book modname is STILL independent (per-chapter counter)
# =========================================================================
class TestBookModnameStillIndependent:
    """Pin: book chapters remain per-chapter scope even with the
    section-wide opt-in. Each chapter is a standalone "booklet"
    that the user navigates as its own entity.
    """

    def _make_builder(self, section_wide: bool):
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._section_wide_indexing = section_wide
        return rb

    def test_two_book_chapters_stay_independent(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File

        # Two book chapters in the same section
        files = [
            File(module_id=100, section_name='S', section_id=1,
                 module_name='Chapter 1', content_filepath='/Ch1/',
                 content_filename='p1.html',
                 content_fileurl='x', content_filesize=100,
                 content_timemodified=0,
                 module_modname='book', content_type='description',
                 content_isexternalfile=False),
            File(module_id=200, section_name='S', section_id=1,
                 module_name='Chapter 2', content_filepath='/Ch2/',
                 content_filename='p1.html',
                 content_fileurl='x', content_filesize=100,
                 content_timemodified=0,
                 module_modname='book', content_type='description',
                 content_isexternalfile=False),
        ]
        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files(files)

        # Both start at 0 (book chapter is its own booklet)
        assert [f.position_in_section for f in files] == [0, 0]


# =========================================================================
# Page/assign modnames — still part of the section's sequential order
# =========================================================================
class TestModuleDirectoryModnamesInSectionWideOrder:
    """Pin: page, assign, quiz, etc. (modules in
    MODULE_DIRECTORY_SUFFIXES that are NOT book) are part of the
    section's sequential order. The section-wide opt-in treats
    them as section-scoped, not module-scoped.
    """

    def _make_builder(self, section_wide: bool):
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._section_wide_indexing = section_wide
        return rb

    def test_two_assign_modules_in_section_get_sequential(self):
        """Two assign modules, each with one file. With the opt-in,
        the second assign should be index 1, not 0 (per-module scope).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File

        files = [
            File(module_id=100, section_name='S', section_id=1,
                 module_name='Assign 1', content_filepath='/',
                 content_filename='a1.pdf',
                 content_fileurl='x', content_filesize=100,
                 content_timemodified=0,
                 module_modname='assign', content_type='resource_file',
                 content_isexternalfile=False),
            File(module_id=200, section_name='S', section_id=1,
                 module_name='Assign 2', content_filepath='/',
                 content_filename='a2.pdf',
                 content_fileurl='x', content_filesize=100,
                 content_timemodified=0,
                 module_modname='assign', content_type='resource_file',
                 content_isexternalfile=False),
        ]
        rb = self._make_builder(section_wide=True)
        rb._assign_positions_to_files(files)
        assert [f.position_in_section for f in files] == [0, 1]
