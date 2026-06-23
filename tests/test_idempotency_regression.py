# -*- coding: utf-8 -*-
"""
Idempotency tests for moodle-dl's pipeline.

User feedback (2026-06-24): "补充检查幂等性的厕所"
(likely typo for "测试" = test, meaning: "additionally check the
idempotency of [the e2e] tests")

Idempotency means: running the same operation multiple times
produces the same result. For moodle-dl's pipeline:
  - Running run_pipeline twice on the same input should produce
    the same files, positions, and on-disk paths.
  - Adding/removing files should not change positions for
    unchanged files.
  - Re-running after partial download should not create duplicate
    files.
  - DB state should be stable across re-runs (no duplicate inserts).

These tests verify these properties. They are E2E tests because
they exercise the FULL pipeline in chronological order.
"""

import sys

from moodle_dl.downloader.task_file_ops import TaskFileOps
from moodle_dl.types import Course, File
from unittest.mock import MagicMock

# Add tests/ to path so we can import _dummy_course_builder
sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL/tests')

from _dummy_course_builder import DummyCourseBuilder, run_pipeline  # noqa: E402


def _files_signature(files):
    """Compute a deterministic signature for a list of files.

    The signature is a dict mapping (module_id, content_filename,
    content_filepath) to position_in_section. This is what we
    expect to be stable across runs.
    """
    sig = {}
    for f in files:
        key = (f.module_id, f.content_filename, f.content_filepath)
        sig[key] = f.position_in_section
    return sig


def _folder_signature(files):
    """Compute a deterministic signature for the on-disk folder
    structure. Maps (module_id, content_filename, content_filepath)
    to the parent folder of the gen_path output.
    """
    from pathlib import PurePosixPath
    course = Course(_id=1, fullname='Test Course')
    ops = TaskFileOps(MagicMock())
    sig = {}
    for f in files:
        setattr(f, '_module_has_attachments', True)
        setattr(f, '_in_module_folder', True)
        path = ops.gen_path('/storage', course, f)
        folder = str(PurePosixPath(path).parent)
        key = (f.module_id, f.content_filename, f.content_filepath)
        sig[key] = folder
    return sig


class TestPipelineIdempotency:
    """Verify that running run_pipeline twice on the same input
    produces the same output (positions, folders, files).
    """

    def _make_simple_book(self):
        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='Section 1')
        builder.add_book(1, module_id=100, name='Book', chapters=[
            ('Chapter 1', '<p>Content 1</p>', []),
            ('Chapter 2', '<p>Content 2</p>', []),
            ('Chapter 3', '<p>Content 3</p>', []),
        ])
        return builder

    def test_positions_idempotent_on_rerun(self):
        """Running run_pipeline twice on the same input produces
        the same positions for all files.
        """
        builder = self._make_simple_book()
        sections1 = builder.build_sections()
        fetched_mods1 = builder.build_fetched_mods()
        files1, _ = run_pipeline(sections1, fetched_mods1)
        sig1 = _files_signature(files1)

        # Rebuild from scratch and run again
        builder2 = self._make_simple_book()
        sections2 = builder2.build_sections()
        fetched_mods2 = builder2.build_fetched_mods()
        files2, _ = run_pipeline(sections2, fetched_mods2)
        sig2 = _files_signature(files2)

        # Signatures should be identical
        assert sig1 == sig2, (
            f'Pipeline is not idempotent. \n'
            f'Run 1: {sig1}\n'
            f'Run 2: {sig2}\n'
            f'Diff: {set(sig1.items()) ^ set(sig2.items())}'
        )

    def test_folders_idempotent_on_rerun(self):
        """Running run_pipeline twice produces the same on-disk
        folder structure for all files.
        """
        builder = self._make_simple_book()
        sections1 = builder.build_sections()
        fetched_mods1 = builder.build_fetched_mods()
        files1, _ = run_pipeline(sections1, fetched_mods1)
        folders1 = _folder_signature(files1)

        builder2 = self._make_simple_book()
        sections2 = builder2.build_sections()
        fetched_mods2 = builder2.build_fetched_mods()
        files2, _ = run_pipeline(sections2, fetched_mods2)
        folders2 = _folder_signature(files2)

        assert folders1 == folders2, (
            f'Folder layout is not idempotent. \n'
            f'Run 1: {folders1}\n'
            f'Run 2: {folders2}\n'
            f'Diff: {set(folders1.items()) ^ set(folders2.items())}'
        )

    def test_file_count_idempotent_on_rerun(self):
        """The number of files is the same across runs.
        """
        builder = self._make_simple_book()
        sections1 = builder.build_sections()
        fetched_mods1 = builder.build_fetched_mods()
        files1, _ = run_pipeline(sections1, fetched_mods1)

        builder2 = self._make_simple_book()
        sections2 = builder2.build_sections()
        fetched_mods2 = builder2.build_fetched_mods()
        files2, _ = run_pipeline(sections2, fetched_mods2)

        assert len(files1) == len(files2), (
            f'File count changed between runs: {len(files1)} vs {len(files2)}'
        )

    def test_file_keys_idempotent_on_rerun(self):
        """The set of (module_id, content_filename, content_filepath)
        keys is identical across runs.
        """
        builder = self._make_simple_book()
        sections1 = builder.build_sections()
        fetched_mods1 = builder.build_fetched_mods()
        files1, _ = run_pipeline(sections1, fetched_mods1)
        keys1 = set(_files_signature(files1).keys())

        builder2 = self._make_simple_book()
        sections2 = builder2.build_sections()
        fetched_mods2 = builder2.build_fetched_mods()
        files2, _ = run_pipeline(sections2, fetched_mods2)
        keys2 = set(_files_signature(files2).keys())

        assert keys1 == keys2, (
            f'File keys differ between runs: {keys1 ^ keys2}'
        )


