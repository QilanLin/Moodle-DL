# -*- coding: utf-8 -*-
r"""
Tests pinning the contracts from the **official Moodle Mobile App**
(TypeScript) reference repo for the 3 user-raised book-module problems.

The official repo is at:
  /Users/linqilan/CodingProjects/moodle/moodle_mobile_app_official_repo_for_reference

Specifically consulted for this file (all file:line citations are in the
official TypeScript mobile app repo, NOT the PHP repo):

* ``src/addons/mod/book/services/book.ts:110-157`` —
  ``getContentsMap()`` groups each ``CoreCourseModuleContentFile`` into
  a per-chapter ``{indexUrl, paths, tags, timemodified}`` map, where
  the chapter number is parsed from the filepath (``/\(\d+\)/``,
  line 123). Files NOT matching that pattern are dropped (line 125).
  ⇒ CONTRACT: chapter identity in the mobile app comes from the
  filepath's ``/\d+/`` directory name — which is the **chapter
  DB id** in the official PHP contract (``mod/book/lib.php:573``).
  moodle-dl must preserve this association when extracting the
  TOC and chapter sub-files for offline download.

* ``src/addons/mod/book/services/book.ts:195-201`` —
  ``getToc()`` returns the parsed JSON of ``contents[0].content``
  (the structure file produced by PHP ``mod/book/lib.php:622``).
  ⇒ CONTRACT: the mobile app reads the TOC from the SAME
  ``contents[0].content`` field that the PHP API exposes.
  moodle-dl's book.py parses the same field
  (``book_toc = json.loads(content[0]['content'])``).

* ``src/addons/mod/book/services/book.ts:209-254`` —
  ``getTocList()`` flattens the nested TOC into a flat list of
  ``{id, title, level, indexNumber, hidden}`` where ``id`` is
  parsed from ``chapter.href.replace('/index.html', '')`` (line 221)
  ⇒ CONTRACT: the mobile app derives the chapter id from the
  href string (``/index.html`` suffix removed). The id is then
  used by ``contents.ts:160`` (``slideToItem({ id: chapterId })``)
  and by ``toc.ts:60`` (``loadChapter(id: number)``) for in-app
  navigation. The mobile app **does NOT** navigate by file path —
  it navigates by chapter DB id.
  ⇒ This is the KEY contract for Problem 2: the offline TOC's
  ``<a href>`` should use the on-disk folder name (because
  the offline copy has no Angular router to resolve
  ``slideToItem``); but the FOLDER NAME must be deterministic
  and match what the chapter's ``index.html`` was written to.

* ``src/addons/mod/book/services/book.ts:293-295`` —
  ``isFileDownloadable()`` returns ``file.type === 'file'`` —
  i.e. ONLY files whose ``type`` is literally ``'file'`` are
  treated as chapter content / attachments. Other types
  (``'content'``, ``'directory'``, etc.) are skipped
  (see the ``if (!this.isFileDownloadable(content)) return;``
  at line 118).
  ⇒ CONTRACT: the mobile app does NOT process
  ``cookie_mod-kalvidres`` or ``url-description-book`` files
  separately. They arrive via ``core_course_get_contents`` as
  ``type: 'file'`` entries inside the BOOK cm's contents array,
  with ``filepath`` starting with ``/{chapter_id}/`` (because
  the PHP ``mod/book/lib.php:593`` builds the filepath that way).
  moodle-dl must mirror this: cookie_mod and url-description-book
  sub-files are NOT separate cms — they are contents of the book cm.

* ``src/addons/mod/book/services/book.ts:84-101`` —
  ``getChapterContent()`` fetches the chapter's ``index.html`` and
  then calls ``CoreDom.restoreSourcesInHtml(content, paths)``
  (line 100) to rewrite the embedded ``<iframe>``, ``<img>``,
  ``<video>``, ``<audio>``, ``<source>``, ``<track>``, ``<embed>``
  ``src`` attributes (see ``src/core/static/dom.ts:1177-1198``)
  using the per-chapter ``paths`` map built in ``getContentsMap``.
  ⇒ CONTRACT: in the mobile app, the chapter's embedded
  Kaltura/cookie_mod iframe is rendered **inside the chapter
  HTML** (not as a separate download). The iframe ``src`` is
  rewritten to point at the corresponding ``content.fileurl``
  in the ``paths`` map — meaning the iframe keeps its
  REMOTE URL. The mobile app does NOT extract Kaltura videos
  to standalone files; moodle-dl's
  ``_extract_kaltura_videos_from_html`` (book.py:577+) is a
  moodle-dl-specific offline-only feature.
  ⇒ Implication for Problem 3: the print book HTML's
  ``<source src="...">`` must point to the SAME on-disk
  folder where the chapter's ``index.html`` was saved (with
  the ``*NN*`` prefix), because in offline mode there's no
  remote URL fallback.

* ``src/addons/mod/book/components/toc/toc.ts:60-62`` —
  ``loadChapter(id: number)`` dismisses the TOC modal with
  the chapter id. ``src/addons/mod/book/pages/contents/contents.ts:155-161`` —
  ``changeChapter(chapterId: number)`` calls
  ``swipeSlidesComponent()?.slideToItem({ id: chapterId })``.
  ⇒ CONTRACT: navigation is by chapter DB id (integer),
  not by file path. moodle-dl's offline equivalent
  (the Table of Contents.html) cannot use this mechanism,
  so it must use the chapter's on-disk folder name in href.

If any of these tests fail because the mobile app source has
changed, the docstring cites the file:line so the contract can
be re-verified.
"""

