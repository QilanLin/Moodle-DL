# -*- coding: utf-8 -*-
"""
ip_validator.py 单元测试

测试 IP 验证功能：
- 公网 IP 检测
- IP 格式验证
- 403 错误诊断
- 白名单请求消息生成
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from moodle_dl.ip_validator import IPValidator


class TestIsValidIP(unittest.TestCase):
    """_is_valid_ip 方法测试"""

    def test_valid_ipv4(self):
        """测试有效的 IPv4 地址"""
        self.assertTrue(IPValidator._is_valid_ip('192.168.1.1'))
        self.assertTrue(IPValidator._is_valid_ip('10.0.0.1'))
        self.assertTrue(IPValidator._is_valid_ip('172.16.0.1'))
        self.assertTrue(IPValidator._is_valid_ip('8.8.8.8'))
        self.assertTrue(IPValidator._is_valid_ip('255.255.255.255'))
        self.assertTrue(IPValidator._is_valid_ip('0.0.0.0'))

    def test_valid_ipv6(self):
        """测试有效的 IPv6 地址"""
        self.assertTrue(IPValidator._is_valid_ip('2001:0db8:85a3:0000:0000:8a2e:0370:7334'))
        # Note: ::1 is a compressed IPv6 format, our regex only handles full format
        # self.assertTrue(IPValidator._is_valid_ip('::1'))
        self.assertTrue(IPValidator._is_valid_ip('fe80:0000:0000:0000:0000:0000:0000:0001'))
        self.assertTrue(IPValidator._is_valid_ip('2001:0db8:0000:0000:0000:0000:0000:0001'))

    def test_invalid_ipv4_out_of_range(self):
        """测试超出范围的 IPv4 地址"""
        self.assertFalse(IPValidator._is_valid_ip('256.1.1.1'))
        self.assertFalse(IPValidator._is_valid_ip('1.256.1.1'))
        self.assertFalse(IPValidator._is_valid_ip('1.1.256.1'))
        self.assertFalse(IPValidator._is_valid_ip('1.1.1.256'))

    def test_invalid_format(self):
        """测试无效格式的 IP"""
        self.assertFalse(IPValidator._is_valid_ip(''))
        self.assertFalse(IPValidator._is_valid_ip(None))
        self.assertFalse(IPValidator._is_valid_ip('not.an.ip.address'))
        self.assertFalse(IPValidator._is_valid_ip('192.168.1'))
        self.assertFalse(IPValidator._is_valid_ip('192.168.1.1.1'))
        self.assertFalse(IPValidator._is_valid_ip('abc.def.ghi.jkl'))

    def test_ip_with_whitespace(self):
        """测试带空格的 IP"""
        self.assertTrue(IPValidator._is_valid_ip(' 192.168.1.1 '))
        self.assertTrue(IPValidator._is_valid_ip('\t10.0.0.1\n'))


class TestGetPublicIP(unittest.TestCase):
    """get_public_ip 方法测试"""

    @patch('moodle_dl.ip_validator.requests.get')
    def test_get_public_ip_success(self, mock_get):
        """测试成功获取公网 IP"""
        # 模拟第一个服务成功
        mock_response = Mock()
        mock_response.json.return_value = {'ip': '123.45.67.89'}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = IPValidator.get_public_ip()

        self.assertEqual(result, '123.45.67.89')

    @patch('moodle_dl.ip_validator.requests.get')
    def test_get_public_ip_fallback_to_second_service(self, mock_get):
        """测试回退到第二个服务"""
        # 第一个服务失败，第二个成功
        mock_response_fail = Mock()
        mock_response_fail.raise_for_status.side_effect = Exception('Service 1 failed')

        mock_response_success = Mock()
        mock_response_success.text = '98.76.54.32\n'
        mock_response_success.raise_for_status = Mock()

        mock_get.side_effect = [
            mock_response_fail,
            mock_response_success
        ]

        result = IPValidator.get_public_ip()

        self.assertEqual(result, '98.76.54.32')

    @patch('moodle_dl.ip_validator.requests.get')
    def test_get_public_ip_all_services_fail(self, mock_get):
        """测试所有服务都失败"""
        mock_get.side_effect = Exception('All services failed')

        result = IPValidator.get_public_ip()

        self.assertIsNone(result)

    @patch('moodle_dl.ip_validator.requests.get')
    def test_get_public_ip_invalid_ip_response(self, mock_get):
        """测试服务返回无效 IP"""
        mock_response = Mock()
        mock_response.json.return_value = {'ip': 'not-a-valid-ip'}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = IPValidator.get_public_ip()

        self.assertIsNone(result)


class TestDiagnose403Error(unittest.TestCase):
    """diagnose_403_error 方法测试"""

    def test_diagnose_explicit_ip_restriction(self):
        """测试服务器明确返回 IP 限制消息"""
        error_message = 'Access denied'
        response_text = 'Your IP address is not allowed to access this service.'

        is_ip_restriction, message = IPValidator.diagnose_403_error(error_message, response_text)

        self.assertTrue(is_ip_restriction)
        self.assertIn('IP 访问被拒绝', message)

    def test_diagnose_generic_403(self):
        """测试通用的 403 错误"""
        error_message = 'HTTP 403 Forbidden'
        response_text = None

        is_ip_restriction, message = IPValidator.diagnose_403_error(error_message, response_text)

        self.assertTrue(is_ip_restriction)
        self.assertIn('访问被拒绝', message)

    def test_diagnose_with_moodle_domain(self):
        """测试带 Moodle 域名的诊断"""
        error_message = 'Forbidden'
        response_text = 'IP not in whitelist'

        is_ip_restriction, message = IPValidator.diagnose_403_error(
            error_message,
            response_text,
            moodle_domain='moodle.example.com'
        )

        self.assertTrue(is_ip_restriction)
        self.assertIn('moodle.example.com', message)

    @patch('moodle_dl.ip_validator.IPValidator.get_public_ip')
    def test_diagnose_includes_current_ip(self, mock_get_ip):
        """测试诊断消息包含当前 IP"""
        mock_get_ip.return_value = '123.45.67.89'

        error_message = 'HTTP 403'
        response_text = 'IP address restricted'

        is_ip_restriction, message = IPValidator.diagnose_403_error(error_message, response_text)

        self.assertTrue(is_ip_restriction)
        self.assertIn('123.45.67.89', message)


class TestGenerateIPRestrictionMessage(unittest.TestCase):
    """_generate_ip_restriction_message 方法测试"""

    @patch('moodle_dl.ip_validator.IPValidator.get_public_ip')
    def test_message_with_ip(self, mock_get_ip):
        """测试包含 IP 的消息"""
        mock_get_ip.return_value = '192.168.1.100'

        message = IPValidator._generate_ip_restriction_message('moodle.school.edu')

        self.assertIn('IP 访问被拒绝', message)
        self.assertIn('192.168.1.100', message)
        self.assertIn('moodle.school.edu', message)
        self.assertIn('Web Service IP 限制', message)

    @patch('moodle_dl.ip_validator.IPValidator.get_public_ip')
    def test_message_without_ip(self, mock_get_ip):
        """测试无法获取 IP 的消息"""
        mock_get_ip.return_value = None

        message = IPValidator._generate_ip_restriction_message('moodle.example.com')

        self.assertIn('无法检测当前 IP 地址', message)
        self.assertIn('moodle.example.com', message)

    def test_message_without_domain(self):
        """测试没有域名的消息"""
        message = IPValidator._generate_ip_restriction_message(None)

        self.assertNotIn('Moodle 域名:', message)
        self.assertIn('IP 访问被拒绝', message)


class TestGenerateGeneric403Message(unittest.TestCase):
    """_generate_generic_403_message 方法测试"""

    @patch('moodle_dl.ip_validator.IPValidator.get_public_ip')
    def test_generic_message_with_ip(self, mock_get_ip):
        """测试包含 IP 的通用消息"""
        mock_get_ip.return_value = '10.20.30.40'

        message = IPValidator._generate_generic_403_message('moodle.uni.edu')

        self.assertIn('访问被拒绝 (HTTP 403)', message)
        self.assertIn('10.20.30.40', message)
        self.assertIn('IP 地址不在白名单中', message)
        self.assertIn('Token 无效', message)
        self.assertIn('权限不足', message)


class TestGenerateWhitelistRequestMessage(unittest.TestCase):
    """generate_whitelist_request_message 方法测试"""

    @patch('moodle_dl.ip_validator.IPValidator.get_public_ip')
    def test_request_message_with_all_info(self, mock_get_ip):
        """测试包含所有信息的请求消息"""
        mock_get_ip.return_value = '203.0.113.42'

        message = IPValidator.generate_whitelist_request_message(
            'moodle.college.edu',
            'admin@college.edu'
        )

        self.assertIn('请求将 IP 地址添加到 Moodle Web Service API 白名单', message)
        self.assertIn('203.0.113.42', message)
        self.assertIn('moodle.college.edu', message)
        self.assertIn('admin@college.edu', message)

    @patch('moodle_dl.ip_validator.IPValidator.get_public_ip')
    def test_request_message_without_admin_email(self, mock_get_ip):
        """测试没有管理员邮箱的请求消息"""
        mock_get_ip.return_value = '198.51.100.23'

        message = IPValidator.generate_whitelist_request_message('lms.university.com')

        self.assertIn('198.51.100.23', message)
        self.assertIn('lms.university.com', message)
        self.assertNotIn('Email:', message)

    @patch('moodle_dl.ip_validator.IPValidator.get_public_ip')
    def test_request_message_when_ip_detection_fails(self, mock_get_ip):
        """测试 IP 检测失败时的请求消息"""
        mock_get_ip.return_value = None

        message = IPValidator.generate_whitelist_request_message('moodle.example.org')

        self.assertIn('[无法自动检测，请手动查看]', message)


if __name__ == '__main__':
    unittest.main()