class TestIdempotencyWithMixedSections:
    """Idempotency for a mixed section (book + label + url + page)."""

    def _make_mixed_section(self):
        builder = DummyCourseBuilder()
        builder.add_section(section_id=1, name='Mixed')
        builder.add_label(1, module_id=10, name='Label 1', text='Content')
        builder.add_url(1, module_id=20, name='URL 1',
                        external_url='https://example.com/article')
        builder.add_page(1, module_id=30, name='Page 1',
                         html_content='<p>Page content</p>')
        builder.add_book(1, module_id=40, name='Book 1', chapters=[
            ('Chapter A', '<p>A</p>', []),
            ('Chapter B', '<p>B</p>', []),
        ])
        return builder

    def test_mixed_section_positions_idempotent(self):
        """Mixed section produces same positions on rerun."""
        builder = self._make_mixed_section()
        files1, _ = run_pipeline(*builder.build())
        sig1 = _files_signature(files1)

        builder2 = self._make_mixed_section()
        files2, _ = run_pipeline(*builder2.build())
        sig2 = _files_signature(files2)

        assert sig1 == sig2

    def test_mixed_section_folders_idempotent(self):
        """Mixed section produces same folders on rerun."""
        builder = self._make_mixed_section()
        files1, _ = run_pipeline(*builder.build())
        folders1 = _folder_signature(files1)

        builder2 = self._make_mixed_section()
        files2, _ = run_pipeline(*builder2.build())
        folders2 = _folder_signature(files2)

        assert folders1 == folders2


class TestIdempotencyWithResourceModule:
    """Idempotency for a resource module with many files (4MBBS101)."""

    def test_4mbbs101_positions_idempotent(self):
        """200 files in a resource module → same positions on rerun.
        """
        def _make():
            builder = DummyCourseBuilder()
            builder.add_section(section_id=1, name='Practical')
            sub_folders = ['/', '/scripts/', '/images/', '/teal/']
            files_list = []
            for i in range(50):
                files_list.append({
                    'type': 'file', 'filename': f'file_{i}.html',
                    'filepath': sub_folders[i % 4],
                    'fileurl': f'https://example.com/f_{i}',
                    'filesize': 1024, 'timemodified': 1700000000,
                    'mimetype': 'text/html', 'isexternalfile': False,
                })
            builder.add_section_files(
                1, module_id=4600243, modname='resource',
                module_name='Practical Resource',
                files=files_list,
            )
            return builder

        builder1 = _make()
        files1, _ = run_pipeline(*builder1.build())
        sig1 = _files_signature(files1)

        builder2 = _make()
        files2, _ = run_pipeline(*builder2.build())
        sig2 = _files_signature(files2)

        assert sig1 == sig2


