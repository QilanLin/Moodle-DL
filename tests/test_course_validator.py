# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock

from moodle_dl.exceptions import MoodleAPIError, MoodleAuthError
from moodle_dl.moodle.course_validator import CourseValidator, validate_course_with_web_api


class TestCourseValidator(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.opts = MagicMock()
        self.request_helper = MagicMock()
        self.validator = CourseValidator(self.config, self.opts, self.request_helper)

    def test_uses_provided_request_helper(self):
        self.assertIs(self.validator.request_helper, self.request_helper)

    def test_validate_course_success_returns_first_course(self):
        self.request_helper.post.return_value = [{'id': 42, 'fullname': 'Algorithms'}]

        course = self.validator.validate_course_exists_and_accessible(42)

        self.assertEqual(course['id'], 42)
        self.request_helper.post.assert_called_once_with(
            'core_course_get_courses',
            {'options': {'ids': {'0': 42}}},
        )

    def test_validate_course_raises_value_error_for_moodle_exception_list(self):
        self.request_helper.post.return_value = [
            {'exception': 'moodle_exception', 'errorcode': 'invalidcourseid', 'message': 'Course not accessible'}
        ]

        with self.assertRaisesRegex(ValueError, 'Context 检查失败'):
            self.validator.validate_course_exists_and_accessible(42)

    def test_validate_course_raises_value_error_for_exception_dict(self):
        self.request_helper.post.return_value = {
            'exception': 'moodle_exception',
            'errorcode': 'forbidden',
            'message': 'No permission',
        }

        with self.assertRaisesRegex(ValueError, 'API 错误 \\(forbidden\\) - No permission'):
            self.validator.validate_course_exists_and_accessible(42)

    def test_validate_course_converts_api_errors_to_value_error(self):
        self.request_helper.post.side_effect = MoodleAuthError('Invalid token')

        with self.assertRaisesRegex(ValueError, 'Invalid token'):
            self.validator.validate_course_exists_and_accessible(42)

    def test_validate_course_wraps_unexpected_errors(self):
        self.request_helper.post.side_effect = RuntimeError('network is unavailable')

        with self.assertRaisesRegex(RuntimeError, '验证时出错 - network is unavailable'):
            self.validator.validate_course_exists_and_accessible(42)

    def test_validate_course_rejects_empty_and_invalid_responses(self):
        for response in ([], {}, 'not-json'):
            with self.subTest(response=response):
                self.request_helper.post.return_value = response
                with self.assertRaises(ValueError):
                    self.validator.validate_course_exists_and_accessible(42)

    def test_validate_course_returns_none_without_request_helper(self):
        validator = CourseValidator(self.config, self.opts, request_helper=None)
        validator.request_helper = None

        self.assertIsNone(validator.validate_course_exists_and_accessible(42))
        self.assertFalse(validator.validate_course_has_content(42))

    def test_validate_course_has_content(self):
        self.request_helper.post.return_value = [{'id': 1, 'modules': []}]

        self.assertTrue(self.validator.validate_course_has_content(42))
        self.request_helper.post.assert_called_once_with('core_course_get_contents', {'courseid': 42})

    def test_validate_course_has_content_returns_false_for_errors(self):
        responses = [
            [],
            None,
            [{'exception': 'moodle_exception'}],
            MoodleAPIError('API failed'),
        ]

        for response in responses:
            with self.subTest(response=response):
                self.request_helper.reset_mock()
                if isinstance(response, Exception):
                    self.request_helper.post.side_effect = response
                    self.request_helper.post.return_value = None
                else:
                    self.request_helper.post.side_effect = None
                    self.request_helper.post.return_value = response

                self.assertFalse(self.validator.validate_course_has_content(42))

    def test_validate_course_with_web_api_checks_optional_content(self):
        self.request_helper.post.side_effect = [
            [{'id': 42, 'fullname': 'Algorithms'}],
            [{'id': 1, 'modules': []}],
        ]

        course = validate_course_with_web_api(
            self.config, self.opts, 42, check_content=True, request_helper=self.request_helper
        )

        self.assertEqual(course['id'], 42)

    def test_validate_course_with_web_api_returns_none_when_content_missing(self):
        self.request_helper.post.side_effect = [
            [{'id': 42, 'fullname': 'Algorithms'}],
            [],
        ]

        self.assertIsNone(
            validate_course_with_web_api(
                self.config, self.opts, 42, check_content=True, request_helper=self.request_helper
            )
        )


if __name__ == '__main__':
    unittest.main()
