# -*- coding: utf-8 -*-
"""
Pin the file path / filename contract for downloaded Moodle modules.

Contract (user-confirmed 2026-06-22):

  1. If a Moodle module has NO attachments (only its description
     HTML preview), the description file is FLATTENED into the
     section directory and uses the section-wide *NN* prefix:

       Section/
       ├── *02* Lecture 3： Probabilistic methods 1.html.md

     NOT:
       Section/
       └── Lecture 3： Probabilistic methods 1/
           └── *02* Lecture 3： Probabilistic methods 1.html.md

  2. If a Moodle module HAS attachments (resource_file, label_file,
     assign_file, video, ...), the module gets its own subdirectory
     and the files inside do NOT carry the *NN* prefix:

       Section/
       └── Lecture 3： Probabilistic methods 1/
           ├── Lecture 3： Probabilistic methods 1.html.md
           └── Lecture 3： Probabilistic methods 1.pdf

     The module folder name serves as the position marker (the
     folder is the Nth module in the section's server-order).

Rationale (user quotes):
  - "若创建文件夹则一个文件夹里只会有一个文件的情况就不要
    创建文件夹"
  - "Lecture 3： Probabilistic methods 1 应该被下载为
    「*xx* Lecture 3： Probabilistic methods 1」 而
    Lecture 3： Probabilistic methods 1 文件夹下的 .pdf 和
    .html.md 就不再需要*xx*前缀序号了"
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# gen_path: flat vs folder depending on module attachments
# =========================================================================
class TestGenPathSingletonDescriptionModule:
    """A module with only a description HTML (no attachments) is
    FLATTENED into the section directory. The file's basename is
    the module's name (not the truncated description text).

    Rationale: a single-file folder with the same name as its
    sole file is visual noise. The user explicitly asked for it to
    be flattened.
    """

    def _path_for(self, module_modname, module_name, content_type,
                  content_filename, has_attachments=False):
        """Build a path using TaskFileOps.gen_path. We need a
        minimal File + Course to call it.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from moodle_dl.types import Course, File
        from unittest.mock import MagicMock

        course = Course(
            _id=1,
            fullname='Course X',
        )
        f = File(
            module_id=100,
            section_name='Week 3',
            section_id=1,
            module_name=module_name,
            content_filepath='/',
            content_filename=content_filename,
            content_fileurl='https://example.com/x',
            content_filesize=100,
            content_timemodified=0,
            module_modname=module_modname,
            content_type=content_type,
            content_isexternalfile=False,
        )
        # Attach the has_attachments flag (computed by result_builder
        # before download).
        f._module_has_attachments = has_attachments
        return TaskFileOps(MagicMock()).gen_path('/storage', course, f)

    def test_label_only_description_module_is_flattened(self):
        """A label module with only a description file goes
        directly into the section directory. No module-name
        subfolder is created.

        We simulate the full download path:
            saved_to = Path(destination) / filename
        because task.py:995 uses that pattern to build the final
        file path. destination alone doesn't carry the trailing
        ``/`` after the module name when content_filepath is
        ``/``; the Path / filename join re-introduces it.
        """
        import pathlib
        dest = self._path_for(
            module_modname='label',
            module_name='Privacy and Confidentiality',
            content_type='description',
            content_filename='Privacy and Confidentiality',
            has_attachments=False,  # singleton description
        )
        # Singleton → flat path (no module layer)
        saved_to = str(pathlib.Path(dest) / 'Privacy and Confidentiality.md')
        # Module folder should NOT appear in the saved_to path
        assert '/Privacy and Confidentiality/Privacy' not in saved_to, (
            f'Label singleton description should be flat, got {saved_to}'
        )
        # File should land directly in section dir
        assert saved_to.endswith('/Week 3/Privacy and Confidentiality.md'), (
            f'Path should end with section/file, got {saved_to}'
        )

    def test_label_with_attachments_uses_module_folder(self):
        """A label module WITH attachments (label_file) keeps the
        module folder. The folder name is the module name.
        """
        import pathlib
        dest = self._path_for(
            module_modname='label',
            module_name='Privacy and Confidentiality',
            content_type='label_file',
            content_filename='100.png',
            has_attachments=True,
        )
        # With attachments → module folder path
        saved_to = str(pathlib.Path(dest) / '100.png')
        assert '/Privacy and Confidentiality/100.png' in saved_to, (
            f'Label with attachments should use module folder, '
            f'got {saved_to}'
        )

    def test_resource_module_uses_folder_when_has_resource_file(self):
        """A resource module with a resource_file (PDF/ZIP/etc.)
        keeps its module folder."""
        import pathlib
        dest = self._path_for(
            module_modname='resource',
            module_name='Lecture 3 slides',
            content_type='resource_file',
            content_filename='Lecture 3 slides.pdf',
            has_attachments=True,
        )
        saved_to = str(pathlib.Path(dest) / 'Lecture 3 slides.pdf')
        assert '/Lecture 3 slides/Lecture 3 slides.pdf' in saved_to, (
            f'Resource module with attachment should use folder, '
            f'got {saved_to}'
        )

    def test_resource_module_description_only_is_flattened(self):
        """A resource module whose description is the only file
        (no actual resource_file) is also flattened."""
        import pathlib
        dest = self._path_for(
            module_modname='resource',
            module_name='Quiz intro',
            content_type='description',
            content_filename='Quiz intro',
            has_attachments=False,
        )
        saved_to = str(pathlib.Path(dest) / 'Quiz intro.md')
        assert '/Quiz intro/Quiz' not in saved_to, (
            f'Resource singleton description should be flat, '
            f'got {saved_to}'
        )

    def test_assign_module_description_only_is_flattened(self):
        import pathlib
        dest = self._path_for(
            module_modname='assign',
            module_name='Lab 1',
            content_type='description',
            content_filename='Lab 1',
            has_attachments=False,
        )
        saved_to = str(pathlib.Path(dest) / 'Lab 1.md')
        assert '/Lab 1/Lab' not in saved_to

    def test_quiz_module_description_only_is_flattened(self):
        import pathlib
        dest = self._path_for(
            module_modname='quiz',
            module_name='Quiz 1',
            content_type='description',
            content_filename='Quiz 1',
            has_attachments=False,
        )
        saved_to = str(pathlib.Path(dest) / 'Quiz 1.md')
        assert '/Quiz 1/Quiz' not in saved_to


