import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from moodle_dl.cli.authenticators import (
    AuthenticationError,
    BaseAuthenticator,
    BrowserSelector,
    ConfigurationTransaction,
    ConfigurationTransactionError,
    ExportBrowserCookiesHelper,
    NormalAuthenticator,
    SSOAuthenticator,
    TokenAcquisitionResult,
    _read_bool_env,
)
from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import MoodleDlOpts, MoodleURL


def make_opts(**overrides):
    values = {
        "username": None,
        "password": None,
        "token": None,
        "path": ".",
        "verbose": 0,
        "log_responses": False,
        "skip_cert_verify": False,
        "allow_insecure_ssl": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_config():
    config = Mock(spec=ConfigHelper)
    config.get_property_or.return_value = None
    config.get_misc_files_path.return_value = "/tmp/moodle-dl-test"
    config.get_auth_manager.return_value = Mock()
    return config


def make_url(domain="example.test", path="/"):
    return MoodleURL(use_http=False, domain=domain, path=path)


class DummyAuthenticator(BaseAuthenticator):
    def acquire_token(self):
        return TokenAcquisitionResult(token="token", private_token="private")


def test_read_bool_env_recognizes_true_false_and_invalid(caplog):
    cases = {
        "1": True,
        "true": True,
        "YES": True,
        "on": True,
        "0": False,
        "False": False,
        "n": False,
        "off": False,
    }

    for raw, expected in cases.items():
        with patch.dict(os.environ, {"MOODLE_DL_BOOL_TEST": raw}, clear=False):
            assert _read_bool_env("MOODLE_DL_BOOL_TEST") is expected

    with patch.dict(os.environ, {}, clear=True):
        assert _read_bool_env("MOODLE_DL_BOOL_TEST") is None

    with patch.dict(os.environ, {"MOODLE_DL_BOOL_TEST": "maybe"}, clear=False):
        assert _read_bool_env("MOODLE_DL_BOOL_TEST") is None

    assert "无法识别" in caplog.text


def test_token_result_rejects_non_string_token():
    with pytest.raises(AuthenticationError, match="字符串"):
        TokenAcquisitionResult(token=123, private_token="private").validate()


def test_configuration_transaction_reports_property_write_failure():
    config = Mock(spec=ConfigHelper)
    config.set_tokens.return_value = None
    config.set_moodle_URL.return_value = None
    config.set_property.side_effect = RuntimeError("disk full")
    transaction = ConfigurationTransaction(config, make_url())

    transaction.add_token("token", "private")
    transaction.add_property("preferred_browser", "firefox")

    with pytest.raises(ConfigurationTransactionError, match="提交失败"):
        transaction.commit()

    assert transaction.is_committed() is False
    config.set_tokens.assert_called_once()
    config.set_moodle_URL.assert_called_once()


def test_browser_selector_interactive_choice_and_invalid_choice():
    config = make_config()

    with patch("moodle_dl.utils.Cutie.select", return_value=4):
        assert BrowserSelector.select_or_load(config) == "brave"

    with patch("moodle_dl.utils.Cutie.select", return_value=99):
        with pytest.raises(AuthenticationError, match="无效的浏览器选择"):
            BrowserSelector.select_or_load(config)


def test_export_browser_cookies_helper_missing_file_and_loader_failure():
    with patch("moodle_dl.cli.authenticators.os.path.exists", return_value=False):
        with pytest.raises(FileNotFoundError):
            ExportBrowserCookiesHelper.load_export_module()

    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock(side_effect=RuntimeError("boom"))))
    with (
        patch("moodle_dl.cli.authenticators.os.path.exists", return_value=True),
        patch("importlib.util.spec_from_file_location", return_value=spec),
        patch("importlib.util.module_from_spec", return_value=SimpleNamespace()),
    ):
        with pytest.raises(ImportError, match="无法加载 export_browser_cookies"):
            ExportBrowserCookiesHelper.load_export_module()


