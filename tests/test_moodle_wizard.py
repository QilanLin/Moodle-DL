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
from types import SimpleNamespace

from moodle_dl.cli.moodle_wizard import MoodleWizard
from moodle_dl.types import MoodleDlOpts, MoodleURL
from moodle_dl.config import ConfigHelper
from moodle_dl.exceptions import RequestRejectedError


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

    @patch('moodle_dl.cli.moodle_wizard.MoodleWizard.interactively_get_moodle_url')
    @patch('moodle_dl.cli.moodle_wizard.NormalAuthenticator')
    @patch('moodle_dl.cli.moodle_wizard.Log.error')
    def test_acquire_token_unexpected_error_is_logged_and_reraised(self, mock_log_error, mock_normal_auth, mock_get_url):
        """测试未预期异常会记录并重新抛出"""
        mock_url = MoodleURL(False, 'moodle.example.com', '/')
        mock_get_url.return_value = mock_url
        mock_authenticator = Mock()
        mock_authenticator.execute.side_effect = RuntimeError('unexpected')
        mock_normal_auth.return_value = mock_authenticator

        self.opts.sso = False
        self.opts.token = None

        with self.assertRaises(RuntimeError):
            self.wizard.interactively_acquire_token(use_stored_url=True)

        mock_log_error.assert_called_once()


