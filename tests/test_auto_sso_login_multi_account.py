# -*- coding: utf-8 -*-
"""
auto_sso_login.py 多账号检测功能单元测试

测试多账号检测、选择和过滤功能：
- 多 Microsoft 账号检测（基于 ESTSAUTHPERSISTENT）
- 账号 Cookies 过滤
- 用户选择提示（模拟）
- ESTSUSERLIST 解析
"""

import unittest
from unittest.mock import Mock, patch, call
from moodle_dl.auto_sso_login import (
    _detect_multiple_accounts,
    _filter_cookies_by_account,
    _prompt_user_for_account_selection,
    _parse_estsuserlist,
)


class TestDetectMultipleAccounts(unittest.TestCase):
    """_detect_multiple_accounts 函数测试"""

    def test_no_microsoft_accounts(self):
        """测试没有 Microsoft 账号的场景"""
        cookies = [
            {'name': 'session', 'domain': '.example.com', 'value': 'abc123'},
            {'name': 'user_pref', 'domain': '.google.com', 'value': 'dark_mode'},
        ]

        result = _detect_multiple_accounts(cookies, 'firefox')

        self.assertEqual(len(result), 0)

    def test_single_microsoft_account(self):
        """测试单个 Microsoft 账号的场景（不需要选择）"""
        cookies = [
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_auth_value_12345'},
            {'name': 'ESTSAUTH', 'domain': '.login.microsoftonline.com', 'value': 'session_value'},
        ]

        result = _detect_multiple_accounts(cookies, 'chrome')

        self.assertEqual(len(result), 0)  # 单个账号不返回，不需要选择

    def test_multiple_microsoft_accounts(self):
        """测试多个 Microsoft 账号的场景"""
        cookies = [
            # 账号 1 的会话
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_auth_for_account_1'},
            {'name': 'ESTSAUTH', 'domain': '.login.microsoftonline.com', 'value': 'session1'},
            # 账号 2 的会话
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.live.com', 'value': 'ests_auth_for_account_2'},
            {'name': 'ESTSAUTH', 'domain': '.login.live.com', 'value': 'session2'},
            # 其他 cookies
            {'name': 'session', 'domain': '.example.com', 'value': 'abc123'},
        ]

        result = _detect_multiple_accounts(cookies, 'firefox')

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['ests_auth_persistent'], 'ests_auth_for_account_1')
        self.assertEqual(result[1]['ests_auth_persistent'], 'ests_auth_for_account_2')

    def test_estssso_tiles_detected(self):
        """测试检测到 ESTSSSOTILES=1 的场景"""
        cookies = [
            {'name': 'ESTSSSOTILES', 'domain': '.login.microsoftonline.com', 'value': '1'},
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests1'},
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.live.com', 'value': 'ests2'},
        ]

        result = _detect_multiple_accounts(cookies, 'edge')

        self.assertEqual(len(result), 2)

    def test_groups_cookies_by_domain(self):
        """测试 cookies 按域分组"""
        cookies = [
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.microsoftonline.com', 'value': 'ests_1'},
            {'name': 'cookie1', 'domain': '.microsoftonline.com', 'value': 'val1'},
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.live.com', 'value': 'ests_2'},
            {'name': 'cookie2', 'domain': '.live.com', 'value': 'val2'},
        ]

        result = _detect_multiple_accounts(cookies, 'chrome')

        self.assertEqual(len(result), 2)
        # 验证每个账号都有相关的 cookies
        self.assertGreaterEqual(len(result[0]['cookies']), 1)
        self.assertGreaterEqual(len(result[1]['cookies']), 1)

    def test_single_estsauth_with_multiple_users_in_estsuserlist(self):
        """测试单个 ESTSAUTHPERSISTENT 但 ESTSUSERLIST 有多个用户的场景

        这是用户实际遇到的场景：
        - 只有一个 ESTSAUTHPERSISTENT cookie（浏览器当前会话）
        - ESTSSSOTILES=1 表示账号选择器被触发
        - ESTSUSERLIST 包含多个用户信息

        根据 Microsoft 文档，ESTSAUTHPERSISTENT 在同一浏览器配置文件中通常只有一个，
        但 ESTSSSOTILES=1 表示存在多个账号，需要用户选择。
        """
        import base64
        import json

        # 模拟用户的实际场景
        users_data = [
            {'login_name': 'user1@kcl.ac.uk', 'display_name': 'User One KCL'},
            {'login_name': 'user2@kcl.ac.uk', 'display_name': 'User Two KCL'},
        ]
        encoded_users = base64.b64encode(json.dumps(users_data).encode()).decode()

        cookies = [
            # 只有 1 个 ESTSAUTHPERSISTENT（当前会话）
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': '1.AQUAFM9wg_MWFky4PH...'},
            # ESTSSSOTILES=1 表示账号选择器被触发
            {'name': 'ESTSSSOTILES', 'domain': '.login.microsoftonline.com', 'value': '1'},
            # ESTSUSERLIST 包含多个用户
            {'name': 'ESTSUSERLIST', 'domain': '.login.microsoftonline.com', 'value': encoded_users},
            # 其他 Microsoft cookies
            {'name': 'ESTSAUTH', 'domain': '.login.microsoftonline.com', 'value': 'session_value'},
            {'name': 'brcap', 'domain': '.login.microsoftonline.com', 'value': '0'},
            # 非 Microsoft cookies
            {'name': 'MoodleSession', 'domain': '.keats.kcl.ac.uk', 'value': 'moodle_sess'},
        ]

        result = _detect_multiple_accounts(cookies, 'firefox')

        # 应该检测到 2 个账号（来自 ESTSUSERLIST）
        self.assertEqual(len(result), 2)

        # 验证用户信息正确关联
        self.assertEqual(result[0]['user_info']['email'], 'user1@kcl.ac.uk')
        self.assertEqual(result[0]['user_info']['display_name'], 'User One KCL')
        self.assertEqual(result[1]['user_info']['email'], 'user2@kcl.ac.uk')
        self.assertEqual(result[1]['user_info']['display_name'], 'User Two KCL')

        # 验证两个账号共享同一个 ESTSAUTHPERSISTENT 值
        self.assertEqual(result[0]['ests_auth_persistent'], result[1]['ests_auth_persistent'])

        # 验证每个账号都有 Microsoft cookies
        self.assertGreater(len(result[0]['cookies']), 0)
        self.assertGreater(len(result[1]['cookies']), 0)

    def test_single_estsauth_without_estsuserlist(self):
        """测试单个 ESTSAUTHPERSISTENT 且没有 ESTSUSERLIST 的场景

        这是单账号场景，不应该触发多账号选择。
        """
        cookies = [
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_auth_only_one'},
            {'name': 'ESTSAUTH', 'domain': '.login.microsoftonline.com', 'value': 'session_value'},
            {'name': 'MoodleSession', 'domain': '.keats.kcl.ac.uk', 'value': 'moodle_sess'},
        ]

        result = _detect_multiple_accounts(cookies, 'firefox')

        # 只有一个 ESTSAUTHPERSISTENT 且没有 ESTSUSERLIST，不触发多账号选择
        self.assertEqual(len(result), 0)

    def test_estsssotiles_1_but_empty_estsuserlist(self):
        """测试 ESTSSSOTILES=1 但 ESTSUSERLIST 为空或无效的场景"""
        cookies = [
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_auth'},
            {'name': 'ESTSSSOTILES', 'domain': '.login.microsoftonline.com', 'value': '1'},
            # 没有 ESTSUSERLIST
        ]

        result = _detect_multiple_accounts(cookies, 'firefox')

        # ESTSSSOTILES=1 但没有有效的 ESTSUSERLIST，只有一个 ESTSAUTHPERSISTENT
        # 不应该触发多账号选择（因为没有足够信息区分账号）
        self.assertEqual(len(result), 0)


