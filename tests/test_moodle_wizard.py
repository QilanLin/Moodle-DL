# -*- coding: utf-8 -*-
"""
moodle_wizard.py 单元测试

测试 Moodle 配置向导功能：
- Token 获取流程
- Moodle URL 规范化
- 认证器选择（SSO vs 普通）
- 错误处理
"""

import unittest
from unittest.mock import MagicMock, Mock, patch, AsyncMock
from io import StringIO

from moodle_dl.cli.moodle_wizard import MoodleWizard
from moodle_dl.types import MoodleDlOpts, MoodleURL
from moodle_dl.config import ConfigHelper


class TestMoodleWizardInit(unittest.TestCase):
    """MoodleWizard 初始化测试"""

    def test_init(self):
        """测试初始化"""
        config = Mock(spec=ConfigHelper)
        opts = Mock(spec=MoodleDlOpts)

        wizard = MoodleWizard(config, opts)

        self.assertEqual(wizard.config, config)
        self.assertEqual(wizard.opts, opts)


class TestInteractivelyGetMoodleUrl(unittest.TestCase):
    """interactively_get_moodle_url 方法测试"""

    def setUp(self):
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.wizard = MoodleWizard(self.config, self.opts)

    @patch('builtins.input', return_value='https://moodle.example.com')
    @patch('moodle_dl.moodle.moodle_service.MoodleService.split_moodle_url')
    def test_get_url_with_https(self, mock_split, mock_input):
        """测试获取带 https 的 URL"""
        mock_split.return_value = ('moodle.example.com', '/')

        result = self.wizard.interactively_get_moodle_url(use_stored_url=False)

        self.assertIsInstance(result, MoodleURL)
        mock_split.assert_called_once()

    @patch('builtins.input', return_value='moodle.example.com')
    @patch('moodle_dl.moodle.moodle_service.MoodleService.split_moodle_url')
    def test_get_url_without_protocol(self, mock_split, mock_input):
        """测试获取不带协议的 URL（自动添加 https）"""
        mock_split.return_value = ('moodle.example.com', '/')

        result = self.wizard.interactively_get_moodle_url(use_stored_url=False)

        self.assertIsInstance(result, MoodleURL)

    @patch('builtins.input', return_value='http://moodle.example.com')
    @patch('moodle_dl.moodle.moodle_service.MoodleService.split_moodle_url')
    @patch('moodle_dl.utils.Log.warning')
    def test_get_url_with_http_shows_warning(self, mock_warning, mock_split, mock_input):
        """测试使用 http 时显示警告"""
        mock_split.return_value = ('moodle.example.com', '/')

        result = self.wizard.interactively_get_moodle_url(use_stored_url=False)

        # Verify warning was shown
        self.assertTrue(mock_warning.called)
        self.assertTrue(result.use_http)

    def test_get_url_from_storage(self):
        """测试从存储中获取 URL"""
        stored_url = MoodleURL(False, 'moodle.example.com', '/')
        self.config.get_moodle_URL.return_value = stored_url

        result = self.wizard.interactively_get_moodle_url(use_stored_url=True)

        self.assertEqual(result, stored_url)


