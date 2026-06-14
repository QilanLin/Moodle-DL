# -*- coding: utf-8 -*-
"""
Tests for the gen_path / saved_to / html-resource-rewrite pipeline.

The user's bug (and what we now want to fix):
  - HTML files reference "assets/css/main.css" + "assets/js/*.js"
  - All those resource files are content_filepath="/"
    (i.e. moodle-dl parsed the HTML and the resources are
    inline at the page level, not in a sub-path)
  - Module_modname = 'resource' (the page is a resource
    module; the assets are inner files of the resource)
  - gen_path() currently routes these to path_of_file() (line 466
    in downloader/task.py) which omits module_name from the path.
  - So saved_to ends up as <ws>/<course>/<section>/*189* main.css
    (the section root, not under the module folder)
  - This is correct for the file system (the file IS there)
    but the HTML's relative href="assets/css/main.css" then
    resolves to <ws>/<course>/<section>/assets/css/main.css
    which doesn't exist on disk.

After the fix, gen_path() should route resource-module files
with content_filepath="/" through path_of_file_in_module(),
so the file lands in <ws>/<course>/<section>/<module_name>/
and the HTML's "assets/css/main.css" relative href resolves to
<ws>/<course>/<section>/<module_name>/assets/css/main.css
which DOES exist (we put it there).

Pin points:
  1. gen_path() for a 'resource' module file with content_filepath
     '/' should include the module_name in the resulting path
     (so assets/css/main.css relative references work).
  2. After the fix, the existing test_html_localizer's
     assets/css/main.css scenario must still pass (the
     rewrite_html_links_to_local_paths mapping is unaffected;
     it just now finds the file at a slightly different path).
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.html_localizer import (
    build_local_resource_map, rewrite_html_links_to_local_paths,
)
from moodle_dl.downloader.task import Task
from moodle_dl.downloader.task_file_ops import TaskFileOps
from moodle_dl.types import File, MoodleDlOpts, Course


def make_course():
    return Course(
        _id=86122,
        fullname='4MBBS101 Molecular & Cell Genetics 20~21',
        files=[],
    )


def make_file(content_filepath, content_filename,
              module_modname='resource', module_name=None,
              module_id=4600243):
    return File(
        module_id=module_id,
        section_name='Practical Sessions Parts 1, 2 and 3',
        section_id=1325357,
        module_name=module_name or 'Interactive Virtual Practical Sessions 1, 2, 3 - Use of PCR to genotype individuals',
        content_filepath=content_filepath,
        content_filename=content_filename,
        content_fileurl=f'https://keats.kcl.ac.uk/pluginfile.php/1/{content_filename}',
        content_filesize=1,
        content_timemodified=1,
        module_modname=module_modname,
        content_type='file',
        content_isexternalfile=False,
    )


class TestGenPathForResourceModule(unittest.TestCase):
    """Pin the gen_path() behaviour for resource-module files
    with content_filepath='/'."""

    def test_resource_module_with_root_filepath_includes_module_in_path(self):
        """A 'resource' module file with content_filepath='/'
        should be saved UNDER the module folder, not at the
        section root. This is what makes
            <link href='assets/css/main.css'>
        resolve correctly when the file is at
            <ws>/<course>/<section>/<module>/assets/css/main.css
        """
        with tempfile.TemporaryDirectory() as td:
            course = make_course()
            f = make_file('/', 'main.css')
            dest = TaskFileOps(MagicMock()).gen_path(td, course, f)
            # After the fix, the module name should be in the path
            self.assertIn(
                'Interactive Virtual Practical Sessions', dest,
                f"Module name missing in gen_path: {dest}",
            )
            # The full saved_to (with filename) should be inside
            # the module folder, not the section root
            saved_to = os.path.join(dest, '*01* main.css')
            self.assertIn(
                'Interactive Virtual Practical Sessions', saved_to,
                f"main.css would land in wrong path: {saved_to}",
            )

    def test_resource_module_with_subdir_filepath_works(self):
        """Sanity check: when content_filepath is already a
        subdirectory like '/assets/css/', gen_path() must
        also include the module folder so that the HTML's
        'assets/css/main.css' relative href resolves
        correctly. This is the SAME fix the resource_module
        case needs."""
        with tempfile.TemporaryDirectory() as td:
            course = make_course()
            f = make_file('/assets/css/', 'main.css')
            dest = TaskFileOps(MagicMock()).gen_path(td, course, f)
            self.assertIn('assets/css', dest)
            self.assertIn(
                'Interactive Virtual Practical Sessions', dest,
                'Subdir path should also include module name',
            )

    def test_book_module_with_root_filepath_works(self):
        """'book' module is already on the 'use path_of_file_in_module' path.
        Pin that this still works."""
        with tempfile.TemporaryDirectory() as td:
            course = make_course()
            f = make_file('/', 'chapter1.html', module_modname='book')
            dest = TaskFileOps(MagicMock()).gen_path(td, course, f)
            self.assertIn('Interactive Virtual Practical Sessions', dest)


class TestResourceHtmlRewriting(unittest.TestCase):
    """End-to-end: simulate the full moodle-dl save → HTML rewrite
    pipeline for a resource module with sub-assets. After the fix,
    the HTML's assets/css/main.css reference should resolve
    correctly through rewrite_html_links_to_local_paths()."""

    def test_resource_html_assets_rewrite_correctly(self):
        with tempfile.TemporaryDirectory() as td:
            ws = os.path.join(td, 'workspace')
            course = make_course()
            # The HTML file in the module's content (root level).
            html = make_file('/', 'index.html', module_name='Resource')
            # The CSS/JS/image are referenced from the HTML with
            # relative paths like "assets/css/main.css". For rewrite
            # to work via canonical_resource_url, the file URLs in
            # the DB must match what the HTML references — they
            # need to be absolute moodle pluginfile URLs.
            #
            # In real moodle-dl, result_builder.py parses the HTML
            # and converts each "assets/css/main.css" reference to
            # a full pluginfile URL with a path token before saving
            # the file. We mimic that here.
            css_url = (
                'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/resource/css/main.css'
                '?token=secret&forcedownload=1'
            )
            js_url = (
                'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/resource/js/jquery.min.js'
                '?token=secret'
            )
            img_url = (
                'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/resource/images/logo.png'
                '?token=secret'
            )

            css = make_file('/', 'main.css', module_name='Resource')
            css.content_fileurl = css_url
            js = make_file('/', 'jquery.min.js', module_name='Resource')
            js.content_fileurl = js_url
            img = make_file('/', 'logo.png', module_name='Resource')
            img.content_fileurl = img_url

            # Simulate moodle-dl saving: dest = <ws>/<course>/<section>/<module>/<filename>
            for f in [html, css, js, img]:
                d = TaskFileOps(MagicMock()).gen_path(ws, course, f)
                os.makedirs(d, exist_ok=True)
                full = os.path.join(d, f'*{1:02d}* {f.content_filename}')
                with open(full, 'w') as fp:
                    fp.write('placeholder')
                f.saved_to = full

            html_path = html.saved_to
            html_content = (
                f'<html><head>'
                f'<link rel="stylesheet" href="{css_url}">'
                f'<script src="{js_url}"></script>'
                f'</head><body>'
                f'<img src="{img_url}">'
                f'</body></html>'
            )
            with open(html_path, 'w') as fp:
                fp.write(html_content)

            files = [html, css, js, img]
            local_map = build_local_resource_map(files)
            rewritten, count = rewrite_html_links_to_local_paths(
                html_content, html_path, local_map,
            )

            # After the fix, all 3 references should rewrite
            self.assertEqual(
                count, 3,
                f'All 3 rewrites expected, got {count}\n{rewritten}',
            )
            # Verify NO references to the missing absolute pluginfile
            # URLs remain in the rewritten HTML
            self.assertNotIn(css_url, rewritten)
            self.assertNotIn(js_url, rewritten)
            self.assertNotIn(img_url, rewritten)
            # Verify the rewritten refs are now relative paths
            # pointing into the module folder where we put the files
            self.assertIn('*01* main.css', rewritten)
            self.assertIn('*01* jquery.min.js', rewritten)
            self.assertIn('*01* logo.png', rewritten)


if __name__ == '__main__':
    unittest.main()
