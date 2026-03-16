# -*- coding: utf-8 -*-
"""
request_helper.py 单元测试

测试 HTTP 请求助手的核心功能：
- URL 构建
- 数据编码
- 错误处理
- 响应解析
"""

import json
import unittest
from unittest.mock import MagicMock, Mock, patch, AsyncMock
import urllib.parse

from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.exceptions import MoodleAPIError, MoodleAuthError, MoodleNetworkError
from moodle_dl.types import MoodleURL, MoodleDlOpts


class TestGetRESTPostUrl(unittest.TestCase):
    """_get_REST_POST_URL 静态方法测试"""

    def test_get_rest_post_url_basic(self):
        """测试基本 REST URL 构建"""
        url = RequestHelper._get_REST_POST_URL('https://example.com/moodle/', 'core_course_get_contents')
        expected = 'https://example.com/moodle/webservice/rest/server.php?moodlewsrestformat=json&wsfunction=core_course_get_contents'
        self.assertEqual(url, expected)

    def test_get_rest_post_url_without_trailing_slash(self):
        """测试不带尾部斜杠的 URL"""
        url = RequestHelper._get_REST_POST_URL('https://example.com/moodle', 'core_webservice_get_site_info')
        # The implementation doesn't add a slash automatically
        expected = 'https://example.com/moodlewebservice/rest/server.php?moodlewsrestformat=json&wsfunction=core_webservice_get_site_info'
        self.assertEqual(url, expected)

    def test_get_rest_post_url_with_subdomain(self):
        """测试带子域名的 URL"""
        url = RequestHelper._get_REST_POST_URL('https://moodle.uni.edu/webservice/', 'mod_forum_get_forums_by_courses')
        self.assertIn('mod_forum_get_forums_by_courses', url)
        self.assertIn('moodlewsrestformat=json', url)


class TestGetPostData(unittest.TestCase):
    """_get_POST_DATA 静态方法测试"""

    def test_get_post_data_minimal(self):
        """测试最小 POST 数据"""
        data = RequestHelper._get_POST_DATA('core_course_get_contents', 'token123', None)
        self.assertIn('wsfunction', data)
        self.assertIn('wstoken', data)
        self.assertEqual(data['wsfunction'], 'core_course_get_contents')
        self.assertEqual(data['wstoken'], 'token123')
        self.assertEqual(data['moodlewssettingfilter'], 'true')

    def test_get_post_data_with_custom_data(self):
        """测试带自定义数据的 POST"""
        custom_data = {'courseid': 123, 'options': [{'name': 'excludemodules', 'value': 'true'}]}
        data = RequestHelper._get_POST_DATA('core_course_get_contents', 'token123', custom_data)
        self.assertEqual(data['courseid'], 123)
        self.assertEqual(data['wsfunction'], 'core_course_get_contents')
        self.assertEqual(data['wstoken'], 'token123')


class TestRecursiveUrlencode(unittest.TestCase):
    """recursive_urlencode 静态方法测试"""

    def test_simple_dict(self):
        """测试简单字典编码"""
        data = {'key1': 'value1', 'key2': 'value2'}
        result = RequestHelper.recursive_urlencode(data)
        self.assertIn('key1=value1', result)
        self.assertIn('key2=value2', result)

    def test_nested_dict(self):
        """测试嵌套字典编码"""
        data = {'courseid': 123, 'options': {'name': 'test', 'value': 'true'}}
        result = RequestHelper.recursive_urlencode(data)
        # Moodle REST API format: options[name]=test&options[value]=true
        self.assertIn('courseid=123', result)
        self.assertIn('options[name]=test', result)
        self.assertIn('options[value]=true', result)

    def test_deeply_nested_dict(self):
        """测试深度嵌套字典"""
        data = {'a': {'b': {'c': 'value'}}}
        result = RequestHelper.recursive_urlencode(data)
        self.assertIn('a[b][c]=value', result)

    def test_special_characters(self):
        """测试特殊字符编码"""
        data = {'name': 'test value', 'path': '/some/path'}
        result = RequestHelper.recursive_urlencode(data)
        # URL encoding should handle spaces
        self.assertIn('name=', result)
        self.assertIn('path=', result)

    def test_unicode_characters(self):
        """测试 Unicode 字符编码"""
        data = {'course_name': '课程名称'}
        result = RequestHelper.recursive_urlencode(data)
        # Should be URL encoded
        self.assertIn('course_name=', result)

    def test_list_values(self):
        """测试列表编码为 Moodle 风格数组参数"""
        data = {'userids': [936373, 936374]}
        result = RequestHelper.recursive_urlencode(data)
        self.assertIn('userids[0]=936373', result)
        self.assertIn('userids[1]=936374', result)

    def test_list_of_dicts(self):
        """测试列表中的字典编码"""
        data = {
            'options': [
                {'name': 'excludemodules', 'value': 'true'},
                {'name': 'excludecontents', 'value': 'false'},
            ]
        }
        result = RequestHelper.recursive_urlencode(data)
        self.assertIn('options[0][name]=excludemodules', result)
        self.assertIn('options[0][value]=true', result)
        self.assertIn('options[1][name]=excludecontents', result)
        self.assertIn('options[1][value]=false', result)

    def test_bool_values(self):
        """测试布尔值编码为 1/0，兼容 Moodle PARAM_BOOL"""
        data = {'options': {'includenotapproved': False, 'showall': True}}
        result = RequestHelper.recursive_urlencode(data)
        self.assertIn('options[includenotapproved]=0', result)
        self.assertIn('options[showall]=1', result)


