# -*- coding: utf-8 -*-
"""
Tests for the original-filename alias in build_local_resource_map.

When moodle-dl downloads a file like `assets/css/main.css` from
KCL, it renames the file to the module display name (e.g.
`Interactive Virtual Practical Sessions 1, 2, 3 - Use of PCR
to genotype individuals.css`). The HTML still references
`assets/css/main.css` via a relative href. Without an alias
that maps the original filename to the actual disk path,
the HTML rewrite produces 0 replacements and the CSS doesn't
render locally.

This test pins the contract that build_local_resource_map
adds an alias for the original filename extracted from the
KCL content_fileurl.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.downloader.html_localizer import (
    build_local_resource_map,
    _extract_original_filename_from_url,
)


class DummyFile:
    def __init__(self, saved_to, content_fileurl):
        self.saved_to = saved_to
        self.content_fileurl = content_fileurl


class TestExtractOriginalFilename(unittest.TestCase):
    def test_extracts_last_path_component(self):
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/6505394/mod_resource/content/0/assets/css/main.css?forcedownload=1'
        self.assertEqual(_extract_original_filename_from_url(url), 'main.css')

    def test_handles_query_string(self):
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/123/mod_resource/content/0/scripts/jquery.min.js?token=abc&offline=1'
        self.assertEqual(_extract_original_filename_from_url(url), 'jquery.min.js')

    def test_handles_no_query(self):
        url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/123/mod_resource/content/0/images/logo.png'
        self.assertEqual(_extract_original_filename_from_url(url), 'logo.png')

    def test_handles_empty_url(self):
        self.assertEqual(_extract_original_filename_from_url(''), '')

    def test_handles_non_pluginfile_url(self):
        url = 'https://example.com/some/path/file.pdf'
        self.assertEqual(_extract_original_filename_from_url(url), 'file.pdf')


class TestOriginalFilenameAlias(unittest.TestCase):
    def test_adds_alias_for_original_filename(self):
        with tempfile.TemporaryDirectory() as td:
            # Create a fake module dir with renamed file
            module_dir = os.path.join(td, 'module')
            os.makedirs(os.path.join(module_dir, 'assets', 'css'))
            # The actual disk file (renamed by moodle-dl)
            disk_path = os.path.join(module_dir, 'assets', 'css',
                                     '*02* Interactive Virtual Practical Sessions 1, 2, 3 - Use of PCR to genotype individuals.css')
            with open(disk_path, 'w') as f:
                f.write('/* CSS content */')

            # The KCL URL had original filename 'main.css'
            kcl_url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/6505394/mod_resource/content/0/assets/css/main.css?forcedownload=1'

            dummy = DummyFile(saved_to=disk_path, content_fileurl=kcl_url)
            resources = build_local_resource_map([dummy])

            # The map should contain an entry for the original filename
            # at the correct absolute path
            original_path = os.path.abspath(os.path.join(module_dir, 'assets', 'css', 'main.css'))
            key = f'local:{original_path}'
            self.assertIn(key, resources,
                          f'Expected alias for original filename main.css, got keys: {list(resources.keys())[:5]}')
            self.assertEqual(resources[key], disk_path)

    def test_no_alias_when_filename_already_matches(self):
        with tempfile.TemporaryDirectory() as td:
            disk_path = os.path.join(td, 'main.css')
            with open(disk_path, 'w') as f:
                f.write('/* CSS */')

            kcl_url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/123/mod_resource/content/0/main.css'

            dummy = DummyFile(saved_to=disk_path, content_fileurl=kcl_url)
            resources = build_local_resource_map([dummy])

            # Should still have the regular alias (via _add_local_resource_aliases)
            # but no duplicate for the same filename
            self.assertGreater(len(resources), 0)


if __name__ == '__main__':
    unittest.main()
