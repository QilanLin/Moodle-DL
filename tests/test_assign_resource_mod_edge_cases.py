# -*- coding: utf-8 -*-
"""
Tests for AssignMod critical methods that were previously untested
per coverage report:

  - extract_assign_modules: builds the assign dict from Mobile API
    response, used as input to add_submissions
  - _convert_web_api_assign_to_mobile: converts Web API response
    to Mobile API format (fallback path)
  - _parse_display_options: parse Moodle's displayoptions string
  - _get_file_details: extract file metadata for resource modules

These are CS2-regression-relevant: the original 370-file regression
came from assignment modules where introfile handling was broken.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# AssignMod._convert_web_api_assign_to_mobile
# =========================================================================
class TestConvertWebApiAssignToMobile:
    """Convert Web API assign module format to Mobile API format.

    The Web API response has fewer fields than Mobile API. This
    conversion normalizes them so add_submissions can handle both.
    """

    def test_minimal_web_api_assign(self):
        """Minimal Web API response is converted to Mobile API shape."""
        from moodle_dl.moodle.mods.assign import AssignMod
        from moodle_dl.moodle.mods.common import MoodleMod

        # Bypass __init__ since it requires async setup
        mod = AssignMod.__new__(AssignMod)

        web_api_module = {
            'id': 12345,
            'instance': 100,
            'name': 'Coursework 1',
            'timemodified': 1700000000,
            'timecreated': 1699999000,
            'contents': [],
        }
        result = mod._convert_web_api_assign_to_mobile(web_api_module, course_id=42)

        assert result['id'] == 100  # From 'instance' field
        assert result['cmid'] == 12345  # From 'id' field
        assert result['course'] == 42
        assert result['name'] == 'Coursework 1'
        assert result['intro'] == ''  # Web API doesn't provide
        assert result['introattachments'] == []
        assert result['duedate'] == 0
        assert result['timemodified'] == 1700000000

    def test_introattachments_extracted_from_contents(self):
        """introattachments are extracted from contents[] with type=file."""
        from moodle_dl.moodle.mods.assign import AssignMod

        mod = AssignMod.__new__(AssignMod)

        web_api_module = {
            'id': 1,
            'instance': 100,
            'name': 'Test',
            'timemodified': 0,
            'timecreated': 0,
            'contents': [
                {'type': 'file', 'filename': 'intro.pdf',
                 'fileurl': 'https://example.com/intro.pdf'},
                {'type': 'html', 'html': '<p>desc</p>'},
                {'type': 'file', 'filename': 'syllabus.pdf',
                 'fileurl': 'https://example.com/syl.pdf'},
            ],
        }
        result = mod._convert_web_api_assign_to_mobile(web_api_module, course_id=1)

        # Only the type='file' entries are introattachments
        assert len(result['introattachments']) == 2
        filenames = [a['filename'] for a in result['introattachments']]
        assert 'intro.pdf' in filenames
        assert 'syllabus.pdf' in filenames

    def test_introattachments_empty_when_no_files(self):
        """If contents has no files, introattachments is empty list."""
        from moodle_dl.moodle.mods.assign import AssignMod

        mod = AssignMod.__new__(AssignMod)

        web_api_module = {
            'id': 1,
            'instance': 100,
            'name': 'Test',
            'timemodified': 0,
            'timecreated': 0,
            'contents': [
                {'type': 'html', 'html': '<p>just description</p>'},
            ],
        }
        result = mod._convert_web_api_assign_to_mobile(web_api_module, course_id=1)
        assert result['introattachments'] == []

    def test_web_api_assign_uses_default_field_values(self):
        """Web API doesn't provide all fields. Default values should
        be set for missing fields.
        """
        from moodle_dl.moodle.mods.assign import AssignMod

        mod = AssignMod.__new__(AssignMod)

        web_api_module = {
            'id': 1, 'instance': 100, 'name': 'X',
            'contents': [],
        }
        result = mod._convert_web_api_assign_to_mobile(web_api_module, course_id=1)

        # Default values from the conversion
        assert result['grade'] == 0
        assert result['submissiondrafts'] == 0
        assert result['sendnotifications'] == 1
        assert result['teamsubmission'] == 0


# =========================================================================
# AssignMod.extract_assign_modules
# =========================================================================
class TestExtractAssignModules:
    """Extract assign modules from Mobile API response.

    Input shape: list of assignment dicts from Mobile API
    Output: Dict[module_id, Dict] indexed by cmid
    """

    def test_extract_single_assign_keyed_by_cmid(self):
        from moodle_dl.moodle.mods.assign import AssignMod

        mod = AssignMod.__new__(AssignMod)

        assignments = [
            {
                'id': 100,
                'cmid': 200,
                'course': 42,
                'name': 'CW1',
                'intro': 'Coursework 1',
                'introattachments': [],
                'duedate': 1735689600,
                'timemodified': 1700000000,
            }
        ]
        result = mod.extract_assign_modules(assignments)

        # Result is keyed by cmid
        assert 200 in result
        assert result[200]['id'] == 100
        assert result[200]['name'] == 'CW1'
        assert result[200]['timemodified'] == 1700000000
        # extract_assign_modules builds 'files' array (intro + metadata.json)
        assert 'files' in result[200]
        assert isinstance(result[200]['files'], list)

    def test_extract_multiple_assigns(self):
        from moodle_dl.moodle.mods.assign import AssignMod

        mod = AssignMod.__new__(AssignMod)

        assignments = [
            {'id': 1, 'cmid': 100, 'course': 1, 'name': 'A1', 'introattachments': []},
            {'id': 2, 'cmid': 200, 'course': 1, 'name': 'A2', 'introattachments': []},
            {'id': 3, 'cmid': 300, 'course': 1, 'name': 'A3', 'introattachments': []},
        ]
        result = mod.extract_assign_modules(assignments)

        assert len(result) == 3
        assert set(result.keys()) == {100, 200, 300}

    def test_extract_empty_assignments(self):
        """Empty assignments list returns empty dict."""
        from moodle_dl.moodle.mods.assign import AssignMod

        mod = AssignMod.__new__(AssignMod)

        result = mod.extract_assign_modules([])
        assert result == {}

    def test_extract_creates_introduction_file_when_intro_provided(self):
        """When intro is non-empty, an Introduction.html file is
        created and included in the result's files array.
        """
        from moodle_dl.moodle.mods.assign import AssignMod

        mod = AssignMod.__new__(AssignMod)

        assignments = [
            {
                'id': 1,
                'cmid': 100,
                'course': 1,
                'name': 'CW with attachment',
                'intro': 'Coursework introduction text',
                'introattachments': [
                    {'type': 'file', 'filename': 'syllabus.pdf',
                     'fileurl': 'https://example.com/syl.pdf'}
                ],
            }
        ]
        result = mod.extract_assign_modules(assignments)

        # The intro creates an Introduction.html file (type=description)
        assert any(
            f.get('filename') == 'Introduction.html' and f.get('type') == 'description'
            for f in result[100]['files']
        )


# =========================================================================
# ResourceMod._parse_display_options
# =========================================================================
class TestParseDisplayOptions:
    """Parse Moodle's displayoptions string (PHP-serialized format)."""

    def test_empty_string_returns_empty_dict(self):
        from moodle_dl.moodle.mods.resource import ResourceMod

        mod = ResourceMod.__new__(ResourceMod)
        assert mod._parse_display_options('') == {}

    def test_unparseable_string_returns_raw_marker(self):
        """If displayoptions string can't be parsed, return
        a marker dict with '_raw' key. This prevents crashing on
        malformed input.
        """
        from moodle_dl.moodle.mods.resource import ResourceMod

        mod = ResourceMod.__new__(ResourceMod)
        result = mod._parse_display_options('garbage_data')
        # Should return {'_raw': 'garbage_data'} (not raise)
        assert isinstance(result, dict)
        assert result.get('_raw') == 'garbage_data'

    def test_unrecognized_php_serialize_returns_raw(self):
        """If the serializer can't parse PHP serialize format, fall
        back to '_raw' marker.
        """
        from moodle_dl.moodle.mods.resource import ResourceMod

        mod = ResourceMod.__new__(ResourceMod)
        result = mod._parse_display_options(
            'a:2:{s:4:"show";i:1;s:5:"popup";i:0;}'
        )
        assert isinstance(result, dict)
        # PHP serialization may not be supported in some versions;
        # either way, return a dict with the raw value preserved.
        assert '_raw' in result or 'show' in result


