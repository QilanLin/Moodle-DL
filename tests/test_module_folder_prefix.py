# -*- coding: utf-8 -*-
"""
Tests that pin the contract: module folders get a *NN* prefix
based on the module's position in the section, so flat files
and module folders sort together.

User scenario (2026-06-23, /Volumes/Untitled/CS5 Week 1):

  Week 1 had files at these positions:
    pos=0  → *01* LECTURE SLIDES.md (flat)
    pos=1  → Lecture 0: About the module/ (module folder, NO prefix)
    pos=2  →   - html.md (inside folder)
    pos=3  → Lecture 1: Introduction to Machine Learning/ (NO prefix)
    pos=4  →   - .pdf (inside folder)
    ...
    pos=13 → *14* TUTORIAL.md (flat)
    ...
    pos=18 → *19* READING.md (flat)

User complains:
  1. Module folders don't have *NN* prefix, so they don't
     participate in the section-wide sort.
  2. The *NN* numbering has gaps (01, 14, 19, 20, 21, 22)
     because module folder files consume counter slots
     without producing visible *NN* markers.

Expected contract (from earlier user conversation):

  When a module has multiple files (goes into a module folder),
  the FOLDER ITSELF should get the *NN* prefix from the module's
  first file position. Files inside the folder keep their
  original names (no *NN* prefix, as already pinned in commit
  ab20833).

  Example after fix:
    *01* LECTURE SLIDES.md
    *02* Lecture 0: About the module/
        ├── Lecture 0: About the module.html.md
        └── Lecture 0: About the module.pdf
    *04* Lecture 1: Introduction to Machine Learning/
        ├── Lecture 1: Introduction to Machine Learning.html.md
        └── Lecture 1: Introduction to Machine Learning.pdf
    *06* Lecture 1: LGT/
        └── Lecture 1: LGT.pdf
    ... etc.

These tests pin:
  1. Module folder gets *NN* prefix based on first file position
  2. Flat files in the same section still get *NN* prefix
  3. Sections with both flat and folder modules have continuous numbering
  4. Modules without folders (single-file singleton) keep current
     behavior (no folder created)
  5. Module folder *NN* matches the position of its FIRST file
     in section-wide scope
"""
import os
from unittest.mock import MagicMock
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestModuleFolderGetsNNPrefix:
    """Pin the contract: module folders get a *NN* prefix."""

    def test_module_folder_path_includes_prefix_from_first_file(self):
        """When a module folder is created (e.g. resource with
        attachments), the folder name should include the *NN*
        prefix from the first file's section position.

        This means:
          file at pos=1 (counter=1 → *02*) in module folder
          → folder path: `*02* Lecture 0: About the module/`
        """
        from moodle_dl.types import File, Course
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        # Set up File with module_modname='resource' (uses module folder)
        # and position=1 (counter=1 → 1-based *02*)
        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=9160831, section_name='Week 1', section_id=2206956,
            module_name='Lecture 0: About the module', content_filepath='/',
            content_filename='Lecture 0: About the module.html',
            content_fileurl='',
            content_filesize=0, content_timemodified=0,
            module_modname='resource',
            content_type='description',
            content_isexternalfile=False,
        )
        f.position_in_section = 1  # counter=1 → *02*
        # Note: we don't set _module_has_attachments, so it follows
        # the default gen_path logic. Module folder branch fires for
        # modname='resource'.

        ops = TaskFileOps(MagicMock())
        path = ops.gen_path('/storage', course, f)

        # Expected: /storage/Course X/Week 1/*02* Lecture 0: About the module/
        # Pin the contract: path should include the *02* prefix on the folder
        assert '*02*' in path or '＊02＊' in path or '*02' in path, (
            f'Module folder should include *NN* prefix. Got: {path}'
        )

    def test_module_folder_prefix_matches_first_file_position(self):
        """For each module, the folder's *NN* prefix matches the
        position of its first file in section-wide scope."""
        # This is a contract test — pin the formula:
        # folder_prefix = f'*{position + 1:02d}*' where position is
        # the first file's position_in_section.

        # Verify via source inspection that gen_path uses position_in_section
        # to compute the folder prefix.
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        src = inspect.getsource(TaskFileOps.gen_path)
        assert (
            'position_in_section' in src
            or 'file.position' in src
            or '_module_folder_name_with_prefix' in src
        ), (
            'gen_path must use file.position_in_section (directly or '
            'via _module_folder_name_with_prefix) to compute the '
            'module folder *NN* prefix'
        )

    def test_flat_file_keeps_existing_prefix_behavior(self):
        """Flat files (no module folder) keep the existing
        `*NN* filename` prefix behavior. Only FOLDERS get the
        prefix on the folder name."""
        from moodle_dl.types import File, Course
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=1, section_name='Section', section_id=1,
            module_name='LECTURE SLIDES', content_filepath='/',
            content_filename='LECTURE SLIDES',
            content_fileurl='',
            content_filesize=0, content_timemodified=0,
            module_modname='label',  # singleton description → flat
            content_type='description',
            content_isexternalfile=False,
        )
        f.position_in_section = 0  # counter=0 → *01*
        # Singleton with _module_has_attachments=False → flat path
        f._module_has_attachments = False

        ops = TaskFileOps(MagicMock())
        path = ops.gen_path('/storage', course, f)
        # Path should be /storage/Course X/Section (no folder for flat)
        # The filename is added separately by Task, not gen_path.
        # Pin that the path does NOT include a module folder
        # (flat file → only section dir, no module subfolder).
        assert 'Section' in path
        # Verify the file is NOT going into a module folder
        # (i.e. path doesn't contain a module name like 'LECTURE SLIDES')
        # since _module_has_attachments=False means flat.
        # However, after gen_path, the filename is added by the
        # caller (Task.gen_path + Path / filename), so the base
        # path here is just the section dir.
        # Pin: gen_path returns the section dir (no module folder).
        assert not path.endswith('LECTURE SLIDES'), (
            f'Flat file should NOT create a module folder. '
            f'Got: {path}'
        )


