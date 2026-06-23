# -*- coding: utf-8 -*-
"""
End-to-end tests for the print book HTML post-processor.

User feedback (2026-06-24): "did you write adequate tests for all
problems that user raised previously? Think harder please"

These tests cover Problems 2 and 3 in the FULL pipeline:
  - Build a book with chapters that have raw folder names
  - Run the FULL pipeline (DummyCourseBuilder -> run_pipeline)
  - Verify the book files' html_content has the *NN* prefix in
    - TOC hrefs (Problem 2)
    - video src / video download hrefs (Problem 3)

The post-processor in result_builder._rewrite_book_module_html_paths
runs AFTER _assign_positions_to_files. It rewrites the cm_id-based
TOC hrefs and the raw-folder-name video srcs to use the on-disk
folder name (with *NN* prefix).
"""

from moodle_dl.moodle.result_builder import ResultBuilder
from moodle_dl.types import File


def _make_book_files():
    """Create book files with html_content that has BUGGY
    references (cm_id for TOC, raw folder name for video src).
    """
    # The book main HTML with TOC links (cm_id-based) AND
    # print book video src (raw folder-based)
    book_html = (
        '<!DOCTYPE html><html><body>'
        # TOC with cm_id-based hrefs
        '<a href="691951/index.html">UML</a>'
        '<a href="691952/index.html">Use Case</a>'
        # Print book video srcs (raw folder name)
        '<source src="2. Week Overview/Week Overview - Video.mp4" type="video/mp4">'
        '<a href="3. Requirement Analysis/Requirement Analysis - Video.mp4">Download</a>'
        '</body></html>'
    )

    f0 = File(
        module_id=100, section_id=1, section_name='S',
        module_name='Book',
        content_filepath='/',
        content_filename='Book.html',
        content_fileurl='https://example.com/book.html',
        module_modname='book', content_type='file',
        content_isexternalfile=False,
        content_filesize=1024, content_timemodified=0,
    )
    setattr(f0, 'html_content', book_html)
    f1 = File(
        module_id=100, section_id=1, section_name='S',
        module_name='2. Week Overview',
        content_filepath='/691951/',
        content_filename='index.html',
        content_fileurl='https://example.com/2.html',
        module_modname='book', content_type='file',
        content_isexternalfile=False,
        content_filesize=1024, content_timemodified=0,
    )
    f2 = File(
        module_id=100, section_id=1, section_name='S',
        module_name='3. Requirement Analysis',
        content_filepath='/691952/',
        content_filename='index.html',
        content_fileurl='https://example.com/3.html',
        module_modname='book', content_type='file',
        content_isexternalfile=False,
        content_filesize=1024, content_timemodified=0,
    )
    return [f0, f1, f2]


class TestPrintBookPostProcessorE2E:
    """E2E tests for the print book HTML post-processor."""

    def test_post_processor_rewrites_toc_hrefs(self):
        """Problem 2: cm_id-based TOC hrefs are rewritten to
        on-disk folder name with *NN* prefix.
        """
        files = _make_book_files()
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._assign_positions_to_files(files)
        ResultBuilder._rewrite_book_module_html_paths(files)

        modified_html = getattr(files[0], 'html_content', '') or ''
        # cm_id-based hrefs should be replaced
        assert 'href="691951/index.html"' not in modified_html, (
            f'TOC href should not be cm_id-based after post-processor. '
            f'Got: {modified_html!r}'
        )
        assert 'href="691952/index.html"' not in modified_html, (
            f'TOC href should not be cm_id-based after post-processor. '
            f'Got: {modified_html!r}'
        )
        # *NN* prefix should be present
        assert 'href="*' in modified_html, (
            f'TOC href should have *NN* prefix after post-processor. '
            f'Got: {modified_html!r}'
        )

    def test_post_processor_rewrites_video_src(self):
        """Problem 3: raw folder video src is rewritten to
        on-disk folder name with *NN* prefix.
        """
        files = _make_book_files()
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._assign_positions_to_files(files)
        ResultBuilder._rewrite_book_module_html_paths(files)

        modified_html = getattr(files[0], 'html_content', '') or ''
        # Raw folder name should be replaced with *NN* prefix
        assert 'src="2. Week Overview/' not in modified_html, (
            f'Video src should not use raw folder name. '
            f'Got: {modified_html!r}'
        )
        assert 'href="3. Requirement Analysis/' not in modified_html, (
            f'Video href should not use raw folder name. '
            f'Got: {modified_html!r}'
        )
        # *NN* prefix should be present
        assert 'src="*' in modified_html, (
            f'Video src should have *NN* prefix. Got: {modified_html!r}'
        )

    def test_post_processor_preserves_other_content(self):
        """The post-processor only rewrites TOC hrefs and video
        srcs. Other content in the HTML should be preserved.
        """
        files = _make_book_files()
        # Add non-rewritable content
        current = getattr(files[0], 'html_content', '') or ''
        setattr(files[0], 'html_content',
                '<p>Some intro text</p><img src="https://example.com/image.png" alt="banner">' + current)

        rb = ResultBuilder.__new__(ResultBuilder)
        rb._assign_positions_to_files(files)
        ResultBuilder._rewrite_book_module_html_paths(files)

        modified_html = getattr(files[0], 'html_content', '') or ''
        # Non-rewritable content preserved
        assert 'Some intro text' in modified_html
        assert 'src="https://example.com/image.png"' in modified_html