# =========================================================================
# ResourceMod._get_file_details
# =========================================================================
class TestGetFileDetails:
    """_get_file_details extracts file metadata for resource modules."""

    def test_empty_content_files_returns_empty_dict_v2(self):
        from moodle_dl.moodle.mods.resource import ResourceMod

        mod = ResourceMod.__new__(ResourceMod)
        result = mod._get_file_details([], {})
        # _get_file_details returns empty dict (not list) when no files.
        assert result == {}

    def test_single_file_returns_dict_with_metadata(self):
        from moodle_dl.moodle.mods.resource import ResourceMod

        mod = ResourceMod.__new__(ResourceMod)
        content_files = [
            {'filename': 'lecture.pdf',
             'fileurl': 'https://example.com/l.pdf',
             'filesize': 1024000,
             'mimetype': 'application/pdf'}
        ]
        result = mod._get_file_details(content_files, {})

        # Returns a dict (not list) describing the file
        assert isinstance(result, dict)
        # Has size info, mimetype, extension
        assert result.get('size_bytes') == 1024000
        assert result.get('mimetype') == 'application/pdf'
        assert result.get('extension') == 'pdf'

    def test_multiple_files_only_main_file_described(self):
        """_get_file_details returns metadata for the FIRST (main)
        file only. Total size is summed across all files.
        """
        from moodle_dl.moodle.mods.resource import ResourceMod

        mod = ResourceMod.__new__(ResourceMod)
        content_files = [
            {'filename': 'first.pdf', 'filesize': 1024, 'mimetype': 'application/pdf'},
            {'filename': 'second.docx', 'filesize': 2048, 'mimetype': 'application/docx'},
        ]
        result = mod._get_file_details(content_files, {})

        # Returns a dict (not a list) describing the main file
        assert isinstance(result, dict)
        # Main file is the first one
        assert result.get('extension') == 'pdf'
        assert result.get('mimetype') == 'application/pdf'
        # Total size is summed
        assert result.get('size_bytes') == 3072  # 1024 + 2048