from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from moodle_dl.cookie_manager import (
    CookieManager,
    convert_netscape_cookies_to_playwright,
    create_cookie_manager_from_client,
)


def make_manager(config=None, auth_manager=None):
    config = config or Mock()
    config.get_property_or.return_value = "firefox"
    manager = CookieManager(config, "example.test", "/tmp/cookies.txt")
    manager._auth_manager = auth_manager
    return manager


def write_netscape_cookie_file(path, rows):
    lines = ["# Netscape HTTP Cookie File"]
    lines.extend(rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_init_raises_when_auth_manager_creation_returns_falsey():
    with patch("moodle_dl.auth_session_manager.AuthSessionManager", return_value=None):
        with pytest.raises(RuntimeError, match="认证管理器初始化失败"):
            CookieManager(Mock(), "example.test", "/tmp/cookies.txt", db_file="state.db")


def test_save_cookies_to_db_returns_none_without_manager_or_on_create_failure():
    manager = make_manager(auth_manager=None)
    assert manager.save_cookies_to_db([{"name": "MoodleSession"}]) is None

    auth_manager = Mock()
    auth_manager.create_session.side_effect = RuntimeError("database locked")
    manager = make_manager(auth_manager=auth_manager)

    assert manager.save_cookies_to_db([{"name": "MoodleSession"}]) is None


def test_refresh_session_returns_none_when_refresh_fails():
    auth_manager = Mock()
    auth_manager.get_valid_session.return_value = {"session_id": "old"}
    auth_manager.refresh_session.side_effect = RuntimeError("refresh failed")
    manager = make_manager(auth_manager=auth_manager)

    assert manager.refresh_session_with_new_cookies([{"name": "MoodleSession"}]) is None


def test_get_client_ip_falls_back_when_socket_lookup_fails():
    socket_module = SimpleNamespace(
        gaierror=OSError,
        gethostname=Mock(return_value="host"),
        gethostbyname=Mock(side_effect=OSError("dns failed")),
    )

    with patch.dict("sys.modules", {"socket": socket_module}):
        assert CookieManager._get_client_ip() == "127.0.0.1"


def test_load_export_module_returns_cached_module():
    cached_module = object()
    manager = make_manager()
    manager._export_module = cached_module

    assert manager._load_export_module() is cached_module


def test_load_export_module_handles_missing_file_and_loader_failure():
    manager = make_manager()

    with patch("moodle_dl.cookie_manager.os.path.exists", return_value=False):
        assert manager._load_export_module() is None

    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock(side_effect=RuntimeError("boom"))))
    with (
        patch("moodle_dl.cookie_manager.os.path.exists", return_value=True),
        patch("moodle_dl.cookie_manager.importlib.util.spec_from_file_location", return_value=spec),
        patch("moodle_dl.cookie_manager.importlib.util.module_from_spec", return_value=SimpleNamespace()),
    ):
        assert manager._load_export_module() is None


def test_load_export_module_loads_module_successfully():
    manager = make_manager()
    module = SimpleNamespace()
    loader = Mock()
    spec = SimpleNamespace(loader=loader)

    with (
        patch("moodle_dl.cookie_manager.os.path.exists", return_value=True),
        patch("moodle_dl.cookie_manager.importlib.util.spec_from_file_location", return_value=spec),
        patch("moodle_dl.cookie_manager.importlib.util.module_from_spec", return_value=module),
    ):
        assert manager._load_export_module() is module

    loader.exec_module.assert_called_once_with(module)


def test_refresh_cookies_auto_sso_success_returns_true():
    manager = make_manager()

    with patch("moodle_dl.auto_sso_login.auto_login_with_sso_sync", return_value=True) as auto_login:
        assert manager.refresh_cookies() is True

    auto_login.assert_called_once_with(
        moodle_domain="example.test",
        cookies_path="/tmp/cookies.txt",
        preferred_browser="firefox",
        headless=True,
        auth_manager=None,
    )


def test_refresh_cookies_falls_back_to_browser_export_and_saves_session():
    auth_manager = Mock()
    manager = make_manager(auth_manager=auth_manager)
    manager.refresh_session_with_new_cookies = Mock(return_value="new-session")
    cookies = [{"name": "MoodleSession", "value": "abc"}]

    with (
        patch("moodle_dl.auto_sso_login.auto_login_with_sso_sync", return_value=False),
        patch("moodle_dl.auto_sso_login.extract_all_cookies_from_browser", return_value=cookies),
    ):
        assert manager.refresh_cookies() is True

    manager.refresh_session_with_new_cookies.assert_called_once_with(
        new_cookies=cookies,
        source="browser_export",
    )


def test_refresh_cookies_handles_empty_and_exceptional_browser_export():
    manager = make_manager()

    with patch("moodle_dl.auto_sso_login.extract_all_cookies_from_browser", return_value=[]):
        assert manager.refresh_cookies(use_auto_sso=False) is False

    with patch("moodle_dl.auto_sso_login.extract_all_cookies_from_browser", side_effect=RuntimeError("locked")):
        assert manager.refresh_cookies(use_auto_sso=False) is False


@pytest.mark.asyncio
async def test_refresh_cookies_uses_threaded_sso_when_event_loop_is_running():
    manager = make_manager()

    with patch("moodle_dl.auto_sso_login.auto_login_with_sso_sync", return_value=True):
        assert manager.refresh_cookies() is True


