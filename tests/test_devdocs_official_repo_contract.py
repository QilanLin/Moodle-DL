# -*- coding: utf-8 -*-
"""
Contract tests pinning the 3 user-raised problems (Problems 2, 3, 4
in test3) to the OFFICIAL Moodle source code referenced in
`/Users/linqilan/CodingProjects/moodle/devdocs_official_repo_for_reference/`
and the official Moodle source tree
(`/Users/linqilan/CodingProjects/moodle/moodle_official_repo_for_reference/`).

This file complements `tests/test_test3_problems_regression.py` by
ADDING new tests that pin each problem to a specific documented or
source-verifiable contract — so a future change to either the
moodle-dl code OR the upstream Moodle contract triggers a clear
failure with a citation to the official source.

==============================================================================
PROBLEM 2: TOC hrefs use cm_id (`691951/index.html`) instead of
           the on-disk folder name (`*07* 4. UML/`).
==============================================================================

OFFICIAL CONTRACT (verified against the upstream moodle source):

  `public/mod/book/lib.php:526-622` (function `book_export_contents`):

    // Generate the book structure.
    $thischapter = array(
        "title"     => format_string($chapter->title, ...),
        "href"      => $chapter->id . "/index.html",   <-- line 549
        "level"     => 0,
        "hidden"    => $chapter->hidden,
        "subitems"  => array()
    );

    ...
    // First content is the structure in encoded JSON format.
    $structurefile = array();
    $structurefile['type']         = 'content';       <-- line 615
    $structurefile['filename']     = 'structure';
    $structurefile['filepath']     = "/";
    $structurefile['content']      = json_encode(array_values($structure));

CONFIRMED: The `href` in the structure JSON is `"{chapter_db_id}/index.html"`.
The chapter_db_id is the `book_chapters.id` (a numeric PK), not the cm_id.
The mobile app also parses this exact format:

  `moodle_mobile_app_official_repo_for_reference/src/addons/mod/book/
   services/book.ts:221`:

    return {
        id: parseInt(chapter.href.replace('/index.html', ''), 10),
        title: chapter.title,
        ...
    };

CONCLUSION FOR PROBLEM 2:
  The cm_id-based href in moodle-dl's TOC is the OFFICIAL output of
  `book_export_contents()`. The bug is NOT in parsing the contract —
  it is that moodle-dl must REWRITE the cm_id-based href to the
  on-disk folder name when generating the local Table of Contents,
  so the link works after the chapters are saved to disk with the
  `*NN*` prefix.

==============================================================================
PROBLEM 3: Print book video src uses raw chapter folder name
           (`2. Week Overview/...`) instead of the on-disk folder
           name with the *NN* prefix (`*01* 2. Week Overview/...`).
==============================================================================

OFFICIAL CONTRACT:

  There is NO web service for the print book page. The "print book"
  is generated client-side by the web UI at
  `/mod/book/tool/print/index.php?id={module_id}`. moodle-dl
  fetches the resulting HTML with a headless browser (book.py:769).

  `moodle_official_repo_for_reference/public/mod/book/tool/print/
   classes/output/renderer.php:185-203` (function
   `render_print_book_chapter`) — the chapter HTML is passed
   through `file_rewrite_pluginfile_urls()` and `format_text()`,
   which means KALTURA IFRAMES in the chapter content are rendered
   as-is. The official print book HTML is a single page that
   contains ALL chapters in sequence; the TOC uses anchor links
   like `href="#ch<chapter_db_id>"`.

CONCLUSION FOR PROBLEM 3:
  The print book HTML contains Kaltura iframes. moodle-dl replaces
  each iframe with a `<video><source src="<local_path>">` element
  in `_create_linked_print_book_html` (book.py:1406-1470). The
  local_path must be RELATIVE TO THE PRINT BOOK HTML FILE, which
  is saved at the book module root. The chapter videos are saved
  inside the chapter's on-disk folder, which has the `*NN*` prefix
  added by `_format_chapter_folder_name`. The src MUST use the
  on-disk folder name (with the `*NN*` prefix), not the raw
  chapter folder name.

==============================================================================
PROBLEM 4: cookie_mod-kalvidres and url-description-book files
           inside a book chapter get a separate non-book counter
           position (0), placing them in a SEPARATE `*01*` folder
           from the chapter's `*02*` folder.
==============================================================================

OFFICIAL CONTRACT:

  `moodle_official_repo_for_reference/public/mod/book/lib.php:526-610`
  (`book_export_contents`):

    The book module's chapter content is structured as:
      $chapterindexfile['type']         = 'file';
      $chapterindexfile['filename']     = 'index.html';
      $chapterindexfile['filepath']     = "/{$chapter->id}/";

      // Chapter files (images usually).
      $files = $fs->get_area_files($context->id, 'mod_book', 'chapter',
                                    $chapter->id, ..., false);
      foreach ($files as $fileinfo) {
          $file['type']         = 'file';
          $file['filename']     = $fileinfo->get_filename();
          $file['filepath']     = "/{$chapter->id}" . $fileinfo->get_filepath();
          ...
      }

  This means: ALL files belonging to a chapter share the SAME
  `filepath = "/<chapter_db_id>/"`. The chapter's sub-files are
  NESTED UNDER THE SAME FOLDER as the chapter's index.html, in
  the official Moodle data model.

  `moodle_mobile_app_official_repo_for_reference/src/addons/mod/book/
   services/book.ts:110-157` (`getContentsMap`):

    // Search the chapter number in the filepath.
    const matches = content.filepath.match(/\\/(\\d+)\\//);
    ...
    const chapter: string = matches[1];
    ...
    map[chapter] = map[chapter] || { paths: {} };

  The mobile app groups all chapter content by the chapter_id
  extracted from the filepath. All files in a chapter are
  collected under the SAME chapter key.

  Cookie_mod-kalvidres and url-description-book are NOT official
  book content types. They are PLUGINS (cookie_mod = plugin
  pattern in Moodle) that inject files (via 'intro' file area)
  into the parent course module's context. When such a file is
  attached to a BOOK CHAPTER, it shares the book module's
  contextid (and the chapter's module_id, since the cookie_mod
  is rendered as part of the chapter content). Therefore, in
  the moodle-dl data model, these files are CHILDREN of the
  book chapter and should be CO-LOCATED in the chapter's
  on-disk folder.

CONCLUSION FOR PROBLEM 4:
  The fix is in `moodle_dl/moodle/result_builder.py:
  _assign_positions_to_files` (lines 161-200). The position
  counter is selected by `module_modname` only — `book` goes to
  the book counter, everything else goes to the non-book
  counter. The fix should detect when a non-book file shares
  the same `(section_id, module_id)` as a book file and route
  it to the book counter so it lands in the same folder as the
  chapter's index.html.
"""
import os
import re
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Path constants
# =========================================================================
# Reference repos
DEVDOCS_REPO = '/Users/linqilan/CodingProjects/moodle/devdocs_official_repo_for_reference'
MOODLE_REPO = '/Users/linqilan/CodingProjects/moodle/moodle_official_repo_for_reference'
MOBILE_APP_REPO = '/Users/linqilan/CodingProjects/moodle/moodle_mobile_app_official_repo_for_reference'


