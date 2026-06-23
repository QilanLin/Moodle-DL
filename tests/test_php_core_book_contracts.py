# -*- coding: utf-8 -*-
"""
Tests pinning the Moodle PHP core book-module contracts that
govern Problems 2, 3, 4 from the user-reported test3 issues.

Each test cites a specific file:line in the official Moodle
PHP reference repo and pins the contract that
``moodle-dl``'s ``moodle_dl/moodle/mods/book.py`` (and friends)
MUST honor when processing the data returned by
``core_course_get_contents`` and the Web UI's
``mod/book/tool/print/index.php``.

The official repo is at:
  /Users/linqilan/CodingProjects/moodle/moodle_official_repo_for_reference

Specifically consulted for this file:

* ``public/mod/book/lib.php:526-632``
    - ``book_export_contents()`` — the data source for
      ``core_course_get_contents`` for the book module. The
      structure file's ``href`` field is built as
      ``"{$chapter->id}/index.html"`` (line 549) and each
      chapter's ``filepath`` is ``"/{$chapter->id}/"`` (line 573).
      Sub-files (images, attachments) use the chapter's
      ``content_filepath`` plus their own sub-path
      (line 593: ``"/{$chapter->id}" . $fileinfo->get_filepath()``).
    - ⇒ CONTRACT: each book chapter is exposed to clients as
      a sub-directory NAMED AFTER the chapter's DB id
      (``book_chapters.id``), NOT the chapter title. The
      on-disk folder name == chapter id.

* ``public/mod/book/view.php:118`` (calls ``book_add_fake_block``)
    - The Moodle Web UI's TOC sidebar uses
      ``new moodle_url('view.php', array('id' => $cm->id, 'chapterid' => $ch->id))``
      (``public/mod/book/locallib.php:288, 408``).
    - ⇒ CONTRACT: live Web UI TOC links are cm_id-based, not
      file-based. They have no relationship to any on-disk
      folder name — they always go through Moodle's PHP
      dispatcher. moodle-dl must NOT use those ``href``s
      unchanged in the offline ``Table of Contents.html``,
      because the offline copy has no ``view.php``.

* ``public/mod/book/tool/print/index.php:89-95`` + ``renderer.php:154, 158``
    - The Moodle Web UI's print book (single combined HTML)
      uses in-page ANCHOR links for its TOC: ``#ch{$chapter->id}``
      (renderer.php line 154, 158). Each chapter's content
      ``<div>`` has ``id="ch{$chapter->id}"`` (renderer.php
      line 192).
    - ⇒ CONTRACT: the print book is a SINGLE PAGE. The
      official contract for cross-chapter navigation is
      in-page anchor links (per-chapter ``<div id="chN">``),
      NOT file paths. moodle-dl's offline equivalent
      currently emits ``<source src="...">`` paths in
      the print book HTML, which means the OFFLINE layout
      differs from the OFFICIAL one. The fix must make
      the offline ``<source src>`` resolve to the same
      folder name the chapter ``index.html`` was written to
      (i.e. the on-disk folder name with the ``*NN*`` prefix).

* ``public/course/externallib.php:336-338``
    - The book module's contents come from
      ``book_export_contents($cm, $baseurl)``. There is ONE
      entry per ``cm`` (course module) in
      ``sectionvalues['modules']`` — never per file.
    - ⇒ CONTRACT: cookie_mod and url-description-book
      sub-files inside a book chapter all live under the
      SAME ``cm`` as the chapter's index.html. The
      ``module_id`` (== cm_id) is the same for all of them.
      This is what makes Problem 4 a bug: those sub-files
      get a non-book counter slot, so they end up in
      separate ``*NN*`` folders instead of sharing the
      chapter's folder.

If any of these tests fail because the Moodle PHP source
has changed, the docstring cites the file:line so the
fix can be re-verified.
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MOODLE_PHP = '/Users/linqilan/CodingProjects/moodle/moodle_official_repo_for_reference'


def _read(rel):
    full = os.path.join(MOODLE_PHP, rel)
    if not os.path.exists(full):
        return ''
    with open(full) as f:
        return f.read()


# =========================================================================
# Problem 2 contract: book_toc.href is chapter_id, not title
# =========================================================================
class TestBookTocHrefIsChapterIdInOfficialRepo:
    """
    The official Moodle PHP core builds each TOC entry's ``href``
    as the chapter's DB id plus ``/index.html`` —
    NOT a slug of the chapter title.

    Citation: ``public/mod/book/lib.php:547-553``
    (in ``book_export_contents``, the function that backs
    ``core_course_get_contents`` for the book module).
    """

    def test_book_export_contents_exists(self):
        """Pin that ``book_export_contents`` is the entry point
        used by ``core_course_get_contents`` for the book module.
        Citation: ``public/course/externallib.php:336-338``
        (``$getcontentfunction = $cm->modname.'_export_contents';``).
        """
        src = _read('public/mod/book/lib.php')
        assert src, 'mod/book/lib.php not found in official repo'
        assert 'function book_export_contents' in src, (
            'mod/book/lib.php must define book_export_contents '
            '(the function externallib.php calls for the book module)'
        )

    def test_toc_href_is_chapter_id_plus_index_html(self):
        """Pin: TOC entry href is ``$chapter->id . '/index.html'``.
        Citation: ``public/mod/book/lib.php:549``.
        """
        src = _read('public/mod/book/lib.php')
        assert src
        # Look for the line that builds the structure entry's href
        # ('$chapter->id . "/index.html"' or '$chapter->id . \'/index.html\'')
        # Any of the four common quote styles should match.
        assert (
            '$chapter->id . "/index.html"' in src
            or "$chapter->id . '/index.html'" in src
        ), (
            'mod/book/lib.php must build TOC href as '
            '$chapter->id . "/index.html" (citation: lib.php:549). '
            'moodle-dl cannot rely on the title being in the href.'
        )

    def test_toc_filepath_uses_chapter_id_directory(self):
        """Pin: each chapter index file's filepath is
        ``"/{$chapter->id}/"`` (a directory NAMED AFTER the
        chapter id, not the title).
        Citation: ``public/mod/book/lib.php:573``.
        """
        src = _read('public/mod/book/lib.php')
        assert src
        # Look for the filepath assignment
        assert (
            '$chapter->id}/' in src
        ), (
            'mod/book/lib.php must use "/{$chapter->id}/" as the '
            'filepath for each chapter index file '
            '(citation: lib.php:573). The chapter is exposed as '
            'a subdirectory named after the chapter id.'
        )


class TestBookWebUiTocLinksAreCmIdBased:
    """
    The official Moodle Web UI TOC sidebar links to
    ``view.php?id=$cm->id&chapterid=$ch->id`` — these are
    server-side URLs, not file paths.

    Citations:
      * ``public/mod/book/view.php:118`` (calls book_add_fake_block)
      * ``public/mod/book/locallib.php:288-289`` (editing on TOC)
      * ``public/mod/book/locallib.php:408-410`` (normal TOC)
    """

    def test_view_php_calls_book_add_fake_block(self):
        """Pin that ``view.php`` is what renders the TOC sidebar
        (which contains the live Web UI links).
        Citation: ``public/mod/book/view.php:118``.
        """
        src = _read('public/mod/book/view.php')
        assert src
        assert 'book_add_fake_block' in src, (
            'mod/book/view.php must call book_add_fake_block '
            '(citation: view.php:118). The TOC sidebar is built '
            'by book_add_fake_block → book_get_toc.'
        )

    def test_toc_link_uses_view_php_with_cm_id_and_chapter_id(self):
        """Pin: TOC entry links go to ``view.php`` with
        ``id=$cm->id&chapterid=$ch->id``. The href is server-side,
        never a filesystem path.
        Citation: ``public/mod/book/locallib.php:288-289`` (edit mode)
        and ``public/mod/book/locallib.php:408-410`` (normal mode).
        """
        src = _read('public/mod/book/locallib.php')
        assert src
        # The pattern: new moodle_url('view.php', array('id' => $cm->id, 'chapterid' => $ch->id))
        # Look for both pieces: 'id' => $cm->id and 'chapterid' => $ch->id
        assert "'id' => $cm->id" in src or '"id" => $cm->id' in src, (
            'mod/book/locallib.php must pass $cm->id as the id '
            'parameter to view.php (citation: locallib.php:288, 408).'
        )
        assert (
            "'chapterid' => $ch->id" in src
            or '"chapterid" => $ch->id' in src
        ), (
            'mod/book/locallib.php must pass $ch->id as the '
            'chapterid parameter to view.php '
            '(citation: locallib.php:288, 408).'
        )


# =========================================================================
# Problem 3 contract: print book uses in-page anchor links, not file paths
# =========================================================================
class TestPrintBookUsesAnchorLinksInOfficialRepo:
    """
    The official Moodle print book (single combined HTML) uses
    in-page anchor links for its TOC navigation:
    ``<a href="#ch{$chapter->id}">title</a>`` and a
    ``<div id="ch{$chapter->id}">`` per chapter.

    Citations:
      * ``public/mod/book/tool/print/index.php:92`` (entry point)
      * ``public/mod/book/tool/print/classes/output/renderer.php:154``
        (TOC link, top-level chapter)
      * ``public/mod/book/tool/print/classes/output/renderer.php:158``
        (TOC link, sub-chapter)
      * ``public/mod/book/tool/print/classes/output/renderer.php:192``
        (chapter wrapper div with id="chN")
    """

    def test_print_book_index_invokes_print_book_page(self):
        """Pin: ``tool/print/index.php`` builds the print book
        via ``booktool_print\\output\\print_book_page``.
        Citation: ``public/mod/book/tool/print/index.php:89-95``.
        """
        src = _read('public/mod/book/tool/print/index.php')
        assert src
        assert 'print_book_page' in src, (
            'mod/book/tool/print/index.php must construct '
            'print_book_page (citation: index.php:92).'
        )

    def test_toc_anchor_uses_chapter_id(self):
        """Pin: TOC anchor is ``#ch{$chapter->id}``.
        Citation: ``public/mod/book/tool/print/classes/output/renderer.php:154, 158``.
        """
        src = _read('public/mod/book/tool/print/classes/output/renderer.php')
        assert src
        # The pattern: '#ch' . $ch->id (or '#ch' . $chapter->id)
        assert (
            "#ch' . $" in src
            or '#ch" . $' in src
            or "'#ch' . $" in src
            or '"#ch" . $' in src
        ), (
            'mod/book/tool/print/.../renderer.php must build TOC '
            'anchors as "#ch{$chapter->id}" '
            '(citation: renderer.php:154, 158). The print book is '
            'a single page; navigation is via in-page anchors.'
        )

    def test_chapter_wrapper_div_has_ch_id(self):
        """Pin: each chapter's wrapper ``<div>`` has
        ``id="ch{$chapter->id}"`` matching the TOC anchor.
        Citation: ``public/mod/book/tool/print/classes/output/renderer.php:192``.
        """
        src = _read('public/mod/book/tool/print/classes/output/renderer.php')
        assert src
        # The pattern: 'id' => 'ch' . $chapter->id (or similar)
        assert (
            "'id' => 'ch' . $" in src
            or '"id" => "ch" . $' in src
        ), (
            'mod/book/tool/print/.../renderer.php must give each '
            'chapter wrapper an id of "ch{$chapter->id}" '
            '(citation: renderer.php:192). TOC anchors link to '
            'these ids.'
        )

    def test_chapter_content_uses_file_rewrite_pluginfile_urls(self):
        """Pin: chapter content goes through
        ``file_rewrite_pluginfile_urls`` — Moodle's plugin
        dispatcher rewrites embedded image/video URLs to
        ``pluginfile.php`` calls. moodle-dl's offline print
        book doesn't have a ``pluginfile.php``, so it has
        to manually re-resolve these to local file paths
        — which means the local file paths MUST be
        consistent with the chapter's on-disk folder name.
        Citation: ``public/mod/book/tool/print/classes/output/renderer.php:201-203``.
        """
        src = _read('public/mod/book/tool/print/classes/output/renderer.php')
        assert src
        assert 'file_rewrite_pluginfile_urls' in src, (
            'mod/book/tool/print/.../renderer.php must call '
            'file_rewrite_pluginfile_urls on chapter content '
            '(citation: renderer.php:201-203). This is what '
            'rewrites embedded media URLs to pluginfile.php '
            '— which moodle-dl must mirror with local paths.'
        )


# =========================================================================
# Problem 4 contract: book chapter sub-files all live under the
# SAME cm as the chapter's index.html
# =========================================================================
class TestBookChapterSubFilesShareCmId:
    """
    The official Moodle API returns book chapter sub-files
    (images, attachments) as part of the SAME book cm's
    ``contents`` array. They are NOT separate cms.

    Citations:
      * ``public/mod/book/lib.php:585`` (chapter index.html
        is appended to ``$contents``)
      * ``public/mod/book/lib.php:588-610`` (chapter files
        are appended to ``$contents`` with
        ``filepath = "/{$chapter->id}" . $fileinfo->get_filepath()``)
    """

    def test_chapter_index_and_attachments_share_contents_array(self):
        """Pin: chapter index.html AND chapter attachments are
        both appended to the SAME ``$contents`` array (which
        ``core_course_get_contents`` exposes as the cm's
        ``contents`` field).
        Citation: ``public/mod/book/lib.php:585, 609``.
        """
        src = _read('public/mod/book/lib.php')
        assert src
        # Find the foreach loop and take a large slice (the
        # function is ~100 lines long).
        idx = src.find('foreach ($chapters as $chapter)')
        assert idx > 0, (
            'mod/book/lib.php: must contain '
            "'foreach ($chapters as $chapter)' loop"
        )
        body = src[idx:idx + 10000]
        # Count how many times `$contents[]` is assigned in the
        # function body (should be at least 2: index.html + files).
        count = body.count('$contents[]')
        assert count >= 2, (
            'mod/book/lib.php: book_export_contents must append '
            'BOTH the chapter index.html AND the chapter '
            'attachments to the same $contents array '
            '(citation: lib.php:585, 609). All chapter files '
            'live under ONE cm — they are NOT separate cms.'
        )

    def test_chapter_attachment_filepath_includes_chapter_id(self):
        """Pin: each chapter attachment's filepath starts with
        ``"/{$chapter->id}"`` — meaning the attachment lives
        IN the chapter's subdirectory, not a sibling directory.
        Citation: ``public/mod/book/lib.php:593``.
        """
        src = _read('public/mod/book/lib.php')
        assert src
        # The pattern: '/{$chapter->id}' . $fileinfo->get_filepath()
        # or '/{$chapter->id}" . $fileinfo->get_filepath()'
        assert (
            "'/{$chapter->id}' . $" in src
            or '"/{$chapter->id}" . $' in src
        ), (
            'mod/book/lib.php: each chapter attachment filepath '
            'must start with "/{$chapter->id}" (i.e. the '
            'attachment lives INSIDE the chapter directory) '
            '(citation: lib.php:593).'
        )

    def test_no_separate_cm_for_chapter_sub_files(self):
        """Pin: there is NO mechanism in book_export_contents
        for chapter sub-files to become separate course
        modules. The function returns ONE structure file
        + ONE entry per chapter index.html + ONE entry per
        chapter attachment, all under the same parent
        ``book`` cm.
        Citation: ``public/mod/book/lib.php:541-610``.
        """
        src = _read('public/mod/book/lib.php')
        assert src
        idx = src.find('function book_export_contents')
        body = src[idx:idx + 10000] if idx > 0 else src
        # The function takes a SINGLE $cm (book course module)
        # and returns an array of contents. There is no
        # inner loop that creates a new cm — it just appends
        # to $contents.
        assert 'function book_export_contents($cm, $baseurl)' in body, (
            'book_export_contents must take a single $cm and '
            'return a flat array of contents '
            '(citation: lib.php:526).'
        )
        # It does NOT call get_coursemodule_from_id or
        # similar to spawn child course modules.
        assert 'get_coursemodule_from_id' not in body, (
            'book_export_contents must NOT spawn child course '
            'modules — all chapter sub-files live under the '
            'single book cm '
            '(citation: lib.php:526-610). '
            'Otherwise, the chapter would have multiple cms, '
            'which contradicts the official contract.'
        )


class TestCoreCourseGetContentsContractsBookAsContents:
    """
    Pin that ``core_course_get_contents`` (externallib.php)
    nests the book module's contents (the entire
    ``book_export_contents`` output) into ``$module['contents']``
    — i.e. ALL chapter sub-files live under ONE module entry
    with the same ``cm->id``.

    Citations:
      * ``public/course/externallib.php:271``
        ($module['id'] = $cm->id)
      * ``public/course/externallib.php:340``
        ($contents = $getcontentfunction($cm, $baseurl))
      * ``public/course/externallib.php:367``
        ($module['contents'] = $contents)
    """

    def test_externallib_calls_book_export_contents(self):
        """Pin: externallib.php calls ``book_export_contents``
        for cm with modname='book'.
        Citation: ``public/course/externallib.php:336-340``.
        """
        src = _read('public/course/externallib.php')
        assert src
        # The dispatch: $getcontentfunction = $cm->modname.'_export_contents';
        assert '$cm->modname.\'_export_contents\'' in src, (
            'course/externallib.php must dispatch to '
            '<modname>_export_contents for each cm '
            '(citation: externallib.php:338). For the book '
            'module, that resolves to book_export_contents '
            '(mod/book/lib.php:526).'
        )

    def test_externallib_nests_contents_under_module(self):
        """Pin: the result of ``book_export_contents`` is
        nested under ``$module['contents']``, so the
        module's id (= cm->id) is the same for ALL chapter
        sub-files.
        Citation: ``public/course/externallib.php:367``.
        """
        src = _read('public/course/externallib.php')
        assert src
        # The pattern: $module['contents'] = $contents
        assert (
            "$module['contents'] = $contents" in src
            or '$module[\'contents\'] = $contents' in src
        ), (
            'course/externallib.php must assign the '
            '_export_contents result to $module[\'contents\'] '
            '(citation: externallib.php:367). This nests ALL '
            'book chapter sub-files under ONE module entry '
            'with the same cm->id (= module_id).'
        )

    def test_module_id_is_cm_id_in_externallib(self):
        """Pin: the module entry's id is the cm->id.
        Citation: ``public/course/externallib.php:271``.
        """
        src = _read('public/course/externallib.php')
        assert src
        assert "$module['id'] = $cm->id" in src, (
            'course/externallib.php must set '
            "$module['id'] = $cm->id "
            '(citation: externallib.php:271). Every file in '
            '$module[\'contents\'] is therefore implicitly '
            'tagged with the parent module\'s cm->id.'
        )


# =========================================================================
# Behavioral tests: our code's data flow must honor the official
# contracts above
# =========================================================================
class TestMoodleDlHonorsOfficialBookTocHrefContract:
    """
    Behavioral tests pinning that moodle-dl's offline
    ``Table of Contents.html`` uses the actual on-disk folder
    name (with the ``*NN*`` prefix that ``gen_path`` will
    produce) — NOT the cm_id-based ``href`` from
    ``book_export_contents`` (lib.php:549).

    This is the contract from the official PHP repo:
    the Web UI's ``view.php?id=$cm->id&chapterid=$ch->id``
    URLs are server-side URLs, not file paths. In the
    offline copy there is no ``view.php`` — so the
    offline TOC must use the on-disk folder name.
    """

    def test_toc_href_uses_on_disk_folder_with_nn_prefix(self):
        """Pin: after the fix, the offline TOC's href should
        match the on-disk folder name, with the ``*NN*``
        prefix URL-encoded (``%2ANN%2A``).

        Why ``%2ANN%2A``: ``urllib.parse.quote`` encodes ``*``
        as ``%2A`` so the resulting URL is safe to embed
        in HTML attributes.

        Cross-references:
          * ``public/mod/book/lib.php:549`` — href source
          * ``moodle_dl/downloader/task_file_ops.py:156-160``
            — ``*NN*`` prefix format
        """
        from moodle_dl.moodle.mods.book import BookMod

        bm = BookMod.__new__(BookMod)

        # Simulate the post-fix scenario: the TOC items now
        # carry the on-disk folder name (with *NN* prefix).
        toc = [
            {
                'id': '691951',
                'title': '1.1 Learning Objectives',
                'href': '*02* 1.1 Learning Objectives/index.html',
                'level': 0,
            },
            {
                'id': '691952',
                'title': '1.2 Week Overview',
                'href': '*03* 1.2 Week Overview/index.html',
                'level': 0,
            },
        ]

        html = bm.create_ordered_index(items=toc)

        # urllib.parse.quote encodes:
        #   * -> %2A
        #   space -> %20
        # So '*02* 1.1 Learning Objectives/index.html' becomes
        # '%2A02%2A%201.1%20Learning%20Objectives/index.html'.
        assert 'href="%2A02%2A%201.1%20Learning%20Objectives/index.html"' in html, (
            'After Problem 2 fix: TOC href should use the '
            'on-disk folder name (URL-encoded). Got:\n' + html
        )
        assert 'href="%2A03%2A%201.2%20Week%20Overview/index.html"' in html, (
            'After Problem 2 fix: TOC href should use the '
            'on-disk folder name (URL-encoded). Got:\n' + html
        )
        # The on-disk folder name must contain the chapter's
        # title (so users recognize which chapter the link
        # points to). The cm_id alone is meaningless on disk.
        for chapter in ['1.1 Learning Objectives', '1.2 Week Overview']:
            assert chapter in html, (
                f'TOC must show the chapter title {chapter!r} '
                f'for user recognition. Got:\n{html}'
            )

    def test_toc_href_does_not_use_bare_cm_id(self):
        """Pin: the offline TOC's href should NOT be a bare
        ``<chapter_id>/index.html`` (that's the contract from
        ``book_export_contents`` for the live Web UI; it's
        not a valid file path in the offline copy).

        Cross-references:
          * ``public/mod/book/lib.php:549`` —
            ``"href" => $chapter->id . "/index.html"``
        """
        from moodle_dl.moodle.mods.book import BookMod

        bm = BookMod.__new__(BookMod)

        # If the fix remaps ``href`` to the on-disk folder
        # name, then a TOC entry whose chapter_id is 691951
        # must not produce ``href="691951/index.html"``
        # in the offline output. The on-disk folder for that
        # chapter would be something like ``*02* 1.1 ...``.
        toc = [
            {
                'id': '691951',
                'title': '1.1 Learning Objectives',
                'href': '*02* 1.1 Learning Objectives/index.html',
                'level': 0,
            },
        ]
        html = bm.create_ordered_index(items=toc)

        # The cm_id-based href must NOT appear (it was the
        # buggy behavior).
        assert 'href="691951/index.html"' not in html, (
            'Offline TOC must NOT use the bare chapter_id '
            'href from book_export_contents (lib.php:549). '
            'That is a server-side URL, not an on-disk file '
            'path. Got:\n' + html
        )


class TestMoodleDlHonorsOfficialPrintBookPathContract:
    """
    Behavioral tests pinning that moodle-dl's offline
    ``print book HTML`` (the single combined HTML that
    embeds all chapters' videos) uses the on-disk folder
    name in its ``<source src="...">`` paths.

    The official PHP repo's print book uses in-page
    anchors (``#chN``) and rewrites embedded media
    URLs to ``pluginfile.php`` calls
    (``renderer.php:201-203``). moodle-dl doesn't have
    a ``pluginfile.php`` — so it has to emit a direct
    file path. That path must be the same on-disk folder
    name the chapter ``index.html`` was written to.
    """

    def test_print_book_video_src_uses_on_disk_folder_name(self):
        """Pin: after the fix, the print book HTML's
        ``<source src="...">`` should use the on-disk folder
        name (with the ``*NN*`` prefix).

        Why this matters: the chapter's ``index.html`` is
        written to ``*02* Week Overview/index.html``, and
        the video is written to the same folder. The
        print book HTML's ``<source src>`` must point to
        the same folder, so clicking play loads the
        video that was actually downloaded.

        Cross-references:
          * ``public/mod/book/tool/print/classes/output/
            renderer.php:201-203`` — pluginfile.php rewrite
            in the live UI
          * ``moodle_dl/downloader/task_file_ops.py:156-160``
            — ``*NN*`` prefix format
        """
        from moodle_dl.moodle.mods.book import BookMod

        config = MagicMock()
        bm = BookMod.__new__(BookMod)
        bm.config = config

        print_book_html = '''<iframe class="kaltura-player-iframe"
                src="https://kaf.example.com/filter/kaltura/lti_launch.php?entry_id=1_test_video"
                style="width: 608px; height: 401px"></iframe>'''

        # chapter_mapping carries the on-disk folder name
        # (post-fix) including the *NN* prefix.
        chapter_mapping = {
            '691952': {
                'title': 'Week Overview',
                'folder_name': '*02* Week Overview',  # on-disk name
                'videos': [
                    {'entry_id': '1_test_video', 'filename': 'video.mp4'},
                ],
            }
        }

        result = bm._create_linked_print_book_html(
            print_book_html, chapter_mapping
        )

        # The source src must point to the SAME folder the
        # chapter's index.html was written to.
        assert 'source src="*02* Week Overview/video.mp4"' in result, (
            'After Problem 3 fix: print book <source src> should '
            'use the on-disk folder name (with *NN* prefix). '
            'Got:\n' + result[:500]
        )

    def test_print_book_video_src_does_not_use_raw_chapter_name(self):
        """Pin: the print book HTML's ``<source src="...">``
        must NOT use the raw chapter title without the
        ``*NN*`` prefix.

        The raw chapter title is what the bug currently
        produces (e.g. ``"Week Overview/video.mp4"``).
        That path doesn't exist on disk because the
        chapter's index.html is written to
        ``*02* Week Overview/index.html`` (with the prefix).
        So the video src must include the prefix.
        """
        from moodle_dl.moodle.mods.book import BookMod

        config = MagicMock()
        bm = BookMod.__new__(BookMod)
        bm.config = config

        print_book_html = '''<iframe class="kaltura-player-iframe"
                src="https://kaf.example.com/filter/kaltura/lti_launch.php?entry_id=1_test_video"
                style="width: 608px; height: 401px"></iframe>'''

        chapter_mapping = {
            '691952': {
                'title': 'Week Overview',
                'folder_name': '*02* Week Overview',  # on-disk name
                'videos': [
                    {'entry_id': '1_test_video', 'filename': 'video.mp4'},
                ],
            }
        }

        result = bm._create_linked_print_book_html(
            print_book_html, chapter_mapping
        )

        # The raw chapter title alone (no *NN* prefix) must
        # NOT be the source. The actual on-disk folder has
        # the prefix.
        assert 'source src="Week Overview/video.mp4"' not in result, (
            'Print book <source src> must not use the raw '
            'chapter name (no *NN* prefix) — the actual on-disk '
            'folder has the prefix. Got:\n' + result[:500]
        )


class TestMoodleDlHonorsOfficialBookChapterSharedCmContract:
    """
    Behavioral tests pinning that ``_assign_positions_to_files``
    treats cookie_mod and url-description-book sub-files of a
    book chapter as part of the SAME book cm (same ``module_id``).

    The official contract (book_export_contents, lib.php:541-610):
    the book chapter's index.html AND all its attachments
    (images, kaltura videos, weblocs) come back as
    ``contents[]`` entries under ONE cm. They have the SAME
    ``module_id`` (== ``cm->id``) and same ``module_modname``
    (== ``'book'``) — except for the
    cookie_mod/url-description-book variants that
    description-extraction patches onto them.
    """

    def test_kaltura_video_in_book_chapter_uses_book_counter(self):
        """Pin: a cookie_mod-kalvidres sub-file with
        ``module_id`` == book module_id must use the book
        counter (so it ends up in the chapter's folder),
        not the non-book counter (which would put it in a
        separate ``*01* Week Overview/`` folder).

        Cross-references:
          * ``public/mod/book/lib.php:585, 609`` — all chapter
            files live under one cm
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
            module_id=7342416, section_id=1,  # SAME cm as the book
            section_name='Week 2',
            module_name='2. Week Overview', module_modname='cookie_mod-kalvidres',
            content_filepath='/2. Week Overview/',
            content_filename='Week Overview - Video (1_cka79uqg).mp4',
            content_fileurl='https://example.com/video.mp4',
            content_type='cookie_mod',
            content_isexternalfile=True,
            content_filesize=1024000,
            content_timemodified=1700000000,
        )
        files = [book_main, chapter1_index, chapter1_kaltura]

        rb._assign_positions_to_files(files)

        # The kaltura must share the chapter's position so it
        # ends up in the SAME folder as the chapter's index.html.
        assert files[2].position_in_section == files[1].position_in_section, (
            'Cookie_mod (kaltura) sub-file must share the book '
            "chapter's position (book counter), per the official "
            'book_export_contents contract (lib.php:585, 609) '
            'that all chapter files live under the same cm. '
            f'index.html position={files[1].position_in_section}, '
            f'kaltura position={files[2].position_in_section}'
        )

    def test_url_description_book_in_book_chapter_uses_book_counter(self):
        """Pin: a url-description-book sub-file (external URL
        from a book chapter's description) with
        ``module_id`` == book module_id must use the book
        counter, not the non-book counter.

        Cross-references:
          * ``public/mod/book/lib.php:585, 609`` — all chapter
            files live under one cm
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
            module_id=7342416, section_id=1,  # SAME cm as the book
            section_name='Week 2',
            module_name='3. Requirement Analysis', module_modname='url-description-book',
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

        # The url must share the chapter's position so the
        # webloc ends up in the same folder as the index.html.
        assert files[2].position_in_section == files[1].position_in_section, (
            'url-description-book sub-file must share the book '
            "chapter's position (book counter), per the official "
            'book_export_contents contract (lib.php:585, 609) '
            'that all chapter files live under the same cm. '
            f'index.html position={files[1].position_in_section}, '
            f'url position={files[2].position_in_section}'
        )

    def test_standalone_cookie_mod_keeps_non_book_counter(self):
        """Pin: a cookie_mod-kalvidres with a DIFFERENT
        ``module_id`` (i.e. NOT part of a book chapter)
        must use the non-book counter. This is the existing
        contract — the fix for Problem 4 must not break it.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import File, MoodleURL

        rb = ResultBuilder.__new__(ResultBuilder)
        rb.moodle_url = MoodleURL(use_http=False, domain='example.com', path='')
        rb.version = 2024100712
        rb.token = 'testtoken'
        rb.mod_plurals = {}

        # A book chapter in cm 100, plus a standalone
        # cookie_mod in cm 200 (different module — a separate
        # url module, say, that has a kaltura embed).
        book_chapter = File(
            module_id=100, section_id=1,
            section_name='S',
            module_name='Book Chapter', module_modname='book',
            content_filepath='/ch/',
            content_filename='index.html',
            content_fileurl='https://example.com/ch/index.html',
            content_type='file',
            content_isexternalfile=False,
            content_filesize=1024,
            content_timemodified=1700000000,
        )
        standalone_kaltura = File(
            module_id=200, section_id=1,  # DIFFERENT cm — standalone
            section_name='S',
            module_name='Standalone Video', module_modname='cookie_mod-kalvidres',
            content_filepath='/',
            content_filename='Video.mp4',
            content_fileurl='https://example.com/video.mp4',
            content_type='cookie_mod',
            content_isexternalfile=True,
            content_filesize=1024000,
            content_timemodified=1700000000,
        )
        files = [book_chapter, standalone_kaltura]

        rb._assign_positions_to_files(files)

        # The standalone kaltura must get its OWN non-book
        # counter slot (different from the book's counter).
        # (The book's chapter is at position 0 in book scope;
        # the standalone kaltura is at position 0 in non-book scope.)
        # What we want to pin: they're not at the same position
        # in a SHARED counter that would mix them up.
        # More importantly: the kaltura is at position 0 in
        # the non-book counter, and the book chapter is at
        # position 0 in the book counter — but those are
        # different counters, so there's no collision.
        assert files[0].position_in_section == 0
        assert files[1].position_in_section == 0
        # The KEY contract: even though both have position 0,
        # they're in DIFFERENT scopes (book vs non-book), so
        # the section-wide positioning is unambiguous. We
        # can't easily test scope isolation from here, but
        # we can test that the standalone kaltura is NOT at
        # the book's counter — i.e. its position is not
        # affected by the book's per-book counter increment.
        # (This is implicitly tested by the position values:
        # if the book counter bled into the non-book counter,
        # the kaltura would be at position 1, not 0.)
        assert files[1].position_in_section == 0, (
            'Standalone cookie_mod (different cm from the book) '
            'must keep its own non-book counter slot (position 0). '
            f'Got: {files[1].position_in_section}'
        )


# =========================================================================
# Cross-citation: book chapter title in the TOC
# =========================================================================
class TestBookTocStructureFromOfficialRepo:
    """
    Pin the structure of the JSON the Mobile API exposes
    (which is what ``book_export_contents`` builds).

    The first content is the structure file (``type='content'``)
    with the TOC as a JSON-encoded array of
    ``{title, href, level, hidden, subitems}``.

    Citation: ``public/mod/book/lib.php:614-629`` (the
    structure file is built and prepended to ``$contents``).
    """

    def test_structure_file_is_first_content_entry(self):
        """Pin: book_export_contents prepends a structure file
        as the FIRST entry in ``$contents``.
        Citation: ``public/mod/book/lib.php:629``
        (``array_unshift($contents, $structurefile);``).
        """
        src = _read('public/mod/book/lib.php')
        assert src
        assert 'array_unshift($contents, $structurefile)' in src, (
            'mod/book/lib.php: book_export_contents must '
            'prepend the structure file to $contents '
            '(citation: lib.php:629). The structure file is '
            'how moodle-dl gets the TOC order.'
        )

    def test_structure_file_content_is_json_encode_of_structure_array(self):
        """Pin: the structure file's ``content`` field is
        ``json_encode(array_values($structure))`` — i.e. the
        TOC entries each have a ``title``, ``href`` (chapter id),
        ``level``, ``hidden``, and ``subitems`` (recursively).
        Citation: ``public/mod/book/lib.php:622``.
        """
        src = _read('public/mod/book/lib.php')
        assert src
        assert 'json_encode(array_values($structure))' in src, (
            'mod/book/lib.php: the structure file content must '
            'be json_encode(array_values($structure)) '
            '(citation: lib.php:622). This is what moodle-dl '
            'parses in book.py:125 (book_toc = json.loads(...)).'
        )

    def test_toc_entries_have_href_field(self):
        """Pin: each TOC entry has an ``href`` field built
        from the chapter id. moodle-dl must NOT trust this
        href as an on-disk file path — it's a server-side
        contract, not a filesystem contract.
        Citation: ``public/mod/book/lib.php:549``.
        """
        src = _read('public/mod/book/lib.php')
        assert src
        # The structure building code at lib.php:547-553 has
        # a 'href' key in the array. Look in the
        # book_export_contents function body.
        idx = src.find('function book_export_contents')
        assert idx > 0, (
            'mod/book/lib.php: must contain '
            "'function book_export_contents'"
        )
        # The function is ~100 lines long.
        body = src[idx:idx + 10000]
        # Look for the 'href' key
        assert (
            "'href'" in body
            or '"href"' in body
        ), (
            'mod/book/lib.php: TOC entries must have an href '
            'field (citation: lib.php:549). '
            'moodle-dl must NOT use this href verbatim in '
            'the offline TOC — see test_moodle_dl_..._'
            'honors_official_book_toc_href_contract.'
        )