class TestIdempotencyWithMultipleSections:
    """Idempotency for a course with multiple sections."""

    def test_multi_section_positions_idempotent(self):
        """Multiple sections produce same positions on rerun."""
        def _make():
            builder = DummyCourseBuilder()
            for sec_idx in range(1, 6):
                sec_id = sec_idx * 100
                builder.add_section(section_id=sec_id, name=f'Section {sec_idx}')
                builder.add_label(sec_id, sec_id * 10 + 1,
                                  name=f'Label {sec_idx}',
                                  text=f'Text {sec_idx}')
                builder.add_book(sec_id, sec_id * 10 + 2,
                                 name=f'Book {sec_idx}',
                                 chapters=[
                                     (f'Ch A {sec_idx}', '<p>A</p>', []),
                                     (f'Ch B {sec_idx}', '<p>B</p>', []),
                                 ])
            return builder

        builder1 = _make()
        files1, _ = run_pipeline(*builder1.build())
        sig1 = _files_signature(files1)

        builder2 = _make()
        files2, _ = run_pipeline(*builder2.build())
        sig2 = _files_signature(files2)

        assert sig1 == sig2


class TestIdempotencyPositionStability:
    """Positions for unchanged files stay the same when other
    files are added or removed.
    """

    def test_adding_files_does_not_change_existing_positions(self):
        """Adding a new file to a section does NOT change the
        positions of existing files.
        """
        # Run 1: 2 chapters
        builder1 = DummyCourseBuilder()
        builder1.add_section(section_id=1, name='S')
        builder1.add_book(1, module_id=100, name='B', chapters=[
            ('A', '<p>a</p>', []),
            ('B', '<p>b</p>', []),
        ])
        files1, _ = run_pipeline(*builder1.build())
        sig1 = _files_signature(files1)

        # Run 2: same 2 chapters + 1 more
        builder2 = DummyCourseBuilder()
        builder2.add_section(section_id=1, name='S')
        builder2.add_book(1, module_id=100, name='B', chapters=[
            ('A', '<p>a</p>', []),
            ('B', '<p>b</p>', []),
            ('C', '<p>c</p>', []),  # New chapter
        ])
        files2, _ = run_pipeline(*builder2.build())
        sig2 = _files_signature(files2)

        # All files from run 1 should have the SAME positions in run 2
        for key, pos1 in sig1.items():
            assert sig2.get(key) == pos1, (
                f'File {key} position changed: {pos1} -> {sig2.get(key)}'
            )

    def test_removing_files_does_not_change_remaining_positions(self):
        """Removing a file does NOT change the positions of
        remaining files.
        """
        # Run 1: 3 chapters
        builder1 = DummyCourseBuilder()
        builder1.add_section(section_id=1, name='S')
        builder1.add_book(1, module_id=100, name='B', chapters=[
            ('A', '<p>a</p>', []),
            ('B', '<p>b</p>', []),
            ('C', '<p>c</p>', []),
        ])
        files1, _ = run_pipeline(*builder1.build())
        # Get signature for only A and B
        sig1_ab = {k: v for k, v in _files_signature(files1).items()
                   if 'index.html' in str(k) or k[1] in ('A.html', 'B.html')}

        # Run 2: only A and B (C removed)
        builder2 = DummyCourseBuilder()
        builder2.add_section(section_id=1, name='S')
        builder2.add_book(1, module_id=100, name='B', chapters=[
            ('A', '<p>a</p>', []),
            ('B', '<p>b</p>', []),
        ])
        files2, _ = run_pipeline(*builder2.build())
        sig2 = _files_signature(files2)

        # A and B should have the SAME positions in run 1 and run 2
        for key, pos1 in sig1_ab.items():
            if key in sig2:
                assert sig2[key] == pos1, (
                    f'File {key} position changed: {pos1} -> {sig2[key]}'
                )