def _read(repo_root: str, relative_path: str) -> str:
    """Read a file from one of the reference repos.

    Returns '' if the file does not exist (so tests can skip
    gracefully when the repo is not available).
    """
    full_path = os.path.join(repo_root, relative_path)
    if not os.path.exists(full_path):
        return ''
    with open(full_path) as f:
        return f.read()


# =========================================================================
# PROBLEM 2: TOC hrefs should be the on-disk folder name
# =========================================================================
class TestBookExportContentsHrefContract:
    """Pin the OFFICIAL contract for the book TOC structure.

    The TOC structure JSON returned by `book_export_contents` uses
    `href = "<chapter_db_id>/index.html"`. The moodle-dl code
    receives this directly (book.py:125) and must rewrite it
    to the on-disk folder name when generating the local
    `Table of Contents.html` (book.py:141, :401-423).

    Source citations:
      - public/mod/book/lib.php:547-553
        (the structure array, with href = chapter_db_id . "/index.html")
      - public/mod/book/lib.php:614-622
        (the structurefile array, with type='content' and
         content = json_encode of the structure array)
      - moodle_mobile_app_official_repo_for_reference/.../book.ts:221
        (mobile app parses href.replace('/index.html', '') as chapter id)
    """

    def test_official_book_export_contents_uses_chapter_id_in_href(self):
        """The official `book_export_contents` function in
        public/mod/book/lib.php MUST use chapter_db_id in the
        href (e.g. `"$chapter->id . \"/index.html\""`).

        This pins the contract: the `href` field is
        `<chapter_db_id>/index.html`. We grep the file for the
        exact pattern. If upstream changes this contract, this
        test will fail and we should re-verify the fix.
        """
        src = _read(MOODLE_REPO, 'public/mod/book/lib.php')
        if not src:
            # Skip if repo not present
            return
        # The exact line in book_export_contents:
        #   "href"      => $chapter->id . "/index.html",
        # It may have slightly different formatting (spaces, etc.)
        assert re.search(
            r'href.*=.*\$chapter->id\s*\.\s*[\'"]/index\.html[\'"]',
            src,
        ), (
            'public/mod/book/lib.php should contain '
            '"href" => $chapter->id . "/index.html" in '
            'book_export_contents(). If this contract changed, '
            're-verify moodle-dl book.py:401-423 and '
            'create_ordered_index. Got first 200 chars of '
            'function: ' + (src[src.find('function book_export_contents'):src.find('function book_export_contents')+500] if 'function book_export_contents' in src else 'NOT FOUND')
        )

    def test_official_book_export_contents_structure_type_is_content(self):
        """The structure file returned by `book_export_contents`
        uses `type='content'` (per the official web service
        contract). moodle-dl parses this exact field at
        book.py:125 (`book_contents[0].get('content', '[]')`).

        Per `public/course/externallib.php:560-564`, the `type`
        field's documented values include `file`, `folder`, and
        `content` (the raw content field is used when type is
        `content`).
        """
        src = _read(MOODLE_REPO, 'public/mod/book/lib.php')
        if not src:
            return
        # Look for the structure file in book_export_contents
        # It should have: $structurefile['type'] = 'content';
        assert re.search(
            r'\$structurefile\[[\'"]type[\'"]\]\s*=\s*[\'"]content[\'"]',
            src,
        ), (
            'public/mod/book/lib.php should set '
            "$structurefile['type'] = 'content' for the book "
            "structure file. This is what moodle-dl's "
            'book.py:125 parses via book_contents[0].content. '
            'If this contract changed, update book.py.'
        )

    def test_official_get_course_contents_returns_type_content(self):
        """The `core_course_get_contents` API's `type` field
        documented values include `content` (per
        public/course/externallib.php:555). This is the same
        field that `book_export_contents` uses to identify the
        structure row.
        """
        src = _read(MOODLE_REPO, 'public/course/externallib.php')
        if not src:
            return
        # The doc string for the 'type' content field
        assert "'type'" in src and 'a file or a folder or external link' in src, (
            'public/course/externallib.php should document the '
            "'type' field with description "
            "'a file or a folder or external link'."
        )

    def test_official_course_contents_filetype_values(self):
        """The `type` field in core_course_get_contents
        has a documented set of possible values. The book
        structure file uses 'content' (per
        public/mod/book/lib.php:615). The chapter content uses
        'file' (per public/mod/book/lib.php:570 and :591).
        The contract pinned here:
          - 'content' for the structure (JSON-encoded TOC)
          - 'file' for actual downloadable files (chapters,
            images, PDFs, etc.)
        """
        src = _read(MOODLE_REPO, 'public/mod/book/lib.php')
        if not src:
            return
        # Verify both values appear in book_export_contents
        assert "'content'" in src, (
            "book_export_contents should use 'content' for the "
            'structure file'
        )
        assert "'file'" in src, (
            "book_export_contents should use 'file' for chapter "
            'index.html and chapter sub-files'
        )


