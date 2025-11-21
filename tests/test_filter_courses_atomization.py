# -*- coding: utf-8 -*-
"""
Unit tests for filter_courses() atomization.

Tests the refactored atomic functions and their integration:
- _load_filter_config()
- _verify_and_setup_cookies()
- _check_course_availability()
- _check_module_download_conditions()
- _check_file_filter_conditions()
- _filter_course_files()
- _should_keep_description_url()
- _filter_description_urls()
- filter_courses() integration
"""

import unittest
from unittest.mock import MagicMock, patch, call
from typing import List

from moodle_dl.types import Course, File
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.config import ConfigHelper


class TestLoadFilterConfig(unittest.TestCase):
    """Tests for _load_filter_config() function."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = MagicMock(spec=ConfigHelper)
    
    def test_load_filter_config_all_values(self):
        """Test loading all filter configuration values."""
        # Setup
        self.config.get_download_course_ids.return_value = [1, 2, 3]
        self.config.get_dont_download_course_ids.return_value = [4, 5]
        self.config.get_download_public_course_ids.return_value = [6]
        self.config.get_download_descriptions.return_value = True
        self.config.get_download_links_in_descriptions.return_value = False
        self.config.get_exclude_file_extensions.return_value = ['exe', 'dll']
        self.config.get_max_file_size.return_value = 1000000
        self.config.has_property.side_effect = lambda prop: prop == 'download_course_ids'
        
        # Execute
        result = MoodleService._load_filter_config(self.config)
        
        # Verify
        self.assertEqual(result['download_course_ids'], [1, 2, 3])
        self.assertEqual(result['dont_download_course_ids'], [4, 5])
        self.assertEqual(result['download_public_course_ids'], [6])
        self.assertTrue(result['download_descriptions'])
        self.assertFalse(result['download_links_in_descriptions'])
        self.assertEqual(result['exclude_file_extensions'], ['exe', 'dll'])
        self.assertEqual(result['max_file_size'], 1000000)
        self.assertTrue(result['use_whitelist'])

    def test_load_filter_config_whitelist_mode(self):
        """Test that use_whitelist is True when download_course_ids property exists."""
        self.config.has_property.side_effect = lambda prop: prop == 'download_course_ids'
        
        result = MoodleService._load_filter_config(self.config)
        self.assertTrue(result['use_whitelist'])

    def test_load_filter_config_blacklist_mode(self):
        """Test that use_whitelist is False when dont_download_course_ids property exists."""
        self.config.has_property.side_effect = lambda prop: prop == 'dont_download_course_ids'
        
        result = MoodleService._load_filter_config(self.config)
        self.assertFalse(result['use_whitelist'])

    def test_load_filter_config_no_mode(self):
        """Test that use_whitelist is None when neither property exists."""
        self.config.has_property.return_value = False
        
        result = MoodleService._load_filter_config(self.config)
        self.assertIsNone(result['use_whitelist'])


class TestVerifyAndSetupCookies(unittest.TestCase):
    """Tests for _verify_and_setup_cookies() function."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = MagicMock(spec=ConfigHelper)
    
    @patch('moodle_dl.moodle.moodle_service.logging')
    def test_no_cookie_handler_returns_config_value(self, mock_logging):
        """Test that function returns config value when no cookie handler provided."""
        self.config.get_download_also_with_cookie.return_value = True
        
        result = MoodleService._verify_and_setup_cookies(self.config, None)
        
        self.assertTrue(result)

    @patch('moodle_dl.moodle.moodle_service.logging')
    def test_valid_cookies_returns_true(self, mock_logging):
        """Test that valid cookies result in True."""
        self.config.get_download_also_with_cookie.return_value = False
        
        cookie_handler = MagicMock()
        cookie_handler.test_cookies.return_value = True
        
        result = MoodleService._verify_and_setup_cookies(self.config, cookie_handler)
        
        self.assertTrue(result)

    @patch('moodle_dl.moodle.moodle_service.logging')
    def test_invalid_cookies_returns_config_value(self, mock_logging):
        """Test that invalid cookies with download_with_cookie=False returns False."""
        self.config.get_download_also_with_cookie.return_value = False
        
        cookie_handler = MagicMock()
        cookie_handler.test_cookies.return_value = False
        
        result = MoodleService._verify_and_setup_cookies(self.config, cookie_handler)
        
        self.assertFalse(result)

    @patch('moodle_dl.moodle.moodle_service.logging')
    def test_invalid_cookies_with_flag_returns_true(self, mock_logging):
        """Test that invalid cookies with download_with_cookie=True still returns True."""
        self.config.get_download_also_with_cookie.return_value = True
        
        cookie_handler = MagicMock()
        cookie_handler.test_cookies.return_value = False
        
        result = MoodleService._verify_and_setup_cookies(self.config, cookie_handler)
        
        self.assertTrue(result)