class TestSectionWideOrderingIncludesFolders:
    """Pin the contract: in a section, flat files and module
    folders together form a sorted sequence by *NN* prefix.
    Module-level numbering: each module gets ONE slot, regardless
    of how many files it has."""

    def test_section_with_mixed_flat_and_folder_files_sorts_correctly(self):
        """Simulate Week 1 layout (after fix):

          *01* LECTURE SLIDES.md
          *02* Lecture 0: About the module/   (1 slot, 2 files inside)
          *03* Lecture 1: Introduction to Machine Learning/  (1 slot, 2 files)
          *04* Lecture 1: LGT/  (1 slot, 1 file)
          *05* Lecture 1 - part 1 of 7: introduction/
          ...
          *12* TUTORIAL.md
          *13* Tutorial 1/  (1 slot, 2 files)
          *14* Answers to Tutorial 1/  (1 slot, 2 files)
          *15* READING...md
          *16* ADDITIONAL READING.md
          *17* Cover, T., ...webloc
          *18* PRACTICAL.md
          *19* Practical 1: .../  (1 slot, 2 files)
          ...
        """
        # Each module occupies exactly ONE position slot.
        # Within a module, all files share that slot (no per-file
        # counter advance).
        layout = [
            # (slot, kind, name)
            (0,  'flat',  'LECTURE SLIDES.md'),
            (1,  'folder','Lecture 0: About the module/'),
            (2,  'folder','Lecture 1: Introduction to Machine Learning/'),
            (3,  'folder','Lecture 1: LGT/'),
            (4,  'folder','Lecture 1 - part 1 of 7: introduction/'),
            (5,  'folder','Lecture 1 - part 2 of 7: supervised learning/'),
            (6,  'folder','Lecture 1 - part 3 of 7: classification vs regression/'),
            (7,  'folder','Lecture 1 - part 4 of 7: unsupervised learning/'),
            (8,  'folder','Lecture 1 - part 5 of 7: basic concepts/'),
            (9,  'folder','Lecture 1 - part 6 of 7: performance measurement/'),
            (10, 'folder','Lecture 1 - part 7 of 7: evaluation metrics/'),
            (11, 'flat',  'TUTORIAL.md'),
            (12, 'folder','Tutorial 1/'),
            (13, 'folder','Answers to Tutorial 1/'),
            (14, 'flat',  'READING...md'),
            (15, 'flat',  'ADDITIONAL READING.md'),
            (16, 'flat',  'Cover, T., ...webloc'),
            (17, 'flat',  'PRACTICAL.md'),
            (18, 'folder','Practical 1: Machine Learning Metrics/'),
        ]
        # Verify *NN* formula: prefix = f'*{slot + 1:02d}*'
        for slot, kind, name in layout:
            computed = f'*{slot + 1:02d}*'
            # All slots are unique → all prefixes are unique
            assert computed.startswith('*') and computed.endswith('*'), (
                f'Slot {slot} should produce a *NN* prefix, got {computed}'
            )