class TestCheckResponseCode(unittest.TestCase):
    """_check_response_code 方法测试"""

    def setUp(self):
        """设置测试环境"""
        self.config = Mock()
        self.opts = MoodleDlOpts()
        self.moodle_url = MoodleURL(False, 'moodle.example.com', '/')
        self.request_helper = RequestHelper(self.config, self.opts, self.moodle_url, 'test_token')

    def test_response_code_200(self):
        """测试 200 状态码（成功）"""
        mock_response = Mock()
        mock_response.status_code = 200
        # Should not raise any exception
        self.request_helper._check_response_code(mock_response)

    def test_response_code_401(self):
        """测试 401 状态码（未授权）"""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.headers = {}
        mock_response.text = 'Unauthorized'

        with self.assertRaises(MoodleAuthError) as context:
            self.request_helper._check_response_code(mock_response)

        self.assertIn('401', str(context.exception))

    def test_response_code_403(self):
        """测试 403 状态码（禁止访问）"""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.headers = {}
        mock_response.text = 'Forbidden'

        with self.assertRaises(MoodleAuthError) as context:
            self.request_helper._check_response_code(mock_response)

        self.assertIn('403', str(context.exception))

    def test_response_code_500(self):
        """测试 500 状态码（服务器错误）"""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.headers = {}
        mock_response.text = 'Internal Server Error'

        with self.assertRaises(MoodleAPIError) as context:
            self.request_helper._check_response_code(mock_response)

        self.assertIn('500', str(context.exception))


class TestCheckJsonForMoodleError(unittest.TestCase):
    """check_json_for_moodle_error 方法测试"""

    def setUp(self):
        self.config = Mock()
        self.opts = MoodleDlOpts()
        self.moodle_url = MoodleURL(use_http=False, domain='example.com', path='/')
        self.request_helper = RequestHelper(self.config, self.opts, self.moodle_url, 'test_token')

    def test_valid_response_no_error(self):
        """测试有效响应（无错误）"""
        resp_json = {'courses': [{'id': 1, 'fullname': 'Test Course'}]}
        # Should not raise any exception
        self.request_helper.check_json_for_moodle_error(resp_json, 'http://test.com', {})

    def test_error_response_with_invalidtoken(self):
        """测试 invalidtoken 错误"""
        resp_json = {
            'error': 'Invalid token',
            'errorcode': 'invalidtoken'
        }

        with self.assertRaises(MoodleAuthError) as context:
            self.request_helper.check_json_for_moodle_error(resp_json, 'http://test.com', {})

        self.assertIn('认证或权限错误', str(context.exception))

    def test_error_response_with_accessdenied(self):
        """测试 accessdenied 错误"""
        resp_json = {
            'error': 'Access denied',
            'errorcode': 'accessdenied'
        }

        with self.assertRaises(MoodleAuthError) as context:
            self.request_helper.check_json_for_moodle_error(resp_json, 'http://test.com', {})

        self.assertIn('认证或权限错误', str(context.exception))

    def test_error_response_with_general_error(self):
        """测试一般 API 错误"""
        resp_json = {
            'error': 'Some error occurred',
            'errorcode': 'generalexception'
        }

        with self.assertRaises(MoodleAPIError) as context:
            self.request_helper.check_json_for_moodle_error(resp_json, 'http://test.com', {})

        self.assertIn('拒绝了请求', str(context.exception))

    def test_exception_response_with_invalidtoken(self):
        """测试 exception 格式的响应（invalidtoken）"""
        resp_json = {
            'exception': 'moodle_exception',
            'errorcode': 'invalidtoken',
            'message': 'Invalid token, please login again'
        }

        with self.assertRaises(MoodleAuthError) as context:
            self.request_helper.check_json_for_moodle_error(resp_json, 'http://test.com', {})

        self.assertIn('token 已过期', str(context.exception))

    def test_exception_response_with_requireloginerror(self):
        """测试 exception 格式的响应（requireloginerror）"""
        resp_json = {
            'exception': 'moodle_exception',
            'errorcode': 'requireloginerror',
            'message': 'Session expired'
        }

        with self.assertRaises(MoodleAuthError) as context:
            self.request_helper.check_json_for_moodle_error(resp_json, 'http://test.com', {})

        self.assertIn('认证或权限错误', str(context.exception))

    def test_exception_response_with_general_exception(self):
        """测试一般 exception"""
        resp_json = {
            'exception': 'invalid_parameter_exception',
            'errorcode': 'invalidparameter',
            'message': 'Invalid parameter value'
        }

        with self.assertRaises(MoodleAPIError) as context:
            self.request_helper.check_json_for_moodle_error(resp_json, 'http://test.com', {})

        self.assertIn('拒绝了请求', str(context.exception))


