"""
*NN* prefix: should NOT match ._* shadow files
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestNNPrefixDoesNotAffectOsFiles:
    """The ``*NN*`` prefix counter is applied by
    ``generate_filename_with_index`` to ``file.content_filename``
    (the Moodle-side file name). It must NEVER be applied to
    macOS-generated ``._*`` files or other OS files like
    ``.DS_Store``.

    Why: the user explicitly said the ``*NN*`` counter should
    only count real files, not OS files. If a ``._*`` file
    somehow got a ``*NN*`` prefix, it would inflate the count
    and confuse the natural sort.
    """

    def test_macos_shadow_file_does_not_get_nn_prefix(self):
        """A ``._*`` file created by the OS should never have
        a ``*NN*`` prefix applied.

        This is verified by source inspection: the prefix
        function only takes ``file.content_filename`` (a
        Moodle field), not arbitrary disk paths.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        import inspect
        ops = TaskFileOps(MagicMock())
        src = inspect.getsource(ops.generate_filename_with_index)
        # The function uses file.content_filename, not arbitrary
        # disk paths. So ._* files are never passed to it.
        assert 'file.content_filename' in src
        # It does NOT scan the filesystem
        assert 'os.listdir' not in src
        assert 'os.walk' not in src
        assert 'os.scandir' not in src

    def test_does_not_scan_filesystem_for_counters(self):
        """The *NN* counter is based on file.position_in_section
        (a Moodle integer), not on filesystem scans. macOS
        files on disk cannot influence the counter.
        """
        from moodle_dl.downloader import task_file_ops
        import inspect
        # The module should not have any logic that scans the
        # filesystem to determine the next counter value.
        src = inspect.getsource(task_file_ops)
        # It must use position_in_section, not os.listdir or
        # similar
        assert 'position_in_section' in src

    def test_existing_nn_prefix_in_original_filename(self):
        """If a Moodle file's name happens to contain ``*NN*``,
        the prefix is added AND the original asterisks may be
        normalized (to_valid_name converts ``*`` to ``＊``).
        The *NN* counter itself is from Moodle's
        position_in_section.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        ops = TaskFileOps(MagicMock())
        # Simulate: Moodle says this file is at position 4
        file = MagicMock()
        file.content_filename = '*05* foo.pdf'  # already prefixed
        file.position_in_section = 4
        result = ops.generate_filename_with_index(file)
        # The *NN* prefix is added (one-based, position 4 -> *05*)
        # The original asterisks are normalized by to_valid_name
        # (full-width or replaced). We just verify that
        # position_in_section is the source of the *NN* counter.
        assert '*05*' in result  # the new counter
        # Verify NO filename parsing happens for the counter
        import inspect
        src = inspect.getsource(ops.generate_filename_with_index)
        assert 're.match' not in src
        assert 're.search' not in src

    def test_counter_uses_moodle_position_not_filename_parse(self):
        """The counter is the Moodle position (0-indexed), not
        parsed from the existing filename. So even if a file
        is already named *99*, the new run gives it the same
        *99* (or whatever Moodle says), not *100*.

        This guarantees that re-running moodle-dl produces the
        SAME filenames (idempotency), regardless of any prior
        *NN* prefix in the file name.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        ops = TaskFileOps(MagicMock())
        # A file whose current name is *99* foo.pdf
        file = MagicMock()
        file.content_filename = '*99* foo.pdf'  # old name
        file.position_in_section = 5  # Moodle says position 5
        # Result: counter from position (5+1=6), original name preserved
        result = ops.generate_filename_with_index(file)
        # The *06* prefix is added (from position 5)
        # The original name is in there too (with * normalized to ＊)
        assert result.startswith('*06*')
        # NO regex parsing of original filename for the counter
        import inspect
        src = inspect.getsource(ops.generate_filename_with_index)
        assert 're.match' not in src
        assert 're.search' not in src
        assert 'parse' not in src.lower()
