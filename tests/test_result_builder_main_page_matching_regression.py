# -*- coding: utf-8 -*-
import unittest

from moodle_dl.moodle.result_builder import ResultBuilder
from moodle_dl.types import MoodleURL


class TestResultBuilderMainPageMatchingRegression(unittest.TestCase):
    def setUp(self):
        moodle_url = MoodleURL(use_http=False, domain="keats.kcl.ac.uk", path="/")
        self.builder = ResultBuilder(
            moodle_url=moodle_url,
            version=2024010100,
            mod_plurals={"resource": "resources"},
            token="token_abc",
        )
        self.fetched_mods = {
            "resource": {
                9243725: {
                    "id": 9243725,
                    "name": "Expected Behaviour",
                    "files": [
                        {
                            "type": "file",
                            "filename": "Expected Behaviour.pdf",
                            "filepath": "/",
                            "fileurl": "https://keats.kcl.ac.uk/files/expected-behaviour.pdf",
                            "filesize": 123,
                            "timemodified": 1,
                        }
                    ],
                }
            }
        }

    def test_module_stays_in_real_section_when_core_contents_include_modules(self):
        course_sections = [
            {
                "id": 2206861,
                "name": "Module Overview",
                "modules": [
                    {
                        "id": 9243725,
                        "name": "Expected Behaviour",
                        "modname": "resource",
                        "url": "https://keats.kcl.ac.uk/mod/resource/view.php?id=9243725",
                        "contents": [],
                    }
                ],
            }
        ]

        files = self.builder.get_files_in_sections(course_sections, self.fetched_mods)

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].section_name, "Module Overview")
        self.assertFalse(
            any(file.section_name == "Resources not on main page" for file in files)
        )

    def test_module_falls_back_when_core_contents_omit_modules(self):
        course_sections = [{"id": 2206861, "name": "Module Overview", "modules": []}]

        files = self.builder.get_files_in_sections(course_sections, self.fetched_mods)

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].section_name, "Resources not on main page")


if __name__ == "__main__":
    unittest.main()