class TestParseESTSUSERLIST(unittest.TestCase):
    """_parse_estsuserlist 函数测试"""

    def test_no_estsuserlist_cookie(self):
        """测试没有 ESTSUSERLIST cookie 的场景"""
        cookies = [
            {'name': 'session', 'domain': '.example.com', 'value': 'abc123'},
        ]

        result = _parse_estsuserlist(cookies)

        self.assertEqual(len(result), 0)

    def test_invalid_base64(self):
        """测试无效的 base64 编码"""
        cookies = [
            {'name': 'ESTSUSERLIST', 'domain': '.microsoftonline.com', 'value': 'not_valid_base64!!!'},
        ]

        result = _parse_estsuserlist(cookies)

        self.assertEqual(len(result), 0)

    def test_valid_json_array(self):
        """测试有效的 JSON 数组格式"""
        import base64
        import json

        users_data = [
            {'login_name': 'user1@example.com', 'display_name': 'User One'},
            {'login_name': 'user2@example.com', 'display_name': 'User Two'},
        ]
        encoded = base64.b64encode(json.dumps(users_data).encode()).decode()

        cookies = [
            {'name': 'ESTSUSERLIST', 'domain': '.microsoftonline.com', 'value': encoded},
        ]

        result = _parse_estsuserlist(cookies)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['email'], 'user1@example.com')
        self.assertEqual(result[0]['display_name'], 'User One')
        self.assertEqual(result[1]['email'], 'user2@example.com')

    def test_valid_json_object_with_users_field(self):
        """测试包含 users 字段的 JSON 对象"""
        import base64
        import json

        users_data = {
            'users': [
                {'email': 'user1@test.com', 'display_name': 'Test User 1'},
                {'upn': 'user2@test.com', 'name': 'Test User 2'},
            ]
        }
        encoded = base64.b64encode(json.dumps(users_data).encode()).decode()

        cookies = [
            {'name': 'ESTSUSERLIST', 'domain': '.microsoftonline.com', 'value': encoded},
        ]

        result = _parse_estsuserlist(cookies)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['email'], 'user1@test.com')
        self.assertEqual(result[1]['email'], 'user2@test.com')