import os
import re
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MOBILE_APP_TS = (
    '/Users/linqilan/CodingProjects/moodle/'
    'moodle_mobile_app_official_repo_for_reference'
)


def _read_ts(rel):
    """Read a TypeScript/HTML file from the official mobile app repo."""
    full = os.path.join(MOBILE_APP_TS, rel)
    if not os.path.exists(full):
        return ''
    with open(full, encoding='utf-8') as f:
        return f.read()


def _slice_around(src, marker, before=0, after=400):
    idx = src.find(marker)
    if idx < 0:
        return ''
    start = max(0, idx - before)
    return src[start: idx + after]


# =========================================================================
# Mobile app: getContentsMap groups chapter files by /chapter_id/
# =========================================================================
class TestMobileAppBookGetContentsMap:
    """
    The mobile app's ``getContentsMap()`` parses each file's
    ``filepath`` (a string like ``/NN/...``) and uses the
    ``NN`` part as the chapter key.

    Citation: ``src/addons/mod/book/services/book.ts:110-157``.
    """

    def test_get_contents_map_function_exists(self):
        """Pin that ``getContentsMap()`` is the entry point used to
        group chapter files.

        Citation: ``src/addons/mod/book/services/book.ts:110``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src, 'book.ts not found in mobile app repo'
        assert 'getContentsMap(' in src, (
            'src/addons/mod/book/services/book.ts must define '
            'getContentsMap() (citation: book.ts:110). '
            'moodle-dl mirrors this same grouping.'
        )

    def test_chapter_id_parsed_from_filepath_via_digit_regex(self):
        """Pin: chapter identity comes from
        ``content.filepath.match(/\\/(\\d+)\\//)`` — i.e. the
        numeric chapter directory in the filepath.

        Citation: ``src/addons/mod/book/services/book.ts:123``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        # The exact regex literal should be present. Use raw-string
        # search to avoid Python escape sequence surprises.
        # In TS source: /\/(\d+)\//
        regex_literal = r'''match(/\/(\d+)\//)'''
        assert regex_literal in src, (
            'src/addons/mod/book/services/book.ts must parse the '
            'chapter id from filepath via /\\/(\\d+)\\// '
            '(citation: book.ts:123). moodle-dl must mirror this '
            'contract: chapter identity = numeric directory in '
            'filepath (= book_chapters.id in the DB).'
        )

    def test_chapter_id_files_dropped_when_no_filepath_match(self):
        """Pin: files WITHOUT a numeric directory in their
        filepath are silently dropped by ``return`` (line 125).

        Citation: ``src/addons/mod/book/services/book.ts:124-126``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        # After the regex match, if there's no digit, the
        # function returns early.
        body = _slice_around(src, 'content.filepath.match(/\\/(\\d+)\\//')
        assert 'if (!matches' in body or 'if (!matches || !matches[1])' in body, (
            'src/addons/mod/book/services/book.ts must early-return '
            'when filepath does not contain a numeric directory '
            '(citation: book.ts:124-125). moodle-dl must NOT assume '
            'every file belongs to a chapter.'
        )

    def test_index_html_in_chapter_root_is_chapter_index(self):
        """Pin: when ``content.filename == 'index.html'`` AND the
        filepath is exactly ``/{chapter_id}/`` (root of chapter
        dir), the entry is treated as the chapter's index — its
        ``fileurl`` becomes the chapter's ``indexUrl``.

        Citation: ``src/addons/mod/book/services/book.ts:134-141``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        body = _slice_around(src, "content.filename == 'index.html'")
        assert 'indexUrl' in body, (
            'src/addons/mod/book/services/book.ts must mark '
            "index.html entries with map[chapter].indexUrl "
            '(citation: book.ts:136). moodle-dl mirrors this: '
            'the chapter index is identified by '
            "filename='index.html' AND filepath='/{chapter_id}/'."
        )

    def test_chapter_sub_files_stored_in_paths_map(self):
        """Pin: each non-index file is stored in
        ``map[chapter].paths[relative_path] = content.fileurl``.

        Citation: ``src/addons/mod/book/services/book.ts:150-153``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        # The 'paths' key must be initialized and assigned.
        body = _slice_around(src, 'map[chapter].paths', after=400)
        assert "map[chapter].paths" in body, (
            'src/addons/mod/book/services/book.ts must store '
            'chapter sub-files under map[chapter].paths[...]=fileurl '
            '(citation: book.ts:153). This is the contract for '
            "iframe/img/video src rewriting via restoreSourcesInHtml."
        )

    def test_mobile_app_extracts_chapter_number_only_from_chapter_folder(self):
        """Pin: the mobile app's chapter number is the SAME as
        the directory name in the filepath, which is the chapter
        DB id (book_chapters.id).

        Cross-references:
          * src/addons/mod/book/services/book.ts:123
            (regex extracts the numeric directory)
          * public/mod/book/lib.php:573
            (PHP sets filepath = "/{$chapter->id}/")

        Implication for moodle-dl: when the TOC has
        ``href="12345/index.html"``, the ``12345`` is the chapter
        DB id (not a random slug). The on-disk folder may use a
        DIFFERENT name (e.g. ``*02* Week Overview``) — that is
        the offline equivalent, but it must remain consistent
        with where the chapter's ``index.html`` was written.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        # The regex /\d+/ enforces "digits only" for the chapter
        # directory. The same digit sequence is then used as a
        # key in the contents map.
        regex_literal = r'''match(/\/(\d+)\//)'''
        assert regex_literal in src
        # The chapter string is the captured group (\d+).
        assert 'const chapter: string = matches[1]' in src, (
            'The captured digits are used directly as the map key '
            '(citation: book.ts:128). They correspond to the PHP '
            "lib.php:573 filepath pattern '/{$chapter->id}/'."
        )


# =========================================================================
# Mobile app: getToc / getTocList
# =========================================================================
class TestMobileAppBookTocParsing:
    """
    The mobile app parses the structure file (TOC) from the
    same field that the PHP API exposes:
    ``contents[0].content`` is a JSON-encoded array of TOC entries.
    """

    def test_get_toc_reads_contents_zero_content(self):
        """Pin: ``getToc()`` returns ``parseJSON(contents[0].content)``.

        Citation: ``src/addons/mod/book/services/book.ts:195-201``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        body = _slice_around(src, 'getToc(contents:', before=50, after=300)
        assert 'contents[0].content' in body, (
            'src/addons/mod/book/services/book.ts: getToc must '
            'read contents[0].content (citation: book.ts:200). '
            'This is the same JSON field that PHP lib.php:622 '
            "json_encode()'s and that moodle-dl's book.py parses."
        )

    def test_get_toc_list_parses_id_from_href(self):
        """Pin: each TOC entry's id is parsed via
        ``parseInt(chapter.href.replace('/index.html', ''), 10)``
        — i.e. href is the integer chapter id, not a slug.

        Citation: ``src/addons/mod/book/services/book.ts:221``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        body = _slice_around(src, 'parseInt(chapter.href.replace', after=200)
        assert "chapter.href.replace('/index.html'" in body, (
            'src/addons/mod/book/services/book.ts: chapter id is '
            "parsed by stripping '/index.html' suffix from href "
            '(citation: book.ts:221). The href is an integer '
            'chapter id (= book_chapters.id).'
        )

    def test_toc_list_builds_index_number_string(self):
        """Pin: ``indexNumber`` is built as
        ``previousNumber + (hidden ? 'x.' : `${chapterNumber}.`)``
        — e.g. ``'1.2'`` for chapter 2 at level 1.

        Citation: ``src/addons/mod/book/services/book.ts:218``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        body = _slice_around(src, 'fullChapterNumber', after=400)
        assert 'indexNumber: fullChapterNumber' in body, (
            'src/addons/mod/book/services/book.ts: each chapter '
            'must have an indexNumber string built from '
            'previousNumber + chapterNumber '
            '(citation: book.ts:218, 224). moodle-dl displays the '
            'same chapter number in the offline TOC.'
        )


# =========================================================================
# Mobile app: TOC navigation is by chapter id, not file path
# =========================================================================
class TestMobileAppTocNavigationByChapterId:
    """
    In the mobile app, clicking a TOC entry dispatches the chapter id
    (integer). There is no href-based file-path navigation.

    Citations:
      * src/addons/mod/book/components/toc/toc.ts:60-62 (loadChapter)
      * src/addons/mod/book/pages/contents/contents.ts:155-161 (changeChapter)
    """

    def test_toc_component_loads_by_chapter_id(self):
        """Pin: the TOC modal's click handler calls
        ``loadChapter(id: number)`` where ``id`` is the chapter id.

        Citation: ``src/addons/mod/book/components/toc/toc.ts:60``.
        """
        src = _read_ts('src/addons/mod/book/components/toc/toc.ts')
        assert src
        assert 'loadChapter(id: number)' in src, (
            'src/addons/mod/book/components/toc/toc.ts must '
            'export loadChapter(id: number) '
            '(citation: toc.ts:60). The TOC click handler '
            'dispatches the chapter id (integer), not a URL.'
        )

    def test_toc_template_uses_chapter_id_for_click(self):
        """Pin: the TOC template binds click → ``loadChapter(chapter.id)``.

        Citation: ``src/addons/mod/book/components/toc/toc.html:16``.
        """
        src = _read_ts('src/addons/mod/book/components/toc/toc.html')
        assert src
        assert 'loadChapter(chapter.id)' in src, (
            'src/addons/mod/book/components/toc/toc.html must '
            'bind (click)="loadChapter(chapter.id)" on each TOC '
            'item (citation: toc.html:16). Navigation is by id.'
        )

    def test_change_chapter_calls_slide_to_item_with_id(self):
        """Pin: the contents page's ``changeChapter`` calls
        ``slideToItem({ id: chapterId })``.

        Citation: ``src/addons/mod/book/pages/contents/contents.ts:160``.
        """
        src = _read_ts('src/addons/mod/book/pages/contents/contents.ts')
        assert src
        assert 'slideToItem({ id: chapterId })' in src, (
            'src/addons/mod/book/pages/contents/contents.ts must '
            'call slideToItem({ id: chapterId }) '
            '(citation: contents.ts:160). Navigation is by id.'
        )

    def test_no_href_in_toc_template_navigation(self):
        """Pin: the TOC template does NOT use ``href`` for navigation.

        Citation: ``src/addons/mod/book/components/toc/toc.html``.
        """
        src = _read_ts('src/addons/mod/book/components/toc/toc.html')
        assert src
        # The TOC uses <ion-item (click)=...> — there should be
        # NO <a href="..."> elements pointing to a file path.
        # If there is a literal href, it must be a JS-router link,
        # not a file path.
        assert '<a href' not in src, (
            'src/addons/mod/book/components/toc/toc.html must '
            'NOT contain <a href="..."> elements — the mobile '
            "app's TOC uses Angular click handlers, not file paths. "
            'moodle-dl cannot mirror this contract because the '
            'offline copy has no Angular router — the offline '
            'TOC must use the on-disk folder name in href.'
        )


# =========================================================================
# Mobile app: isFileDownloadable uses type==='file' filter
# =========================================================================
class TestMobileAppIsFileDownloadableContract:
    """
    The mobile app only treats files with ``type === 'file'`` as
    chapter content / attachments. Other types (``'content'``,
    ``'directory'``) are silently skipped.

    Citation: ``src/addons/mod/book/services/book.ts:293-295``.
    """

    def test_is_file_downloadable_type_equals_file(self):
        """Pin: ``isFileDownloadable()`` returns ``file.type === 'file'``.

        Citation: ``src/addons/mod/book/services/book.ts:294``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        # Slice around the isFileDownloadable function definition.
        body = _slice_around(src, 'isFileDownloadable(file', after=100)
        assert 'file.type === ' in body and "'file'" in body, (
            'src/addons/mod/book/services/book.ts: isFileDownloadable '
            "must return file.type === 'file' (citation: book.ts:294). "
            'Files with type=content (e.g. the structure file) and '
            'type=directory are NOT chapter content.'
        )

    def test_non_file_types_excluded_from_contents_map(self):
        """Pin: in ``getContentsMap``, files failing
        ``isFileDownloadable`` are skipped via early return.

        Citation: ``src/addons/mod/book/services/book.ts:117-120``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        body = _slice_around(src, 'if (!this.isFileDownloadable(content))', after=100)
        assert 'return' in body, (
            'src/addons/mod/book/services/book.ts: getContentsMap '
            'must early-return non-file entries '
            '(citation: book.ts:118-120). moodle-dl mirrors this: '
            "only files with type='file' are processed as chapter content."
        )


# =========================================================================
# Mobile app: chapter content rendered with embedded iframe intact
# =========================================================================
class TestMobileAppChapterContentRendering:
    """
    The mobile app fetches the chapter's ``index.html`` and renders
    it as HTML. Embedded iframes (Kaltura) are kept in the HTML and
    their ``src`` is rewritten via ``restoreSourcesInHtml()``.

    Citation: ``src/addons/mod/book/services/book.ts:84-101`` and
    ``src/core/static/dom.ts:1169-1222``.
    """

    def test_get_chapter_content_calls_restore_sources_in_html(self):
        """Pin: ``getChapterContent()`` calls
        ``CoreDom.restoreSourcesInHtml(content, paths)`` before
        returning.

        Citation: ``src/addons/mod/book/services/book.ts:100``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        assert 'restoreSourcesInHtml(' in src, (
            'src/addons/mod/book/services/book.ts: getChapterContent '
            'must call CoreDom.restoreSourcesInHtml '
            '(citation: book.ts:100). moodle-dl mirrors this: '
            'the chapter HTML is fetched once and rendered; '
            'embedded media srcs are rewritten against the paths map.'
        )

    def test_restore_sources_rewrites_iframe_src(self):
        """Pin: ``restoreSourcesInHtml()`` rewrites the ``src`` attribute
        of ``img, video, audio, source, track, iframe, embed``.

        Citation: ``src/core/static/dom.ts:1177``.
        """
        src = _read_ts('src/core/static/dom.ts')
        assert src
        body = _slice_around(src, "querySelectorAll<HTMLElement>('img", after=200)
        # The selector should include iframe.
        assert 'iframe' in body, (
            'src/core/static/dom.ts: restoreSourcesInHtml must '
            'rewrite iframe src attributes (citation: dom.ts:1177). '
            'moodle-dl uses the same contract for chapter content.'
        )

    def test_contents_page_renders_chapter_content_as_html(self):
        """Pin: the contents page passes the chapter content
        through ``<core-format-text [text]="chapter.content" />``
        — i.e. it renders the HTML, NOT extracts embedded videos.

        Citation: ``src/addons/mod/book/pages/contents/contents.html:30``.
        """
        src = _read_ts('src/addons/mod/book/pages/contents/contents.html')
        assert src
        assert '[text]="chapter.content"' in src, (
            'src/addons/mod/book/pages/contents/contents.html: '
            'chapter content is rendered via <core-format-text '
            '[text]="chapter.content" /> (citation: contents.html:30). '
            'The mobile app does NOT extract Kaltura videos; '
            'moodle-dl must do so explicitly (book.py:577+).'
        )


# =========================================================================
# Mobile app: chapter sub-files are in the SAME cm as index.html
# (cross-cite with PHP lib.php:585, 609)
# =========================================================================
class TestMobileAppChapterSubFilesShareBookCm:
    """
    In the mobile app, chapter sub-files (kaltura videos, images,
    attachments) come from ``core_course_get_contents`` as entries
    in the book cm's ``contents[]`` array. They share the SAME
    ``cmid`` as the chapter's ``index.html``.

    The mobile app does NOT have any per-chapter content type
    (no `cookie_mod-kalvidres`, no `url-description-book`).
    It treats ALL chapter files as plain ``type='file'`` entries
    (citation: book.ts:294).

    Cross-references:
      * src/addons/mod/book/services/book.ts:117-120 — non-'file'
        entries are dropped, but the structure-file `type='content'`
        is handled separately in getToc.
      * public/mod/book/lib.php:585, 609 (PHP) — chapter
        index.html + chapter attachments live under ONE cm.
    """

    def test_no_special_handling_for_cookie_mod_or_url_description_in_mobile_app(self):
        """Pin: the mobile app has NO special case for
        ``cookie_mod-kalvidres`` or ``url-description-book``
        modnames — because the official Moodle Mobile App
        treats ALL chapter content as ``type='file'`` (via
        book.ts:294 ``isFileDownloadable``), there is no
        need for a per-modname branch.

        This is the **mobile app's** contract (NOT the PHP
        one). It means: in the mobile app's data model,
        cookie_mod and url-description-book sub-files inside
        a book chapter come back with ``modname='book'``
        (or with no modname override at all), and the
        mobile app groups them with the chapter's other
        files via filepath chapter-id matching
        (book.ts:123).

        Implication for moodle-dl: when extracting book
        chapter sub-files, moodle-dl must group all files
        (including cookie_mod and url-description-book)
        into the SAME book cm position. See
        test_kaltura_video_in_book_chapter_uses_book_counter
        in test_php_core_book_contracts.py.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        # Search for any reference to cookie_mod, kalvidres,
        # url-description, or url-description-book.
        assert 'cookie_mod' not in src, (
            'src/addons/mod/book/services/book.ts must NOT mention '
            'cookie_mod — the mobile app treats all chapter '
            'files uniformly as type=file '
            '(citation: book.ts:294). moodle-dl mirrors this '
            'by ensuring cookie_mod and url-description-book '
            'sub-files share the book cm position.'
        )
        assert 'kalvidres' not in src, (
            'src/addons/mod/book/services/book.ts must NOT mention '
            'kalvidres — the mobile app does not special-case '
            'Kaltura videos at the file-handling level; they are '
            'embedded iframes inside the chapter HTML.'
        )
        assert 'url-description' not in src, (
            'src/addons/mod/book/services/book.ts must NOT mention '
            'url-description — the mobile app does not extract '
            'URL sub-files separately.'
        )

    def test_chapter_paths_map_groups_all_files_by_chapter_id(self):
        """Pin: the mobile app's contents map keys EVERY
        chapter file under the same chapter number (parsed
        from filepath), regardless of what kind of file it is.

        This is the **mobile app's** representation of the
        PHP contract that all chapter files share one cm.

        Citation: ``src/addons/mod/book/services/book.ts:123-153``.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        # All entries are stored under the same chapter key.
        # Look for the actual assignment in the function body.
        assert 'map[chapter].paths[' in src, (
            'src/addons/mod/book/services/book.ts: every chapter '
            'file is stored under map[chapter].paths[...]=fileurl '
            '(citation: book.ts:153). The chapter key is the '
            'numeric directory in filepath — which corresponds '
            'to the PHP lib.php:573 filepath "/{$chapter->id}/".'
        )


# =========================================================================
# Behavioral cross-checks: moodle-dl must honor the mobile app's
# contracts (re-pinned from mobile app's perspective)
# =========================================================================
class TestMoodleDlHonorsMobileAppContracts:
    """
    Behavioral tests pinning that moodle-dl's offline
    representation is consistent with the mobile app's
    data model — even though the offline copy can't use
    the mobile app's Angular router.

    The key insight: the mobile app navigates by chapter id
    (integer) — but the offline copy has no router, so it
    must navigate by file path. Therefore the file path
    in the offline TOC must be the same on-disk folder
    name where the chapter's ``index.html`` was saved.
    """

    def test_offline_toc_href_uses_on_disk_folder_name_like_mobile_app_id_routing(self):
        """Pin: offline TOC's ``<a href>`` uses the on-disk
        folder name (with ``*NN*`` prefix). This is the
        offline equivalent of the mobile app's
        ``loadChapter(chapter.id)`` dispatch
        (toc.ts:60): in both cases the navigation target
        is a stable identifier that resolves to a single
        chapter — for the mobile app it's the integer id,
        for the offline copy it's the folder name.

        Cross-references:
          * src/addons/mod/book/components/toc/toc.ts:60
            — loadChapter(id: number) dispatches integer id
          * src/addons/mod/book/components/toc/toc.html:16
            — (click)="loadChapter(chapter.id)"
          * moodle_dl/moodle/mods/book.py:401 (create_ordered_index)
            — generates the offline TOC
        """
        from moodle_dl.moodle.mods.book import BookMod

        bm = BookMod.__new__(BookMod)

        toc = [
            {
                'id': '691951',  # chapter DB id (= 1.1)
                'title': '1.1 Learning Objectives',
                'href': '*02* 1.1 Learning Objectives/index.html',
                'level': 0,
            },
        ]
        html = bm.create_ordered_index(items=toc)

        # URL-encoded form (* -> %2A, space -> %20)
        assert 'href="%2A02%2A%201.1%20Learning%20Objectives/index.html"' in html, (
            'After Problem 2 fix: TOC href should use the on-disk '
            'folder name (URL-encoded). This mirrors the mobile '
            "app's loadChapter(chapter.id) contract: navigation "
            'target = stable identifier (id for the app, folder '
            'name for the offline copy). Got:\n' + html
        )

    def test_chapter_sub_files_share_folder_like_mobile_app_chapter_paths_map(self):
        """Pin: when the book module produces chapter sub-files
        (cookie_mod kaltura, url-description-book, attachments),
        they all land in the SAME on-disk folder — mirroring
        the mobile app's ``map[chapter].paths[...]=fileurl``
        grouping (book.ts:153).

        Cross-references:
          * src/addons/mod/book/services/book.ts:153 — paths map
          * moodle_dl/moodle/result_builder.py:86 — _assign_positions_to_files
          * tests/test_php_core_book_contracts.py —
            TestMoodleDlHonorsOfficialBookChapterSharedCmContract
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File, MoodleURL

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.moodle_url = MoodleURL(use_http=False, domain='example.com', path='')
        rb.version = 2024100712
        rb.token = 'testtoken'
        rb.mod_plurals = {}

        # The mobile app groups all chapter sub-files under
        # ONE chapter key (book.ts:153). Here we mirror that
        # by ensuring all 3 files (book index, kaltura, webloc)
        # with the SAME module_id get the SAME position.
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
        chapter1_index = File(
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
        chapter1_kaltura = File(
            module_id=7342416, section_id=1,  # SAME module_id
            section_name='Week 2',
            module_name='2. Week Overview', module_modname='cookie_mod-kalvidres',
            content_filepath='/2. Week Overview/',
            content_filename='Week Overview - Video (1_xxx).mp4',
            content_fileurl='https://example.com/video.mp4',
            content_type='cookie_mod',
            content_isexternalfile=True,
            content_filesize=1024000,
            content_timemodified=1700000000,
        )
        files = [book_main, chapter1_index, chapter1_kaltura]

        rb._assign_positions_to_files(files)

        # Contract: the kaltura MUST share the chapter's position.
        # Mirrors book.ts:153 — both go under map[chapter].
        assert files[2].position_in_section == files[1].position_in_section, (
            'Per the mobile app getContentsMap contract '
            '(book.ts:153), all chapter sub-files (cookie_mod, '
            'url-description-book, attachments) are grouped under '
            'the same chapter key in the contents map. moodle-dl '
            'must mirror this: chapter sub-files share the book '
            'cm position so they land in the same on-disk folder. '
            f'Got: index.html pos={files[1].position_in_section}, '
            f'kaltura pos={files[2].position_in_section}'
        )

    def test_chapter_sub_files_share_folder_with_url_description_book(self):
        """Same as above, but for ``url-description-book``
        sub-files (the webloc case from test3).
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
        chapter3_index = File(
            module_id=7342416, section_id=1,
            section_name='Week 2',
            module_name='3. Requirement Analysis', module_modname='book',
            content_filepath='/3. Requirement Analysis/',
            content_filename='index.html',
            content_fileurl='https://example.com/3/index.html',
            content_type='file',
            content_isexternalfile=False,
            content_filesize=1024,
            content_timemodified=1700000000,
        )
        chapter3_url = File(
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
        files = [book_main, chapter3_index, chapter3_url]

        rb._assign_positions_to_files(files)

        assert files[2].position_in_section == files[1].position_in_section, (
            'Per the mobile app getContentsMap contract '
            '(book.ts:153), url-description-book sub-files are '
            'grouped with the chapter in the contents map. '
            'moodle-dl must mirror this by sharing the book '
            'cm position. Got: index.html pos='
            f'{files[1].position_in_section}, '
            f'webloc pos={files[2].position_in_section}'
        )

    def test_print_book_video_src_uses_on_disk_folder_for_offline_renderer(self):
        """Pin: the offline print book HTML's ``<source src>``
        must point to the same on-disk folder where the
        chapter's ``index.html`` was saved.

        Cross-references:
          * src/addons/mod/book/services/book.ts:84-101 — chapter
            content fetched and rendered with paths map
          * src/addons/mod/book/services/book.ts:153 — paths map
            keys are RELATIVE paths within the chapter (no
            folder prefix), values are absolute fileurls
          * src/core/static/dom.ts:1177-1198 — restoreSourcesInHtml
            rewrites media src using the paths map

        The mobile app keeps iframe src as a REMOTE URL after
        restoreSourcesInHtml (the iframe still loads from the
        server). The offline copy has no remote URL fallback —
        so the offline ``<source src>`` must use the on-disk
        folder name (with the ``*NN*`` prefix), so clicking
        play loads the file that was actually downloaded.
        """
        from moodle_dl.moodle.mods.book import BookMod

        config = MagicMock()
        bm = BookMod.__new__(BookMod)
        bm.config = config

        print_book_html = '''<iframe class="kaltura-player-iframe"
                src="https://kaf.example.com/filter/kaltura/lti_launch.php?entry_id=1_test_video"
                style="width: 608px; height: 401px"></iframe>'''

        # The on-disk folder name (with *NN* prefix) is the
        # chapter's index.html path. The video must be in
        # the same folder.
        chapter_mapping = {
            '691952': {
                'title': 'Week Overview',
                'folder_name': '*02* Week Overview',
                'videos': [
                    {'entry_id': '1_test_video', 'filename': 'video.mp4'},
                ],
            }
        }

        result = bm._create_linked_print_book_html(
            print_book_html, chapter_mapping
        )

        assert 'source src="*02* Week Overview/video.mp4"' in result, (
            'Offline print book <source src> should use the '
            'on-disk folder name (with *NN* prefix). This mirrors '
            "the mobile app's per-chapter paths map (book.ts:153) — "
            'both the index.html and the iframe-replaced video '
            'must be in the SAME folder. Got:\n' + result[:500]
        )


# =========================================================================
# Mobile app: no native kaltura extraction (offline-only feature)
# =========================================================================
class TestMobileAppNoKalturaExtraction:
    """
    The mobile app does NOT extract Kaltura videos to standalone
    files — it keeps the iframe as-is in the chapter HTML and
    relies on the iframe to stream the video from the server.

    moodle-dl's ``_extract_kaltura_videos_from_html`` (book.py:577+)
    is therefore an OFFLINE-ONLY feature, with no equivalent in
    the mobile app's TypeScript source.
    """

    def test_no_standalone_kaltura_extraction_in_mobile_app(self):
        """Pin: there is NO ``extractKalturaVideos`` (or
        equivalent) in the mobile app's book module.

        This documents that the mobile app's data model is
        ``chapter_html = "<iframe>...</iframe>"`` — the
        video is rendered inline. moodle-dl's offline
        equivalent (extracting the video to a downloadable
        file) is a moodle-dl-specific feature.
        """
        src = _read_ts('src/addons/mod/book/services/book.ts')
        assert src
        assert 'extractKaltura' not in src, (
            'src/addons/mod/book/services/book.ts must NOT '
            'contain extractKaltura* — the mobile app does '
            'not extract Kaltura videos to standalone files. '
            'It keeps the iframe inline. moodle-dl must do '
            'the extraction explicitly (book.py:577+).'
        )
        assert 'kaltura' not in src.lower(), (
            'src/addons/mod/book/services/book.ts must NOT '
            'mention kaltura — Kaltura handling happens at '
            'the iframe-rendering layer '
            '(src/core/static/dom.ts:1177), not at the book '
            'module layer.'
        )


if __name__ == '__main__':
    import unittest
    unittest.main()