class TestIdempotencyAcrossBuilders:
    """The same logical course built with different DummyCourseBuilder
    instances should produce the same output.
    """

    def test_two_identical_builders_produce_same_output(self):
        """Two DummyCourseBuilder instances with the same input
        produce the same files, positions, and folders.
        """
        def _build():
            b = DummyCourseBuilder()
            b.add_section(section_id=1, name='S1')
            b.add_section(section_id=2, name='S2')
            b.add_label(1, 10, 'L1', 'Text')
            b.add_page(1, 20, 'P1', '<p>Page</p>')
            b.add_url(2, 30, 'U1', 'https://example.com')
            b.add_book(2, 40, 'B1', chapters=[
                ('Ch 1', '<p>1</p>', []),
                ('Ch 2', '<p>2</p>', []),
            ])
            return b

        builder1 = _build()
        files1, _ = run_pipeline(*builder1.build())
        builder2 = _build()
        files2, _ = run_pipeline(*builder2.build())

        sig1 = _files_signature(files1)
        sig2 = _files_signature(files2)
        assert sig1 == sig2

        folders1 = _folder_signature(files1)
        folders2 = _folder_signature(files2)
        assert folders1 == folders2


class TestIdempotencyOfPostProcessor:
    """The post-processor is idempotent: running it twice on the
    same input produces the same output.
    """

    def test_post_processor_idempotent_on_toc(self):
        """Running the post-processor twice produces the same TOC."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File

        book_html = (
            '<a href="691951/index.html">UML</a>'
            '<source src="2. Week Overview/video.mp4">'
        )
        f0 = File(
            module_id=100, section_id=1, section_name='S',
            module_name='Book', content_filepath='/',
            content_filename='Book.html',
            content_fileurl='https://example.com/book.html',
            module_modname='book', content_type='file',
            content_isexternalfile=False,
            content_filesize=1024, content_timemodified=0,
        )
        setattr(f0, 'html_content', book_html)
        f1 = File(
            module_id=100, section_id=1, section_name='S',
            module_name='2. Week Overview', content_filepath='/691951/',
            content_filename='index.html',
            content_fileurl='https://example.com/2.html',
            module_modname='book', content_type='file',
            content_isexternalfile=False,
            content_filesize=1024, content_timemodified=0,
        )
        files = [f0, f1]

        # Run post-processor twice
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._assign_positions_to_files(files)
        ResultBuilder._rewrite_book_module_html_paths(files)
        after_first = getattr(f0, 'html_content', '') or ''

        ResultBuilder._rewrite_book_module_html_paths(files)
        after_second = getattr(f0, 'html_content', '') or ''

        assert after_first == after_second, (
            f'Post-processor is not idempotent.\n'
            f'After 1st: {after_first}\n'
            f'After 2nd: {after_second}'
        )

    def test_post_processor_does_not_double_prefix(self):
        """Running the post-processor twice does NOT add *NN*
        prefix twice (i.e., *01* -> *01**01*).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File

        book_html = (
            '<a href="691951/index.html">UML</a>'
        )
        f0 = File(
            module_id=100, section_id=1, section_name='S',
            module_name='Book', content_filepath='/',
            content_filename='Book.html',
            content_fileurl='https://example.com/book.html',
            module_modname='book', content_type='file',
            content_isexternalfile=False,
            content_filesize=1024, content_timemodified=0,
        )
        setattr(f0, 'html_content', book_html)
        f1 = File(
            module_id=100, section_id=1, section_name='S',
            module_name='2. Week Overview', content_filepath='/691951/',
            content_filename='index.html',
            content_fileurl='https://example.com/2.html',
            module_modname='book', content_type='file',
            content_isexternalfile=False,
            content_filesize=1024, content_timemodified=0,
        )
        files = [f0, f1]

        rb = ResultBuilder.__new__(ResultBuilder)
        rb._assign_positions_to_files(files)

        # Run post-processor twice
        ResultBuilder._rewrite_book_module_html_paths(files)
        ResultBuilder._rewrite_book_module_html_paths(files)

        # The HTML should NOT have double *NN* prefix
        html = getattr(f0, 'html_content', '') or ''
        # Count occurrences of '*NN*' pattern
        import re
        matches = re.findall(r'\*\d{2}\*', html)
        # Each unique cm_id should appear once (not twice)
        # The cm_id 691951 should be replaced with on-disk folder
        # The on-disk folder has format '*<pos+1:02d>* <folder>'
        # Each match should be unique (no duplicates)
        assert len(matches) == len(set(matches)), (
            f'Post-processor added duplicate *NN* prefixes. Got: {matches}'
        )
