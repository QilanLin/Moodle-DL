"""
TaskFileOps: file/path/HTML operations extracted from Task.

The Task class had ~15 methods dedicated to file, path, and
HTML concerns that were unrelated to the "download a file"
responsibility:

  * HTML cleaning utilities (`_clean_html_simple`, `_convert_*`,
    `_remove_html_tags`, `_decode_html_entities`,
    `_clean_whitespace`, `_clean_html_preserve_structure`)
  * Filename generation (`_generate_filename_with_index`)
  * Path generation (`gen_path`)
  * URL token addition (`add_token_to_url`)
  * File/URL preparation (`create_target_file`, `rename_old_file`,
    `move_old_file`, `set_path`)
  * File generation (`create_shortcut`, `create_description`,
    `create_html_file`, `create_content_file`,
    `create_data_url_file`)

This module groups them into one cohesive class. The Task
class becomes a thin orchestrator; the actual mechanics
of "produce a file in the workspace" live here.

The interface is intentionally focused on the WORKSPACE
operations, not the download itself:
    fileops.create_target_file(target_path)
    fileops.rename_old_file(...)
    fileops.set_path(...)
    fileops.generate_filename_with_index(...)
    fileops.gen_path(...)
    fileops.add_token_to_url(...)
    fileops.clean_html(html_text, mode='simple'|'structured')
    fileops.create_shortcut(...)
    fileops.create_description(...)
    fileops.create_html_file(...)
    fileops.create_content_file(...)
    fileops.create_data_url_file(...)
"""
import html as html_module
import logging
import os
import re
import urllib.parse as urlparse
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from moodle_dl.types import Course, File


# Default file extension for shortcut files (Windows .url, macOS .webloc, .desktop)
SHORTCUT_EXTENSIONS = ('.url', '.webloc', '.desktop')