class TestInteractivelyAcquireToken(unittest.TestCase):
    """interactively_acquire_token 方法测试"""

    def setUp(self):
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.wizard = MoodleWizard(self.config, self.opts)

    @patch('moodle_dl.cli.moodle_wizard.MoodleWizard.interactively_get_moodle_url')
    @patch('moodle_dl.cli.moodle_wizard.SSOAuthenticator')
    def test_acquire_token_with_sso_flag(self, mock_sso_auth, mock_get_url):
        """测试使用 SSO 标志时选择 SSO 认证器"""
        from moodle_dl.types import MoodleURL
        mock_url = MoodleURL(False, 'moodle.example.com', '/')
        mock_get_url.return_value = mock_url

        mock_authenticator = Mock()
        mock_authenticator.execute.return_value = 'test_token_123'
        mock_sso_auth.return_value = mock_authenticator

        self.opts.sso = True

        result = self.wizard.interactively_acquire_token(use_stored_url=False)

        self.assertEqual(result, 'test_token_123')
        mock_sso_auth.assert_called_once()
        mock_authenticator.execute.assert_called_once()

    @patch('moodle_dl.cli.moodle_wizard.MoodleWizard.interactively_get_moodle_url')
    @patch('moodle_dl.cli.moodle_wizard.SSOAuthenticator')
    def test_acquire_token_with_existing_token(self, mock_sso_auth, mock_get_url):
        """测试已有 token 时选择 SSO 认证器"""
        from moodle_dl.types import MoodleURL
        mock_url = MoodleURL(False, 'moodle.example.com', '/')
        mock_get_url.return_value = mock_url

        mock_authenticator = Mock()
        mock_authenticator.execute.return_value = 'test_token_456'
        mock_sso_auth.return_value = mock_authenticator

        self.opts.sso = False
        self.opts.token = 'existing_token'

        result = self.wizard.interactively_acquire_token(use_stored_url=False)

        # When opts.token is not None, SSOAuthenticator is used
        self.assertEqual(result, 'test_token_456')
        mock_sso_auth.assert_called_once()

    @patch('moodle_dl.cli.moodle_wizard.MoodleWizard.interactively_get_moodle_url')
    @patch('moodle_dl.cli.moodle_wizard.NormalAuthenticator')
    def test_acquire_token_normal_login(self, mock_normal_auth, mock_get_url):
        """测试普通登录时选择 NormalAuthenticator"""
        from moodle_dl.types import MoodleURL
        mock_url = MoodleURL(False, 'moodle.example.com', '/')
        mock_get_url.return_value = mock_url

        mock_authenticator = Mock()
        mock_authenticator.execute.return_value = 'normal_token_789'
        mock_normal_auth.return_value = mock_authenticator

        self.opts.sso = False
        self.opts.token = None

        result = self.wizard.interactively_acquire_token(use_stored_url=False)

        self.assertEqual(result, 'normal_token_789')
        mock_normal_auth.assert_called_once()

    @patch('moodle_dl.cli.moodle_wizard.MoodleWizard.interactively_get_moodle_url')
    @patch('moodle_dl.cli.moodle_wizard.SSOAuthenticator')
    def test_acquire_token_authentication_error(self, mock_sso_auth, mock_get_url):
        """测试认证失败时的错误处理"""
        from moodle_dl.types import MoodleURL
        from moodle_dl.cli.authenticators import AuthenticationError

        mock_url = MoodleURL(False, 'moodle.example.com', '/')
        mock_get_url.return_value = mock_url

        mock_authenticator = Mock()
        mock_authenticator.execute.side_effect = AuthenticationError('Login failed')
        mock_sso_auth.return_value = mock_authenticator

        self.opts.sso = True

        # Should re-raise AuthenticationError
        with self.assertRaises(AuthenticationError):
            self.wizard.interactively_acquire_token(use_stored_url=False)

    @patch('moodle_dl.cli.moodle_wizard.MoodleWizard.interactively_get_moodle_url')
    @patch('moodle_dl.cli.moodle_wizard.SSOAuthenticator')
    def test_acquire_token_configuration_error(self, mock_sso_auth, mock_get_url):
        """测试配置提交失败时的错误处理"""
        from moodle_dl.types import MoodleURL
        from moodle_dl.cli.authenticators import ConfigurationTransactionError

        mock_url = MoodleURL(False, 'moodle.example.com', '/')
        mock_get_url.return_value = mock_url

        mock_authenticator = Mock()
        mock_authenticator.execute.side_effect = ConfigurationTransactionError('Config save failed')
        mock_sso_auth.return_value = mock_authenticator

        self.opts.sso = True

        # Should re-raise ConfigurationTransactionError
        with self.assertRaises(ConfigurationTransactionError):
            self.wizard.interactively_acquire_token(use_stored_url=False)


class TestDeprecatedMethods(unittest.TestCase):
    """已废弃方法测试"""

    def setUp(self):
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.wizard = MoodleWizard(self.config, self.opts)

    @patch('moodle_dl.cli.moodle_wizard.MoodleWizard.interactively_acquire_token')
    @patch('logging.warning')
    def test_interactively_acquire_normal_token_deprecated(self, mock_logging_warning, mock_acquire_token):
        """测试 interactively_acquire_normal_token 显示废弃警告"""
        mock_acquire_token.return_value = 'test_token'

        result = self.wizard.interactively_acquire_normal_token(use_stored_url=False)

        # Verify deprecation warning was logged
        self.assertTrue(mock_logging_warning.called)
        # The warning message uses Chinese "已废弃"
        warning_msg = str(mock_logging_warning.call_args[0][0])
        self.assertTrue('已废弃' in warning_msg or 'deprecated' in warning_msg.lower())
        self.assertEqual(result, 'test_token')

    @patch('moodle_dl.cli.moodle_wizard.MoodleWizard.interactively_acquire_token')
    @patch('logging.warning')
    def test_interactively_acquire_sso_token_deprecated(self, mock_logging_warning, mock_acquire_token):
        """测试 interactively_acquire_sso_token 显示废弃警告"""
        mock_acquire_token.return_value = 'test_token'

        result = self.wizard.interactively_acquire_sso_token(use_stored_url=False)

        # Verify deprecation warning was logged
        self.assertTrue(mock_logging_warning.called)
        # The warning message uses Chinese "已废弃"
        warning_msg = str(mock_logging_warning.call_args[0][0])
        self.assertTrue('已废弃' in warning_msg or 'deprecated' in warning_msg.lower())
        self.assertEqual(result, 'test_token')


if __name__ == '__main__':
    unittest.main()
