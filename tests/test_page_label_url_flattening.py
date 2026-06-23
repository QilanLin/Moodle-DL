# -*- coding: utf-8 -*-
"""
User report (2026-06-24, /Volumes/Untitled/test3, 4CCS1ISE Introduction
to Software Engineering(23~24 SEM2 000001)):

  Section 2 has 1 label module + 11 page modules + 1 book module:

  Section 2: 2 - Requirements Analysis, UML and Use Case Diagrams (19-25/Jan)
    *01* Week 2 - Requirements Analysis, UML and Use Case Diagrams/   <-- label module (singleton description)
      Table of Contents.html
      *01* 2. Week Overview           <-- page module (singleton page)
      *01* 3. Requirement Analysis    <-- page module (singleton page)
      *01* 4. UML                     <-- page module (singleton page)
      *01* 5. Use Case Diagrams       <-- page module (singleton page)
      *02* 1. Learning Objectives     <-- page module (singleton page)
      *03* 2. Week Overview           <-- page module (kaltura+video)
      *04* 2. Week Overview           <-- page module (pptx attachment)
      *05* 3. Requirement Analysis    <-- page module (kaltura+video)
      *06* 3. Requirement Analysis    <-- page module (index.html)
      *07* 4. UML                    <-- page module (kaltura+video)
      ...

  Problems:
  1. Label module "Week 2 - Requirements Analysis..." creates a folder
     with only the description HTML inside (singleton description).
     Per the existing rule (commit ab20833), singleton-description
     modules should be FLATTENED to the section dir.
     Why is the label NOT being flattened?
     → Because the label has introfiles (the page modules' content
       is mistakenly counted as label's introfiles?) OR
     → Because the gen_path code unconditionally puts `label` modname
       in a folder regardless of attachment count.

  2. 4 page modules (2. Week Overview, 3. Requirement Analysis, 4. UML,
     5. Use Case Diagrams) all have ONLY the page's own index.html
     (no kaltura, no attachments). They should be FLATTENED to flat
     files in the section dir, but they're getting `*01*` folders
     with a single file inside.

  Root cause (in gen_path):
    The condition `file.module_modname in ('resource', 'page', 'url',
    'label')` ALWAYS puts these modnames in a folder, regardless of
    whether the module has real attachments.

  Fix: Remove the modname-based always-folder rule. Let
  `_module_has_attachments` be the sole decider. A module goes in a
  folder only if it has real attachments (resource_file, label_file,
  cookie_mod, etc.) — NOT if its only file is its own content
  (description, page content, page index.html).

  Other modnames that should be tested:
    - label with introfiles (real label with inline image)
    - label with description only (singleton)
    - page with index.html only (singleton)
    - page with kaltura video
    - page with attachment (e.g. embedded file)
    - url with introfile
    - url without introfile
    - resource with main file
    - resource with description only
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_file(module_id, module_modname, section_name, section_id, content_filename,
               content_filepath='/', content_fileurl='', content_type='file',
               content_isexternalfile=False, has_attachments=False,
               module_name=None, position_in_section=None):
    """Build a File for gen_path testing."""
    from moodle_dl.types import File

    f = File(
        module_id=module_id,
        section_id=section_id,
        section_name=section_name,
        module_name=module_name or f'Module {module_id}',
        module_modname=module_modname,
        content_filepath=content_filepath,
        content_filename=content_filename,
        content_fileurl=content_fileurl,
        content_type=content_type,
        content_isexternalfile=content_isexternalfile,
        content_filesize=1024,
        content_timemodified=1700000000,
    )
    setattr(f, '_module_has_attachments', has_attachments)
    if position_in_section is not None:
        f.position_in_section = position_in_section
    return f


def _make_course():
    from moodle_dl.types import Course
    return Course(_id=1, fullname='Test Course')


# =========================================================================
# Page module flattening
# =========================================================================
class TestPageModuleFlattening:
    """Page modules with ONLY the page's own index.html (no
    attachments) should be FLATTENED, not in a folder.

    This is the same contract as singleton-description labels:
    if the only file is the module's own content, no folder is needed.
    """

    def test_page_with_only_index_html_is_flattened(self):
        """Page module with only its own index.html → flat file.

        Before fix: gen_path put it in `*01* Module Name/index.html`
        (a folder with one file — visual noise).
        After fix: gen_path should produce `*01* Module Name.html`
        (flat file in section dir).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        f = _make_file(
            module_id=100, module_modname='page',
            section_name='Week 2', section_id=1,
            content_filename='index.html',
            has_attachments=False,  # ONLY the page's own content
        )
        f.position_in_section = 0
        path = ops.gen_path('/storage', _make_course(), f)
        # gen_path returns directory only. The file name is added
        # later. So we check the directory part.
        assert path == '/storage/Test Course/Week 2', (
            f'Page with only index.html should be flat (in section dir), '
            f'got: {path!r}'
        )

    def test_page_with_pptx_attachment_keeps_folder(self):
        """Page module WITH a .pptx attachment → keeps folder.

        Real case: ISE-Week02 - Use cases.pptx is a page module
        that contains a presentation. The pptx is a real attachment
        (not the page's own index.html), so the module folder is
        needed and the pptx should be downloaded inside.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        f = _make_file(
            module_id=200, module_modname='page',
            section_name='Week 2', section_id=1,
            content_filename='ISE-Week02 - Use cases.pptx',
            content_type='file',
            has_attachments=True,  # has pptx
        )
        f.position_in_section = 10
        path = ops.gen_path('/storage', _make_course(), f)
        # Should be in a folder with *11* prefix
        assert '*11*' in path, (
            f'Page with pptx should be in folder with *11* prefix, '
            f'got: {path!r}'
        )
        # Folder name should include the module name
        assert 'Module 200' in path

    def test_page_with_kaltura_video_keeps_folder(self):
        """Page module WITH a kaltura video (cookie_mod) → keeps folder.

        The page's own index.html + a kaltura video means the module
        has real attachments, so a folder is appropriate.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        f = _make_file(
            module_id=200, module_modname='page',
            section_name='Week 3', section_id=1,
            content_filename='2. Week Overview.html',
            has_attachments=True,  # has kaltura video
        )
        f.position_in_section = 2
        path = ops.gen_path('/storage', _make_course(), f)
        # Should have a module folder
        # Path is /storage/Test Course/Week 3/*03* Module 200 (dir)
        assert '*03*' in path, (
            f'Page with kaltura should have *NN* prefix folder, got: {path!r}'
        )
        assert 'Week 3' in path