def test_base_authenticator_requires_acquired_result_before_commit():
    auth = DummyAuthenticator(make_config(), Mock(spec=MoodleDlOpts), make_url())

    with pytest.raises(ConfigurationTransactionError, match="没有 token"):
        auth.commit_configuration()

    with pytest.raises(AuthenticationError, match="Token 获取结果为空"):
        auth._validate_result()


def test_normal_authenticator_validates_interactive_credentials():
    auth = NormalAuthenticator(make_config(), make_opts(), make_url())

    with patch("moodle_dl.cli.authenticators.input", return_value="  "):
        with pytest.raises(AuthenticationError, match="用户名不能为空"):
            auth._get_credentials()

    with (
        patch("moodle_dl.cli.authenticators.input", return_value="student"),
        patch("moodle_dl.cli.authenticators.getpass", return_value=""),
    ):
        with pytest.raises(AuthenticationError, match="密码不能为空"):
            auth._get_credentials()


def test_normal_authenticator_cli_mode_rejects_empty_token():
    auth = NormalAuthenticator(
        make_config(),
        make_opts(username="student", password="secret"),
        make_url(),
    )
    service = Mock()
    service.obtain_login_token.return_value = ("", None)

    with patch("moodle_dl.cli.authenticators.MoodleService", return_value=service):
        with pytest.raises(AuthenticationError, match="未收到有效的 token"):
            auth.acquire_token()


def test_normal_authenticator_retries_interactive_rejected_login_then_succeeds():
    auth = NormalAuthenticator(make_config(), make_opts(), make_url())
    service = Mock()
    service.obtain_login_token.side_effect = [
        RequestRejectedError("bad credentials"),
        ("token", "private"),
    ]

    with (
        patch("moodle_dl.cli.authenticators.input", side_effect=["student", "student"]),
        patch("moodle_dl.cli.authenticators.getpass", side_effect=["wrong", "secret"]),
        patch("moodle_dl.cli.authenticators.MoodleService", return_value=service),
        patch.object(auth, "_prompt_cookies_export") as prompt_export,
    ):
        result = auth.acquire_token()

    assert result.token == "token"
    assert result.private_token == "private"
    assert service.obtain_login_token.call_count == 2
    prompt_export.assert_called_once()


def test_normal_authenticator_exhausts_retryable_login_errors():
    auth = NormalAuthenticator(make_config(), make_opts(), make_url())
    service = Mock()
    service.obtain_login_token.side_effect = ValueError("invalid response")

    with (
        patch("moodle_dl.cli.authenticators.input", side_effect=["student", "student", "student"]),
        patch("moodle_dl.cli.authenticators.getpass", side_effect=["secret", "secret", "secret"]),
        patch("moodle_dl.cli.authenticators.MoodleService", return_value=service),
    ):
        with pytest.raises(AuthenticationError, match="与 Moodle 系统通信时出错: invalid response"):
            auth.acquire_token()

    assert service.obtain_login_token.call_count == 3


def test_prompt_cookies_export_skips_when_user_declines():
    auth = NormalAuthenticator(make_config(), make_opts(), make_url())

    with (
        patch("moodle_dl.utils.Cutie.prompt_yes_or_no", return_value=False),
        patch.object(ExportBrowserCookiesHelper, "load_export_module") as load_module,
    ):
        auth._prompt_cookies_export()

    load_module.assert_not_called()


def test_prompt_cookies_export_runs_helper_when_available():
    config = make_config()
    auth = NormalAuthenticator(config, make_opts(), make_url(path="/moodle"))
    export_module = Mock()
    export_module.export_cookies_interactive.return_value = True

    with (
        patch("moodle_dl.utils.Cutie.prompt_yes_or_no", return_value=True),
        patch.object(ExportBrowserCookiesHelper, "load_export_module", return_value=export_module),
        patch("moodle_dl.utils.PathTools.get_cookies_path", return_value="/tmp/cookies.txt"),
    ):
        auth._prompt_cookies_export()

    export_module.export_cookies_interactive.assert_called_once_with(
        domain="example.test",
        output_file="/tmp/cookies.txt",
        ask_browser=True,
        auto_get_token=False,
    )