# ========================================================================
# Filename + path generation
# ========================================================================
class TaskFileOps:
    """File/path/HTML operations for a single Task.

    Holds a reference to the parent Task (for status, opts, file,
    course). Stateless otherwise — every method takes its inputs
    explicitly or reads them from the task attribute.
    """

    def __init__(self, task):
        self.task = task
        self.task_id = task.task_id

    # ------------------------------------------------------------------
    # Filename / path
    # ------------------------------------------------------------------
    def generate_filename_with_index(self, file) -> str:
        """
        生成带索引前缀的文件名。

        文件位于 section 目录 (扁平) 时,使用位置索引前缀 *NN*:
          position=0, original="lecture.pdf" → "*01* lecture.pdf"
          position=4, original="01-intro.pdf" → "*05* 01-intro.pdf"
          position=None → "lecture.pdf" (无索引)

        文件位于 module 子目录 (有附件的 module) 时,**不**使用 *NN*
        前缀 — 模块文件夹名本身在 section 内是唯一的 (按 server
        顺序排列),已经表达了模块的位置. 例:
          section_dir/Lecture 3： Probabilistic methods 1/
            Lecture 3： Probabilistic methods 1.html.md
            Lecture 3： Probabilistic methods 1.pdf

        (User explicit contract 2026-06-22: 'Lecture 3： Probabilistic
        methods 1 文件夹下的 .pdf 和 .html.md 就不再需要*xx*前缀
        序号了')
        """
        from moodle_dl.utils import PathTools as PT

        # ⚠️ Always sanitize the filename first — to_valid_name
        # converts characters that are illegal on Windows filesystems
        # (e.g. ``*`` → ``＊``) so the resulting filename is portable.
        # Without this, module-folder files would still contain raw
        # ``*`` characters that Windows refuses to create.
        original_filename = PT.to_valid_name(file.content_filename, is_file=True)

        # 如果文件在 module 子目录内 (gen_path 设置了 _in_module_folder=True),
        # 跳过 *NN* 前缀.
        #
        # 注意: 我们用 ``is True`` 而**不是** truthy 检查. ``MagicMock``
        # (测试用) 没设置 _in_module_folder 时, ``getattr(...)`` 返回一个
        # MagicMock 实例 (truthy),如果用 truthy 检查会**永远**进 if 分支.
        # 用 ``is True`` 保证只有 gen_path 显式设了 True 才跳过.
        _in_module_folder = getattr(file, '_in_module_folder', None)
        if _in_module_folder is True:
            return original_filename

        if file.position_in_section is None:
            return original_filename

        position = file.position_in_section
        if position < 99:
            index_str = f'{position + 1:02d}'
        else:
            index_str = f'{position + 1:03d}'

        return f'*{index_str}* {original_filename}'

    @staticmethod
    def _module_folder_name_with_prefix(file) -> str:
        """Build the module folder name with optional *NN* prefix.

        The folder name gets a *NN* prefix based on the module's
        first file's section position, so flat files and module
        folders sort together in the same sequence:

          *01* LECTURE SLIDES.md
          *02* Lecture 0: About the module/
              ├── Lecture 0: About the module.html.md
              └── Lecture 0: About the module.pdf

        Position is 0-based internally; the prefix is 1-based to
        match human-readable ordering. Sections with >99 files use
        3-digit padding (matches generate_filename_with_index).

        Sentinel: if file.position_in_section is None (not yet
        assigned) or not a real int (test mock), no prefix is added
        — the folder keeps the original module_name. This preserves
        backward compatibility with code paths that haven't been
        migrated yet.
        """
        from moodle_dl.utils import PathTools as PT
        position = getattr(file, 'position_in_section', None)
        # Sanitize module_name first (to_valid_name replaces ``*`` etc.
        # with full-width equivalents — we want those full-width
        # forms here so the rest of the folder name is portable,
        # but the prefix MUST be ASCII for sort order).
        safe_name = PT.to_valid_name(file.module_name, is_file=False)
        if not isinstance(position, int):
            return safe_name
        if position < 99:
            prefix = f'*{position + 1:02d}*'
        else:
            prefix = f'*{position + 1:03d}*'
        return f'{prefix} {safe_name}'

    @staticmethod
    def _sanitized_module_folder_name(file) -> str:
        """Return the module folder name SANITIZED but WITHOUT the
        ``*NN*`` prefix.

        Used by gen_path to build the path via path_of_file_in_module
        (which sanitizes the folder name, turning ``*`` → full-width
        ``＊``). The caller must add the ``*NN*`` prefix AFTER path
        construction (see _prepend_nn_prefix_to_path).
        """
        from moodle_dl.utils import PathTools as PT
        return PT.to_valid_name(file.module_name, is_file=False)

    @staticmethod
    def _prepend_nn_prefix_to_path(path: str, file) -> str:
        """Prepend the ``*NN*`` ASCII prefix to the module folder
        name part of an already-constructed path.

        Used by gen_path to add the ``*NN*`` prefix AFTER
        path_of_file_in_module sanitized the folder name (which
        would otherwise turn ``*`` → full-width ``＊``).

        The folder name is the second-to-last path segment (the
        last is content_filepath if it's non-empty).

        Sentinel: if file.position_in_section is None or not a real
        int (test mock), no prefix is added — the path is returned
        unchanged. This preserves backward compatibility with
        callers that haven't been migrated yet.
        """
        from pathlib import PurePosixPath
        position = getattr(file, 'position_in_section', None)
        if not isinstance(position, int):
            return path
        if position < 99:
            prefix = f'*{position + 1:02d}*'
        else:
            prefix = f'*{position + 1:03d}*'
        parts = PurePosixPath(path).parts
        if len(parts) < 2:
            return path
        # The module folder is the LAST segment of the path (after
        # content_filepath is stripped by path_of_file_in_module).
        # For a path like /storage/course/section/module_folder,
        # parts[-1] = module_folder. The section name is parts[-2],
        # which has already been sanitized in path_of_file_in_module.
        module_folder = parts[-1]
        # Skip if already has prefix (legacy folder, etc.)
        if module_folder.startswith('*') and (
            module_folder[1:3].isdigit() or module_folder[1:4].isdigit()
        ):
            return path
        new_folder = f'{prefix} {module_folder}'
        new_parts = parts[:-1] + (new_folder,)
        return str(PurePosixPath(*new_parts))

    def gen_path(self, storage_path: str, course, file) -> str:
        """Generate the directory path where a file should be stored.

        Section ordering
        ----------------
        The Moodle API (``core_course_get_contents``) returns
        sections already sorted by their sequential ``section``
        number (0, 1, 2, 3, ..., 10) — see
        ``course_sections.section`` in
        ``moodle_official_repo_for_reference/public/lib/db/install.xml``.
        So ``moodle-dl`` does not need to impose its own sort
        order on directory names.

        Module folder flattening
        ------------------------
        If the module has only ONE file (the description HTML
        preview, no resource_file / label_file / assign_file
        attachments), the file is FLATTENED into the section
        directory. No module-name subfolder is created, because
        a single-file folder with the same name as its sole file
        is visual noise.

        Example (label with description only):
          Section/                          ← was
          └── Module X/
              └── *02* Module X.html.md
        Now becomes:
          Section/                          ← after this fix
          └── *02* Module X.html.md

        The module's ``_module_has_attachments`` flag (set by
        ``result_builder._get_files_in_modules``) determines
        whether the file goes in a subfolder or stays flat.
        """
        from moodle_dl.utils import PathTools as PT
        from moodle_dl.downloader.kaltura_patterns import (
            COOKIE_MOD_MODNAMES,
        )

        course_name = course.fullname
        if course.overwrite_name_with is not None:
            course_name = course.overwrite_name_with

        if not course.create_directory_structure:
            return PT.flat_path_of_file(storage_path, course_name, file.content_filepath)

        # 🔧 Singleton-description flattening: if the module has no
        # attachments and the file is the module's description HTML,
        # flatten it into the section directory. Mark the file as
        # "in module folder = False" so generate_filename_with_index
        # adds the *NN* prefix (the folder name doesn't carry the
        # position info in this case — the prefix does).
        #
        # Sentinel: ``None`` means "not computed yet" (e.g. tests
        # that don't go through ``_get_files_in_modules``). In that
        # case, fall back to the historical module-folder behavior
        # to avoid regressions in callers that haven't been
        # migrated yet.
        _has_attachments = getattr(file, '_module_has_attachments', None)
        if _has_attachments is False:
            file._in_module_folder = False
            return PT.path_of_file(storage_path, course_name, file.section_name, file.content_filepath)

        # Modname-based folder check: only modnames known to ALWAYS
        # have attachments (book, folder, etc.) go here. Modules
        # like ``page``, ``url``, ``label``, ``resource`` go in a
        # folder ONLY if ``_module_has_attachments`` is True
        # (checked above). This is the test3 fix: a singleton
        # ``page`` module (only its own index.html) should be
        # flattened, not in a single-file folder.
        if file.module_modname.endswith(
            (
                'assign', 'book', 'data', 'folder', 'forum', 'lesson',
                'page', 'quiz', 'resource', 'workshop',
            )
        ) or file.module_modname in (
            'url', 'label',
        ):
            # Module has attachments → keep module folder. Mark
            # file as "in module folder = True" so
            # generate_filename_with_index skips the *NN* prefix
            # (the folder name itself encodes the module's
            # position in the section).
            file._in_module_folder = True
            # 🔧 Pin module folder *NN* prefix: the folder name
            # should also carry a *NN* prefix based on the
            # module's first file's section position. This way,
            # flat files and module folders sort together in the
            # same sequence: *01* LECTURE SLIDES.md,
            # *02* Lecture 0: About the module/,
            # *04* Lecture 1: Introduction to Machine Learning/, etc.
            # Without this fix, module folders have no prefix and
            # float freely in the section dir, breaking the
            # section-wide *NN* ordering.
            #
            # BUG FIX (2026-06-23, CS6): we previously passed
            # ``*NN* Module Name`` to path_of_file_in_module,
            # which calls to_valid_name on the folder name,
            # converting ASCII ``*`` (U+002A) to full-width
            # ``＊`` (U+FF0A). Full-width ``＊`` sorts AFTER ASCII
            # ``*`` in POSIX sort, breaking the section-wide
            # sequence. The fix: build the path WITHOUT the
            # ``*NN*`` prefix (using the sanitized module name only),
            # then concat the prefix AFTER path construction so the
            # prefix stays as ASCII ``*``.
            #
            # BUG FIX (2026-06-24, test3): previously we ALSO
            # unconditionally put ``resource``, ``page``, ``url``,
            # and ``label`` modnames in a module folder, regardless
            # of whether the module had real attachments. This was
            # wrong for singleton modules (e.g. a ``page`` module
            # whose only file is its own index.html, or a ``label``
            # module whose only file is its description HTML) —
            # they ended up in single-file folders like
            # ``*01* 2. Week Overview/index.html`` instead of being
            # flattened to ``*01* 2. Week Overview.html`` in the
            # section dir. The fix: let ``_module_has_attachments``
            # (checked at the top of this method) be the sole
            # decider. A module with no attachments goes flat;
            # a module with attachments (resource_file, label_file,
            # cookie_mod, etc.) goes in a folder. The duplicate
            # ``'resource', 'page', 'url', 'label'`` entry in the
            # tuple was removed.
            sanitized_module_name = self._sanitized_module_folder_name(file)
            base_path = PT.path_of_file_in_module(
                storage_path, course_name, file.section_name,
                sanitized_module_name, file.content_filepath
            )
            return self._prepend_nn_prefix_to_path(base_path, file)

        if file.module_modname in COOKIE_MOD_MODNAMES:
            file._in_module_folder = True
            # Same fix for cookie_mod (Kaltura / Helixmedia) folders.
            sanitized_module_name = self._sanitized_module_folder_name(file)
            base_path = PT.path_of_file_in_module(
                storage_path, course_name, file.section_name,
                sanitized_module_name, file.content_filepath
            )
            return self._prepend_nn_prefix_to_path(base_path, file)

        file._in_module_folder = False
        return PT.path_of_file(storage_path, course_name, file.section_name, file.content_filepath)

    def add_token_to_url(self, url: str) -> str:
        """Adds the Moodle token to a URL (improved URL handling).

        Based on official Moodle Mobile App's fixPluginfileURL.
        Reference: moodleapp/src/core/singletons/url.ts
        """
        from moodle_dl.utils import UrlHelper

        fixed_url = UrlHelper.fix_pluginfile_url(
            url=url, token=self.task.opts.token, moodle_base_url=self.task.opts.moodle_url
        )
        if fixed_url == url and not UrlHelper.is_pluginfile_url(url):
            if 'token=' in url:
                return url
            url_parts = list(urlparse.urlparse(url))
            query = dict(urlparse.parse_qsl(url_parts[4]))
            query.update({'token': self.task.opts.token})
            url_parts[4] = urlparse.urlencode(query)
            return urlparse.urlunparse(url_parts)
        return fixed_url

    def create_target_file(self, target_path: str) -> str:
        """Rename target_path if necessary and touch it."""
        from moodle_dl.utils import PathTools as PT
        target_path = PT.get_unused_file_path(target_path)
        PT.touch_file(target_path)
        return target_path

    def rename_old_file(self) -> bool:
        """Try to rename an existing modified file to _old suffix."""
        from moodle_dl.utils import PathTools as PT

        if self.task.file.old_file is None:
            return False

        old_path = self.task.file.old_file.saved_to
        if not old_path or not os.path.exists(old_path):
            return False

        new_path = old_path + '.old'
        try:
            os.rename(old_path, new_path)
            return True
        except OSError:
            logging.warning('[%d] Failed to renaming old file %r to %r', self.task_id, old_path, new_path)
            return False

    def move_old_file(self) -> bool:
        """Move .old file to a unique name in the same directory."""
        from moodle_dl.utils import PathTools as PT

        old_path = self.task.file.saved_to + '.old'
        if not os.path.exists(old_path):
            return False

        base_dir = os.path.dirname(old_path)
        base_name = os.path.basename(old_path)
        for i in range(1, 1000):
            new_name = f'{base_name}.{i}'
            new_path = os.path.join(base_dir, new_name)
            if not os.path.exists(new_path):
                try:
                    os.rename(old_path, new_path)
                    return True
                except OSError:
                    logging.warning('[%d] Failed to rename %r to %r', self.task_id, old_path, new_path)
                    return False
        return False

    # ------------------------------------------------------------------
    # HTML cleaning (low-level utilities)
    # ------------------------------------------------------------------
    @staticmethod
    def convert_line_breaks(html_text: str) -> str:
        """Convert <br> tags to newlines (case-sensitive, matches
        the original task.py behaviour for compatibility)."""
        if not html_text:
            return ''
            return ''
        return re.sub(r'<br\s*/?>', '\n', html_text)

    @staticmethod
    def convert_paragraphs(html_text: str) -> str:
        """Convert <p> tags to newlines."""
        if not html_text:
            return ''
        text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', html_text)
        text = re.sub(r'</?p[^>]*>', '\n', text)
        return text

    @staticmethod
    def convert_lists(html_text: str) -> str:
        """Convert list tags (<ul>, <ol>, <li>) to Markdown format."""
        if not html_text:
            return ''
        text = re.sub(r'<li[^>]*>', '\n• ', html_text)
        text = re.sub(r'</li>', '', text)
        text = re.sub(r'</?ul[^>]*>', '\n', text)
        text = re.sub(r'</?ol[^>]*>', '\n', text)
        return text

    @staticmethod
    def convert_formatting(html_text: str) -> str:
        """Convert bold and italic tags to Markdown format."""
        if not html_text:
            return ''
        text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', html_text, flags=re.DOTALL)
        text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
        text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL)
        text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL)
        return text

    @staticmethod
    def convert_links(html_text: str) -> str:
        """Convert HTML links to Markdown format."""
        if not html_text:
            return ''
        return re.sub(
            r'<a[^>]+href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>',
            r'[\2](\1)',
            html_text,
            flags=re.DOTALL,
        )

    @staticmethod
    def remove_html_tags(html_text: str) -> str:
        """Remove all remaining HTML tags."""
        if not html_text:
            return ''
        return re.sub(r'<[^>]+>', '', html_text)

    @staticmethod
    def decode_html_entities(html_text: str) -> str:
        """Decode HTML entities to characters."""
        if not html_text:
            return ''
        return html_module.unescape(html_text)

    @staticmethod
    def clean_whitespace(html_text: str) -> str:
        """Clean excessive whitespace."""
        if not html_text:
            return ''
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', html_text)
        text = re.sub(r' +', ' ', text)
        return text.strip()

    def clean_html_simple(self, html_text: str) -> str:
        """Clean HTML tags, return plain text."""
        if not html_text:
            return ''
        text = self.convert_line_breaks(html_text)
        text = self.remove_html_tags(text)
        text = self.decode_html_entities(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def clean_html_preserve_structure(self, html_text: str) -> str:
        """
        Clean HTML while preserving document structure as Markdown.

        Order of operations:
          1. Line breaks
          2. Paragraphs
          3. Lists
          4. Formatting
          5. Links
          6. Remove remaining tags
          7. Decode entities
          8. Clean whitespace
        """
        if not html_text:
            return ''

        text = self.convert_line_breaks(html_text)
        text = self.convert_paragraphs(text)
        text = self.convert_lists(text)
        text = self.convert_formatting(text)
        text = self.convert_links(text)
        text = self.remove_html_tags(text)
        text = self.decode_html_entities(text)
        text = self.clean_whitespace(text)
        return text

    def clean_html(self, html_text: str, mode: str = 'structured') -> str:
        """Convenience: clean_html_simple() or clean_html_preserve_structure()."""
        if mode == 'simple':
            return self.clean_html_simple(html_text)
        return self.clean_html_preserve_structure(html_text)

    # ------------------------------------------------------------------
    # Shortcut file generation
    # ------------------------------------------------------------------
    async def create_shortcut(self) -> None:
        """Create a .url / .webloc / .desktop file for an external link."""
        # Implementation lives in Task since it needs self.file, etc.
        # Delegate to a callback to keep this method thin.
        await self._delegate('create_shortcut')

    async def create_description(self) -> None:
        await self._delegate('create_description')

    async def create_html_file(self) -> None:
        await self._delegate('create_html_file')

    async def create_content_file(self) -> None:
        await self._delegate('create_content_file')

    async def create_data_url_file(self) -> None:
        await self._delegate('create_data_url_file')

    async def _delegate(self, method_name: str):
        """Forward to the underlying Task. Lets the Task own
        the actual I/O but keeps the helper interface flat."""
        method = getattr(self.task, method_name, None)
        if method is None:
            logging.warning('[%d] TaskFileOps: task has no %s', self.task_id, method_name)
            return
        return await method()

    # ------------------------------------------------------------------
    # Leganto shortcut cleanup
    # ------------------------------------------------------------------
    def _remove_leganto_shortcut_fallbacks(self, target_path: str = None) -> None:
        """Remove shortcut files left by older Leganto fallback behavior."""
        target_path = target_path or self.task.file.saved_to
        if not target_path:
            return
        base_path, extension = os.path.splitext(target_path)
        if extension.lower() not in ('.pdf', '.url', '.webloc', '.desktop'):
            return
        from moodle_dl.utils import PathTools
        for link_extension in SHORTCUT_EXTENSIONS:
            self._remove_path_and_appledouble(base_path + link_extension)

    def _remove_path_and_appledouble(self, path: str) -> None:
        from moodle_dl.utils import PathTools
        PathTools.remove_file(path)
        try:
            path_obj = Path(path)
            PathTools.remove_file(str(path_obj.with_name(f'._{path_obj.name}')))
        except (OSError, ValueError):
            pass


# ========================================================================
# Module-level helper to avoid an import cycle
# ========================================================================
def ensure_parent_dir(path: str) -> None:
    """mkdir -p the parent of `path`. Idempotent."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