# =========================================================================
# Label module flattening
# =========================================================================
class TestLabelModuleFlattening:
    """Label modules with ONLY a description (no introfiles) should
    be FLATTENED, not in a folder.
    """

    def test_label_with_only_description_is_flattened(self):
        """Label module with only description HTML → flat file.

        The label has:
          - description file (type='description')
          - metadata.json (type='content')
        Both are NOT in ATTACHMENT_CONTENT_TYPES, so
        _module_has_attachments should be False.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        f = _make_file(
            module_id=300, module_modname='label',
            section_name='Week 2', section_id=1,
            content_filename='Week 2 - Requirements.html',
            content_type='description',
            has_attachments=False,
        )
        f.position_in_section = 0
        path = ops.gen_path('/storage', _make_course(), f)
        # Should be flat (in section dir)
        assert path == '/storage/Test Course/Week 2', (
            f'Label with only description should be flat, got: {path!r}'
        )

    def test_label_with_introfile_keeps_folder(self):
        """Label module WITH an inline image (introfile) → keeps folder.

        The label has:
          - introfile (type='label_file')  ← in ATTACHMENT_CONTENT_TYPES
          - description (type='description')
        So _module_has_attachments is True, folder is appropriate.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        f = _make_file(
            module_id=400, module_modname='label',
            section_name='Week 1', section_id=1,
            content_filename='Module.html',
            content_type='description',
            has_attachments=True,  # has label_file introfile
        )
        f.position_in_section = 1
        path = ops.gen_path('/storage', _make_course(), f)
        # Should be in a folder (with *NN* prefix)
        assert '*02*' in path
        # Should NOT be a flat file directly
        assert path != '/storage/Test Course/Week 1/*02* Module.html'


