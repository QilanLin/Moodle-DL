# -*- coding: utf-8 -*-
"""
Tests for the on-disk path repair tool.

The bug: a 'resource' / 'page' / 'url' / 'label' module file
with content_filepath='/' or '/sub/dir/' was saved at
the section root (without the module_name subfolder).
Result: any HTML referencing relative paths like
'assets/css/main.css' broke, because the browser resolved
those paths relative to the HTML's location, and the asset
was NOT in the expected assets/ subfolder.

The tool moves already-downloaded files from their buggy
flat location to the correct nested location under
<ws>/<course>/<section>/<module_name>/, and rewrites any
HTML <link>/<script>/<img> references whose target was at
the old (buggy) location so they point to the new location.

This is a U-disk-only operation. The original U-disk
contents are NEVER deleted until the move has been verified
and the DB updated.
"""
import os
import sqlite3
import unittest
from unittest.mock import MagicMock

from moodle_dl.downloader.task_path_repair import (
    compute_correct_saved_to,
    move_buggy_files,
    rewrite_html_references,
    find_buggy_files,
)


def make_workspace(tmp_path):
    """Create a fake workspace with the bug-affected file layout."""
    ws = tmp_path / 'ws'
    course = ws / '4MBBS101 Molecular & Cell Genetics 20~21'
    section = course / 'Practical Sessions Parts 1, 2 and 3'
    section.mkdir(parents=True)
    # Buggy location: a CSS file at the section root.
    css = section / '*189* main.css'
    css.write_text('body{color:red}', encoding='utf-8')
    # The HTML file at the same section root.
    html = section / '*01* Interactive Virtual Practical Sessions 1, 2, 3 - Use of PCR to genotype individuals.html'
    html.write_text(
        '<html><head><link rel="stylesheet" href="assets/css/main.css"></head></html>',
        encoding='utf-8',
    )
    return ws, course, section, html, css


class TestComputeCorrectSavedTo(unittest.TestCase):
    """Compute the correct path that gen_path (with the fix) would
    have produced for a buggy-saved file."""

    def test_root_filepath_resource_module(self):
        ws = '/tmp/ws'
        course_fullname = '4MBBS101 Molecular & Cell Genetics 20~21'
        section_name = 'Practical Sessions Parts 1, 2 and 3'
        module_name = (
            'Interactive Virtual Practical Sessions 1, 2, 3 - '
            'Use of PCR to genotype individuals'
        )
        content_filepath = '/'
        # Without a real File (the production helper takes
        # all the relevant string fields).
        result = compute_correct_saved_to(
            ws=ws,
            course_fullname=course_fullname,
            section_name=section_name,
            module_name=module_name,
            content_filepath=content_filepath,
            # The on-disk buggy basename — we treat it as a
            # candidate to look up under the corrected path.
            buggy_filename='*189* main.css',
        )
        self.assertIn('Interactive Virtual Practical Sessions', result)
        self.assertIn('*189* main.css', result)
        # And critically: NOT just the section root, it MUST
        # include the module_name as a directory.
        expected_dir = os.path.join(ws, course_fullname, section_name)
        self.assertNotEqual(
            os.path.dirname(result),
            expected_dir,
            f'expected dir to be module folder, got {expected_dir!r}\n'
            f'actual dir:    {os.path.dirname(result)!r}\n'
            f'full result:   {result!r}',
        )


class TestMoveBuggyFiles(unittest.TestCase):
    """Move a buggy file from the section root to the correct
    module-folder location."""

    def test_move_css_to_module_folder(self):
        import shutil
        from pathlib import Path
        # Set up
        workdir = Path('/tmp/moodle_repair_test')
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir()
        ws, course, section, html, css = make_workspace(workdir)

        # The move
        moves = move_buggy_files(
            ws=str(ws),
            course_fullname='4MBBS101 Molecular & Cell Genetics 20~21',
            section_name='Practical Sessions Parts 1, 2 and 3',
            module_name=(
                'Interactive Virtual Practical Sessions 1, 2, 3 - '
                'Use of PCR to genotype individuals'
            ),
            buggy_filenames_with_subdir=[('*189* main.css', 'assets/css')],
        )
        # We should have made one move.
        self.assertEqual(len(moves), 1)
        old, new, subdir = moves[0]
        self.assertFalse(os.path.exists(old))  # moved
        self.assertTrue(os.path.exists(new))
        # Content preserved.
        with open(new) as f:
            self.assertEqual(f.read(), 'body{color:red}')
        # The new path should be under the module folder with
        # the assets/css/ subdir preserved.
        self.assertIn('Interactive Virtual Practical Sessions', new)
        self.assertIn('assets/css', new)


class TestRewriteHtmlReferences(unittest.TestCase):
    """Rewrite the HTML's relative href/src to point to the
    new module-folder location."""

    def test_rewrite_relative_assets_link(self):
        from pathlib import Path
        workdir = Path('/tmp/moodle_repair_test_2')
        if workdir.exists():
            import shutil
            shutil.rmtree(workdir)
        workdir.mkdir()
        ws, course, section, html, css = make_workspace(workdir)

        # After the file move, the new path is:
        new_css = (
            section / 'Interactive Virtual Practical Sessions 1, 2, 3 - '
            'Use of PCR to genotype individuals' / '*189* main.css'
        )

        # The HTML references "assets/css/main.css" — we need
        # to rewrite it to the relative path to the new location.
        new_relative = os.path.relpath(new_css, section)
        rewrite_html_references(
            html_path=str(html),
            old_relative_paths=['assets/css/main.css'],
            new_relative_paths=[new_relative],
        )
        with open(html) as f:
            content = f.read()
        # The old reference should be gone.
        self.assertNotIn('"assets/css/main.css"', content)
        # The new reference should be in.
        self.assertIn('*189* main.css', content)


class TestFindBuggyFiles(unittest.TestCase):
    """Identify buggy files in a workspace by comparing
    saved_to (in the DB) to what compute_correct_saved_to
    would have produced."""

    def test_find_returns_buggy_resource_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # Create a fake DB
            db_path = os.path.join(td, 'moodle_state.db')
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE files (
                  file_id INTEGER PRIMARY KEY,
                  course_id INTEGER,
                  course_fullname TEXT,
                  section_name TEXT,
                  section_id INTEGER,
                  module_id INTEGER,
                  module_name TEXT,
                  module_modname TEXT,
                  content_filepath TEXT,
                  content_filename TEXT,
                  download_status TEXT,
                  saved_to TEXT
                )
            """)
            # Insert a buggy resource file
            cur.execute("""
                INSERT INTO files
                (file_id, course_id, course_fullname, section_name,
                 module_id, module_name, module_modname,
                 content_filepath, content_filename,
                 download_status, saved_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                1, 86122, '4MBBS101 Molecular & Cell Genetics 20~21',
                'Practical Sessions Parts 1, 2 and 3',
                4600243, 'Interactive Virtual Practical Sessions 1, 2, 3 - Use of PCR',
                'resource', '/', 'main.css', 'success',
                '/Volumes/Untitled/.../Practical Sessions Parts 1, 2 and 3/*189* main.css',
            ))
            conn.commit()
            conn.close()

            conn = sqlite3.connect(db_path)
            buggy = find_buggy_files(conn)
            conn.close()
            self.assertEqual(len(buggy), 1)
            f = buggy[0]
            self.assertEqual(f['file_id'], 1)
            self.assertEqual(f['module_modname'], 'resource')
            self.assertEqual(f['course_fullname'], '4MBBS101 Molecular & Cell Genetics 20~21')


if __name__ == '__main__':
    unittest.main()