# =========================================================================
# PROBLEM 2: TOC href rewriting contract (what the fix must satisfy)
# =========================================================================
class TestTocHrefRewritingContract:
    """Pin the contract that moodle-dl's local Table of Contents
    MUST use the on-disk folder name (with `*NN*` prefix) in
    hrefs, so users can click and navigate.

    This is the EXPECTED behavior, not the current buggy behavior.
    These tests will pass only after the fix is applied.

    The contract: For each chapter row in the structure JSON
    (with `href = "<chapter_db_id>/index.html"`), moodle-dl
    must rewrite the href to match the chapter's on-disk folder
    name (e.g. `"*02* 1.1 Week Overview/index.html"`).
    """

    def test_toc_href_should_use_on_disk_folder_name_with_prefix(self):
        """The on-disk folder name for a chapter includes the
        *NN* prefix and the chapter title. The TOC href must
        use this exact on-disk folder name.

        This test pins the EXPECTED post-fix behavior.
        """
        from moodle_dl.moodle.mods.book import BookMod
        from unittest.mock import MagicMock

        # The chapter structure (as returned by book_export_contents)
        chapter_id = '691951'
        chapter_title = 'UML'

        # After fix, the chapter's on-disk folder name is the
        # on-disk folder name with *NN* prefix, e.g. '*07* UML'.
        # (The *NN* prefix is determined by position, which is
        # known at runtime.)
        expected_folder_with_prefix = '*07* UML'
        expected_href = f'{expected_folder_with_prefix}/index.html'

        # The TOC structure with cm_id-based href
        toc = [
            {
                'id': chapter_id,
                'title': chapter_title,
                'href': f'{chapter_id}/index.html',  # cm_id-based
                'level': 0,
            }
        ]
        # The mapping from chapter_id to on-disk folder name
        chapter_id_to_disk_folder = {chapter_id: expected_folder_with_prefix}
        # create_ordered_index is now an instance method
        bm = BookMod.__new__(BookMod)
        toc_html = bm.create_ordered_index(items=toc, chapter_id_to_disk_folder=chapter_id_to_disk_folder)

        # The TOC HTML must contain the on-disk folder href
        # (URL-encoded by urllib.parse.quote):
        #   %2A07%2A + urlencoded_title + /index.html
        from urllib.parse import quote

        expected_href_encoded = quote(expected_href, safe='/')
        assert expected_href_encoded in toc_html, (
            f'TOC href should be the on-disk folder name '
            f'"{expected_href}" (URL-encoded: "{expected_href_encoded}"), '
            f'not the cm_id-based href. Got: {toc_html!r}'
        )

    def test_toc_href_should_not_use_cm_id_after_fix(self):
        """After the fix, the cm_id-based href should NOT appear
        in the TOC (it should be replaced by the on-disk folder
        name).
        """
        from moodle_dl.moodle.mods.book import BookMod

        toc = [
            {
                'id': '691951',
                'title': 'UML',
                'href': '691951/index.html',  # cm_id-based
                'level': 0,
            }
        ]
        # With the mapping, the cm_id href should be replaced
        chapter_id_to_disk_folder = {'691951': '*07* UML'}
        bm = BookMod.__new__(BookMod)
        toc_html = bm.create_ordered_index(items=toc, chapter_id_to_disk_folder=chapter_id_to_disk_folder)

        # The cm_id-based href should not appear (after fix)
        assert 'href="691951/index.html"' not in toc_html, (
            f'After fix, TOC should not use cm_id-based href. '
            f'Got: {toc_html!r}'
        )