class TestFilterCookiesByAccount(unittest.TestCase):
    """_filter_cookies_by_account 函数测试"""

    def setUp(self):
        """设置测试数据"""
        self.sample_cookies = [
            # 非 Microsoft cookies（应该全部保留）
            {'name': 'MoodleSession', 'domain': '.moodle.example.com', 'value': 'moodle_sess'},
            {'name': 'session', 'domain': '.example.com', 'value': 'abc123'},
            {'name': 'user_pref', 'domain': '.google.com', 'value': 'dark_mode'},
            # 账号 1 的 Microsoft cookies
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_auth_1'},
            {'name': 'cookie1', 'domain': '.microsoftonline.com', 'value': 'val1'},
            # 账号 2 的 Microsoft cookies（应该被过滤掉）
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.live.com', 'value': 'ests_auth_2'},
            {'name': 'cookie2', 'domain': '.live.com', 'value': 'val2'},
        ]

    def test_filters_non_microsoft_cookies(self):
        """测试保留所有非 Microsoft cookies"""
        selected_account = {
            'id': 'session_1',
            'ests_auth_persistent': 'ests_auth_1',
            'domain': '.login.microsoftonline.com',
            'user_info': {'email': 'user1@example.com'},
            'cookies': [
                {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_auth_1'},
                {'name': 'cookie1', 'domain': '.microsoftonline.com', 'value': 'val1'},
            ]
        }

        result = _filter_cookies_by_account(self.sample_cookies, selected_account)

        # 检查非 Microsoft cookies 是否保留
        non_ms_cookies = [c for c in result if 'microsoft' not in c['domain'].lower() and 'live.com' not in c['domain'].lower()]
        self.assertGreaterEqual(len(non_ms_cookies), 3)

    def test_filters_selected_account_microsoft_cookies(self):
        """测试保留选中账号的 Microsoft cookies"""
        selected_account = {
            'id': 'session_1',
            'ests_auth_persistent': 'ests_auth_1',
            'domain': '.login.microsoftonline.com',
            'user_info': {'email': 'user1@example.com'},
            'cookies': [
                {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_auth_1'},
            ]
        }

        result = _filter_cookies_by_account(self.sample_cookies, selected_account)

        # 检查是否包含选中账号的 Microsoft cookies
        ms_cookies = [c for c in result if '.microsoftonline.com' in c['domain']]
        self.assertGreater(len(ms_cookies), 0)

    def test_excludes_other_account_microsoft_cookies(self):
        """测试排除其他账号的 Microsoft cookies"""
        selected_account = {
            'id': 'session_1',
            'ests_auth_persistent': 'ests_auth_1',
            'domain': '.login.microsoftonline.com',
            'user_info': {'email': 'user1@example.com'},
            'cookies': [
                {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_auth_1'},
            ]
        }

        result = _filter_cookies_by_account(self.sample_cookies, selected_account)

        # 检查是否排除了账号 2 的 ESTSAUTHPERSISTENT cookie
        account2_cookies = [c for c in result if c.get('value') == 'ests_auth_2']
        self.assertEqual(len(account2_cookies), 0)

    def test_only_one_ests_auth_persistent(self):
        """测试结果中只包含一个 ESTSAUTHPERSISTENT cookie"""
        selected_account = {
            'id': 'session_1',
            'ests_auth_persistent': 'ests_auth_1',
            'domain': '.login.microsoftonline.com',
            'user_info': {'email': 'user1@example.com'},
            'cookies': []
        }

        result = _filter_cookies_by_account(self.sample_cookies, selected_account)

        # 应该只有一个 ESTSAUTHPERSISTENT cookie
        ests_auth_cookies = [c for c in result if c.get('name') == 'ESTSAUTHPERSISTENT']
        self.assertEqual(len(ests_auth_cookies), 1)
        self.assertEqual(ests_auth_cookies[0]['value'], 'ests_auth_1')


class TestPromptUserForAccountSelection(unittest.TestCase):
    """_prompt_user_for_account_selection 函数测试"""

    def setUp(self):
        """设置测试数据"""
        self.sample_accounts = [
            {
                'id': 'session_1',
                'ests_auth_persistent': 'ests_auth_value_1',
                'domain': '.login.microsoftonline.com',
                'user_info': {'email': 'user1@example.com', 'display_name': 'User One'},
                'cookies': []
            },
            {
                'id': 'session_2',
                'ests_auth_persistent': 'ests_auth_value_2',
                'domain': '.login.live.com',
                'user_info': {'email': 'user2@example.com', 'display_name': 'User Two'},
                'cookies': []
            },
        ]

    @patch('builtins.input', return_value='1')
    def test_selects_first_account(self, mock_input):
        """测试选择第一个账号"""
        result = _prompt_user_for_account_selection(self.sample_accounts)

        self.assertEqual(result['ests_auth_persistent'], 'ests_auth_value_1')
        self.assertEqual(result['user_info']['email'], 'user1@example.com')
        mock_input.assert_called_once()

    @patch('builtins.input', return_value='2')
    def test_selects_second_account(self, mock_input):
        """测试选择第二个账号"""
        result = _prompt_user_for_account_selection(self.sample_accounts)

        self.assertEqual(result['ests_auth_persistent'], 'ests_auth_value_2')
        self.assertEqual(result['user_info']['email'], 'user2@example.com')

    @patch('builtins.input', side_effect=['invalid', '5', '1'])
    def test_retries_on_invalid_input(self, mock_input):
        """测试无效输入时重试"""
        result = _prompt_user_for_account_selection(self.sample_accounts)

        self.assertEqual(result['ests_auth_persistent'], 'ests_auth_value_1')
        self.assertEqual(mock_input.call_count, 3)

    @patch('builtins.input', side_effect=EOFError())
    def test_eof_defaults_to_first_account(self, mock_input):
        """测试非交互环境（EOF）默认选择第一个账号"""
        result = _prompt_user_for_account_selection(self.sample_accounts)

        self.assertEqual(result['ests_auth_persistent'], 'ests_auth_value_1')

    @patch('builtins.input', side_effect=KeyboardInterrupt())
    def test_keyboard_interrupt_defaults_to_first_account(self, mock_input):
        """测试 Ctrl+C 中断时默认选择第一个账号"""
        result = _prompt_user_for_account_selection(self.sample_accounts)

        self.assertEqual(result['ests_auth_persistent'], 'ests_auth_value_1')

    def test_shows_display_name_when_available(self):
        """测试显示用户显示名称（如果有）"""
        accounts_with_names = [
            {
                'id': 's1',
                'ests_auth_persistent': 'e1',
                'domain': '.microsoft.com',
                'user_info': {'email': 'test@example.com', 'display_name': 'Test User'},
                'cookies': []
            }
        ]

        with patch('builtins.input', return_value='1'):
            result = _prompt_user_for_account_selection(accounts_with_names)

            self.assertEqual(result['user_info']['display_name'], 'Test User')


class TestIntegration(unittest.TestCase):
    """集成测试"""

    def test_full_multi_account_flow(self):
        """测试完整的多账号处理流程"""
        # 模拟浏览器中有两个 Microsoft 账号的 cookies
        cookies = [
            # 账号 1
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'ests_account_1_xyz'},
            {'name': 'ESTSAUTH', 'domain': '.login.microsoftonline.com', 'value': 'session1'},
            # 账号 2
            {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.live.com', 'value': 'ests_account_2_abc'},
            {'name': 'ESTSAUTH', 'domain': '.login.live.com', 'value': 'session2'},
            # 其他 cookies
            {'name': 'other', 'domain': '.example.com', 'value': 'other_value'},
        ]

        # 检测多账号
        accounts = _detect_multiple_accounts(cookies, 'firefox')

        self.assertEqual(len(accounts), 2)

        # 选择第一个账号
        selected = accounts[0]

        # 过滤 cookies
        filtered = _filter_cookies_by_account(cookies, selected)

        # 验证：只有选中的 ESTSAUTHPERSISTENT 存在
        ests_auth_list = [c for c in filtered if c.get('name') == 'ESTSAUTHPERSISTENT']
        self.assertEqual(len(ests_auth_list), 1)
        self.assertEqual(ests_auth_list[0]['value'], 'ests_account_1_xyz')

        # 验证：非 Microsoft cookies 仍然存在
        other_cookies = [c for c in filtered if c.get('name') == 'other']
        self.assertEqual(len(other_cookies), 1)


if __name__ == '__main__':
    unittest.main()
