# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from moodle_dl.moodle.result_builder import ResultBuilder
from moodle_dl.types import MoodleURL


class TestResultBuilderPluginfileAndBookStructure(unittest.TestCase):
    def setUp(self):
        moodle_url = MoodleURL(use_http=False, domain="keats.kcl.ac.uk", path="/")
        self.builder = ResultBuilder(
            moodle_url=moodle_url,
            version=2024010100,
            mod_plurals={},
            token="test_token_123",
        )

    def test_pluginfile_fix_uses_config_token_and_base_url(self):
        module_contents = [
            {
                "type": "file",
                "filename": "lecture.pdf",
                "filepath": "/",
                "fileurl": "https://keats.kcl.ac.uk/pluginfile.php/123/mod_resource/content/1/lecture.pdf",
                "filesize": 100,
                "timemodified": 123,
            }
        ]

        with patch("moodle_dl.moodle.result_builder.UrlHelper.fix_pluginfile_url") as mock_fix:
            mock_fix.return_value = "https://keats.kcl.ac.uk/webservice/pluginfile.php/123/mod_resource/content/1/lecture.pdf?token=test_token_123&offline=1"
            files = self.builder._handle_files(
                module_contents,
                section_id=1,
                section_name="General",
                module_id=10,
                module_name="Lecture",
                module_modname="resource",
            )

        self.assertEqual(len(files), 1)
        mock_fix.assert_called_once_with(
            module_contents[0]["fileurl"],
            token="test_token_123",
            moodle_base_url="https://keats.kcl.ac.uk/",
        )
        self.assertIn("/webservice/pluginfile.php", files[0].content_fileurl)

    def test_book_structure_with_null_fileurl_is_normalized_to_content_file(self):
        module_contents = [
            {
                "type": "file",  # Some servers return "file" even for structure
                "filename": "structure",
                "filepath": "/",
                "fileurl": None,
                "content": '[{"title":"Chapter 1"}]',
                "filesize": 0,
                "timemodified": 123,
            }
        ]

        files = self.builder._handle_files(
            module_contents,
            section_id=1,
            section_name="Module Guide",
            module_id=8834172,
            module_name="Key Module Information",
            module_modname="book",
        )

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].content_type, "content")
        self.assertEqual(files[0].content_fileurl, "")
        self.assertEqual(files[0].content, '[{"title":"Chapter 1"}]')


if __name__ == "__main__":
    unittest.main()