class TestLogFailedRequest(unittest.TestCase):
    """log_failed_request 方法测试"""

    def setUp(self):
        self.config = Mock()
        self.opts = MoodleDlOpts()
        self.moodle_url = MoodleURL(use_http=False, domain='example.com', path='/')
        self.request_helper = RequestHelper(self.config, self.opts, self.moodle_url, 'test_token')

    @patch('moodle_dl.moodle.request_helper.logging')
    def test_log_failed_request_with_password(self, mock_logging):
        """测试记录包含密码的失败请求（应被审查）"""
        data = {'username': 'test', 'password': 'secret123', 'wstoken': 'token123'}
        self.request_helper.log_failed_request('http://test.com', data)

        # Check that logging.debug was called
        mock_logging.debug.assert_called_once()
        call_args = str(mock_logging.debug.call_args)
        self.assertIn('http://test.com', call_args)
        # Password should be censored
        self.assertIn('censored', call_args)
        self.assertNotIn('secret123', call_args)
        self.assertNotIn('token123', call_args)

    @patch('moodle_dl.moodle.request_helper.logging')
    def test_log_failed_request_with_privatetoken(self, mock_logging):
        """测试记录包含 privatetoken 的失败请求（应被审查）"""
        data = {'privatetoken': 'private_secret'}
        self.request_helper.log_failed_request('http://test.com', data)

        call_args = str(mock_logging.debug.call_args)
        self.assertIn('censored', call_args)
        self.assertNotIn('private_secret', call_args)

    @patch('moodle_dl.moodle.request_helper.logging')
    def test_log_failed_request_without_sensitive_data(self, mock_logging):
        """测试记录不包含敏感数据的失败请求"""
        data = {'courseid': 123, 'userid': 456}
        self.request_helper.log_failed_request('http://test.com', data)

        call_args = str(mock_logging.debug.call_args)
        self.assertIn('courseid', call_args)
        self.assertIn('123', call_args)


class TestInitialParse(unittest.TestCase):
    """_initial_parse 方法测试"""

    def setUp(self):
        self.config = Mock()
        self.opts = MoodleDlOpts()
        self.moodle_url = MoodleURL(use_http=False, domain='example.com', path='/')
        self.request_helper = RequestHelper(self.config, self.opts, self.moodle_url, 'test_token')

    def test_initial_parse_success(self):
        """测试成功解析 JSON 响应"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'courses': [{'id': 1}]}

        result = self.request_helper._initial_parse(mock_response, 'http://test.com', {})
        self.assertEqual(result, {'courses': [{'id': 1}]})

    def test_initial_parse_with_error_code(self):
        """测试包含错误代码的响应"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'error': 'Invalid token', 'errorcode': 'invalidtoken'}

        with self.assertRaises(MoodleAuthError):
            self.request_helper._initial_parse(mock_response, 'http://test.com', {})

    def test_initial_parse_invalid_json(self):
        """测试无效 JSON 响应"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError('Invalid JSON')
        mock_response.text = 'Not valid JSON'

        with self.assertRaises(MoodleAPIError) as context:
            self.request_helper._initial_parse(mock_response, 'http://test.com', {})

        self.assertIn('JSON 解析失败', str(context.exception))

    def test_initial_parse_unexpected_error(self):
        """测试 JSON 解析时的意外错误"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = Exception('Unexpected error')
        mock_response.text = 'Some response'

        with self.assertRaises(MoodleAPIError) as context:
            self.request_helper._initial_parse(mock_response, 'http://test.com', {})

        self.assertIn('意外错误', str(context.exception))