class TestCheckCourseAvailability(unittest.TestCase):
    """Tests for _check_course_availability() function."""
    
    def test_no_courses_list_returns_true(self):
        """Test that function returns True when courses_list is None."""
        course = MagicMock(spec=Course)
        course.id = 1
        
        result = MoodleService._check_course_availability(course, None)
        
        self.assertTrue(result)

    def test_course_found_in_list(self):
        """Test that function returns True when course is found in list."""
        course = MagicMock(spec=Course)
        course.id = 1
        
        online_course = MagicMock(spec=Course)
        online_course.id = 1
        
        result = MoodleService._check_course_availability(course, [online_course])
        
        self.assertTrue(result)

    @patch('moodle_dl.moodle.moodle_service.logging')
    def test_course_not_found_returns_false(self, mock_logging):
        """Test that function returns False when course is not found."""
        course = MagicMock(spec=Course)
        course.id = 1
        
        online_course = MagicMock(spec=Course)
        online_course.id = 2
        
        result = MoodleService._check_course_availability(course, [online_course])
        
        self.assertFalse(result)

    @patch('moodle_dl.moodle.moodle_service.logging')
    def test_course_not_found_logs_warning(self, mock_logging):
        """Test that warning is logged when course not found."""
        course = MagicMock(spec=Course)
        course.id = 999
        
        online_course = MagicMock(spec=Course)
        online_course.id = 1
        
        MoodleService._check_course_availability(course, [online_course])
        
        mock_logging.warning.assert_called_once()


class TestCheckModuleDownloadConditions(unittest.TestCase):
    """Tests for _check_module_download_conditions() function."""
    
    def test_all_modules_pass_conditions(self):
        """Test when all modules pass download conditions."""
        file = MagicMock(spec=File)
        config = MagicMock(spec=ConfigHelper)
        
        mod1 = MagicMock()
        mod1.MOD_NAME = 'resource'
        mod1.download_condition.return_value = True
        
        mod2 = MagicMock()
        mod2.MOD_NAME = 'forum'
        mod2.download_condition.return_value = True
        
        result, failing_mod = MoodleService._check_module_download_conditions(
            file, [mod1, mod2], config
        )
        
        self.assertTrue(result)
        self.assertIsNone(failing_mod)

    def test_first_module_fails_condition(self):
        """Test when first module fails condition."""
        file = MagicMock(spec=File)
        config = MagicMock(spec=ConfigHelper)
        
        mod1 = MagicMock()
        mod1.MOD_NAME = 'resource'
        mod1.download_condition.return_value = False
        
        result, failing_mod = MoodleService._check_module_download_conditions(
            file, [mod1], config
        )
        
        self.assertFalse(result)
        self.assertEqual(failing_mod, 'resource')

    def test_module_conditions_stop_at_first_failure(self):
        """Test that conditions check stops at first failure."""
        file = MagicMock(spec=File)
        config = MagicMock(spec=ConfigHelper)
        
        mod1 = MagicMock()
        mod1.MOD_NAME = 'forum'
        mod1.download_condition.return_value = False
        
        mod2 = MagicMock()
        mod2.MOD_NAME = 'page'
        mod2.download_condition.return_value = True
        
        result, failing_mod = MoodleService._check_module_download_conditions(
            file, [mod1, mod2], config
        )
        
        self.assertFalse(result)
        self.assertEqual(failing_mod, 'forum')
        # Only first module should be called
        mod1.download_condition.assert_called_once()