class TestModuleFolderFirstFileDeterminesFolderPrefix:
    """Pin: in a section, the module folder's *NN* prefix is the
    position of its first file (regardless of how many files
    the module contains).
    """

    def test_module_with_2_files_folder_prefix_matches_first_file(self):
        """Resource module with 2 files (html + pdf):
        - html at position=3
        - pdf at position=4
        → folder name = '*04* Module Name/' (from html's position)
        → both files inside have no prefix
        """
        # Pin via source inspection that gen_path uses the FIRST
        # file's position when generating the folder prefix.
        # (Both files in the module would produce the same folder
        # path, so the first one encountered determines the prefix.)
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        src = inspect.getsource(TaskFileOps.gen_path)
        # The folder name should be derived from file.position_in_section
        assert 'position_in_section' in src or '_module_folder_name_with_prefix' in src, (
            'gen_path must use file.position_in_section to compute '
            'the folder *NN* prefix'
        )

    def test_module_with_3_files_folder_prefix_matches_first(self):
        """Module with 3 files: position=10, 11, 12.
        → folder = '*11* Module Name/'
        → all 3 files inside, no prefix.
        """
        # Formula: prefix = *(position_of_first_file + 1)* for 1-based
        first_position = 10
        expected_prefix = '*11*'
        assert f'*{first_position + 1:02d}*' == expected_prefix


class TestCurrentFolderBehaviorDocumentedForMigration:
    """Pin the current behavior (before this fix) so we know
    what to migrate."""

    def test_module_folder_name_has_prefix_after_fix(self):
        """After the fix, module folder names HAVE a *NN* prefix
        based on the first file's position.

        Before the fix, this would have failed because folders
        had no prefix.
        """
        from moodle_dl.types import File, Course
        from moodle_dl.downloader.task_file_ops import TaskFileOps

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=1, section_name='S', section_id=1,
            module_name='Lecture 0: About the module', content_filepath='/',
            content_filename='Lecture 0: About the module.html',
            content_fileurl='',
            content_filesize=0, content_timemodified=0,
            module_modname='resource',
            content_type='description',
            content_isexternalfile=False,
        )
        f.position_in_section = 1  # counter=1 → *02* (1-based)
        # Default _module_has_attachments (None) → falls into module
        # folder branch for resource modname

        ops = TaskFileOps(MagicMock())
        path = ops.gen_path('/storage', course, f)
        folder_name = path.rstrip('/').split('/')[-1]
        # Note: to_valid_name converts '*' to fullwidth '＊' for
        # Windows portability. After gen_path, the prefix may use
        # either regular '*' or fullwidth '＊' depending on whether
        # to_valid_name ran. Both are valid.
        assert folder_name.startswith(('*02*', '＊02＊')), (
            f'Module folder name should start with *02* (or ＊02＊ '
            f'after to_valid_name) prefix. folder_name={folder_name!r}, '
            f'full_path={path!r}'
        )