class TestGetLogin(unittest.TestCase):
    """get_login 方法测试"""

    def setUp(self):
        self.config = Mock()
        self.opts = MoodleDlOpts()
        self.moodle_url = MoodleURL(use_http=False, domain='example.com', path='/')
        self.request_helper = RequestHelper(self.config, self.opts, self.moodle_url, None)

    @patch('moodle_dl.moodle.request_helper.SslHelper.custom_requests_session')
    def test_get_login_success(self, mock_session_class):
        """测试成功获取登录 token"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'token': 'test_token_123'}

        mock_session = Mock()
        mock_session.post.return_value = mock_response
        mock_session_class.return_value = mock_session

        data = {'username': 'testuser', 'password': 'testpass'}
        result = self.request_helper.get_login(data)

        self.assertEqual(result['token'], 'test_token_123')

    @patch('moodle_dl.moodle.request_helper.SslHelper.custom_requests_session')
    def test_get_login_with_error_in_response(self, mock_session_class):
        """测试响应中包含错误"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'error': 'Invalid credentials', 'errorcode': 'invalidlogin'}

        mock_session = Mock()
        mock_session.post.return_value = mock_response
        mock_session_class.return_value = mock_session

        data = {'username': 'testuser', 'password': 'wrongpass'}

        with self.assertRaises(MoodleAPIError):
            self.request_helper.get_login(data)

    @patch('moodle_dl.moodle.request_helper.SslHelper.custom_requests_session')
    def test_get_login_network_error(self, mock_session_class):
        """测试网络连接错误"""
        import requests
        mock_session = Mock()
        mock_session.post.side_effect = requests.ConnectionError('Network error')
        mock_session_class.return_value = mock_session

        data = {'username': 'testuser', 'password': 'testpass'}

        with self.assertRaises(MoodleNetworkError):
            self.request_helper.get_login(data)


class TestPostWithRetry(unittest.TestCase):
    """post 方法测试（含重试机制）"""

    def setUp(self):
        self.config = Mock()
        self.opts = MoodleDlOpts()
        self.moodle_url = MoodleURL(use_http=False, domain='example.com', path='/')
        self.request_helper = RequestHelper(self.config, self.opts, self.moodle_url, 'test_token')

    @patch('moodle_dl.moodle.request_helper.SslHelper.custom_requests_session')
    @patch('time.sleep')
    def test_post_retry_on_connection_error(self, mock_sleep, mock_session_class):
        """测试连接错误时的重试机制"""
        import requests

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'data': 'success'}
        mock_response.url = 'http://test.com'

        # First two calls fail, third succeeds
        mock_session = Mock()
        mock_session.post.side_effect = [
            requests.ConnectionError('Network error'),
            requests.ConnectionError('Network error'),
            mock_response
        ]
        mock_session_class.return_value = mock_session

        result = self.request_helper.post('core_course_get_contents', {'courseid': 1})

        # Should have been called 3 times (2 failures + 1 success)
        self.assertEqual(mock_session.post.call_count, 3)
        self.assertEqual(result['data'], 'success')

    @patch('moodle_dl.moodle.request_helper.SslHelper.custom_requests_session')
    @patch('time.sleep')
    def test_post_max_retries_exceeded(self, mock_sleep, mock_session_class):
        """测试超过最大重试次数"""
        import requests

        mock_session = Mock()
        mock_session.post.side_effect = requests.ConnectionError('Network error')
        mock_session_class.return_value = mock_session

        with self.assertRaises(MoodleNetworkError) as context:
            self.request_helper.post('core_course_get_contents', {})

        self.assertIn('已重试 5 次', str(context.exception))
        # Should be called MAX_RETRIES times
        self.assertEqual(mock_session.post.call_count, 5)

    def test_post_without_token(self):
        """测试没有 token 时抛出异常"""
        self.request_helper.token = None

        with self.assertRaises(ValueError) as context:
            self.request_helper.post('core_course_get_contents', {})

        self.assertIn('token', str(context.exception).lower())


class TestRequestHelperConstants(unittest.TestCase):
    """测试 RequestHelper 常量"""

    def test_request_header(self):
        """测试请求头配置"""
        self.assertIn('User-Agent', RequestHelper.RQ_HEADER)
        self.assertIn('MoodleMobile', RequestHelper.RQ_HEADER['User-Agent'])
        self.assertEqual(RequestHelper.RQ_HEADER['Content-Type'], 'application/x-www-form-urlencoded')

    def test_max_retries(self):
        """测试最大重试次数"""
        self.assertEqual(RequestHelper.MAX_RETRIES, 5)


if __name__ == '__main__':
    unittest.main()