class TestCheckFileFilterConditions(unittest.TestCase):
    """Tests for _check_file_filter_conditions() function."""
    
    def test_file_passes_all_conditions(self):
        """Test file that passes all filter conditions."""
        file = MagicMock(spec=File)
        file.content_type = 'file'
        file.module_modname = 'resource'
        file.content_filename = 'test.pdf'
        file.section_id = 1
        file.content_filesize = 5000000
        
        course = MagicMock(spec=Course)
        course.excluded_sections = []
        
        filter_config = {
            'download_descriptions': True,
            'exclude_file_extensions': ['exe'],
            'max_file_size': 10000000,
        }
        
        with patch('moodle_dl.moodle.moodle_service.determine_ext', return_value='pdf'):
            with patch('moodle_dl.moodle.moodle_service.MoodleService.should_download_section', return_value=True):
                result = MoodleService._check_file_filter_conditions(
                    file, filter_config, True, course
                )
        
        self.assertTrue(result)

    def test_file_excluded_by_extension(self):
        """Test file excluded by extension."""
        file = MagicMock(spec=File)
        file.content_type = 'file'
        file.module_modname = 'resource'
        file.content_filename = 'test.exe'
        file.section_id = 1
        file.content_filesize = 5000000
        
        course = MagicMock(spec=Course)
        course.excluded_sections = []
        
        filter_config = {
            'download_descriptions': True,
            'exclude_file_extensions': ['exe'],
            'max_file_size': 10000000,
        }
        
        with patch('moodle_dl.moodle.moodle_service.determine_ext', return_value='exe'):
            with patch('moodle_dl.moodle.moodle_service.MoodleService.should_download_section', return_value=True):
                result = MoodleService._check_file_filter_conditions(
                    file, filter_config, True, course
                )
        
        self.assertFalse(result)

    def test_file_exceeds_max_size(self):
        """Test file exceeds max file size."""
        file = MagicMock(spec=File)
        file.content_type = 'file'
        file.module_modname = 'resource'
        file.content_filename = 'test.pdf'
        file.section_id = 1
        file.content_filesize = 50000000  # 50MB
        
        course = MagicMock(spec=Course)
        course.excluded_sections = []
        
        filter_config = {
            'download_descriptions': True,
            'exclude_file_extensions': [],
            'max_file_size': 10000000,  # 10MB
        }
        
        with patch('moodle_dl.moodle.moodle_service.determine_ext', return_value='pdf'):
            with patch('moodle_dl.moodle.moodle_service.MoodleService.should_download_section', return_value=True):
                result = MoodleService._check_file_filter_conditions(
                    file, filter_config, True, course
                )
        
        self.assertFalse(result)

    def test_cookie_mod_filtered_without_cookie(self):
        """Test cookie_mod files filtered when cookies not enabled."""
        file = MagicMock(spec=File)
        file.content_type = 'file'
        file.module_modname = 'cookie_mod-kalvidres'
        file.content_filename = 'video.mp4'
        file.section_id = 1
        file.content_filesize = 100000
        
        course = MagicMock(spec=Course)
        course.excluded_sections = []
        
        filter_config = {
            'download_descriptions': True,
            'exclude_file_extensions': [],
            'max_file_size': 0,
        }
        
        with patch('moodle_dl.moodle.moodle_service.determine_ext', return_value='mp4'):
            with patch('moodle_dl.moodle.moodle_service.MoodleService.should_download_section', return_value=True):
                result = MoodleService._check_file_filter_conditions(
                    file, filter_config, False, course  # download_with_cookie = False
                )
        
        self.assertFalse(result)


class TestFilterFiles(unittest.TestCase):
    """Tests for _filter_course_files() function."""
    
    @patch('moodle_dl.moodle.moodle_service.logging')
    def test_filter_course_files_basic(self, mock_logging):
        """Test basic course file filtering."""
        # Create test files
        file1 = MagicMock(spec=File)
        file1.content_type = 'file'
        file1.module_modname = 'resource'
        file1.content_filename = 'test1.pdf'
        file1.section_id = 1
        file1.content_filesize = 5000
        
        file2 = MagicMock(spec=File)
        file2.content_type = 'file'
        file2.module_modname = 'forum'
        file2.content_filename = 'test2.txt'
        file2.section_id = 1
        file2.content_filesize = 3000
        
        course = MagicMock(spec=Course)
        course.excluded_sections = []
        course.files = [file1, file2]
        course.fullname = 'Test Course'
        
        config = MagicMock(spec=ConfigHelper)
        filter_config = {
            'download_descriptions': True,
            'exclude_file_extensions': [],
            'max_file_size': 0,
        }
        
        # Create mock modules
        mod = MagicMock()
        mod.download_condition.return_value = True
        
        with patch('moodle_dl.moodle.moodle_service.determine_ext', return_value='pdf'):
            with patch('moodle_dl.moodle.moodle_service.MoodleService.should_download_section', return_value=True):
                result = MoodleService._filter_course_files(
                    [file1, file2], config, filter_config, True, course, [mod]
                )
        
        # Both files should pass
        self.assertEqual(len(result), 2)


