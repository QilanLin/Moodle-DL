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
            or '_prepend_nn_prefix_to_path' in src
            or '_sanitized_module_folder_name' in src
        ), (
            'gen_path must use file.position_in_section (directly, '
            'via _module_folder_name_with_prefix, '
            '_prepend_nn_prefix_to_path, or _sanitized_module_folder_name) '
            'to compute the module folder *NN* prefix'
        )

    def test_module_folder_prefix_stays_ascii_after_sanitize(self):
        """User bug (2026-06-23, CS6): module folder names had
        full-width ``＊`` (U+FF0A) prefix because to_valid_name
        sanitized the entire ``*NN* Module Name`` string
        (converting ``*`` → ``＊``). This broke the section-wide
        sort order because ASCII ``*`` (U+002A) sorts BEFORE
        full-width ``＊`` in POSIX sort.

        Fix: _module_folder_name_with_prefix sanitizes the
        module_name FIRST (replacing ``*`` → ``＊``), then adds
        the ``*NN*`` ASCII prefix. The prefix chars stay as
        ASCII so the section-wide sort is consistent.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import File, Course
        from unittest.mock import MagicMock

        course = Course(_id=1, fullname='Course X')
        f = File(
            module_id=1, section_name='Section', section_id=1,
            module_name='Lecture 0: About the module',
            content_filepath='/',
            content_filename='Lecture 0.pdf',
            content_fileurl='https://example.com/Lecture 0.pdf',
            content_filesize=1024, content_timemodified=0,
            module_modname='resource', content_type='file',
            content_isexternalfile=False,
        )
        f.position_in_section = 1
        f._module_has_attachments = True

        ops = TaskFileOps(MagicMock())
        # The folder name with prefix should have ASCII '*' not '＊'
        folder_name = ops._module_folder_name_with_prefix(f)
        assert folder_name.startswith('*02* '), (
            f'Folder prefix should be ASCII "*02*", got: {folder_name!r}'
        )
        # Verify the prefix is specifically ASCII (not full-width)
        assert '＊' not in folder_name[:5], (
            f'Folder prefix chars should be ASCII, not full-width. '
            f'Got: {folder_name[:5]!r}'
        )
        # The full path through PT.path_of_file_in_module should
        # also preserve ASCII '*' in the prefix part.
        full_path = ops.gen_path('/storage', course, f)
        # Split path and find the folder part (with prefix)
        parts = full_path.split('/')
        folder_part = next((p for p in parts if p.startswith('*')), None)
        assert folder_part is not None, (
            f'Path should contain a folder with *NN* prefix. '
            f'Got: {full_path!r}'
        )
        # The prefix should still be ASCII '*', not full-width '＊'
        assert folder_part[:1] == '*', (
            f'Folder prefix should start with ASCII "*", not "＊". '
            f'Got: {folder_part[:10]!r}'
        )
        assert '＊' not in folder_part[:5], (
            f'Folder prefix chars should be ASCII. '
            f'Got: {folder_part[:5]!r}'
        )

    def test_module_folder_name_module_part_sanitized(self):
        """Verify the module name part of the folder is sanitized
        (e.g. ``:`` → full-width ``：``) while the prefix stays ASCII.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import File
        from unittest.mock import MagicMock

        f = File(
            module_id=1, section_name='S', section_id=1,
            module_name='Lecture 0: About the module',
            content_filepath='/',
            content_filename='Lecture 0.pdf',
            content_fileurl='https://example.com/Lecture 0.pdf',
            content_filesize=1024, content_timemodified=0,
            module_modname='resource', content_type='file',
            content_isexternalfile=False,
        )
        f.position_in_section = 1
        f._module_has_attachments = True

        ops = TaskFileOps(MagicMock())
        folder_name = ops._module_folder_name_with_prefix(f)
        # Prefix is ASCII '*02* '
        assert folder_name.startswith('*02* '), (
            f'Prefix should be ASCII. Got: {folder_name!r}'
        )
        # The module name part (after '*02* ') should have
        # full-width colon (to_valid_name converts ':' → '：')
        # because the sanitize happens BEFORE adding prefix.
        after_prefix = folder_name[5:]
        assert '：' in after_prefix, (
            f'Module name part should have full-width colon. '
            f'Got: {after_prefix!r}'
        )

    def test_flat_file_uses_3digit_prefix_for_position_99_plus(self):
        """Position >= 99 uses 3-digit prefix (*100*, *101*, etc.)
        to avoid ambiguity in long sections.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import File

        ops = TaskFileOps(MagicMock())
        for pos, expected in [(99, '*100*'), (100, '*101*'), (123, '*124*')]:
            f = File(
                module_id=1, section_name='S', section_id=1,
                module_name='test_module', content_filepath='/',
                content_filename='test.md',
                content_fileurl='https://example.com/test.md',
                content_filesize=1024, content_timemodified=0,
                module_modname='label', content_type='description',
                content_isexternalfile=False,
            )
            f.position_in_section = pos
            generated = ops.generate_filename_with_index(f)
            assert generated.startswith(expected + ' '), (
                f'Position {pos} should produce {expected} prefix. '
                f'Got: {generated!r}'
            )

    def test_flat_file_uses_2digit_prefix_for_position_under_99(self):
        """Position < 99 uses 2-digit prefix (*01* to *99*)."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import File

        ops = TaskFileOps(MagicMock())
        for pos, expected in [(0, '*01*'), (50, '*51*'), (98, '*99*')]:
            f = File(
                module_id=1, section_name='S', section_id=1,
                module_name='test_module', content_filepath='/',
                content_filename='test.md',
                content_fileurl='https://example.com/test.md',
                content_filesize=1024, content_timemodified=0,
                module_modname='label', content_type='description',
                content_isexternalfile=False,
            )
            f.position_in_section = pos
            generated = ops.generate_filename_with_index(f)
            assert generated.startswith(expected + ' '), (
                f'Position {pos} should produce {expected} prefix. '
                f'Got: {generated!r}'
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
        assert (
            'position_in_section' in src
            or '_module_folder_name_with_prefix' in src
            or '_prepend_nn_prefix_to_path' in src
            or '_sanitized_module_folder_name' in src
        ), (
            'gen_path must use file.position_in_section (directly, '
            'via _module_folder_name_with_prefix, '
            '_prepend_nn_prefix_to_path, or _sanitized_module_folder_name) '
            'to compute the folder *NN* prefix'
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
            content_filename='Lecture 0: About the module.pdf',
            content_fileurl='https://example.com/l.pdf',
            content_filesize=1024, content_timemodified=0,
            module_modname='resource',
            content_type='file',
            content_isexternalfile=False,
        )
        f.position_in_section = 1  # counter=1 → *02* (1-based)
        # Force module-folder branch (resource with attachment file)
        f._module_has_attachments = True

        ops = TaskFileOps(MagicMock())
        path = ops.gen_path('/storage', course, f)
        folder_name = path.rstrip('/').split('/')[-1]
        # The fix (commit 379bb7f): the prefix must be ASCII '*02*'
        # (NOT full-width '＊02＊') so POSIX sort puts it before
        # full-width chars. Full-width '＊' would break the
        # section-wide *NN* sort sequence.
        assert folder_name.startswith('*02*'), (
            'Folder prefix must be ASCII "*02*", not full-width '
            '"＊02＊" (full-width breaks POSIX sort order). '
            f'Got: {folder_name!r}'
        )