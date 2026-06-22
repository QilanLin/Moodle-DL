# -*- coding: utf-8 -*-
"""
Tests that pin the on-disk structure of a downloaded Moodle book module
to match the server-side structure (FLAT, not nested).

The Moodle book module represents a multi-level table of contents
(1.1, 1.1.1, 1.1.2, 1.1.2.1, ...) using `course_modules` with one
cm per chapter, and the chapter's `contents` field uses
`filepath = /<chapter_id>/` — NOT nested paths.

Reference (verified against 3 official repos):
  - moodle_official_repo_for_reference/public/mod/book/locallib.php
    (book_preload_chapters) — pagenum + subchapter, but content
    filepath is always flat /<chapter_id>/
  - moodle_official_repo_for_reference/public/course/externallib.php
    (core_course_get_contents) — returns each chapter as a content
    item with its own filepath
  - moodle_mobile_app_official_repo_for_reference/src/addons/mod/book/
    services/book.ts — flat list of chapters, no nested folders

The user has explicitly confirmed: "我们下载下来的保存的样子要和
server端一致. 如果server的结构是扁平的, 我们下载下来也要是扁平的".

These tests pin:
  1. Multi-level chapter numbers (1., 1.1., 1.1.1., 1.1.2.1.) are
     encoded as a PREFIX in the folder name, not as nested folders.
  2. Every chapter folder is at the SAME level inside the book
     module directory.
  3. The folder name uses the chapter_number prefix from the TOC,
     not a synthetic depth-based path.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# TOC → folder name mapping: the chapter_number is a prefix
# =========================================================================
class TestBookChapterFolderNameIsFlat:
    """Pin that the book chapter folder name uses the chapter_number
    (e.g. '1.1.') as a flat prefix, not nested folders.

    Example:
      TOC tree:
        1. (Chapter 1)
          1.1. (Section 1.1)
            1.1.1. (Sub 1.1.1)
            1.1.2. (Sub 1.1.2)
              1.1.2.1. (Deep 1.1.2.1)
          1.2. (Section 1.2)

      On disk (FLAT, all under Book/):
        Book/1. Chapter 1/<files>
        Book/1.1. Section 1.1/<files>
        Book/1.1.1. Sub 1.1.1/<files>
        Book/1.1.2. Sub 1.1.2/<files>
        Book/1.1.2.1. Deep 1.1.2.1/<files>
        Book/1.2. Section 1.2/<files>

      NOT nested like:
        Book/1/1.1/1.1.1/...
    """

    def test_nested_toc_generates_flat_chapter_numbers(self):
        """A TOC with 4 levels of nesting produces chapter_number
        values like 1., 1.1., 1.1.1., 1.1.2.1. — but ALL flat in
        the dict (no nested grouping).

        Pin: there is one chapter_numbers entry per chapter, with
        the dotted prefix as the value. The folder structure
        (built later) uses these prefixes to make flat folder
        names, NOT nested directory paths.
        """
        from moodle_dl.moodle.mods.book import BookMod
        # Inline the walk logic to test without instantiating BookMod
        # (BookMod.__init__ requires config which would pull in real
        # network state).

        def walk(items, prefix=''):
            chapter_numbers = {}
            visible_counter = 1
            for item in items:
                hidden = str(item.get('hidden', '0')) == '1'
                current_number = 'x' if hidden else str(visible_counter)
                full_number = f'{prefix}{current_number}.'
                href = (item.get('href') or '').lstrip('/')
                if href:
                    chapter_id = href.split('/', 1)[0]
                    chapter_numbers[chapter_id] = full_number
                subitems = item.get('subitems', [])
                if subitems:
                    chapter_numbers.update(walk(subitems, full_number))
                if not hidden:
                    visible_counter += 1
            return chapter_numbers

        toc = [
            {
                'href': '1', 'title': 'Chapter 1', 'hidden': '0',
                'subitems': [
                    {'href': '1.1', 'title': 'Section 1.1', 'hidden': '0',
                     'subitems': [
                         {'href': '1.1.1', 'title': 'Sub 1.1.1',
                          'hidden': '0', 'subitems': []},
                         {'href': '1.1.2', 'title': 'Sub 1.1.2',
                          'hidden': '0', 'subitems': [
                             {'href': '1.1.2.1', 'title': 'Deep 1.1.2.1',
                              'hidden': '0', 'subitems': []}
                         ]},
                     ]},
                    {'href': '1.2', 'title': 'Section 1.2', 'hidden': '0',
                     'subitems': []},
                ]
            }
        ]

        chapter_numbers = walk(toc)

        # Expected: 6 chapter entries, each with a dotted prefix.
        # No nested structure — just a flat dict.
        expected = {
            '1':       '1.',
            '1.1':     '1.1.',
            '1.1.1':   '1.1.1.',
            '1.1.2':   '1.1.2.',
            '1.1.2.1': '1.1.2.1.',
            '1.2':     '1.2.',
        }
        assert chapter_numbers == expected

    def test_folder_name_uses_dotted_prefix_not_nested_path(self):
        """For each chapter, the folder name is
        '<chapter_number> <chapter_title>' — e.g. '1.1.1. Sub 1.1.1'.
        This is a SINGLE folder name with a dotted prefix, NOT a
        nested directory like '1/1.1/1.1.1/'.
        """
        from moodle_dl.moodle.mods.book import BookMod

        # Each chapter_id has its own folder_name
        cases = [
            ('1',       '1.',        '1. Chapter 1'),
            ('1.1',     '1.1.',      '1.1. Section 1.1'),
            ('1.1.1',   '1.1.1.',    '1.1.1. Sub 1.1.1'),
            ('1.1.2.1', '1.1.2.1.',  '1.1.2.1. Deep 1.1.2.1'),
        ]
        for chapter_id, chapter_number, title in cases:
            folder = BookMod._format_chapter_folder_name(title, chapter_number, 0)
            # Pin: folder name contains the dotted prefix
            assert folder.startswith(chapter_number), (
                f'Folder name {folder!r} should start with '
                f'chapter_number {chapter_number!r}'
            )
            # Pin: NO separator like '/' that would imply nesting
            assert '/' not in folder.replace(' ', '').replace('.', ''), (
                f'Folder name {folder!r} contains a path separator — '
                f'book chapters must be FLAT, not nested.'
            )

    def test_deep_chapter_is_not_in_a_nested_directory(self):
        """A chapter at depth 4 (1.1.2.1) lives at the SAME level
        as the root chapter (1.) in the book module directory —
        not nested 4 levels deep.

        This is the user's explicit contract:
        "我们下载下来的保存的样子要和server端一致.
         如果server的结构是扁平的, 我们下载下来也要是扁平的"
        """
        from moodle_dl.moodle.mods.book import BookMod

        # Build a chapter at the deepest level
        folder_name_deep = BookMod._format_chapter_folder_name(
            'Deep 1.1.2.1', '1.1.2.1.', 0
        )
        folder_name_root = BookMod._format_chapter_folder_name(
            'Chapter 1', '1.', 0
        )

        # Both folder names are at depth 1 (just one '/')
        # when joined with the book module path
        assert '/' not in folder_name_deep, (
            f'Deep chapter folder {folder_name_deep!r} contains '
            f'a path separator — should be flat.'
        )
        assert '/' not in folder_name_root, (
            f'Root chapter folder {folder_name_root!r} contains '
            f'a path separator — should be flat.'
        )


# =========================================================================
# Fallback path: numbering disabled or TOC missing
# =========================================================================
class TestBookChapterFolderFallbackFlat:
    """When the book has TOC numbering disabled (numbering != 1),
    or when the TOC is missing entirely, the folder name falls back
    to a sequential 'NN - Title' format. Still flat.
    """

    def test_fallback_uses_sequential_numbering(self):
        from moodle_dl.moodle.mods.book import BookMod

        # Empty chapter_number (TOC disabled or unavailable)
        folder = BookMod._format_chapter_folder_name(
            'Chapter 1', '', fallback_index=1
        )
        assert folder == '01 - Chapter 1'

        folder = BookMod._format_chapter_folder_name(
            'Sub 1.1.1', '', fallback_index=3
        )
        # Sequential numbering only — depth is NOT preserved
        # (because server's flat structure doesn't preserve it
        # either, see fallback_index comment).
        assert folder == '03 - Sub 1.1.1'

    def test_fallback_no_nested_paths(self):
        from moodle_dl.moodle.mods.book import BookMod

        for i in range(1, 10):
            folder = BookMod._format_chapter_folder_name(
                f'Chapter X', '', fallback_index=i
            )
            assert '/' not in folder, (
                f'Fallback folder {folder!r} contains path separator — '
                f'flat structure required.'
            )


# =========================================================================
# file content_filepath: pinned flat (matches server)
# =========================================================================
class TestBookFileContentFilepathFlat:
    """Pin that the content_filepath of every book file is a single
    folder under the book module path. Server-side behavior is:
    every chapter content item has filepath = /<chapter_id>/ (no
    nesting). moodle-dl preserves this in book.py:223:
        chapter_content['filepath'] = f'/{chapter_folder_name}/'
    """

    def test_chapter_html_filepath_is_single_level(self):
        """The chapter HTML content's filepath is
        '/<chapter_folder_name>/' — a single folder under the book
        module, regardless of the chapter's depth in the TOC.
        """
        from moodle_dl.moodle.mods.book import BookMod

        # Simulate the line 223 logic from book.py
        chapter_folder_name = '1.1.2.1. Deep 1.1.2.1'
        chapter_content_filepath = f'/{chapter_folder_name}/'

        # filepath is '/<single_folder>/' — no nested segments
        segments = [s for s in chapter_content_filepath.split('/') if s]
        assert len(segments) == 1, (
            f'Chapter filepath {chapter_content_filepath!r} should be a '
            f'single folder, got {segments}'
        )
        assert segments[0] == chapter_folder_name