def test_sso_pre_configure_wraps_browser_selection_errors():
    auth = SSOAuthenticator(make_config(), make_opts(), make_url())

    with patch.object(BrowserSelector, "select_or_load", side_effect=RuntimeError("broken terminal")):
        with pytest.raises(AuthenticationError, match="前置配置出错"):
            auth.pre_configure()


def test_sso_acquire_token_uses_automatic_result_before_manual_prompt():
    auth = SSOAuthenticator(make_config(), make_opts(), make_url())
    auth.preferred_browser = "firefox"
    auth._try_automatic_sso_flow = Mock(return_value=("token", "private"))

    with patch.object(auth, "_get_manual_token") as manual_token:
        result = auth.acquire_token()

    assert result.token == "token"
    assert result.private_token == "private"
    assert result.extra_properties == {"preferred_browser": "firefox"}
    manual_token.assert_not_called()


def test_sso_acquire_token_falls_back_to_manual_and_rejects_empty_result():
    auth = SSOAuthenticator(make_config(), make_opts(), make_url())
    auth.preferred_browser = "firefox"
    auth._try_automatic_sso_flow = Mock(return_value=(None, None))
    auth._get_manual_token = Mock(return_value=("manual-token", "manual-private"))

    result = auth.acquire_token()

    assert result.token == "manual-token"
    assert result.private_token == "manual-private"

    auth._get_manual_token = Mock(return_value=(None, None))
    with pytest.raises(AuthenticationError, match="无法获取有效的 token"):
        auth.acquire_token()


def test_sso_automatic_flow_success_and_fallback_failure():
    auth = SSOAuthenticator(make_config(), make_opts(), make_url())
    auth.preferred_browser = "firefox"
    export_module = Mock()

    with (
        patch.object(ExportBrowserCookiesHelper, "load_export_module", return_value=export_module),
        patch("moodle_dl.utils.PathTools.get_cookies_path", return_value="/tmp/cookies.txt"),
    ):
        auth._perform_sso_auto_login = Mock(return_value=True)
        auth._extract_api_token = Mock(return_value=("token", "private"))
        assert auth._try_automatic_sso_flow() == ("token", "private")

        auth._perform_sso_auto_login = Mock(return_value=False)
        auth._fallback_read_browser_cookies = Mock(return_value=False)
        assert auth._try_automatic_sso_flow() == (None, None)


def test_sso_automatic_flow_handles_missing_export_helper():
    auth = SSOAuthenticator(make_config(), make_opts(), make_url())

    with patch.object(ExportBrowserCookiesHelper, "load_export_module", side_effect=FileNotFoundError):
        assert auth._try_automatic_sso_flow() == (None, None)


class FakeCookie:
    name = "MoodleSession"
    value = "abc123"
    domain = ".example.test"
    path = None
    expires = None
    secure = True

    def has_nonstandard_attr(self, name):
        return name == "HttpOnly"

    def get_nonstandard_attr(self, name, default=None):
        if name == "SameSite":
            return "Strict"
        return default


def test_sso_fallback_read_browser_cookies_converts_and_saves_cookie_batch():
    config = make_config()
    auth_manager = Mock()
    auth_manager.save_sso_cookies.return_value = "session-id"
    config.get_auth_manager.return_value = auth_manager
    auth = SSOAuthenticator(config, make_opts(), make_url())
    auth.preferred_browser = "firefox"
    auth._export_module = Mock()
    auth._export_module.get_cookies_from_browser.return_value = [FakeCookie()]

    assert auth._fallback_read_browser_cookies() is True

    auth_manager.save_sso_cookies.assert_called_once()
    saved_cookies = auth_manager.save_sso_cookies.call_args.args[0]
    assert saved_cookies == [
        {
            "name": "MoodleSession",
            "value": "abc123",
            "domain": ".example.test",
            "path": "/",
            "expires": 0,
            "secure": True,
            "httponly": True,
            "samesite": "Strict",
        }
    ]