# =========================================================================
# URL module flattening
# =========================================================================
class TestURLModuleFlattening:
    """URL modules with only the URL (no introfile) → flat file."""

    def test_url_with_only_external_url_is_flattened(self):
        """URL module pointing to an external website (no introfile)
        should be flat.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        f = _make_file(
            module_id=500, module_modname='url',
            section_name='Section A', section_id=1,
            content_filename='https://example.com',
            content_isexternalfile=True,
            has_attachments=False,
        )
        f.position_in_section = 0
        path = ops.gen_path('/storage', _make_course(), f)
        # Should be flat (in section dir)
        assert path == '/storage/Test Course/Section A', (
            f'URL with no introfile should be flat, got: {path!r}'
        )

    def test_url_with_introfile_keeps_folder(self):
        """URL module WITH an introfile (inline image) → keeps folder."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        f = _make_file(
            module_id=600, module_modname='url',
            section_name='Section B', section_id=1,
            content_filename='https://example.com',
            content_type='url_introfile',
            has_attachments=True,
        )
        f.position_in_section = 1
        path = ops.gen_path('/storage', _make_course(), f)
        # Should be in a folder
        assert '*02*' in path
        assert 'Section B' in path


# =========================================================================
# Resource module flattening (sanity check — should keep folder)
# =========================================================================
class TestResourceModuleFolderKept:
    """Resource modules with their main file should ALWAYS be in a
    folder (they have a real .pdf/.zip attachment).
    """

    def test_resource_with_pdf_keeps_folder(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        f = _make_file(
            module_id=700, module_modname='resource',
            section_name='General', section_id=1,
            content_filename='lecture.pdf',
            content_type='resource_file',
            has_attachments=True,
        )
        f.position_in_section = 0
        path = ops.gen_path('/storage', _make_course(), f)
        # Should be in a folder
        assert '*01*' in path
        assert 'General' in path
        # The module_name is used for the folder
        assert 'Module' in path


# =========================================================================
# Section structure: multiple singleton modules in same section
# =========================================================================
class TestMultipleSingletonsInSection:
    """When a section has multiple singleton-description modules,
    each should get a unique *NN* prefix in the flat section dir.
    """

    def test_four_singleton_pages_get_unique_positions(self):
        """4 page modules with only index.html → 4 flat files with
        *01*, *02*, *03*, *04* prefixes (in section-wide order).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        ops = TaskFileOps(MagicMock())
        page_modules = [
            ('2. Week Overview', 0),
            ('3. Requirement Analysis', 1),
            ('4. UML', 2),
            ('5. Use Case Diagrams', 3),
        ]
        # All 4 page modules should map to the same section dir
        # (they all get *NN* prefix on the FILENAME, not the dir)
        for name, pos in page_modules:
            f = _make_file(
                module_id=100 + pos, module_modname='page',
                section_name='Week 2', section_id=1,
                content_filename='index.html',
                has_attachments=False,
            )
            f.position_in_section = pos
            path = ops.gen_path('/storage', _make_course(), f)
            # All go to the same section dir
            assert path == '/storage/Test Course/Week 2', (
                f'Page {name!r} (pos {pos}) should be in section dir, '
                f'got: {path!r}'
            )
            # And the position is set so generate_filename_with_index
            # will produce *01* index.html, *02* index.html, etc.
            assert f.position_in_section == pos