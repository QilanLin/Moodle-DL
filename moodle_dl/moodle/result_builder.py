# -*- coding: utf-8 -*-
import hashlib
import html
import logging
import mimetypes
import re
import urllib.parse as urlparse
from typing import Dict, List, Optional

from moodle_dl.downloader.kaltura_patterns import (
    CONTENT_TYPE_KALVIDRES_EMBEDDED,
    COOKIE_MOD_MODNAMES,
    MODULE_COOKIE_HELIXMEDIA,
    MODULE_COOKIE_KALVIDRES,
)
from moodle_dl.types import Course, File, MoodleURL
from moodle_dl.utils import PathTools as PT, UrlHelper


class ResultBuilder:
    """
    Combines all fetched mod files and core course files to one result based on File objects
    """
    MODULE_DIRECTORY_SUFFIXES = (
        'assign',
        'book',
        'data',
        'folder',
        'forum',
        'lesson',
        'page',
        'quiz',
        'workshop',
    )
    MODULE_DIRECTORY_MODNAMES = COOKIE_MOD_MODNAMES

    def __init__(self, moodle_url: MoodleURL, version: int, mod_plurals: Dict, token: str = ''):
        self.version = version
        self.moodle_url = moodle_url
        self.moodle_domain = moodle_url.domain
        self.moodle_base_url = moodle_url.url_base
        self.mod_plurals = mod_plurals
        self.token = token or ''

    def get_files_in_sections(self, course_sections: List[Dict], fetched_mods: Dict[str, Dict]) -> List[File]:
        """
        Iterates over all sections of a course to find files (or modules).
        @param course_sections: Contains the sections of the course
        @param fetched_mods: Contains the fetched mods of the course
        @return: A list of files of the course.
        """
        files = []
        for section in course_sections:
            location = {
                'section_id': section.get('id', 0),
                'section_name': section.get('name', ''),
            }
            section_modules = section.get('modules', [])
            section_files = self._get_files_in_modules(section_modules, fetched_mods, **location)

            section_summary = section.get('summary', '')
            if section_summary is not None and section_summary != '':
                location.update(
                    {
                        'module_id': 0,
                        'module_name': 'Section summary',
                        'module_modname': 'section_summary',
                    }
                )
                section_files += self._handle_description(section_summary, **location)

            # 为当前 section 的所有文件（包括 summary）分配位置索引
            self._assign_positions_to_files(section_files)

            files += section_files

        files += self._get_files_not_on_main_page(fetched_mods)

        import logging
        kalvidres_total = len([f for f in files if f.module_modname == MODULE_COOKIE_KALVIDRES])
        if kalvidres_total > 0:
            logging.info(f'🌐 get_files_in_sections() returning {kalvidres_total} Kaltura videos total')

        return files

    def _assign_positions_to_files(self, files: List[File]) -> None:
        """
        为文件列表中的每个文件分配位置索引。

        系统文件（metadata.json、Table of Contents.html、隐藏文件）不分配索引。
        其他文件按照在列表中的顺序分配从 0 开始的递增索引。

        Scope is ``(section_id, book_module_id)``: position indices run
        sequentially across the WHOLE section, matching the Moodle
        server's ``course_sections.sequence`` order. The only per-module
        exception is the **book** modname: each book chapter is a
        standalone "booklet" that gets its own 0-based counter
        (preserves the historical book contract from the user's
        perspective — opening a book chapter in moodle-dl should look
        the same as opening it in the Moodle web UI).

        Module-folder handling (commit ab20833 + folder prefix):
        A module that ends up in a subfolder (has attachments, or its
        modname triggers the module-folder branch in gen_path) gets
        ONE position slot for the entire module — not one per file.
        This keeps the section-wide *NN* numbering continuous when
        flat files and module folders sort together.

        For example, Week 1's section:
          *01* LECTURE SLIDES.md
          *02* Lecture 0: About the module/
              ├── Lecture 0: About the module.html.md  (no prefix)
              └── Lecture 0: About the module.pdf       (no prefix)
          *03* Lecture 1: Introduction to Machine Learning/
              ├── ...
          *04* Lecture 1: LGT/
              ...
          *12* TUTORIAL.md
          *13* Tutorial 1/
              ├── ...

        Cross-checked against:
          - moodle_official_repo_for_reference/public/course/classes/
            section_info.php:514  (get_sequence_cm_infos)
          - moodle_official_repo_for_reference/public/course/classes/
            modinfo.php:1271       (calculate_section_weights)

        @param files: 文件列表（会被原地修改）
        """
        # 🔧 Pin module-level numbering (folder prefix contract):
        # Each non-book module gets ONE position slot in the
        # section-wide counter. All files within the same module
        # share that slot. Module folders (created when the module
        # has attachments) get the prefix *NN* from this slot.
        # Flat files (no attachments) also use this slot for their
        # *NN* prefix.
        #
        # Example (Week 1 section, after fix):
        #   *01* LECTURE SLIDES.md             (slot 0, flat)
        #   *02* Lecture 0: About the module/  (slot 1, folder, 2 files inside)
        #   *03* Lecture 1: Introduction.../   (slot 2, folder, 2 files inside)
        #   *04* Lecture 1: LGT/               (slot 3, folder, 1 file inside)
        #   *05* Lecture 1 - part 1 of 7: .../  (slot 4, folder, 1 file inside)
        #   ...
        #   *12* TUTORIAL.md                   (slot 11, flat)
        #   *13* Tutorial 1/                    (slot 12, folder, 2 files inside)
        #   ...
        #
        # Book modules keep per-book scope (one counter per book,
        # not per chapter) so chapter sub-folders don't collide.
        # See _position_scope_key.
        non_book_position_by_module = {}  # (section_id, module_id) -> position
        book_position_by_book = {}  # (section_id, book_module_id) -> position
        section_non_book_counter = {}  # section_id -> next position
        book_section_book_counter = {}  # section_id -> next book position
        # Track the order in which (section_id, module_id) pairs are
        # first encountered so the section-wide counter advances in
        # input order (matching server fetch order).
        ordered_non_book_keys = []

        for file in files:
            filename = file.content_filename.lower()
            if self._is_system_file(filename):
                file.position_in_section = None
                continue

            section_id = getattr(file, 'section_id', None)
            modname = getattr(file, 'module_modname', '')

            if modname == 'book':
                # Book: per-book scope (so chapter content + chapter
                # images share the same book counter).
                book_module_id = getattr(file, 'module_id', None)
                # The book module's content_filepath carries the
                # chapter identity — but we want one counter PER BOOK,
                # not per chapter. So we use (section_id, book_module_id).
                # However, for cross-book section ordering, books
                # also need their own counter that advances when a
                # new book is encountered. For now, keep the existing
                # book_scope behavior: each book has its own counter.
                book_key = (section_id, book_module_id)
                if book_key not in book_position_by_book:
                    book_position_by_book[book_key] = 0
                pos = book_position_by_book[book_key]
                file.position_in_section = pos
                book_position_by_book[book_key] = pos + 1
            else:
                # Non-book: each module gets one slot.
                module_id = getattr(file, 'module_id', None)
                key = (section_id, module_id)
                if key not in non_book_position_by_module:
                    # New (section_id, module_id) → assign next slot
                    if section_id not in section_non_book_counter:
                        section_non_book_counter[section_id] = 0
                    pos = section_non_book_counter[section_id]
                    section_non_book_counter[section_id] = pos + 1
                    non_book_position_by_module[key] = pos
                    ordered_non_book_keys.append(key)
                # All files in the same module share the same slot
                file.position_in_section = non_book_position_by_module[key]

    @classmethod
    def _position_scope_key(cls, file: File) -> tuple:
        """Compute the scope key for a file's position index.

        Returns ``(section_id, book_module_id)``:
          * section_id: every file in the same Moodle section shares
            one 0-based counter, matching the order the Moodle server
            returns them in (see section_info.php:514
            ``get_sequence_cm_infos`` in the official Moodle repo).
          * book_module_id: only set for the 'book' modname. All
            files in a book module (across all chapter sub-folders
            and image sub-sub-folders) share one 0-based counter
            — so a chapter 1 image and the chapter 1 html are
            numbered *01* and *02*, not both *01*.

        All other modnames (page, assign, quiz, label, url,
        cookie_mod videos, etc.) share the section-wide counter
        with book_scope=None.

        Pin: this was previously content_filepath-scoped for
        non-book modules (every sub-folder got its own counter,
        producing visually confusing *02* next to *04*). Now every
        sub-folder within a non-book section shares the section
        counter, and book modules get one counter per book (not
        per chapter, because Moodle's book module uses different
        content_filepath values for chapter content vs chapter
        image sub-folders, and per-filepath scoping would collide
        *NN* numbers within a single chapter).

        @param file: The File being indexed.
        @return: ``(section_id, book_module_id)`` tuple.

        Module-level scoping for non-book modules is handled
        separately by ``_assign_positions_to_files``: see how it
        groups files by (section_id, module_id) before assigning
        the counter. Book modules keep their per-book scope because
        Moodle's book module uses different content_filepath values
        for chapter content vs chapter image sub-folders, and
        per-filepath scoping would collide *NN* numbers within a
        single chapter.
        """
        module_modname = getattr(file, 'module_modname', '')
        # Only the book modname gets a per-module scope (the
        # book module_id). Everything else (page, assign, quiz,
        # label, url, cookie_mod videos, etc.) shares the
        # section-wide counter.
        book_scope = getattr(file, 'module_id', None) if module_modname == 'book' else None
        return getattr(file, 'section_id', None), book_scope

    @classmethod
    def _uses_module_directory(cls, module_modname: str) -> bool:
        return module_modname.endswith(cls.MODULE_DIRECTORY_SUFFIXES) or module_modname in cls.MODULE_DIRECTORY_MODNAMES

    # Attachment content types — a file with one of these types
    # counts as a "real" attachment (not just the description HTML
    # preview that every module gets). Used by
    # ``_module_has_attachments`` to decide whether the module
    # needs its own subfolder.
    ATTACHMENT_CONTENT_TYPES = frozenset({
        'resource_file',  # resource module's actual file (PDF/ZIP/etc.)
        'label_file',      # label module's inline image / attachment
        'assign_file',     # assign module's submission template
        'cookie_mod',      # Kaltura / Helixmedia video
        'url_introfile',   # URL module's intro file
        'file',            # generic file (page module's index.html, etc.)
        'description-html', # description that has been rendered as HTML
        'video',           # direct video file
        'audio',           # direct audio file
    })

    @classmethod
    def _module_has_attachments(cls, files_for_module) -> bool:
        """Return True if any file in the module is a real
        attachment (not just the description HTML preview).

        Used to decide whether the module needs its own
        subfolder. Modules with only their description HTML
        preview are flattened into the section directory (no
        subfolder with a single file is visually noisy).

        @param files_for_module: List of File objects for a single
            module.
        @return: True if at least one file has an attachment
            content type.
        """
        if not files_for_module:
            return False
        for f in files_for_module:
            content_type = getattr(f, 'content_type', None)
            if content_type in cls.ATTACHMENT_CONTENT_TYPES:
                return True
        return False

    @staticmethod
    def _is_system_file(filename: str) -> bool:
        """
        判断是否为系统文件（不应添加索引前缀）

        系统文件包括：
        - 隐藏文件（以 . 开头）
        - Moodle 生成的元数据文件（*_metadata.json、metadata.json 等）
        - Resource 模块生成的 .json 文件（对应 PDF/MP4 等主文件）
        - 特定模块生成的文件（questions.json、analysis.json 等）
        - 目录和导航文件（table of contents.html）
        - 笔记文件（_notes.md）

        @param filename: 文件名（已转为小写）
        @return: True 如果是系统文件
        """
        import os as _os

        filename_lower = filename.lower()

        # 🔧 Path-traversal fix: extract just the basename before
        # checking for hidden files. Without this, `'../../passwd'`
        # would be classified as a hidden file (because it starts
        # with `.` from the `..`), which means path-traversal
        # filenames get the system-file exemption. This is a
        # **security concern**: while Moodle's API doesn't return
        # such paths today, a future API change or a man-in-the-
        # middle could. Using basename() means we only look at the
        # actual filename, not its parent directory components.
        basename = _os.path.basename(filename_lower)

        # 隐藏文件（以 . 开头）
        if basename.startswith('.'):
            return True

        # 元数据文件 - 通用模式
        if filename_lower.endswith('_metadata.json'):
            return True
        
        # 基础元数据文件
        if filename_lower == 'metadata.json':
            return True

        # 目录和导航文件
        if filename_lower in ('table of contents', 'table of contents.html'):
            return True
        
        # 信息文件（来自各种模块）
        if filename_lower.endswith('_info'):
            return True
        
        # 笔记和文本文件（来自各种模块）
        if filename_lower.endswith('_notes.md'):
            return True
        
        # ✨ Resource 模块生成的 .json 文件
        # 这些是对应 PDF、MP4 等主文件的元数据
        # 例如: "[Mandatory] Week 1 - Recorded Lecture 1 Handouts.json"
        # 对应: "[Mandatory] Week 1 - Recorded Lecture 1 Handouts.pdf"
        if filename_lower.endswith('.json'):
            # 排除通用的 JSON 数据文件，只识别特定模式
            # 如果 .json 前面是通常用于导出的前缀，则是系统文件
            return True  # Resource 模块的所有 .json 都是生成的元数据
        
        # 特定模块生成的文件
        if filename_lower in [
            'questions.json',      # Feedback, Quiz 模块
            'analysis.json',       # Feedback 模块分析
            'grade',               # Lesson 模块成绩
            'entry_metadata.json', # Glossary, Data 模块条目元数据
        ]:
            return True
        
        # Session 文件（Chat 模块）
        if filename_lower.startswith('session_') and filename_lower.endswith('.json'):
            return True

        return False

    def _get_files_in_modules(self, section_modules: List, fetched_mods: Dict[str, Dict], **location) -> List[File]:
        """
        Iterates over all modules to find files (or content) in them.

        For each module, the resulting files are tagged with
        ``_module_has_attachments`` (True if the module has any
        real attachment beyond its description HTML preview).
        This flag drives the on-disk folder structure: a module
        without attachments is FLATTENED into the section
        directory (no subfolder with a single file), while a
        module with attachments keeps its own subfolder.

        @param section_modules: The modules of the section.
        @param fetched_mods: Contains the fetched mods of the course
        @param location: contains
            section_id: int,
            section_name: str,
        @return: A list of files of the section.
        """
        import logging
        files = []
        for module in section_modules:
            module_files_before = len(files)
            location['module_id'] = module.get('id', 0)
            location['module_name'] = module.get('name', '')
            location['module_modname'] = module.get('modname', '')

            module_url = module.get('url', '')
            module_contents = module.get('contents', [])
            module_description = module.get('description', None)

            # 🔍 DEBUG: Log all modules to see which branch books go through
            if 'week 1' in location.get('module_name', '').lower() and 'software' in location.get('module_name', '').lower():
                logging.info(f'🔵 [TRACE] Processing module: name="{location["module_name"]}", modname={location["module_modname"]}, id={location["module_id"]}')
                logging.info(f'🔵 [TRACE]   module_description={module_description is not None}, module_contents={len(module_contents)}, module_url={module_url[:50] if module_url else "None"}')

            # handle not supported modules that results in an index.html special
            if location['module_modname'] in ['moodecvideo']:
                location['module_modname'] = 'index_mod-' + location['module_modname']

            if location['module_modname'] in ['page'] and self.version < 2017051500:
                # legacy pages
                location['module_modname'] = 'index_mod-' + location['module_modname']

            if module_description is not None and location['module_modname'] not in fetched_mods:
                # Handle descriptions of Files, Labels and all mods that we do not handle in separately
                files += self._handle_description(module_description, **location)

            if location['module_modname'] in ['kalvidres', 'helixmedia']:
                # Special handling for specific LTI tools with dedicated extractors
                # Generic 'lti' modules are now handled by the LtiMod module
                location['module_modname'] = 'cookie_mod-' + location['module_modname']
                files += self._handle_cookie_mod(module_url, **location)

            elif location['module_modname'].startswith(('akarifolder', 'index_mod')):
                # Resource and URL modules now have dedicated module handlers
                files += self._handle_files(module_contents, **location)

            elif location['module_modname'] in fetched_mods:
                # find mod module with same module_id
                if location['module_modname'] == 'book':
                    logging.info(f'🟢 [DEBUG] BOOK BRANCH HIT! module_id={location["module_id"]}, module_name={location["module_name"]}')
                    logging.info(f'🟢 [DEBUG] fetched_mods.keys()={list(fetched_mods.keys())}')
                    logging.info(f'🟢 [DEBUG] fetched_mods["book"].keys()={list(fetched_mods.get("book", {}).keys())}')

                mod = fetched_mods.get(location['module_modname'], {}).get(location['module_id'], {})
                mod['on_main_page'] = True
                mod_files = mod.get('files', [])

                # 🔍 DEBUG: Log book module file usage
                if location['module_modname'] == 'book':
                    logging.info(f'🔍 [ResultBuilder] Using files from book module for "{location["module_name"]}" (module_id={location["module_id"]})')
                    logging.info(f'🔍 [ResultBuilder]   Found {len(mod_files)} files in fetched_mods')
                    if location['module_id'] not in fetched_mods.get('book', {}):
                        logging.warning(f'⚠️  Module ID {location["module_id"]} NOT in fetched_mods["book"]!')
                        logging.warning(f'⚠️  Available module IDs: {list(fetched_mods.get("book", {}).keys())}')

                files += self._handle_files(mod_files, **location)
            else:
                # 🔍 DEBUG: Log if book module is not in fetched_mods
                if location['module_modname'] == 'book':
                    logging.warning(f'⚠️  [ResultBuilder] Book module "{location["module_name"]}" NOT in fetched_mods!')
                    logging.warning(f'⚠️  [ResultBuilder]   module_id={location["module_id"]}, modname={location["module_modname"]}')
                    logging.warning(f'⚠️  [ResultBuilder]   Available mods: {list(fetched_mods.keys())}')
                    logging.warning(f'⚠️  [ResultBuilder]   module_contents count: {len(module_contents)}')

                if location['module_modname'] not in ['label']:
                    logging.debug(
                        'Got unhandled module: name=%s mod=%s url=%s',
                        location['module_name'],
                        location['module_modname'],
                        module_url,
                    )

            # Tag this module's files with _module_has_attachments
            # so gen_path can decide folder vs flat. Book modules
            # are special: book.py already sets content_filepath per
            # chapter, so gen_path's flat-vs-folder logic does not
            # apply (book keeps its chapter folder structure).
            module_files_after = len(files)
            module_files = files[module_files_before:module_files_after]
            if module_files and location['module_modname'] != 'book':
                has_attach = self._module_has_attachments(module_files)
                for f in module_files:
                    f._module_has_attachments = has_attach

        total_files_count = len(files)
        kalvidres_in_section = 0
        for f in files:
            if hasattr(f, 'module_modname') and f.module_modname == MODULE_COOKIE_KALVIDRES:
                kalvidres_in_section += 1
                logging.debug(f'Found kalvidres file: {f.content_filename}')

        if total_files_count > 0:
            logging.debug(f'_get_files_in_modules() returning {total_files_count} files total, {kalvidres_in_section} kalvidres')
        if kalvidres_in_section > 0:
            logging.info(f'🔄 _get_files_in_modules() returning {kalvidres_in_section} Kaltura videos in section')

        return files

    def get_mod_plural_name(self, mod_name: str) -> str:
        if mod_name in self.mod_plurals:
            return self.mod_plurals[mod_name].capitalize()
        return mod_name.capitalize()

    def _get_files_not_on_main_page(self, fetched_mods: Dict[str, Dict]) -> List[File]:
        """
        Iterates over all mods to find files (or content) that are not listed on the main page.
        @param fetched_mods: Contains the fetched_mods of the course
        @return: A list of files of mod modules not on the main page.
        """
        import logging

        # 🔍 DEBUG: Log books processing
        if 'book' in fetched_mods:
            logging.info(f'🟡 [NOT_ON_MAIN_PAGE] Found {len(fetched_mods["book"])} book(s) in fetched_mods')
            for module_id, module in list(fetched_mods["book"].items())[:3]:
                logging.info(f'🟡 [NOT_ON_MAIN_PAGE]   Book {module_id}: name={module.get("name", "?")}, on_main_page={("on_main_page" in module)}, files_count={len(module.get("files", []))}')

        files = []
        for mod_name, mod_modules in fetched_mods.items():
            location = {
                'section_id': -1,
                'section_name': f"{self.get_mod_plural_name(mod_name)} not on main page",
            }

            for _, module in mod_modules.items():
                if 'on_main_page' in module:
                    continue
                module_files_before = len(files)
                location.update(
                    {
                        'module_id': module.get('id', 0),
                        'module_name': module.get('name', ''),
                        'module_modname': mod_name,
                    }
                )

                # 🔍 DEBUG: Log when book is processed here
                if mod_name == 'book':
                    logging.info(f'🟡 [NOT_ON_MAIN_PAGE] Processing book: "{location["module_name"]}" (id={location["module_id"]}), files_count={len(module.get("files", []))}')

                # Handle not supported modules that results in an index.html special
                if location['module_modname'] in ['page'] and self.version < 2017051500:
                    location['module_modname'] = 'index_mod-' + location['module_modname']

                files += self._handle_files(module.get('files', []), **location)

                # Tag this module's files with _module_has_attachments
                module_files_after = len(files)
                module_files = files[module_files_before:module_files_after]
                if module_files and mod_name != 'book':
                    has_attach = self._module_has_attachments(module_files)
                    for f in module_files:
                        f._module_has_attachments = has_attach

        return files

    @staticmethod
    def filter_changing_attributes(description: str) -> str:
        """
        Tries to filter ids and stuff,
        that is knowing to change over time in descriptions.
        """
        if description is None:
            return ''

        if not isinstance(description, str):
            return description

        # to avoid changing encodings (see issue #96) we unencode and unquote everything
        description = html.unescape(description)
        description = urlparse.unquote(description)

        # ids can change very quickly
        description = re.sub(r'id="[^"]*"', "", description)
        description = re.sub(r"id='[^']*'", "", description)

        # Embedded images from Moodle can change their timestemp (is such a theme feature)
        # We change every timestemp to -1 the default.
        description = re.sub(
            r"\/theme\/image.php\/(\w+)\/(\w+)\/\d+\/",
            r"/theme/image.php/\g<1>/\g<2>/-1/",
            description,
        )

        # some folder downloads inside a description file may have some session key inside which will always be
        # different. We remove it, to prevent always tagging this file as "modified".
        description = re.sub(r'<input type="hidden" name="sesskey" value="[0-9a-zA-Z]*" \/>', "", description)
        description = re.sub(r"<input type='hidden' name='sesskey' value='[0-9a-zA-Z]*' \/>", "", description)

        return description

    @staticmethod
    def _match_kaltura_entry_id(url: str):
        return re.search(r'entryid/([^/&?#]+)', url, re.IGNORECASE) or re.search(
            r'entryid%2F([^/%&?#]+)', url, re.IGNORECASE
        )

    @staticmethod
    def _get_kaltura_lti_source_url(url: str) -> Optional[str]:
        parsed = urlparse.urlparse(url or '')
        if '/filter/kaltura/lti_launch.php' not in (parsed.path or ''):
            return None

        source_values = urlparse.parse_qs(parsed.query).get('source')
        if not source_values:
            return None

        return html.unescape(urlparse.unquote(source_values[0]))

    @classmethod
    def _kaltura_source_has_player_context(cls, source_url: str) -> bool:
        return bool(
            re.search(r'/(?:playerSkin|uiConfId|uiconf_id)/\d+', source_url, re.IGNORECASE)
            or re.search(r'/isPlaylist/true(?:[/?#]|$)', source_url, re.IGNORECASE)
        )

    @staticmethod
    def _is_kaltura_host(hostname: str) -> bool:
        hostname = (hostname or '').lower()
        return hostname.startswith('kaf.') or 'kaltura' in hostname or hostname in {'media.kcl.ac.uk'}

    @classmethod
    def _is_kaltura_url_candidate(cls, url: str, url_parts) -> bool:
        path = url_parts.path or ''
        url_lower = url.lower()
        has_entry_id = cls._match_kaltura_entry_id(url) is not None
        return (
            '/filter/kaltura/lti_launch.php' in path
            or ('browseandembed' in path and 'entryid' in url_lower)
            or (has_entry_id and (cls._is_kaltura_host(url_parts.hostname) or 'kaltura' in path.lower()))
        )

    @staticmethod
    def _is_helixmedia_url_candidate(url: str, url_parts) -> bool:
        return 'helixmedia' in (url or '').lower() and '/mod/helixmedia/view.php' in (url_parts.path or '')

    def _normalize_extracted_html_url(self, url: str) -> tuple:
        """Normalize an HTML URL candidate before turning it into a File entry."""
        # To avoid different encodings and quotes and so that yt-dlp downloads correctly
        # (See issues #96 and #103), we remove all encodings.
        url = urlparse.unquote(html.unescape(url))
        url_parts = urlparse.urlparse(url)

        if not url_parts.scheme:
            if url_parts.netloc:
                url = f'https:{url}'
            elif url.startswith('/'):
                url = urlparse.urljoin(self.moodle_base_url.rstrip('/') + '/', url)
            else:
                # Relative HTML resources are handled by the HTML localizer
                # after their real Moodle file entry has been downloaded.
                # Creating shortcuts for them produces duplicate .webloc files.
                return '', None
            url_parts = urlparse.urlparse(url)

        return url, url_parts

    def _standard_kaltura_entry_url(self, entry_id: str) -> str:
        return f'https://{self.moodle_domain}/browseandembed/index/media/entryid/{entry_id}'

    def _normalize_kaltura_url(self, url: str, url_parts) -> tuple:
        """Return (normalized_url, entry_id) for Kaltura candidates."""
        if url_parts.hostname == self.moodle_domain and '/filter/kaltura/lti_launch.php' in url_parts.path:
            source_url = self._get_kaltura_lti_source_url(url)
            entry_id = self._extract_kaltura_entry_id(source_url or url)
            if not entry_id:
                return url, None
            if not source_url or not self._kaltura_source_has_player_context(source_url):
                # Older Moodle pages sometimes expose only the entry id.
                url = self._standard_kaltura_entry_url(entry_id)
            logging.info(f'🎬 Converted Kaltura LTI URL to kalvidres format: entry_id={entry_id}')
            return url, entry_id

        if 'browseandembed' in url_parts.path and 'entryid' in url.lower():
            entry_id = self._extract_kaltura_entry_id(url)
            if not entry_id:
                return url, None
            if url_parts.hostname == self.moodle_domain:
                url = self._standard_kaltura_entry_url(entry_id)
            logging.info(f'🎬 Converted Kaltura browseandembed URL to kalvidres format: entry_id={entry_id}')
            return url, entry_id

        if self._is_kaltura_url_candidate(url, url_parts):
            entry_id = self._extract_kaltura_entry_id(url)
            if not entry_id:
                return url, None
            if url_parts.hostname == self.moodle_domain or 'kaf.' in (url_parts.hostname or ''):
                url = self._standard_kaltura_entry_url(entry_id)
            logging.info(f'🎬 Converted Kaltura URL to kalvidres format: entry_id={entry_id}, original_url={url[:80]}...')
            return url, entry_id

        return url, None

    @classmethod
    def _extract_kaltura_entry_id(cls, url: str) -> Optional[str]:
        entry_id_match = cls._match_kaltura_entry_id(url)
        if not entry_id_match:
            return None
        return urlparse.unquote(entry_id_match.group(1))

    def _find_all_urls(
        self,
        content_html: str,
        no_search_for_moodle_urls: bool,
        filter_urls_containing: List[str],
        **location,
    ) -> List[File]:
        """Parses a html string to find all urls in it. Then it creates for every url a file entry.

        This method is now a thin orchestrator over three pure helpers
        in :mod:`moodle_dl.moodle.description_url_extractor`:

          * :func:`extract_urls_from_html` — regex HTML → List[url]
          * :func:`should_skip_url` — multi-condition "should this URL
            be dropped?" decision (the 11-line boolean chain the
            original code had inline)
          * :func:`assign_modname_for_url` — 3-branch modname dispatch
            (kaltura / helixmedia / /webservice/ branch / external)

        The pure-function refactor (June 2026) makes each of these
        decisions unit-testable in isolation. The function-level
        contract is pinned by tests/test_result_builder_more.py and
        tests/test_result_builder_pluginfile_in_descriptions.py.

        @param location: contains
            section_id: int,
            section_name: str,
            module_id: str,
            module_name: str,
            module_modname: str,
            content_filepath: str,
        """
        # Lazy import keeps description_url_extractor's unit tests free
        # of the heavy ResultBuilder / downloader dependency chain.
        from moodle_dl.moodle.description_url_extractor import (
            assign_modname_for_url,
            extract_urls_from_html,
            should_skip_url,
        )

        # TODO: Also parse name or alt of an link to get a better name for URLs
        raw_urls = extract_urls_from_html(content_html)
        logging.debug(f'   🔎 _find_all_urls() found {len(raw_urls)} raw URLs in HTML (length={len(content_html)})')
        if 'kaltura' in content_html.lower():
            kaltura_urls = [u for u in raw_urls if 'kaltura' in u.lower()]
            logging.debug(f'   🎬 Found {len(kaltura_urls)} Kaltura URLs: {kaltura_urls[:2] if kaltura_urls else "None"}')

        result = []
        original_module_modname = location['module_modname']

        for url in raw_urls:
            url, url_parts = self._normalize_extracted_html_url(url)
            if not url:
                continue

            is_kaltura = self._is_kaltura_url_candidate(url, url_parts)
            is_helixmedia = self._is_helixmedia_url_candidate(url, url_parts)

            if should_skip_url(
                url=url,
                url_parts=url_parts,
                moodle_domain=self.moodle_domain,
                original_modname=original_module_modname,
                is_embedded_media=is_kaltura or is_helixmedia,
            ):
                continue

            if any(filter_str and filter_str in url for filter_str in filter_urls_containing):
                continue

            if url_parts.hostname == self.moodle_domain and url_parts.path.find('/theme/image.php/') >= 0:
                url = re.sub(
                    r"\/theme\/image.php\/(\w+)\/(\w+)\/\d+\/",
                    r"/theme/image.php/\g<1>/\g<2>/-1/",
                    url,
                )

            location['module_modname'] = assign_modname_for_url(
                url=url,
                url_parts=url_parts,
                moodle_domain=self.moodle_domain,
                original_modname=original_module_modname,
                is_kaltura=is_kaltura,
                is_helixmedia=is_helixmedia,
            )

            # If a Kaltura launch URL was detected, normalize the URL to
            # the standard kalvidres form (so the downloader hits the
            # right endpoint). The is_kaltura flag stays True because
            # the host / path / entry_id signals don't change.
            if is_kaltura:
                url, _entry_id = self._normalize_kaltura_url(url, url_parts)
            elif is_helixmedia:
                logging.info(f'🎬 Detected HelixMedia URL in description: {url[:80]}...')

            # Determine filename based on URL type
            if url.startswith('data:'):
                # Schema: data:[<mime type>][;charset=<Charset>][;base64],<Data>
                if ',' not in url:
                    logging.warning('Skipping malformed data URL in %s', location.get('module_name', '?'))
                    continue
                embedded_data = url.split(',', 1)[1]
                mime_type = url.split(':', 1)[1].split(',', 1)[0].split(';')[0]
                media_type = mime_type.split('/', 1)[0]
                file_extension_guess = mimetypes.guess_extension(mime_type, strict=False)
                if file_extension_guess is None:
                    file_extension_guess = f'.{media_type}'
                # 🔒 安全改进：使用 SHA256 替代 SHA1（用于文件去重，非安全验证）
                m = hashlib.sha256()
                if len(embedded_data) > 100000:
                    # To improve speed hash only first 100kb if file is bigger
                    m.update(embedded_data[:100000].encode(encoding='utf-8'))
                else:
                    m.update(embedded_data.encode(encoding='utf-8'))
                short_data_hash = m.hexdigest()

                fist_guess_filename = f'embedded_{media_type} ({short_data_hash}){file_extension_guess}'
            elif is_kaltura:
                # Use entry_id for Kaltura videos (extracted upstream by
                # _normalize_kaltura_url when is_kaltura is True).
                entry_id = self._extract_kaltura_entry_id(url) or 'unknown'
                fist_guess_filename = f'Kaltura Video {entry_id}'
            else:
                fist_guess_filename = url
                if len(fist_guess_filename) > 254:
                    fist_guess_filename = fist_guess_filename[:254]

            result.append(
                File(
                    **location,
                    content_filename=fist_guess_filename,
                    content_fileurl=url,
                    content_filesize=0,
                    content_timemodified=0,
                    content_type='description-url',
                    content_isexternalfile=True,
                )
            )
        return result

    def _handle_cookie_mod(self, module_url: str, **location) -> List[File]:
        """
        Create a list of files out of a cookie module
        @param module_url: The url to the cookie module.
        @param location: contains
            section_id: int,
            section_name: str,
            module_id: str,
            module_name: str,
            module_modname: str,
            content_timemodified: int (optional, defaults to 0)
        """
        import logging
        # Extract timemodified from location if provided, otherwise use 0
        content_timemodified = location.pop('content_timemodified', 0)

        file_obj = File(
            **location,
            content_filepath='/',
            content_filename=location['module_name'],
            content_fileurl=module_url,
            content_filesize=0,
            content_timemodified=content_timemodified,
            content_type='cookie_mod',
            content_isexternalfile=True,
        )
        logging.debug(f'Created cookie_mod file: modname={file_obj.module_modname}, filename={file_obj.content_filename}, time={content_timemodified}, url={module_url[:80]}...')
        return [file_obj]

    def _handle_files(self, module_contents: List, **location) -> List[File]:
        """
        Create a list of all files in a module
        @param module_contents: The list of content of the module
        @param location: contains
            section_id: int,
            section_name: str,
            module_id: str,
            module_name: str,
            module_modname: str,
        """
        import logging

        # Debug: Log what we're processing
        if location.get('module_modname') == 'book':
            logging.debug(f'📚 _handle_files() for book: module_name={location.get("module_name")}, contents_count={len(module_contents)}')
            for i, content in enumerate(module_contents):
                logging.debug(f'   Content[{i}]: type={content.get("type")}, filename={content.get("filename", "?")},'
                             f' has_html={("html" in content)}, html_len={len(content.get("html", ""))}')

        files = []
        kalvidres_count = 0
        for content in module_contents:
            content_type = content.get('type', '')
            content_filename = content.get('filename', '')
            content_filepath = content.get('filepath', '/') or '/'
            content_fileurl = content.get('fileurl', '')
            if content_fileurl is None:
                content_fileurl = ''

            # Moodle book "structure" is a content entry with fileurl=null in core.
            # Some instances return type="file" but still include content; normalize to "content".
            if (
                content_type != 'content'
                and location.get('module_modname') == 'book'
                and content_filename == 'structure'
                and content.get('content') is not None
                and content_fileurl == ''
            ):
                content_type = 'content'

            # 🔧 修复 pluginfile URL（关键修复）
            # 确保 URL 包含正确的认证参数和端点路径
            if content_fileurl and 'pluginfile.php' in content_fileurl:
                try:
                    content_fileurl = UrlHelper.fix_pluginfile_url(
                        content_fileurl,
                        token=self.token,
                        moodle_base_url=self.moodle_base_url,
                    )
                    logging.debug(f'   🔧 Fixed pluginfile URL: {content_fileurl[:80]}...')
                except Exception as e:
                    logging.warning(f'   ⚠️ Failed to fix pluginfile URL: {e}')

            if content_type == 'directory_placeholder':
                placeholder_filename = content_filename or '__empty_chapter__'
                placeholder_file = File(
                    **location,
                    content_filepath=content_filepath,
                    content_filename=placeholder_filename,
                    content_fileurl='',
                    content_filesize=0,
                    content_timemodified=content.get('timemodified', 0),
                    content_type='directory_placeholder',
                    content_isexternalfile=False,
                )
                files.append(placeholder_file)

                nested_contents = content.get('contents', [])
                if nested_contents:
                    logging.debug(
                        f'   📁 Directory placeholder "{placeholder_filename}" contains {len(nested_contents)} nested items'
                    )
                    files += self._handle_files(nested_contents, **location)
                continue

            # 对于资源模块 (resource)，优先使用网页显示的标题 (module_name) 作为文件名
            # 这样下载的文件名与 Moodle 网页上看到的标题一致
            # 例如: "[Mandatory] Week 1 - Recorded Lecture 1 Handouts.pdf" 而不是 "Software_Testing_Week_1_...pdf" 或 "1.pdf"
            if location['module_modname'] == 'resource' and content_filename:
                # 从原始 API filename 中提取文件扩展名
                original_filename = content.get('filename', '')
                file_extension = ''
                if original_filename and '.' in original_filename:
                    # 提取扩展名（例如 ".pdf"）
                    file_extension = '.' + original_filename.rsplit('.', 1)[-1]
                else:
                    # 如果无法从 filename 获取，尝试从 mimetype 推断
                    mimetype = content.get('mimetype', '')
                    file_extension = self._get_extension_from_mimetype(mimetype) if mimetype else ''

                # 🐛 FIX (2026-06-23, CS6): if module_name already ends
                # with the file extension, don't append it again. Real
                # case: Moodle's `module_name` for a resource can be
                # set to a name that already includes the file extension
                # (e.g. "pacman-cw1.zip"). The display name then becomes
                # "pacman-cw1.zip" and the API filename is "cw1_pacman.zip".
                # Without this check, content_filename would become
                # "pacman-cw1.zip.zip" (double extension).
                if file_extension and location['module_name'].lower().endswith(file_extension.lower()):
                    content_filename = location['module_name']
                else:
                    # 使用网页显示的标题，并保留文件扩展名
                    content_filename = location['module_name'] + file_extension
                logging.debug(f'🔧 Resource module: using display name "{content_filename}" instead of API filename "{original_filename}"')

            content_description = content.get('description', '')
            content_html = content.get('html', '')

            # Handle embedded Kaltura videos from book chapters
            # Keep module_modname as 'book' so videos are saved inside the book folder
            if content_type == CONTENT_TYPE_KALVIDRES_EMBEDDED:
                logging.info(f'🎥 Processing embedded Kaltura video: {content_filename}')
                # Create File entry, override module_modname to trigger yt-dlp in task.py
                # Path will be: section_name/module_name/content_filepath/content_filename
                # 需要创建一个修改后的 location 副本，覆盖 module_modname
                video_location = {**location, 'module_modname': MODULE_COOKIE_KALVIDRES}
                file_obj = File(
                    **video_location,
                    content_filepath=content_filepath,  # Use filepath from content (e.g., '/691947/')
                    content_filename=content_filename,  # Video name
                    content_fileurl=content_fileurl,  # Kalvidres URL
                    content_filesize=0,
                    content_timemodified=content.get('timemodified', 0),
                    content_type='cookie_mod',  # Mark as cookie_mod for download handling
                    content_isexternalfile=True,
                )
                files.append(file_obj)
                kalvidres_count += 1
                logging.info(f'   Created book-embedded kalvidres file: {content_filename} (path: {content_filepath})')
                continue  # Skip normal file processing for this

            if content_fileurl == '' and location['module_modname'].startswith(('url', 'index_mod', 'cookie_mod')):
                continue

            # Add the extention condition to avoid renaming pdf files or other downloaded content from moodle pages.
            if location['module_modname'].startswith('index_mod') and content_filename.endswith('.html'):
                content_filename = location['module_name']

            file_hash = None
            if content_type in ('description', 'html') and not content.get('no_hash', False):
                hash_source = content_html if content_type == 'html' else content_description
                hashable_description = self.filter_changing_attributes(hash_source)
                # 🔒 安全改进：使用 SHA256 替代 SHA1（用于内容去重）
                m = hashlib.sha256()
                m.update(hashable_description.encode('utf-8'))
                file_hash = m.hexdigest()

            # 🆕 提取扩展元数据字段（从 module 或 content 中）
            # 这些字段通常在 module 对象中，但某些情况下也可能在 content 中
            metadata = {
                'visible': location.get('visible', content.get('visible', 1)),
                'uservisible': location.get('uservisible', content.get('uservisible', 1)),
                'availabilityinfo': location.get('availabilityinfo', content.get('availabilityinfo')),
                'completion': location.get('completion', content.get('completion', 0)),
                'timecreated': location.get('timecreated', content.get('timecreated', 0)),
                'sortorder': location.get('sortorder', content.get('sortorder', 0)),
            }

            new_file = File(
                **location,
                content_filepath=content_filepath,
                content_filename=content_filename,
                content_fileurl=content_fileurl,
                content_filesize=content.get('filesize', 0),
                content_timemodified=content.get('timemodified', 0),
                content_type=content_type,
                content_isexternalfile=content.get('isexternalfile', False),
                file_hash=file_hash,
                # 🆕 扩展元数据
                **metadata,
            )

            if content_type == 'description':
                new_file.text_content = content_description
                content_html = content_description
            if content_type == 'html':
                new_file.html_content = content_html
            if content_type in ('content', 'leganto_pdf'):
                new_file.content = content.get('content', '')

            if content_type in ['description', 'html'] and not content.get('no_search_for_urls', False):
                logging.debug(
                    f'🔍 URL extraction for {content_type}: filename={content_filename}, '
                    f'html_length={len(content_html)}, module={location.get("module_modname", "?")}'
                )
                extracted_files = self._find_all_urls(
                    content_html,
                    no_search_for_moodle_urls=content.get('no_search_for_moodle_urls', False),
                    filter_urls_containing=content.get('filter_urls_during_search_containing', []),
                    **location,
                    content_filepath=content_filepath,
                )
                if extracted_files:
                    logging.debug(f'   ✅ Extracted {len(extracted_files)} URLs from {content_filename}')
                    for extracted_file in extracted_files[:3]:  # Log first 3 URLs
                        logging.debug(f'      - {extracted_file.content_fileurl[:80]}...')
                files += extracted_files

            files.append(new_file)

            # 🆕 处理嵌套的 contents 数组（支持层级化的文件结构）
            # 这使得 feature/print-book 分支中 chapter_content['contents'] 中的视频能被正确处理
            nested_contents = content.get('contents', [])
            if nested_contents:
                logging.debug(f'🔄 Processing nested contents in "{content_filename}": {len(nested_contents)} items')

                # 递归处理嵌套内容，保持相同的 location 上下文（module_id, section_id 等）
                nested_files = self._handle_files(nested_contents, **location)
                files += nested_files

                # 统计嵌套内容中的 Kaltura 视频
                # 注意：nested_file.module_modname 仍然是 'book'，不是 'kalvidres'
                # 所以我们检查 content_type 和 fileurl 来识别视频
                for nested_file in nested_files:
                    if nested_file.content_type == 'cookie_mod' and \
                       ('kalvidres' in nested_file.content_fileurl or 'helixmedia' in nested_file.content_fileurl):
                        kalvidres_count += 1

                logging.debug(f'   ✅ Added {len(nested_files)} nested files from "{content_filename}"')

        if kalvidres_count > 0:
            logging.info(f'📤 _handle_files() returning {kalvidres_count} Kaltura videos for module "{location.get("module_name", "?")}"')

        return files

    @staticmethod
    def _get_extension_from_mimetype(mimetype: str) -> str:
        """
        从 MIME type 推断文件扩展名

        @param mimetype: MIME type (例如 "application/pdf")
        @return: 文件扩展名 (例如 ".pdf")，如果无法推断则返回空字符串
        """
        if not mimetype:
            return ''

        # 使用 Python 的 mimetypes 模块推断扩展名
        extension = mimetypes.guess_extension(mimetype, strict=False)
        return extension if extension else ''

    def _handle_description(
        self,
        module_description: str,
        **location,
    ) -> List[File]:
        """
        Creates a description file
        @param module_description: The description of the module
        @param location: contains
            section_id: int,
            section_name: str,
            module_id: str,
            module_name: str,
            module_modname: str,
        @return: A list of files containing that description and URLs in that description.
        """
        files = []
        content_filepath = '/'

        # 🔒 安全改进：使用 SHA256 替代 SHA1（用于描述去重）
        m = hashlib.sha256()
        hashable_description = self.filter_changing_attributes(module_description)
        m.update(hashable_description.encode('utf-8'))
        hash_description = m.hexdigest()

        if location['module_modname'].startswith(('url', 'index_mod')):
            location['module_modname'] = 'url_description'

        description = File(
            **location,
            content_filepath=content_filepath,
            content_filename=location['module_name'],
            content_fileurl='',
            content_filesize=len(module_description),
            content_timemodified=0,
            content_type='description',
            content_isexternalfile=False,
            file_hash=hash_description,
        )
        description.text_content = module_description
        files.append(description)

        files += self._find_all_urls(
            module_description,
            no_search_for_moodle_urls=False,
            filter_urls_containing=[],
            **location,
            content_filepath=content_filepath,
        )

        return files

    def add_files_to_courses(
        self,
        courses: List[Course],
        core_contents: Dict[int, List[Dict]],
        fetched_mods_files: Dict[str, Dict],
    ):
        """
        @param fetched_mods_files:
            Dictionary of all fetched mod modules files, indexed by mod name, then by courses, then module id
        """
        for course in courses:
            course_sections = core_contents.get(course.id, [])

            fetched_mods = {}
            for mod_name, mod_courses in fetched_mods_files.items():
                fetched_mods[mod_name] = mod_courses.get(course.id, {})

            course.files = self.get_files_in_sections(course_sections, fetched_mods)

    def get_files_from_blocks(self, course_blocks: List[Dict], course_id: int) -> List[File]:
        """
        Processes course blocks and creates File objects for HTML blocks that contain content.
        These blocks typically appear in the course sidebar and can contain important information
        like Key Contacts, announcements, etc.

        @param course_blocks: List of block dictionaries from core_block_get_course_blocks API
        @param course_id: The course ID
        @return: A list of File objects representing the blocks
        """
        files = []

        for block in course_blocks:
            block_name = block.get('name', '')
            block_instance_id = block.get('instanceid', 0)
            block_visible = block.get('visible', True)

            # Only process visible blocks that have contents
            if not block_visible or 'contents' not in block:
                continue

            contents = block['contents']
            title = contents.get('title', '')
            content = contents.get('content', '')

            # Skip blocks without meaningful content
            if not title or not content:
                continue

            # Filter to only include useful blocks (HTML blocks and similar)
            # Skip pure navigation blocks like calendar, search, etc.
            if block_name in ['calendar_month', 'calendar_upcoming', 'search_forums', 'activity_modules']:
                continue

            # Create a safe filename from the block title
            safe_title = PT.to_valid_name(title, is_file=False)
            filename = safe_title

            # Create location info for the block
            location = {
                'module_id': block_instance_id,
                'section_name': '_course_info',  # Special section for course-level info
                'section_id': 0,
                'module_name': title,
                'module_modname': f'block_{block_name}',
            }

            # Calculate hash for change detection
            hash_content = hashlib.md5((content).encode('utf-8')).hexdigest()

            # Create File object for HTML version
            # We save blocks as HTML files so they can be easily converted to Markdown by the downloader
            block_file = File(
                **location,
                content_filepath='/',
                content_filename=filename,
                content_fileurl='',
                content_filesize=len(content),
                content_timemodified=0,
                content_type='html',  # Use 'html' type so downloader saves as .html
                content_isexternalfile=False,
                file_hash=hash_content,
            )
            block_file.html_content = content
            files.append(block_file)

        return files

    def add_blocks_to_course(self, course: Course, course_blocks: List[Dict]):
        """
        Adds block files to a course's file list.

        @param course: The course object to add blocks to
        @param course_blocks: List of block dictionaries for this course
        """
        block_files = self.get_files_from_blocks(course_blocks, course.id)
        course.files.extend(block_files)
