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

        如果文件有 position_in_section，则在原始文件名前添加位置索引前缀。
        ⚠️ 核心原则：保留原始文件名，只添加前缀。

        示例:
            position=0, original="lecture.pdf" → "*01* lecture.pdf"
            position=4, original="01-intro.pdf" → "*05* 01-intro.pdf" (保留原名中的 "01-")
            position=None → "lecture.pdf" (无索引)
        """
        from moodle_dl.utils import PathTools as PT

        original_filename = PT.to_valid_name(file.content_filename, is_file=True)

        if file.position_in_section is None:
            return original_filename

        position = file.position_in_section
        if position < 99:
            index_str = f'{position + 1:02d}'
        else:
            index_str = f'{position + 1:03d}'

        return f'*{index_str}* {original_filename}'

    def gen_path(self, storage_path: str, course, file) -> str:
        """Generate the directory path where a file should be stored.

        Same as the original Task.gen_path, but extracted so the
        path logic can be tested and reused independently.

        Section ordering
        ----------------
        The Moodle API (``core_course_get_contents``) returns
        sections already sorted by their sequential ``section``
        number (0, 1, 2, 3, ..., 10) — see
        ``course_sections.section`` in
        ``moodle_official_repo_for_reference/public/lib/db/install.xml``.
        So ``moodle-dl`` does not need to impose its own sort
        order on directory names.

        On-disk multi-digit sections (e.g. ``Week 1``, ``Week 2``,
        ``..., Week 10``) WILL appear in alphabetical order on
        macOS Finder and Windows Explorer (``Week 1, Week 10,
        Week 2, ...``) because alphabetical sort does not match
        natural sort for multi-digit numbers. The ``moodle-dl
        --list`` command (added in the same commit family)
        displays sections in natural-sort order, hides any
        macOS ``._*`` shadow files, and cross-references the
        SQLite database. Users who want natural sort in Finder
        can:
          * Sort Finder by ``sortorder`` if their filesystem
            supports it, or
          * Use the official moodle.org UI which uses natural
            sort, or
          * Use ``ls -v`` (BSD/macOS) / ``ls -1 --group-directories-first``
            (Linux) which provide natural sort.
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

        if file.module_modname.endswith(
            (
                'assign', 'book', 'data', 'folder', 'forum', 'lesson',
                'page', 'quiz', 'resource', 'workshop',
            )
        ) or file.module_modname in (
            'resource', 'page', 'url', 'label',
        ):
            return PT.path_of_file_in_module(
                storage_path, course_name, file.section_name, file.module_name, file.content_filepath
            )

        if file.module_modname in COOKIE_MOD_MODNAMES:
            return PT.path_of_file_in_module(
                storage_path, course_name, file.section_name, file.module_name, file.content_filepath
            )

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