class TestDeprecatedNormalTokenImplementation(unittest.TestCase):
    """旧版 normal token 实现的兼容性测试"""

    def setUp(self):
        self.config = Mock(spec=ConfigHelper)
        self.config.get_misc_files_path.return_value = '/tmp/moodle'
        self.opts = MoodleDlOpts()
        self.wizard = MoodleWizard(self.config, self.opts)
        self.moodle_url = MoodleURL(False, 'moodle.example.com', '/')

    def _mock_successful_login(self, mock_moodle_service, token='token', private_token='private'):
        mock_moodle_service.return_value.obtain_login_token.return_value = (token, private_token)

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.info')
    @patch('moodle_dl.cli.moodle_wizard.Log.warning')
    @patch('moodle_dl.cli.moodle_wizard.Log.success')
    @patch('builtins.print')
    def test_deprecated_normal_token_automated_success_without_cookie_script(
        self,
        mock_print,
        mock_success,
        mock_warning,
        mock_info,
        mock_moodle_service,
    ):
        """测试自动化旧流程成功，且缺少 cookie 导出脚本时仍保存 token"""
        self.opts.username = 'student'
        self.opts.password = 'secret'
        self._mock_successful_login(mock_moodle_service)

        with (
            patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url) as get_url,
            patch('os.path.exists', return_value=False),
        ):
            result = self.wizard._deprecated_interactively_acquire_normal_token(use_stored_url=True)

        self.assertEqual(result, 'token')
        get_url.assert_called_once_with(True)
        mock_moodle_service.return_value.obtain_login_token.assert_called_once_with(
            'student',
            'secret',
            self.moodle_url,
        )
        self.config.set_tokens.assert_called_once_with('token', 'private')
        self.config.set_moodle_URL.assert_called_once_with(self.moodle_url)
        mock_success.assert_any_call('令牌已成功保存！')
        mock_warning.assert_called_with('⚠️  未找到export_browser_cookies.py')

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.blue_str', return_value='export cookies?')
    @patch('moodle_dl.cli.moodle_wizard.Log.info')
    @patch('moodle_dl.cli.moodle_wizard.Log.success')
    @patch('builtins.print')
    def test_deprecated_normal_token_manual_success_exports_cookies(
        self,
        mock_print,
        mock_success,
        mock_info,
        mock_blue,
        mock_moodle_service,
    ):
        """测试手动旧流程成功后可导出浏览器 cookies"""
        self.opts.username = None
        self.opts.password = None
        self._mock_successful_login(mock_moodle_service, token='manual-token', private_token='manual-private')
        export_module = SimpleNamespace(export_cookies_interactive=Mock(return_value=True))
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock()))

        with (
            patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url),
            patch('builtins.input', return_value='student'),
            patch('moodle_dl.cli.moodle_wizard.getpass', return_value='secret'),
            patch('os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=spec) as spec_from_file,
            patch('importlib.util.module_from_spec', return_value=export_module),
            patch('moodle_dl.utils.PathTools.get_cookies_path', return_value='/tmp/moodle/Cookies.txt'),
            patch('moodle_dl.utils.Cutie.prompt_yes_or_no', return_value=True) as prompt,
        ):
            result = self.wizard._deprecated_interactively_acquire_normal_token()

        self.assertEqual(result, 'manual-token')
        mock_moodle_service.return_value.obtain_login_token.assert_called_once_with(
            'student',
            'secret',
            self.moodle_url,
        )
        spec_from_file.assert_called_once()
        spec.loader.exec_module.assert_called_once_with(export_module)
        prompt.assert_called_once_with('export cookies?', default_is_yes=True)
        export_module.export_cookies_interactive.assert_called_once_with(
            domain='moodle.example.com',
            output_file='/tmp/moodle/Cookies.txt',
            ask_browser=True,
            auto_get_token=False,
        )
        mock_success.assert_any_call('✅ Cookies导出成功！')

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.blue_str', return_value='export cookies?')
    @patch('moodle_dl.cli.moodle_wizard.Log.info')
    @patch('builtins.print')
    def test_deprecated_normal_token_user_can_skip_cookie_export(
        self,
        mock_print,
        mock_info,
        mock_blue,
        mock_moodle_service,
    ):
        """测试用户可以跳过 cookie 导出"""
        self.opts.username = 'student'
        self.opts.password = 'secret'
        self._mock_successful_login(mock_moodle_service)
        export_module = SimpleNamespace(export_cookies_interactive=Mock(return_value=True))
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock()))

        with (
            patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url),
            patch('os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=spec),
            patch('importlib.util.module_from_spec', return_value=export_module),
            patch('moodle_dl.utils.PathTools.get_cookies_path', return_value='/tmp/moodle/Cookies.txt'),
            patch('moodle_dl.utils.Cutie.prompt_yes_or_no', return_value=False),
        ):
            result = self.wizard._deprecated_interactively_acquire_normal_token()

        self.assertEqual(result, 'token')
        export_module.export_cookies_interactive.assert_not_called()
        mock_info.assert_any_call('跳过cookies导出，你可以稍后在配置步骤7导出')

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.blue_str', return_value='export cookies?')
    @patch('moodle_dl.cli.moodle_wizard.Log.warning')
    @patch('builtins.print')
    def test_deprecated_normal_token_reports_cookie_export_failure(
        self,
        mock_print,
        mock_warning,
        mock_blue,
        mock_moodle_service,
    ):
        """测试 cookie 导出返回失败时会提示但不影响 token 返回"""
        self.opts.username = 'student'
        self.opts.password = 'secret'
        self._mock_successful_login(mock_moodle_service)
        export_module = SimpleNamespace(export_cookies_interactive=Mock(return_value=False))
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock()))

        with (
            patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url),
            patch('os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=spec),
            patch('importlib.util.module_from_spec', return_value=export_module),
            patch('moodle_dl.utils.PathTools.get_cookies_path', return_value='/tmp/moodle/Cookies.txt'),
            patch('moodle_dl.utils.Cutie.prompt_yes_or_no', return_value=True),
        ):
            result = self.wizard._deprecated_interactively_acquire_normal_token()

        self.assertEqual(result, 'token')
        mock_warning.assert_any_call('⚠️  Cookies导出失败，你可以稍后在配置步骤7重新导出')

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.warning')
    @patch('moodle_dl.cli.moodle_wizard.Log.info')
    @patch('builtins.print')
    def test_deprecated_normal_token_handles_cookie_export_import_error(
        self,
        mock_print,
        mock_info,
        mock_warning,
        mock_moodle_service,
    ):
        """测试导出脚本依赖缺失时不会影响 token 返回"""
        self.opts.username = 'student'
        self.opts.password = 'secret'
        self._mock_successful_login(mock_moodle_service)
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock(side_effect=ImportError('missing dep'))))

        with (
            patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url),
            patch('os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=spec),
            patch('importlib.util.module_from_spec', return_value=SimpleNamespace()),
        ):
            result = self.wizard._deprecated_interactively_acquire_normal_token()

        self.assertEqual(result, 'token')
        mock_warning.assert_any_call('⚠️  缺少依赖库: missing dep')
        mock_info.assert_any_call('   提示：运行 `pip install browser-cookie3`')

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.warning')
    @patch('builtins.print')
    def test_deprecated_normal_token_handles_cookie_export_runtime_error(
        self,
        mock_print,
        mock_warning,
        mock_moodle_service,
    ):
        """测试 cookie 导出过程异常时不会影响 token 返回"""
        self.opts.username = 'student'
        self.opts.password = 'secret'
        self._mock_successful_login(mock_moodle_service)
        export_module = SimpleNamespace(export_cookies_interactive=Mock(side_effect=RuntimeError('browser locked')))
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock()))

        with (
            patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url),
            patch('os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=spec),
            patch('importlib.util.module_from_spec', return_value=export_module),
            patch('moodle_dl.utils.PathTools.get_cookies_path', return_value='/tmp/moodle/Cookies.txt'),
            patch('moodle_dl.utils.Cutie.prompt_yes_or_no', return_value=True),
            patch('moodle_dl.cli.moodle_wizard.Log.blue_str', return_value='export cookies?'),
        ):
            result = self.wizard._deprecated_interactively_acquire_normal_token()

        self.assertEqual(result, 'token')
        mock_warning.assert_any_call('⚠️  导出cookies出错: browser locked')

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.error')
    @patch('moodle_dl.cli.moodle_wizard.sys.exit', side_effect=SystemExit(1))
    @patch('builtins.print')
    def test_deprecated_normal_token_automated_login_rejection_exits(
        self,
        mock_print,
        mock_exit,
        mock_log_error,
        mock_moodle_service,
    ):
        """测试自动化旧流程登录被拒绝时退出"""
        self.opts.username = 'student'
        self.opts.password = 'secret'
        mock_moodle_service.return_value.obtain_login_token.side_effect = RequestRejectedError('bad credentials')

        with patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url):
            with self.assertRaises(SystemExit):
                self.wizard._deprecated_interactively_acquire_normal_token()

        mock_log_error.assert_called_once()
        mock_exit.assert_called_once_with(1)

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.error')
    @patch('moodle_dl.cli.moodle_wizard.sys.exit', side_effect=SystemExit(1))
    @patch('builtins.print')
    def test_deprecated_normal_token_automated_runtime_error_exits(
        self,
        mock_print,
        mock_exit,
        mock_log_error,
        mock_moodle_service,
    ):
        """测试自动化旧流程 Moodle 通信错误时退出"""
        self.opts.username = 'student'
        self.opts.password = 'secret'
        mock_moodle_service.return_value.obtain_login_token.side_effect = RuntimeError('moodle unavailable')

        with patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url):
            with self.assertRaises(SystemExit):
                self.wizard._deprecated_interactively_acquire_normal_token()

        mock_log_error.assert_called_once()
        mock_exit.assert_called_once_with(1)

    @patch('moodle_dl.cli.moodle_wizard.MoodleService')
    @patch('moodle_dl.cli.moodle_wizard.Log.error')
    @patch('moodle_dl.cli.moodle_wizard.sys.exit', side_effect=SystemExit(1))
    @patch('builtins.print')
    def test_deprecated_normal_token_automated_connection_error_exits(
        self,
        mock_print,
        mock_exit,
        mock_log_error,
        mock_moodle_service,
    ):
        """测试自动化旧流程连接错误时退出"""
        self.opts.username = 'student'
        self.opts.password = 'secret'
        mock_moodle_service.return_value.obtain_login_token.side_effect = ConnectionError('offline')

        with patch.object(self.wizard, 'interactively_get_moodle_url', return_value=self.moodle_url):
            with self.assertRaises(SystemExit):
                self.wizard._deprecated_interactively_acquire_normal_token()

        mock_log_error.assert_called_once_with('offline')
        mock_exit.assert_called_once_with(1)


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
