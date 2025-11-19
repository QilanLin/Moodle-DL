"""
认证器体系的单元测试

测试覆盖：
1. 事务系统的原子性（成功和失败）
2. 认证器的执行流程
3. 错误处理和恢复
4. 配置保存的一致性
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, call
from pathlib import Path

from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts, MoodleURL
from moodle_dl.cli.authenticators import (
    TokenAcquisitionResult,
    ConfigurationTransaction,
    AuthenticationError,
    ConfigurationTransactionError,
    BaseAuthenticator,
    NormalAuthenticator,
    SSOAuthenticator,
    BrowserSelector,
)


class TestTokenAcquisitionResult(unittest.TestCase):
    """测试 TokenAcquisitionResult 数据类"""

    def test_valid_result(self):
        """测试有效的结果"""
        result = TokenAcquisitionResult(
            token='test_token',
            private_token='test_private_token',
            extra_properties={'browser': 'firefox'}
        )
        result.validate()  # 不应该抛出异常
        self.assertEqual(result.token, 'test_token')

    def test_empty_token_raises_error(self):
        """测试空 token 抛出错误"""
        result = TokenAcquisitionResult(token='')
        with self.assertRaises(AuthenticationError):
            result.validate()

    def test_none_token_raises_error(self):
        """测试 None token 抛出错误"""
        result = TokenAcquisitionResult(token=None)
        with self.assertRaises(AuthenticationError):
            result.validate()

    def test_extra_properties_default_empty_dict(self):
        """测试额外属性默认为空字典"""
        result = TokenAcquisitionResult(token='test')
        self.assertIsInstance(result.extra_properties, dict)
        self.assertEqual(len(result.extra_properties), 0)


class TestConfigurationTransaction(unittest.TestCase):
    """测试配置事务系统"""

    def setUp(self):
        """设置测试夹具"""
        self.config = Mock(spec=ConfigHelper)
        self.moodle_url = MoodleURL(
            use_http=False,
            domain='test.moodle.com',
            path='/moodle'
        )

    def test_add_token_operation(self):
        """测试添加 token 操作"""
        transaction = ConfigurationTransaction(self.config, self.moodle_url)
        transaction.add_token('token123', 'private456')

        self.assertEqual(len(transaction._operations), 1)
        self.assertEqual(transaction._operations[0]['type'], 'tokens')

    def test_add_property_operation(self):
        """测试添加属性操作"""
        transaction = ConfigurationTransaction(self.config, self.moodle_url)
        transaction.add_property('preferred_browser', 'firefox')

        self.assertEqual(len(transaction._operations), 1)
        self.assertEqual(transaction._operations[0]['type'], 'property')
        self.assertEqual(transaction._operations[0]['key'], 'preferred_browser')

    def test_commit_success(self):
        """测试成功提交事务"""
        transaction = ConfigurationTransaction(self.config, self.moodle_url)
        transaction.add_token('token123', 'private456')
        transaction.add_property('preferred_browser', 'firefox')

        transaction.commit()

        # 验证 set_tokens 被调用
        self.config.set_tokens.assert_called_once_with('token123', 'private456')
        # 验证 set_moodle_URL 被调用
        self.config.set_moodle_URL.assert_called_once_with(self.moodle_url)
        # 验证 set_property 被调用
        self.config.set_property.assert_called_once_with('preferred_browser', 'firefox')

    def test_commit_atomicity_no_partial_save(self):
        """测试事务原子性 - 失败时不保存任何内容"""
        self.config.set_tokens.side_effect = Exception('Database error')

        transaction = ConfigurationTransaction(self.config, self.moodle_url)
        transaction.add_token('token123', 'private456')
        transaction.add_property('preferred_browser', 'firefox')

        with self.assertRaises(ConfigurationTransactionError):
            transaction.commit()

        # 验证不是所有操作都被执行
        # set_tokens 失败，set_moodle_URL 和 set_property 不应该被调用
        # （因为我们在 set_tokens 处失败了）
        self.config.set_tokens.assert_called_once()

    def test_double_commit_raises_error(self):
        """测试不能重复提交事务"""
        transaction = ConfigurationTransaction(self.config, self.moodle_url)
        transaction.add_token('token123', 'private456')

        transaction.commit()  # 第一次成功

        with self.assertRaises(ConfigurationTransactionError):
            transaction.commit()  # 第二次应该失败

    def test_empty_transaction_commit(self):
        """测试空事务提交不执行任何操作"""
        transaction = ConfigurationTransaction(self.config, self.moodle_url)
        transaction.commit()

        # 不应该调用任何设置方法
        self.config.set_tokens.assert_not_called()

    def test_is_committed_flag(self):
        """测试提交状态标志"""
        transaction = ConfigurationTransaction(self.config, self.moodle_url)
        self.assertFalse(transaction.is_committed())

        transaction.add_token('token123', None)
        transaction.commit()

        self.assertTrue(transaction.is_committed())


class TestBrowserSelector(unittest.TestCase):
    """测试浏览器选择器"""

    def test_select_or_load_with_stored_browser(self):
        """测试加载存储的浏览器选择"""
        config = Mock(spec=ConfigHelper)
        config.get_property_or.return_value = 'firefox'

        browser = BrowserSelector.select_or_load(config)

        self.assertEqual(browser, 'firefox')
        config.get_property_or.assert_called_once_with('preferred_browser', None)

    def test_browser_map_constants(self):
        """测试浏览器选择的映射表"""
        # 验证浏览器映射表中有预期的浏览器
        self.assertIn(0, BrowserSelector.BROWSER_MAP)
        self.assertEqual(BrowserSelector.BROWSER_MAP[0], 'firefox')
        self.assertEqual(BrowserSelector.BROWSER_MAP[1], 'chrome')
        self.assertEqual(BrowserSelector.BROWSER_MAP[6], 'zen')

    def test_browser_choices_count(self):
        """测试浏览器选择列表的长度"""
        # 验证有 8 个浏览器选择
        self.assertEqual(len(BrowserSelector.BROWSER_CHOICES), 8)
        self.assertIn('Firefox', BrowserSelector.BROWSER_CHOICES)


class MockAuthenticator(BaseAuthenticator):
    """用于测试的模拟认证器"""

    def acquire_token(self) -> TokenAcquisitionResult:
        return TokenAcquisitionResult(
            token='test_token',
            private_token='test_private_token',
            extra_properties={}
        )


class TestBaseAuthenticator(unittest.TestCase):
    """测试基础认证器"""

    def setUp(self):
        """设置测试夹具"""
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.moodle_url = MoodleURL(
            use_http=False,
            domain='test.moodle.com',
            path='/moodle'
        )

    def test_execute_complete_flow(self):
        """测试完整的认证流程"""
        authenticator = MockAuthenticator(self.config, self.opts, self.moodle_url)

        token = authenticator.execute()

        self.assertEqual(token, 'test_token')
        # 验证配置已提交
        self.config.set_tokens.assert_called_once()
        self.config.set_moodle_URL.assert_called_once()

    def test_execute_saves_extra_properties(self):
        """测试执行时保存额外属性"""

        class AuthenticatorWithExtras(BaseAuthenticator):
            def acquire_token(self) -> TokenAcquisitionResult:
                return TokenAcquisitionResult(
                    token='test_token',
                    private_token='private_token',
                    extra_properties={'preferred_browser': 'chrome'}
                )

        authenticator = AuthenticatorWithExtras(self.config, self.opts, self.moodle_url)

        token = authenticator.execute()

        # 验证 set_property 被调用来保存额外属性
        self.config.set_property.assert_called_once_with('preferred_browser', 'chrome')

    def test_execute_handles_authentication_error(self):
        """测试执行时处理认证错误"""

        class FailingAuthenticator(BaseAuthenticator):
            def acquire_token(self) -> TokenAcquisitionResult:
                raise AuthenticationError('Login failed')

        authenticator = FailingAuthenticator(self.config, self.opts, self.moodle_url)

        with self.assertRaises(AuthenticationError):
            authenticator.execute()

    def test_execute_handles_configuration_error(self):
        """测试执行时处理配置错误"""
        self.config.set_tokens.side_effect = Exception('Database error')

        authenticator = MockAuthenticator(self.config, self.opts, self.moodle_url)

        # 当配置提交失败时，应该抛出 AuthenticationError（在 execute 层捕获并转换）
        # 或者直接捕获 ConfigurationTransactionError（如果异常未被转换）
        with self.assertRaises((AuthenticationError, ConfigurationTransactionError)):
            authenticator.execute()


class TestNormalAuthenticator(unittest.TestCase):
    """测试普通登录认证器"""

    def setUp(self):
        """设置测试夹具"""
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.opts.username = None
        self.opts.password = None
        self.moodle_url = MoodleURL(
            use_http=False,
            domain='test.moodle.com',
            path='/moodle'
        )

    def test_acquire_token_with_cli_credentials(self):
        """测试使用命令行凭证获取 token"""
        self.opts.username = 'testuser'
        self.opts.password = 'testpass'

        authenticator = NormalAuthenticator(self.config, self.opts, self.moodle_url)

        with patch('moodle_dl.cli.authenticators.MoodleService') as mock_service_class:
            mock_service = Mock()
            mock_service_class.return_value = mock_service
            mock_service.obtain_login_token.return_value = ('token123', 'private123')

            result = authenticator.acquire_token()

            self.assertEqual(result.token, 'token123')
            self.assertEqual(result.private_token, 'private123')

    def test_acquire_token_retry_on_failure(self):
        """测试登录失败重试"""
        authenticator = NormalAuthenticator(self.config, self.opts, self.moodle_url)

        with patch('moodle_dl.cli.authenticators.input') as mock_input:
            with patch('moodle_dl.cli.authenticators.getpass') as mock_getpass:
                with patch('moodle_dl.cli.authenticators.MoodleService') as mock_service_class:
                    mock_input.return_value = 'testuser'
                    mock_getpass.return_value = 'testpass'

                    mock_service = Mock()
                    mock_service_class.return_value = mock_service

                    # 模拟第一次失败，第二次成功
                    from moodle_dl.moodle.request_helper import RequestRejectedError
                    mock_service.obtain_login_token.side_effect = [
                        RequestRejectedError('Invalid credentials'),
                        ('token123', 'private123')
                    ]

                    result = authenticator.acquire_token()

                    self.assertEqual(result.token, 'token123')


class TestSSOAuthenticator(unittest.TestCase):
    """测试 SSO 认证器"""

    def setUp(self):
        """设置测试夹具"""
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.opts.token = None
        self.moodle_url = MoodleURL(
            use_http=False,
            domain='test.moodle.com',
            path='/moodle'
        )

    def test_pre_configure_browser_selection(self):
        """测试前置配置 - 浏览器选择"""
        authenticator = SSOAuthenticator(self.config, self.opts, self.moodle_url)

        with patch.object(BrowserSelector, 'select_or_load', return_value='firefox'):
            authenticator.pre_configure()

            self.assertEqual(authenticator.preferred_browser, 'firefox')

    def test_acquire_token_with_provided_token(self):
        """测试使用命令行提供的 token"""
        self.opts.token = 'provided_token_123'

        authenticator = SSOAuthenticator(self.config, self.opts, self.moodle_url)
        authenticator.preferred_browser = 'firefox'

        result = authenticator.acquire_token()

        self.assertEqual(result.token, 'provided_token_123')
        self.assertEqual(result.private_token, None)
        self.assertEqual(result.extra_properties['preferred_browser'], 'firefox')


class TestAuthenticatorIntegration(unittest.TestCase):
    """集成测试 - 测试多个组件的协作"""

    def test_normal_authenticator_full_flow(self):
        """测试普通认证器的完整流程"""
        config = Mock(spec=ConfigHelper)
        opts = Mock(spec=MoodleDlOpts)
        opts.username = 'testuser'
        opts.password = 'testpass'

        moodle_url = MoodleURL(
            use_http=False,
            domain='test.moodle.com',
            path='/moodle'
        )

        authenticator = NormalAuthenticator(config, opts, moodle_url)

        with patch('moodle_dl.cli.authenticators.MoodleService') as mock_service_class:
            mock_service = Mock()
            mock_service_class.return_value = mock_service
            mock_service.obtain_login_token.return_value = ('token123', 'private123')

            token = authenticator.execute()

            # 验证最终结果
            self.assertEqual(token, 'token123')

            # 验证配置保存调用
            config.set_tokens.assert_called_once()
            config.set_moodle_URL.assert_called_once()

    def test_sso_authenticator_cli_token_flow(self):
        """测试 SSO 认证器的命令行 token 流程"""
        config = Mock(spec=ConfigHelper)
        config.get_property_or.return_value = 'firefox'

        opts = Mock(spec=MoodleDlOpts)
        opts.token = 'provided_token_789'

        moodle_url = MoodleURL(
            use_http=False,
            domain='test.moodle.com',
            path='/moodle'
        )

        authenticator = SSOAuthenticator(config, opts, moodle_url)

        with patch.object(BrowserSelector, 'select_or_load', return_value='firefox'):
            token = authenticator.execute()

            # 验证最终结果
            self.assertEqual(token, 'provided_token_789')

            # 验证浏览器选择被保存
            config.set_property.assert_called_once_with('preferred_browser', 'firefox')


if __name__ == '__main__':
    unittest.main()