def test_sso_fallback_read_browser_cookies_returns_false_for_unusable_inputs():
    auth = SSOAuthenticator(make_config(), make_opts(), make_url())
    assert auth._fallback_read_browser_cookies() is False

    auth._export_module = Mock()
    auth._export_module.get_cookies_from_browser.return_value = []
    assert auth._fallback_read_browser_cookies() is False

    auth._export_module.get_cookies_from_browser.side_effect = RuntimeError("browser locked")
    assert auth._fallback_read_browser_cookies() is False


def test_sso_extract_api_token_uses_valid_cookie_session():
    config = make_config()
    auth_manager = Mock()
    auth_manager.get_valid_session.return_value = {"session_id": "sid"}
    auth_manager.get_session_cookies.return_value = [{"name": "MoodleSession", "value": "abc"}]
    config.get_auth_manager.return_value = auth_manager
    auth = SSOAuthenticator(config, make_opts(), make_url())
    auth._export_module = Mock()
    auth._export_module.extract_api_token_with_playwright_from_cookies.return_value = (
        "token",
        "private",
    )

    assert auth._extract_api_token() == ("token", "private")
    auth._export_module.extract_api_token_with_playwright_from_cookies.assert_called_once_with(
        "example.test",
        [{"name": "MoodleSession", "value": "abc"}],
    )


def test_sso_extract_api_token_returns_empty_on_missing_session_or_token():
    config = make_config()
    auth_manager = Mock()
    config.get_auth_manager.return_value = auth_manager
    auth = SSOAuthenticator(config, make_opts(), make_url())

    assert auth._extract_api_token() == (None, None)

    auth._export_module = Mock()
    auth_manager.get_valid_session.return_value = None
    auth_manager.get_all_sessions.return_value = []
    assert auth._extract_api_token() == (None, None)

    auth_manager.get_valid_session.return_value = {"session_id": "sid"}
    auth_manager.get_session_cookies.return_value = []
    assert auth._extract_api_token() == (None, None)

    auth_manager.get_session_cookies.return_value = [{"name": "MoodleSession"}]
    auth._export_module.extract_api_token_with_playwright_from_cookies.return_value = ("token", None)
    assert auth._extract_api_token() == (None, None)


def test_sso_manual_token_parsing_success_and_failure_paths():
    auth = SSOAuthenticator(make_config(), make_opts(), make_url())

    with (
        patch("moodle_dl.cli.authenticators.SSOReferenceHelper.show_manual_token_help") as show_help,
        patch("moodle_dl.cli.authenticators.input", return_value="moodledl://token=abc"),
        patch("moodle_dl.cli.authenticators.MoodleService.extract_token", return_value=("token", "private")),
    ):
        assert auth._get_manual_token() == ("token", "private")
        show_help.assert_called_once()

    with (
        patch("moodle_dl.cli.authenticators.SSOReferenceHelper.show_manual_token_help"),
        patch("moodle_dl.cli.authenticators.input", return_value=""),
    ):
        assert auth._get_manual_token() == (None, None)

    with (
        patch("moodle_dl.cli.authenticators.SSOReferenceHelper.show_manual_token_help"),
        patch("moodle_dl.cli.authenticators.input", return_value="bad"),
        patch("moodle_dl.cli.authenticators.MoodleService.extract_token", return_value=(None, None)),
    ):
        assert auth._get_manual_token() == (None, None)

    with (
        patch("moodle_dl.cli.authenticators.SSOReferenceHelper.show_manual_token_help"),
        patch("moodle_dl.cli.authenticators.input", return_value="bad"),
        patch("moodle_dl.cli.authenticators.MoodleService.extract_token", side_effect=ValueError("bad")),
    ):
        assert auth._get_manual_token() == (None, None)