# =========================================================================
# generate_filename_with_index: prefix only for flat files
# =========================================================================
class TestFilenamePrefixOnlyForFlatFiles:
    """The *NN* prefix is applied only to files in the SECTION
    directory (flat files). Files INSIDE a module folder do NOT
    get a prefix because the folder name itself encodes the
    module's position in the section.
    """

    def _make_file(self, content_filename, position_in_section,
                   in_module_folder=False):
        from moodle_dl.types import File
        f = File(
            module_id=100,
            section_name='Week 3',
            section_id=1,
            module_name='Lecture 3',
            content_filepath='/Lecture 3/' if in_module_folder else '/',
            content_filename=content_filename,
            content_fileurl='https://example.com/x',
            content_filesize=100,
            content_timemodified=0,
            module_modname='page',
            content_type='description',
            content_isexternalfile=False,
        )
        f.position_in_section = position_in_section
        # gen_path sets this on File objects to signal "inside a
        # module folder" vs "flattened into section dir". We
        # simulate that here.
        f._in_module_folder = in_module_folder
        return f

    def test_flat_file_gets_nn_prefix(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock

        f = self._make_file('Lecture 3 slides', position_in_section=2,
                            in_module_folder=False)
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(f)
        assert filename == '*03* Lecture 3 slides', (
            f'Flat file should get *NN* prefix, got {filename!r}'
        )

    def test_module_folder_file_does_not_get_nn_prefix(self):
        """A file inside a module folder does NOT get *NN* prefix.
        The module folder name is the position marker."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock

        f = self._make_file('Lecture 3 slides.pdf', position_in_section=2,
                            in_module_folder=True)
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(f)
        assert filename == 'Lecture 3 slides.pdf', (
            f'Module-folder file should NOT get *NN* prefix, '
            f'got {filename!r}'
        )

    def test_module_folder_file_with_none_position_unchanged(self):
        """If a file inside a module folder has position_in_section=None
        (e.g. system file), no prefix is added. Same as before."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock

        f = self._make_file('metadata.json', position_in_section=None,
                            in_module_folder=True)
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(f)
        assert filename == 'metadata.json'


# =========================================================================
# Module has_attachments detection
# =========================================================================
class TestModuleHasAttachmentsDetection:
    """result_builder must mark each File with `_module_has_attachments`
    so gen_path knows whether to flatten or use a module folder.
    """

    def test_label_module_with_only_description_is_singleton(self):
        """A label module producing only one file (description)
        should be marked singleton (no attachments)."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        # Set the minimum required attributes
        # Call the helper directly
        files_for_module = [
            _make_file('Privacy.md', modname='label',
                       content_type='description'),
        ]
        has_attachments = rb._module_has_attachments(files_for_module)
        assert has_attachments is False, (
            f'Label with only description should be singleton, '
            f'got has_attachments={has_attachments}'
        )

    def test_label_module_with_label_files_has_attachments(self):
        """A label module with label_file attachments is NOT
        singleton — keep the module folder."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        files_for_module = [
            _make_file('Privacy.md', modname='label',
                       content_type='description'),
            _make_file('100.png', modname='label',
                       content_type='label_file'),
        ]
        has_attachments = rb._module_has_attachments(files_for_module)
        assert has_attachments is True

    def test_resource_module_with_resource_file_has_attachments(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        files_for_module = [
            _make_file('Lecture 3 slides.html', modname='resource',
                       content_type='description'),
            _make_file('Lecture 3 slides.pdf', modname='resource',
                       content_type='resource_file'),
        ]
        has_attachments = rb._module_has_attachments(files_for_module)
        assert has_attachments is True

    def test_resource_module_description_only_no_attachments(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        files_for_module = [
            _make_file('Quiz intro', modname='resource',
                       content_type='description'),
        ]
        has_attachments = rb._module_has_attachments(files_for_module)
        assert has_attachments is False

    def test_assign_with_assign_files_has_attachments(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        files_for_module = [
            _make_file('Lab 1.html', modname='assign',
                       content_type='description'),
            _make_file('submission_template.docx', modname='assign',
                       content_type='assign_file'),
        ]
        has_attachments = rb._module_has_attachments(files_for_module)
        assert has_attachments is True

    def test_cookie_mod_module_has_attachments(self):
        """A Kaltura cookie_mod module always has the video file
        (cookie_mod type is the attachment itself)."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        rb = ResultBuilder.__new__(ResultBuilder)
        files_for_module = [
            _make_file('Lecture.mp4', modname='cookie_mod-kalvidres',
                       content_type='cookie_mod'),
            _make_file('Lecture_notes.md', modname='cookie_mod-kalvidres',
                       content_type='description'),
        ]
        has_attachments = rb._module_has_attachments(files_for_module)
        assert has_attachments is True


# =========================================================================
# Helpers
# =========================================================================
def _make_file(filename, *, modname, content_type):
    """Build a File with the minimum fields needed for module
    has_attachments detection.
    """
    from moodle_dl.types import File
    return File(
        module_id=100,
        section_name='Week 3',
        section_id=1,
        module_name='m',
        content_filepath='/',
        content_filename=filename,
        content_fileurl='https://example.com/x',
        content_filesize=100,
        content_timemodified=0,
        module_modname=modname,
        content_type=content_type,
        content_isexternalfile=False,
    )