class TestShouldKeepDescriptionUrl(unittest.TestCase):
    """Tests for _should_keep_description_url() function."""
    
    def test_keep_url_no_duplicates(self):
        """Test that URL is kept when no duplicates exist."""
        file = MagicMock(spec=File)
        file.content_fileurl = 'http://example.com/file.pdf'
        file.content_type = 'description-url'
        file.module_id = 1
        
        course_files = [file]  # Only this file
        
        result = MoodleService._should_keep_description_url(file, course_files)
        
        self.assertTrue(result)

    def test_filter_url_when_real_file_exists(self):
        """Test that URL is filtered when real file with same URL exists."""
        url = 'http://example.com/file.pdf'
        
        desc_file = MagicMock(spec=File)
        desc_file.content_fileurl = url
        desc_file.content_type = 'description-url'
        desc_file.module_id = 1
        
        real_file = MagicMock(spec=File)
        real_file.content_fileurl = url
        real_file.content_type = 'file'
        real_file.module_id = 2
        
        result = MoodleService._should_keep_description_url(desc_file, [desc_file, real_file])
        
        self.assertFalse(result)

    def test_older_description_url_kept(self):
        """Test that older description URL is kept over newer one."""
        url = 'http://example.com/file.pdf'
        
        older_url = MagicMock(spec=File)
        older_url.content_fileurl = url
        older_url.content_type = 'description-url'
        older_url.module_id = 1
        
        newer_url = MagicMock(spec=File)
        newer_url.content_fileurl = url
        newer_url.content_type = 'description-url'
        newer_url.module_id = 2
        
        # For older_url, should keep
        result = MoodleService._should_keep_description_url(older_url, [older_url, newer_url])
        self.assertTrue(result)
        
        # For newer_url, should filter
        result = MoodleService._should_keep_description_url(newer_url, [older_url, newer_url])
        self.assertFalse(result)


class TestFilterDescriptionUrls(unittest.TestCase):
    """Tests for _filter_description_urls() function."""
    
    def test_filter_all_description_urls_when_disabled(self):
        """Test that all description URLs are filtered when download_links=False."""
        file1 = MagicMock(spec=File)
        file1.content_type = 'description-url'
        
        file2 = MagicMock(spec=File)
        file2.content_type = 'file'
        
        file3 = MagicMock(spec=File)
        file3.content_type = 'description-url'
        
        result = MoodleService._filter_description_urls([file1, file2, file3], False)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], file2)

    def test_keep_description_urls_when_enabled(self):
        """Test that description URLs are kept when download_links=True."""
        file1 = MagicMock(spec=File)
        file1.content_type = 'description-url'
        file1.content_fileurl = 'http://example.com/1'
        file1.module_id = 1
        
        file2 = MagicMock(spec=File)
        file2.content_type = 'file'
        
        file3 = MagicMock(spec=File)
        file3.content_type = 'description-url'
        file3.content_fileurl = 'http://example.com/2'
        file3.module_id = 2
        
        with patch('moodle_dl.moodle.moodle_service.MoodleService._should_keep_description_url', return_value=True):
            result = MoodleService._filter_description_urls([file1, file2, file3], True)
        
        self.assertEqual(len(result), 3)


class TestFilterCoursesIntegration(unittest.TestCase):
    """Integration tests for the complete filter_courses() function."""
    
    @patch('moodle_dl.moodle.moodle_service.logging')
    @patch('moodle_dl.moodle.moodle_service.get_all_mods_classes')
    def test_filter_courses_basic_flow(self, mock_get_mods, mock_logging):
        """Test basic filter_courses() flow."""
        # Setup config
        config = MagicMock(spec=ConfigHelper)
        config.get_download_course_ids.return_value = [1]
        config.get_dont_download_course_ids.return_value = []
        config.get_download_public_course_ids.return_value = []
        config.get_download_descriptions.return_value = True
        config.get_download_links_in_descriptions.return_value = False
        config.get_exclude_file_extensions.return_value = []
        config.get_max_file_size.return_value = 0
        config.get_download_also_with_cookie.return_value = False
        config.has_property.side_effect = lambda prop: prop == 'download_course_ids'
        
        # Setup course
        course = MagicMock(spec=Course)
        course.id = 1
        course.fullname = 'Test Course'
        course.excluded_sections = []
        
        file1 = MagicMock(spec=File)
        file1.content_type = 'file'
        file1.module_modname = 'resource'
        file1.content_filename = 'test.pdf'
        file1.section_id = 1
        file1.content_filesize = 5000
        
        course.files = [file1]
        
        # Setup modules
        mod = MagicMock()
        mod.download_condition.return_value = True
        mock_get_mods.return_value = [mod]
        
        # Setup MoodleService static methods
        with patch.object(MoodleService, 'should_download_course', return_value=True):
            with patch.object(MoodleService, '_check_course_availability', return_value=True):
                with patch('moodle_dl.moodle.moodle_service.determine_ext', return_value='pdf'):
                    with patch.object(MoodleService, 'should_download_section', return_value=True):
                        result = MoodleService.filter_courses([course], config)
        
        # Verify course was included
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 1)


if __name__ == '__main__':
    unittest.main()