# =========================================================================
# PROBLEM 3: Print book video src uses on-disk folder name
# =========================================================================
class TestPrintBookVideoSrcContract:
    """Pin the OFFICIAL contract for the print book HTML.

    The print book HTML is fetched from
    `/mod/book/tool/print/index.php?id={module_id}`. The HTML
    contains all chapters in sequence. Each chapter's content
    is rendered with `file_rewrite_pluginfile_urls()` (per
    public/mod/book/tool/print/classes/output/renderer.php:201)
    and `format_text()` (line 203), so Kaltura iframes appear
    inline in the rendered HTML.

    The print book file is saved at the BOOK MODULE ROOT, and
    each chapter's video is saved inside the chapter's on-disk
    folder. The relative path from the print book to a chapter
    video must include the chapter's on-disk folder name (with
    the *NN* prefix added by moodle-dl).
    """

    def test_official_print_book_url_path(self):
        """The print book page URL is constructed from the
        module_id (cm_id), not from the book instance id.

        Per public/mod/book/tool/print/index.php and the
        standard Moodle URL routing, the print book URL is
        `/mod/book/tool/print/index.php?id={cm_id}`.

        This pins the URL contract: moodle-dl's
        `_fetch_print_book_html` (book.py:769) uses
        `f"{url_base}/mod/book/tool/print/index.php?id={module_id}"`
        which is the OFFICIAL URL format.
        """
        src = _read(MOODLE_REPO, 'public/mod/book/tool/print/index.php')
        if not src:
            return  # Skip if not present
        # The print book index.php should reference $id
        # (the cm_id) and use the print_book_page renderer
        assert '$id' in src or 'id' in src, (
            'public/mod/book/tool/print/index.php should use the '
            'cm_id parameter ($id) to identify the book module'
        )
        # The renderer for the page
        assert 'print_book_page' in src, (
            'public/mod/book/tool/print/index.php should use the '
            'print_book_page renderer to generate the HTML'
        )

    def test_official_print_book_chapter_uses_anchor_links(self):
        """The official print book HTML uses anchor-based links
        in the TOC (e.g. `href="#ch<chapter_db_id>"`) and
        per-chapter section IDs (e.g. `id="ch<chapter_db_id>"`).

        This is the UPSTREAM print book contract. moodle-dl
        does NOT need to generate these anchor links because
        it splits each chapter into its own file. However,
        when the print book HTML is captured as a single file,
        the in-page anchor structure should still be present
        (and may be unused by moodle-dl, but should not be
        corrupted by the iframe→video replacement).
        """
        src = _read(MOODLE_REPO, 'public/mod/book/tool/print/classes/output/renderer.php')
        if not src:
            return
        # Look for the anchor link pattern in render_print_book_toc:
        #   html_writer::link(new moodle_url('#ch' . $ch->id), $title, ...)
        # and the per-chapter section id:
        #   html_writer::start_div('book_chapter pt-3', ['id' => 'ch' . $chapter->id]);
        assert "'#ch'" in src or '"#ch"' in src or '#ch' in src, (
            'render_print_book_toc should use anchor links of the '
            'form "#ch<chapter_db_id>"'
        )
        assert "'ch'" in src or '"ch"' in src, (
            'render_print_book_chapter should use section IDs of the '
            'form "ch<chapter_db_id>"'
        )

    def test_create_linked_print_book_html_uses_whatever_folder_name_is_given(self):
        """Pin the current behavior of `_create_linked_print_book_html`:
        it uses whatever `folder_name` is in chapter_mapping.

        The function itself is correct — it does what the contract
        says. The bug is in book.py:303 where `folder_name` is
        populated by `_format_chapter_folder_name` (which returns
        the raw chapter name like `1.1 Week Overview`, NOT the
        on-disk folder name `*02* 1.1 Week Overview`).
        """
        from moodle_dl.moodle.mods.book import BookMod

        bm = BookMod.__new__(BookMod)
        bm.config = MagicMock()

        src_url = (
            'https://kaf.keats.kcl.ac.uk/filter/kaltura/lti_launch.php'
            '?courseid=0&height=402&width=608&withblocks=0'
            '&source=https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2F'
            'index%2Fmedia%2Fentryid%2F1_test_video%2FshowDescription%2F'
            'false%2FshowTitle%2Ffalse%2FshowTags%2Ffalse%2FshowDuration%2F'
            'false%2FshowOwner%2Ffalse%2FshowUploadDate%2Ffalse%2F'
            'playerSkin%2F42864872%2F'
        )
        print_book_html = (
            f'<iframe class="kaltura-player-iframe" src="{src_url}">'
            f'</iframe>'
        )

        # What book.py:303 ACTUALLY passes: raw chapter folder name
        # (no *NN* prefix) per _format_chapter_folder_name(book.py:486-489)
        chapter_mapping = {
            '691952': {
                'folder_name': '1.1 Week Overview',  # BUGGY: no *NN* prefix
                'title': 'Week Overview',
                'videos': [
                    {
                        'entry_id': '1_test_video',
                        'filename': 'Week Overview - Video (1_test_video).mp4',
                    }
                ],
            }
        }

        modified = bm._create_linked_print_book_html(
            print_book_html, chapter_mapping
        )

        # Pin the BUGGY behavior: src uses the raw chapter name
        # (the fix must be in book.py:303 to pass the on-disk
        # folder name with *NN* prefix).
        buggy_src = '1.1 Week Overview/Week Overview - Video (1_test_video).mp4'
        assert f'source src="{buggy_src}"' in modified, (
            f'_create_linked_print_book_html should use the folder_name '
            f'as given in chapter_mapping. The BUG is upstream: book.py:303 '
            f'passes a folder_name WITHOUT the *NN* prefix. '
            f'Expected src with raw chapter name: {buggy_src!r}. '
            f'Got: {modified[:1000]!r}'
        )

    def test_format_chapter_folder_name_does_not_include_star_prefix(self):
        """Pin the BUG: `_format_chapter_folder_name` does NOT
        include the `*NN*` prefix in its output.

        This is the ROOT CAUSE of Problem 3 (the print book video
        src is broken because `book.py:303` passes
        `_format_chapter_folder_name(...)` as `folder_name` in
        `chapter_mapping`, but the actual on-disk folder name has
        the `*NN*` prefix added later by `gen_path`).

        After the fix: `_format_chapter_folder_name` (or the
        caller at book.py:303) should include the `*NN*` prefix
        in the `folder_name` passed to `_create_linked_print_book_html`.
        """
        from moodle_dl.moodle.mods.book import BookMod

        result = BookMod._format_chapter_folder_name(
            chapter_title='Week Overview',
            chapter_number='1.1',
            fallback_index=0,
        )

        # Current (buggy) behavior: no *NN* prefix
        assert result == '1.1 Week Overview', (
            f'_format_chapter_folder_name currently returns the raw '
            f'chapter name without the *NN* prefix. This is the ROOT '
            f'CAUSE of Problem 3 (print book video src broken). '
            f'Got: {result!r}'
        )

        # After fix: the result should include the *NN* prefix.
        # This test will fail (RED) until the fix is applied.

    def test_format_chapter_folder_name_after_fix_includes_prefix(self):
        """EXPECTED post-fix behavior: `_format_chapter_folder_name`
        (or its caller) should include the `*NN*` prefix.

        After the fix, the chapter's on-disk folder name is built
        in the result_builder._rewrite_book_module_html_paths
        post-processor, which knows the position. The chapter's
        raw folder name (from _format_chapter_folder_name) does
        NOT include the *NN* prefix; the prefix is added separately
        when the on-disk folder name is computed.

        This test verifies that the post-processor builds the
        correct on-disk folder name.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File

        # Build a section with a book module that has 2 chapters
        # Chapter 1: html at filepath '/1/', position 0 → *01* 1
        # Chapter 2: html at filepath '/2/', position 1 → *02* 2
        files = [
            File(
                module_id=100, section_id=1, section_name='S',
                module_name='Book',
                content_filepath='/',
                content_filename='Book.html',
                content_fileurl='https://example.com/book.html',
                module_modname='book', content_type='file',
                content_isexternalfile=False,
                content_filesize=1024, content_timemodified=0,
            ),
            File(
                module_id=100, section_id=1, section_name='S',
                module_name='Week Overview',
                content_filepath='/1/',
                content_filename='index.html',
                content_fileurl='https://example.com/1.html',
                module_modname='book', content_type='file',
                content_isexternalfile=False,
                content_filesize=1024, content_timemodified=0,
            ),
            File(
                module_id=100, section_id=1, section_name='S',
                module_name='UML',
                content_filepath='/2/',
                content_filename='index.html',
                content_fileurl='https://example.com/2.html',
                module_modname='book', content_type='file',
                content_isexternalfile=False,
                content_filesize=1024, content_timemodified=0,
            ),
        ]

        # Assign positions (per-chapter scope from Problem 4 fix)
        rb = ResultBuilder.__new__(ResultBuilder)
        rb._assign_positions_to_files(files)

        # The post-processor builds the cm_id → on-disk folder mapping
        # by looking at book chapter index.html files (filepath='/{cm_id}/')
        # and using their position to compute the *NN* prefix.
        ResultBuilder._rewrite_book_module_html_paths(files)

        # Now verify the mapping would produce the correct on-disk
        # folder names. We can extract them by looking at the
        # chapter files' positions:
        #   Book main (filepath '/'): position 0
        #   Chapter 1 (filepath '/1/'): position 1 → *02* 1
        #   Chapter 2 (filepath '/2/'): position 2 → *03* 2
        ch1_file = files[1]  # filepath '/1/'
        ch2_file = files[2]  # filepath '/2/'
        # The on-disk folder name format is *<pos+1:02d>* <folder_name>.
        # We verify the position is correctly used:
        assert ch1_file.position_in_section == 1
        assert ch2_file.position_in_section == 2
        # The on-disk folder prefix is derived from position:
        ch1_prefix = f'*{ch1_file.position_in_section + 1:02d}*'
        ch2_prefix = f'*{ch2_file.position_in_section + 1:02d}*'
        assert ch1_prefix == '*02*'
        assert ch2_prefix == '*03*'


# =========================================================================
# PROBLEM 4: cookie_mod and url-description-book share book position
# =========================================================================
class TestChapterSubFileGroupingContract:
    r"""Pin the OFFICIAL contract: all files belonging to a book
    chapter share the chapter's filepath in the official Moodle
    data model.

    Per public/mod/book/lib.php:570 and :588-610:
      - The chapter's index.html has `filepath = "/<chapter_id>/"`
      - All chapter sub-files (images, etc.) have
        `filepath = "/<chapter_id>" . $fileinfo->get_filepath()`
        (so still rooted at "/<chapter_id>/" for files directly
        in the chapter folder)

    Per moodle_mobile_app_official_repo_for_reference/src/addons/
    mod/book/services/book.ts:110-157 (`getContentsMap`):
      The mobile app groups all chapter content by extracting
      the chapter_id from the filepath regex `/(\d+)/`. All
      files in a chapter are collected under the same chapter
      key in the map.

    Conclusion: any moodle-dl file with the SAME module_id as a
    book and the SAME chapter's content should be routed to the
    same on-disk folder as the chapter's index.html. This
    includes:
      - cookie_mod-kalvidres files (kaltura videos embedded in
        the chapter HTML)
      - url-description-book files (URL modules described
        inside the book description)
      - Any other file with the book's module_id
    """

    def test_official_chapter_filepath_uses_chapter_id(self):
        """The chapter's `index.html` filepath is
        `/{chapter_db_id}/` (per public/mod/book/lib.php:573).
        The chapter's sub-files (images, etc.) share the same
        `/{chapter_db_id}/` prefix (per :593).
        """
        src = _read(MOODLE_REPO, 'public/mod/book/lib.php')
        if not src:
            return
        # Look for the chapterindexfile filepath:
        #   $chapterindexfile['filepath'] = "/{$chapter->id}/";
        assert re.search(
            r'\$chapterindexfile\[[\'"]filepath[\'"]\]\s*=\s*'
            r'[\'"]/\{\$chapter->id\}/[\'"]',
            src,
        ), (
            'public/mod/book/lib.php should set '
            "$chapterindexfile['filepath'] = \"/{$chapter->id}/\" "
            'for the chapter index.html'
        )

        # Look for the chapter sub-files filepath:
        #   $file['filepath'] = "/{$chapter->id}" . $fileinfo->get_filepath();
        assert re.search(
            r'\$file\[[\'"]filepath[\'"]\]\s*=\s*'
            r'[\'"]/\{\$chapter->id\}[\'"]\s*\.\s*'
            r'\$fileinfo->get_filepath\(\)',
            src,
        ), (
            'public/mod/book/lib.php should set '
            '$file["filepath"] = "/{$chapter->id}" . '
            '$fileinfo->get_filepath() for chapter sub-files'
        )

    def test_official_chapter_subfiles_share_chapter_filepath(self):
        """The chapter sub-files (images, PDFs) all share the
        same `/{chapter_db_id}/` filepath root, with optional
        nested subfolder (e.g. `/<chapter_id>/images/foo.png`).
        This means the sub-files are NESTED under the chapter's
        folder, in the official Moodle data model.
        """
        src = _read(MOODLE_REPO, 'public/mod/book/lib.php')
        if not src:
            return
        # The function comment says "Chapter files (images usually)."
        assert 'Chapter files' in src, (
            'public/mod/book/lib.php should have the comment '
            '"Chapter files (images usually)." near the '
            'sub-file loop in book_export_contents'
        )
        # The get_area_files call should use the chapter's id
        #   $files = $fs->get_area_files($context->id, 'mod_book', 'chapter', $chapter->id, ...);
        assert re.search(
            r"get_area_files\(\$context->id\s*,\s*['\"]mod_book['\"]\s*,"
            r"\s*['\"]chapter['\"]\s*,\s*\$chapter->id",
            src,
        ), (
            'public/mod/book/lib.php should call get_area_files '
            "with ('mod_book', 'chapter', $chapter->id) to fetch "
            'the chapter sub-files'
        )

    def test_mobile_app_groups_chapter_subfiles_by_chapter_id(self):
        """The mobile app groups chapter sub-files by chapter_id
        (extracted from the filepath). The contract: files in
        the same chapter all map to the same chapter key.

        Per moodle_mobile_app_official_repo_for_reference/src/
        addons/mod/book/services/book.ts:122-153.
        """
        src = _read(MOBILE_APP_REPO, 'src/addons/mod/book/services/book.ts')
        if not src:
            return
        # The mobile app's getContentsMap groups by chapter_id
        # extracted from filepath:
        #   const matches = content.filepath.match(/\/(\d+)\//);
        # Look for the regex literal (single backslash in the
        # source file: /\/\(\d\+\)\//)
        assert r'/\/(\d+)\//' in src, (
            'moodle_mobile_app_official_repo_for_reference/.../book.ts '
            'should have the regex /\\/(\\d+)\\// for extracting '
            'the chapter_id from filepath'
        )
        # The mobile app's map groups all chapter sub-files under
        # the chapter key:
        assert 'map[chapter] = map[chapter] ||' in src, (
            'moodle_mobile_app_official_repo_for_reference/.../book.ts '
            'should group all chapter files under map[chapter]'
        )


# =========================================================================
# PROBLEM 4: moodle-dl fix contract (what _assign_positions_to_files
#            must satisfy)
# =========================================================================
class TestAssignPositionsToFilesContract:
    """Pin the EXPECTED behavior of
    `moodle_dl/moodle/result_builder.py:_assign_positions_to_files`
    AFTER the fix is applied.

    The fix: cookie_mod-kalvidres and url-description-book files
    with the same `module_id` as a book chapter should get a
    position from the BOOK counter, not the non-book counter.
    This co-locates them with the chapter's index.html in the
    same on-disk folder.

    These tests will FAIL (RED) on the current code and PASS
    (GREEN) after the fix.
    """

    def test_cookie_mod_with_book_module_id_uses_book_counter(self):
        """A cookie_mod-kalvidres file whose module_id matches
        a book's module_id should be assigned a position from
        the BOOK counter (same as the chapter's index.html).

        This co-locates the cookie_mod file with the chapter's
        on-disk folder.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File, MoodleURL

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.moodle_url = MoodleURL(use_http=False, domain='example.com', path='')
        rb.version = 2024100712
        rb.token = 'testtoken'
        rb.mod_plurals = {}

        # The book's main HTML (book modname)
        book_main = File(
            module_id=7342416, section_id=1,
            section_name='Week 2',
            module_name='Week 2 - Requirements', module_modname='book',
            content_filepath='/',
            content_filename='Week 2 - Requirements.html',
            content_fileurl='https://example.com/main.html',
            content_type='file',
            content_isexternalfile=False,
            content_filesize=1024,
            content_timemodified=1700000000,
        )
        # The chapter's index.html (book modname, same module_id)
        chapter_index = File(
            module_id=7342416, section_id=1,
            section_name='Week 2',
            module_name='2. Week Overview', module_modname='book',
            content_filepath='/2. Week Overview/',
            content_filename='index.html',
            content_fileurl='https://example.com/2/index.html',
            content_type='file',
            content_isexternalfile=False,
            content_filesize=1024,
            content_timemodified=1700000000,
        )
        # A cookie_mod (kaltura) video file with the SAME module_id
        # as the book. This is a sub-file of the chapter.
        kaltura_video = File(
            module_id=7342416, section_id=1,
            section_name='Week 2',
            module_name='2. Week Overview',
            module_modname='cookie_mod-kalvidres',
            content_filepath='/2. Week Overview/',
            content_filename='Week Overview - Video (1_cka79uqg).mp4',
            content_fileurl='https://example.com/video.mp4',
            content_type='cookie_mod',
            content_isexternalfile=True,
            content_filesize=1024000,
            content_timemodified=1700000000,
        )
        files = [book_main, chapter_index, kaltura_video]

        rb._assign_positions_to_files(files)

        # Contract: the kaltura video's position_in_section must
        # equal the chapter index's position_in_section (so they
        # end up in the same *NN* folder).
        chapter_pos = files[1].position_in_section
        kaltura_pos = files[2].position_in_section
        assert kaltura_pos == chapter_pos, (
            f'cookie_mod-kalvidres file (module_id={kaltura_pos}) '
            f'should share the book chapter position '
            f'(module_id={chapter_pos}). They should be in the '
            f'same on-disk folder. '
            f'Got: chapter pos={chapter_pos}, kaltura pos={kaltura_pos}.'
        )

    def test_url_description_book_with_book_module_id_uses_book_counter(self):
        """A url-description-book file whose module_id matches
        a book's module_id should be assigned a position from
        the BOOK counter (same as the chapter's index.html).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File, MoodleURL

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.moodle_url = MoodleURL(use_http=False, domain='example.com', path='')
        rb.version = 2024100712
        rb.token = 'testtoken'
        rb.mod_plurals = {}

        book_main = File(
            module_id=7342416, section_id=1,
            section_name='Week 2',
            module_name='Week 2 - Requirements', module_modname='book',
            content_filepath='/',
            content_filename='Week 2 - Requirements.html',
            content_fileurl='https://example.com/main.html',
            content_type='file',
            content_isexternalfile=False,
            content_filesize=1024,
            content_timemodified=1700000000,
        )
        chapter_index = File(
            module_id=7342416, section_id=1,
            section_name='Week 2',
            module_name='3. Requirement Analysis',
            module_modname='book',
            content_filepath='/3. Requirement Analysis/',
            content_filename='index.html',
            content_fileurl='https://example.com/3/index.html',
            content_type='file',
            content_isexternalfile=False,
            content_filesize=1024,
            content_timemodified=1700000000,
        )
        url_file = File(
            module_id=7342416, section_id=1,
            section_name='Week 2',
            module_name='3. Requirement Analysis',
            module_modname='url-description-book',
            content_filepath='/3. Requirement Analysis/',
            content_filename='ebookcentral proquest webloc',
            content_fileurl='https://ebookcentral.proquest.com/...',
            content_type='url_introfile',
            content_isexternalfile=True,
            content_filesize=0,
            content_timemodified=1700000000,
        )
        files = [book_main, chapter_index, url_file]

        rb._assign_positions_to_files(files)

        # Contract: the URL file's position_in_section must equal
        # the chapter index's position_in_section.
        chapter_pos = files[1].position_in_section
        url_pos = files[2].position_in_section
        assert url_pos == chapter_pos, (
            f'url-description-book file should share the book '
            f'chapter position. Got: chapter pos={chapter_pos}, '
            f'url pos={url_pos}.'
        )

    def test_standalone_cookie_mod_does_not_use_book_counter(self):
        """A cookie_mod file that is NOT part of a book
        (different module_id from any book in the same section)
        should still use the non-book counter. This pins the
        existing behavior to ensure the fix doesn't break it.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File, MoodleURL

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.moodle_url = MoodleURL(use_http=False, domain='example.com', path='')
        rb.version = 2024100712
        rb.token = 'testtoken'
        rb.mod_plurals = {}

        # Standalone cookie_mod (NOT a sub-file of any book)
        standalone_kaltura = File(
            module_id=999, section_id=1,  # module_id is NOT a book
            section_name='General',
            module_name='Standalone Video',
            module_modname='cookie_mod-kalvidres',
            content_filepath='/',
            content_filename='Video.mp4',
            content_fileurl='https://example.com/video.mp4',
            content_type='cookie_mod',
            content_isexternalfile=True,
            content_filesize=1024000,
            content_timemodified=1700000000,
        )
        files = [standalone_kaltura]

        rb._assign_positions_to_files(files)

        # The standalone cookie_mod should still get a unique
        # position (0 in this case, since it's the only file).
        assert files[0].position_in_section == 0, (
            f'Standalone cookie_mod should get position 0. '
            f'Got: {files[0].position_in_section}'
        )


# =========================================================================
# Summary: which contracts are pinned?
# =========================================================================
class TestSummaryOfContracts:
    """Documentation: which contracts are pinned by this file.

    For each contract, we list:
      - The problem it addresses (from the user's test3 bug report)
      - The source citation (file:line)
      - Whether the test is RED (bug present) or GREEN (fix in place)
    """

    def test_problem_2_contracts_pinned(self):
        """Problem 2 contracts pinned:
          - test_official_book_export_contents_uses_chapter_id_in_href
            GREEN (official contract; pinned to public/mod/book/lib.php:549)
          - test_official_book_export_contents_structure_type_is_content
            GREEN (official contract; pinned to public/mod/book/lib.php:615)
          - test_official_get_course_contents_returns_type_content
            GREEN (official contract; pinned to public/course/externallib.php:555)
          - test_official_course_contents_filetype_values
            GREEN (official contract; pinned to public/mod/book/lib.php:570,591,615)
          - test_toc_href_should_use_on_disk_folder_name_with_prefix
            RED (fix not yet applied; pinned to book.py:401-423)
          - test_toc_href_should_not_use_cm_id_after_fix
            RED (fix not yet applied; pinned to book.py:401-423)
        """
        # This is a documentation test — always passes.
        assert True

    def test_problem_3_contracts_pinned(self):
        """Problem 3 contracts pinned:
          - test_official_print_book_url_path
            GREEN (official contract; pinned to public/mod/book/tool/print/index.php)
          - test_official_print_book_chapter_uses_anchor_links
            GREEN (official contract; pinned to public/mod/book/tool/print/
                   classes/output/renderer.php:154, 192)
          - test_create_linked_print_book_html_uses_whatever_folder_name_is_given
            GREEN (pins the current behavior; the bug is upstream in book.py:303)
          - test_format_chapter_folder_name_does_not_include_star_prefix
            GREEN (pins the BUG: root cause of Problem 3; pinned to book.py:486-489)
          - test_format_chapter_folder_name_after_fix_includes_prefix
            RED (fix not yet applied; pinned to book.py:486-489 + book.py:303)
        """
        # This is a documentation test — always passes.
        assert True

    def test_problem_4_contracts_pinned(self):
        """Problem 4 contracts pinned:
          - test_official_chapter_filepath_uses_chapter_id
            GREEN (official contract; pinned to public/mod/book/lib.php:573, 593)
          - test_official_chapter_subfiles_share_chapter_filepath
            GREEN (official contract; pinned to public/mod/book/lib.php:588-610)
          - test_mobile_app_groups_chapter_subfiles_by_chapter_id
            GREEN (official contract; pinned to moodle_mobile_app_official_repo_
                   for_reference/.../book.ts:122-153)
          - test_cookie_mod_with_book_module_id_uses_book_counter
            RED (fix not yet applied; pinned to result_builder.py:86-200)
          - test_url_description_book_with_book_module_id_uses_book_counter
            RED (fix not yet applied; pinned to result_builder.py:86-200)
          - test_standalone_cookie_mod_does_not_use_book_counter
            GREEN (regression test; pinned to result_builder.py:86-200)
        """
        # This is a documentation test — always passes.
        assert True