def test_load_cookies_from_file_parses_valid_netscape_file(tmp_path):
    cookies_file = tmp_path / "Cookies.txt"
    write_netscape_cookie_file(
        cookies_file,
        [".example.test\tTRUE\t/\tTRUE\t1893456000\tMoodleSession\tabc123"],
    )
    manager = make_manager()

    cookies = manager._load_cookies_from_file(str(cookies_file))

    assert cookies == [
        {
            "domain": ".example.test",
            "path": "/",
            "secure": 1,
            "expires": 1893456000,
            "name": "MoodleSession",
            "value": "abc123",
            "httponly": 0,
            "samesite": "Lax",
        }
    ]


def test_load_cookies_from_file_returns_none_for_missing_file(tmp_path):
    manager = make_manager()

    assert manager._load_cookies_from_file(str(tmp_path / "missing.txt")) is None


def test_load_cookies_from_file_uses_manual_fallback_on_load_error(tmp_path):
    cookies_file = tmp_path / "Cookies.txt"
    cookies_file.write_text(".example.test\tTRUE\t/\tTRUE\t0\tMoodleSession\tabc\n", encoding="utf-8")
    manager = make_manager()

    with patch.object(manager, "_fallback_manual_parse", return_value=[{"name": "fallback"}]) as fallback:
        cookies = manager._load_cookies_from_file(str(cookies_file))

    assert cookies == [{"name": "fallback"}]
    fallback.assert_called_once_with(str(cookies_file))


def test_fallback_manual_parse_handles_bool_numeric_and_invalid_fields(tmp_path):
    cookies_file = tmp_path / "Cookies.txt"
    cookies_file.write_text(
        "\n".join(
            [
                "# comment",
                "",
                ".example.test\tTRUE\t/\tTRUE\t1893456000\tsecure_cookie\tone",
                ".example.test\tTRUE\t/path\t0\t0\tplain_cookie\ttwo",
                ".example.test\tTRUE\t/\tbogus\tnot-a-time\tbad_fields\tthree",
                "too\tshort",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manager = make_manager()

    cookies = manager._fallback_manual_parse(str(cookies_file))

    assert cookies == [
        {
            "domain": ".example.test",
            "path": "/",
            "secure": 1,
            "expires": 1893456000,
            "name": "secure_cookie",
            "value": "one",
            "httponly": 1,
            "samesite": "Lax",
        },
        {
            "domain": ".example.test",
            "path": "/path",
            "secure": 0,
            "expires": None,
            "name": "plain_cookie",
            "value": "two",
            "httponly": 1,
            "samesite": "Lax",
        },
        {
            "domain": ".example.test",
            "path": "/",
            "secure": 0,
            "expires": None,
            "name": "bad_fields",
            "value": "three",
            "httponly": 1,
            "samesite": "Lax",
        },
    ]


def test_fallback_manual_parse_returns_none_for_unreadable_file(tmp_path):
    manager = make_manager()

    assert manager._fallback_manual_parse(str(tmp_path / "missing.txt")) is None


def test_is_cookie_expired_response_matches_url_content_and_clean_response():
    assert CookieManager.is_cookie_expired_response("https://example.test/login/index.php") is True
    assert CookieManager.is_cookie_expired_response("https://example.test/course", "Guest user") is True
    assert CookieManager.is_cookie_expired_response("https://example.test/course", "Course content") is False


def test_create_cookie_manager_from_client_uses_client_domain_and_state_db():
    config = Mock()
    config.get_misc_files_path.return_value = "/tmp/misc"
    client = SimpleNamespace(moodle_url=SimpleNamespace(domain="example.test"))

    with (
        patch("moodle_dl.utils.PathTools.get_cookies_path", return_value="/tmp/misc/Cookies.txt"),
        patch("moodle_dl.utils.PathTools.make_path", return_value="/tmp/misc/moodle_state.db"),
        patch("moodle_dl.auth_session_manager.AuthSessionManager", return_value=Mock()) as auth_cls,
    ):
        manager = create_cookie_manager_from_client(client, config)

    assert manager.moodle_domain == "example.test"
    assert manager.cookies_path == "/tmp/misc/Cookies.txt"
    assert manager.db_file == "/tmp/misc/moodle_state.db"
    auth_cls.assert_called_once_with("/tmp/misc/moodle_state.db")


def test_create_cookie_manager_from_client_allows_db_path_lookup_failure():
    config = Mock()
    config.get_misc_files_path.side_effect = ["/tmp/misc", OSError("readonly")]
    client = SimpleNamespace(moodle_url=SimpleNamespace(domain="example.test"))

    with patch("moodle_dl.utils.PathTools.get_cookies_path", return_value="/tmp/misc/Cookies.txt"):
        manager = create_cookie_manager_from_client(client, config)

    assert manager.db_file is None


def test_convert_netscape_cookies_to_playwright_converts_fields(tmp_path):
    cookies_file = tmp_path / "Cookies.txt"
    write_netscape_cookie_file(
        cookies_file,
        [
            ".example.test\tTRUE\t/\tTRUE\t1893456000\tMoodleSession\tabc123",
            ".example.test\tTRUE\t/path\tFALSE\t1893456000000\tplain_cookie\txyz",
        ],
    )

    assert convert_netscape_cookies_to_playwright(str(cookies_file)) == [
        {
            "name": "MoodleSession",
            "value": "abc123",
            "domain": ".example.test",
            "path": "/",
            "expires": 1893456000,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "plain_cookie",
            "value": "xyz",
            "domain": ".example.test",
            "path": "/path",
            "expires": 1893456000,
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
    ]


def test_convert_netscape_cookies_to_playwright_returns_empty_on_load_error(tmp_path):
    cookies_file = tmp_path / "bad.txt"
    cookies_file.write_text("not a cookie jar\n", encoding="utf-8")

    assert convert_netscape_cookies_to_playwright(str(cookies_file)) == []
