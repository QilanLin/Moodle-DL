# -*- coding: utf-8 -*-
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moodle_dl import auto_sso_login


class FakeAsyncPlaywrightContext:
    def __init__(self, playwright):
        self.playwright = playwright

    async def __aenter__(self):
        return self.playwright

    async def __aexit__(self, exc_type, exc, tb):
        return False


def install_fake_playwright(monkeypatch, playwright=None):
    playwright = playwright or SimpleNamespace()
    async_api = SimpleNamespace(
        async_playwright=lambda: FakeAsyncPlaywrightContext(playwright)
    )
    monkeypatch.setitem(sys.modules, 'playwright', SimpleNamespace(async_api=async_api))
    monkeypatch.setitem(sys.modules, 'playwright.async_api', async_api)
    return playwright


def make_cookie(name='session', value='value', domain='.example.com', expires=1234, secure=1):
    cookie = MagicMock()
    cookie.name = name
    cookie.value = value
    cookie.domain = domain
    cookie.path = '/'
    cookie.expires = expires
    cookie.secure = secure
    cookie.has_nonstandard_attr.side_effect = lambda attr: attr == 'HttpOnly'
    cookie.get_nonstandard_attr.return_value = 'None'
    return cookie


def make_browser_flow():
    browser = AsyncMock()
    context = AsyncMock()
    page = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    return browser, context, page


def make_fake_browser_cookie3(**overrides):
    module = SimpleNamespace(
        chrome=MagicMock(return_value=[]),
        firefox=MagicMock(return_value=[]),
        edge=MagicMock(return_value=[]),
        brave=MagicMock(return_value=[]),
        safari=MagicMock(return_value=[]),
    )
    for name, value in overrides.items():
        setattr(module, name, value)
    return module


def test_extract_all_cookies_filters_when_user_selects_account():
    cookies = [
        {'name': 'MoodleSession', 'domain': '.keats.kcl.ac.uk', 'value': 'moodle'},
        {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.microsoftonline.com', 'value': 'account-1'},
        {'name': 'ESTSAUTHPERSISTENT', 'domain': '.login.live.com', 'value': 'account-2'},
    ]
    selected = {'ests_auth_persistent': 'account-2', 'cookies': [cookies[2]]}

    with (
        patch('moodle_dl.auto_sso_login._read_all_cookies_from_browser', return_value=cookies),
        patch('moodle_dl.auto_sso_login._detect_multiple_accounts', return_value=[
            {'ests_auth_persistent': 'account-1', 'cookies': [cookies[1]]},
            selected,
        ]),
        patch('moodle_dl.auto_sso_login._prompt_user_for_account_selection', return_value=selected),
        patch('moodle_dl.auto_sso_login._filter_cookies_by_account', return_value=[cookies[0], cookies[2]]) as filter_cookies,
    ):
        result = auto_sso_login.extract_all_cookies_from_browser(
            'firefox', 'keats.kcl.ac.uk', '/ignored/cookies.txt'
        )

    assert result == [cookies[0], cookies[2]]
    filter_cookies.assert_called_once_with(cookies, selected)


def test_read_all_cookies_from_standard_browser_normalizes_cookie_fields(monkeypatch):
    fake_browser_cookie3 = make_fake_browser_cookie3(
        firefox=MagicMock(return_value=[
            make_cookie('short', expires=1234, secure=0),
            make_cookie('millis', expires=1760000000000, secure=1),
        ])
    )
    monkeypatch.setitem(sys.modules, 'browser_cookie3', fake_browser_cookie3)

    result = auto_sso_login._read_all_cookies_from_browser('firefox')

    assert [cookie['name'] for cookie in result] == ['short', 'millis']
    assert result[0]['expires'] == 1234
    assert result[1]['expires'] == 1760000000
    assert result[0]['httpOnly'] is True
    assert result[0]['secure'] is False
    fake_browser_cookie3.firefox.assert_called_once_with()


def test_read_all_cookies_from_custom_browser_uses_discovered_cookie_path(monkeypatch):
    fake_browser_cookie3 = make_fake_browser_cookie3(
        firefox=MagicMock(return_value=[make_cookie('zen-cookie')])
    )
    monkeypatch.setitem(sys.modules, 'browser_cookie3', fake_browser_cookie3)

    with patch('moodle_dl.auto_sso_login._find_browser_cookie_path', return_value='/tmp/zen/cookies.sqlite'):
        result = auto_sso_login._read_all_cookies_from_browser('zen')

    assert result[0]['name'] == 'zen-cookie'
    fake_browser_cookie3.firefox.assert_called_once_with(cookie_file='/tmp/zen/cookies.sqlite')


def test_read_all_cookies_from_custom_browser_returns_empty_without_cookie_path(monkeypatch):
    monkeypatch.setitem(sys.modules, 'browser_cookie3', make_fake_browser_cookie3())

    with patch('moodle_dl.auto_sso_login._find_browser_cookie_path', return_value=None):
        assert auto_sso_login._read_all_cookies_from_browser('zen') == []


@pytest.mark.asyncio
async def test_launch_chromium_headless_uses_chromium_channel():
    playwright = SimpleNamespace(
        firefox=SimpleNamespace(launch=AsyncMock()),
        chromium=SimpleNamespace(launch=AsyncMock(return_value='browser')),
    )

    result = await auto_sso_login._launch_playwright_browser(
        playwright, 'chrome', headless=True
    )

    assert result == 'browser'
    playwright.chromium.launch.assert_awaited_once_with(
        headless=True,
        channel='chromium',
    )


@pytest.mark.asyncio
async def test_navigate_to_moodle_returns_final_state_and_handles_errors():
    page = AsyncMock()
    page.url = 'https://keats.kcl.ac.uk/my/'
    page.content = AsyncMock(return_value='<html>ok</html>')
    page.goto = AsyncMock()

    with patch('moodle_dl.auto_sso_login._wait_for_sso_redirect', AsyncMock(return_value=True)) as wait_sso:
        result = await auto_sso_login._navigate_to_moodle_and_wait(
            page, 'keats.kcl.ac.uk', 'https://keats.kcl.ac.uk/', 30000, headless=True
        )

    assert result == (True, 'https://keats.kcl.ac.uk/my/', '<html>ok</html>')
    page.goto.assert_awaited_once_with(
        'https://keats.kcl.ac.uk/', wait_until='domcontentloaded', timeout=30000
    )
    wait_sso.assert_awaited_once_with(page, 'keats.kcl.ac.uk', 15, True)

    page.goto.side_effect = RuntimeError('navigation failed')
    page.url = 'https://login.microsoftonline.com/'
    assert await auto_sso_login._navigate_to_moodle_and_wait(
        page, 'keats.kcl.ac.uk', 'https://keats.kcl.ac.uk/', 30000
    ) == (False, 'https://login.microsoftonline.com/', '')


@pytest.mark.asyncio
async def test_wait_for_sso_redirect_logs_headful_account_selection_and_return():
    urls = iter([
        'https://login.microsoftonline.com/common/oauth2/authorize',
        'https://keats.kcl.ac.uk/my/',
    ])

    class FakePage:
        def __init__(self):
            self.wait_for_timeout = AsyncMock()

        @property
        def url(self):
            return next(urls)

    page = FakePage()

    result = await auto_sso_login._wait_for_sso_redirect(
        page, 'keats.kcl.ac.uk', max_wait=2, headless=False
    )

    assert result is True
    assert page.wait_for_timeout.await_count == 2


@pytest.mark.asyncio
async def test_save_session_cookies_handles_context_errors():
    context = AsyncMock()
    context.cookies = AsyncMock(side_effect=RuntimeError('context closed'))

    assert await auto_sso_login._save_session_cookies(context, MagicMock()) is False


@pytest.mark.asyncio
async def test_auto_login_returns_false_when_browser_has_no_cookies(monkeypatch):
    install_fake_playwright(monkeypatch)

    with patch('moodle_dl.auto_sso_login.extract_all_cookies_from_browser', return_value=[]):
        assert await auto_sso_login.auto_login_with_sso(
            'keats.kcl.ac.uk',
            '/tmp/cookies.txt',
            preferred_browser='firefox',
            headless=False,
            auth_manager=MagicMock(),
        ) is False


@pytest.mark.asyncio
async def test_auto_login_success_closes_browser_and_saves_cookies(monkeypatch):
    install_fake_playwright(monkeypatch, SimpleNamespace(name='playwright'))
    browser, context, page = make_browser_flow()
    auth_manager = MagicMock()

    with (
        patch('moodle_dl.auto_sso_login.extract_all_cookies_from_browser', return_value=[
            {'name': 'MoodleSession', 'value': 'abc', 'domain': '.keats.kcl.ac.uk', 'path': '/'}
        ]),
        patch('moodle_dl.auto_sso_login._launch_playwright_browser', AsyncMock(return_value=browser)) as launch,
        patch('moodle_dl.auto_sso_login._setup_browser_context', AsyncMock(return_value=context)) as setup_context,
        patch('moodle_dl.auto_sso_login._navigate_to_moodle_and_wait', AsyncMock(
            return_value=(False, 'https://keats.kcl.ac.uk/my/', '<a href="login/logout.php">Logout</a>')
        )) as navigate,
        patch('moodle_dl.auto_sso_login._check_final_login_status', AsyncMock(return_value=1)) as check_status,
        patch('moodle_dl.auto_sso_login._save_session_cookies', AsyncMock(return_value=True)) as save_cookies,
    ):
        result = await auto_sso_login.auto_login_with_sso(
            'keats.kcl.ac.uk',
            '/tmp/cookies.txt',
            preferred_browser='firefox',
            headless=False,
            auth_manager=auth_manager,
        )

    assert result is True
    launch.assert_awaited_once()
    setup_context.assert_awaited_once()
    context.new_page.assert_awaited_once()
    navigate.assert_awaited_once_with(
        page, 'keats.kcl.ac.uk', 'https://keats.kcl.ac.uk/', 30000, False
    )
    check_status.assert_awaited_once()
    save_cookies.assert_awaited_once_with(context, auth_manager)
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_login_headful_retries_failed_status_then_succeeds(monkeypatch):
    install_fake_playwright(monkeypatch)
    browser, context, _page = make_browser_flow()

    with (
        patch('moodle_dl.auto_sso_login.extract_all_cookies_from_browser', return_value=[
            {'name': 'cookie', 'value': 'value', 'domain': '.keats.kcl.ac.uk', 'path': '/'}
        ]),
        patch('moodle_dl.auto_sso_login._launch_playwright_browser', AsyncMock(return_value=browser)),
        patch('moodle_dl.auto_sso_login._setup_browser_context', AsyncMock(return_value=context)),
        patch('moodle_dl.auto_sso_login._navigate_to_moodle_and_wait', AsyncMock(
            return_value=(False, 'https://login.microsoftonline.com/', '<html>login</html>')
        )) as navigate,
        patch('moodle_dl.auto_sso_login._check_final_login_status', AsyncMock(side_effect=[-1, 1])),
        patch('moodle_dl.auto_sso_login._save_session_cookies', AsyncMock(return_value=True)),
    ):
        result = await auto_sso_login.auto_login_with_sso(
            'keats.kcl.ac.uk',
            '/tmp/cookies.txt',
            headless=False,
            auth_manager=MagicMock(),
        )

    assert result is True
    assert navigate.await_count == 2
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_login_uncertain_status_handles_debug_and_fails(monkeypatch):
    install_fake_playwright(monkeypatch)
    browser, context, _page = make_browser_flow()

    with (
        patch('moodle_dl.auto_sso_login.extract_all_cookies_from_browser', return_value=[
            {'name': 'cookie', 'value': 'value', 'domain': '.keats.kcl.ac.uk', 'path': '/'}
        ]),
        patch('moodle_dl.auto_sso_login._launch_playwright_browser', AsyncMock(return_value=browser)),
        patch('moodle_dl.auto_sso_login._setup_browser_context', AsyncMock(return_value=context)),
        patch('moodle_dl.auto_sso_login._navigate_to_moodle_and_wait', AsyncMock(
            return_value=(False, 'https://keats.kcl.ac.uk/', '<html>unknown</html>')
        )),
        patch('moodle_dl.auto_sso_login._check_final_login_status', AsyncMock(return_value=0)),
        patch('moodle_dl.auto_sso_login._handle_uncertain_login_status', AsyncMock()) as handle_uncertain,
    ):
        result = await auto_sso_login.auto_login_with_sso(
            'keats.kcl.ac.uk',
            '/tmp/cookies.txt',
            headless=True,
            auth_manager=MagicMock(),
        )

    assert result is False
    handle_uncertain.assert_awaited_once_with(
        'https://keats.kcl.ac.uk/', '<html>unknown</html>'
    )
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_login_page_error_logs_current_sso_url_and_closes_browser(monkeypatch):
    install_fake_playwright(monkeypatch)
    browser, context, page = make_browser_flow()
    page.url = 'https://login.microsoftonline.com/common/oauth2/authorize'

    with (
        patch('moodle_dl.auto_sso_login.extract_all_cookies_from_browser', return_value=[
            {'name': 'cookie', 'value': 'value', 'domain': '.keats.kcl.ac.uk', 'path': '/'}
        ]),
        patch('moodle_dl.auto_sso_login._launch_playwright_browser', AsyncMock(return_value=browser)),
        patch('moodle_dl.auto_sso_login._setup_browser_context', AsyncMock(return_value=context)),
        patch('moodle_dl.auto_sso_login._navigate_to_moodle_and_wait', AsyncMock(
            side_effect=RuntimeError('page crashed')
        )),
    ):
        result = await auto_sso_login.auto_login_with_sso(
            'keats.kcl.ac.uk',
            '/tmp/cookies.txt',
            headless=True,
            auth_manager=MagicMock(),
        )

    assert result is False
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_login_reports_missing_playwright_browser(monkeypatch):
    install_fake_playwright(monkeypatch)

    with (
        patch('moodle_dl.auto_sso_login.extract_all_cookies_from_browser', return_value=[
            {'name': 'cookie', 'value': 'value', 'domain': '.keats.kcl.ac.uk', 'path': '/'}
        ]),
        patch('moodle_dl.auto_sso_login._launch_playwright_browser', AsyncMock(
            side_effect=RuntimeError("Executable doesn't exist at /ms-playwright/chromium")
        )),
        patch('moodle_dl.auto_sso_login.logging') as mock_logging,
    ):
        result = await auto_sso_login.auto_login_with_sso(
            'keats.kcl.ac.uk',
            '/tmp/cookies.txt',
            headless=True,
            auth_manager=MagicMock(),
        )

    assert result is False
    assert any('Playwright 浏览器未安装' in args[0] for args, _kwargs in mock_logging.error.call_args_list)


def test_auto_login_sync_delegates_to_async_runner():
    with patch('moodle_dl.auto_sso_login.asyncio.run', return_value=True) as run:
        assert auto_sso_login.auto_login_with_sso_sync(
            'keats.kcl.ac.uk', '/tmp/cookies.txt', 'firefox', False, 123, 'auth'
        ) is True

    coroutine = run.call_args.args[0]
    assert coroutine.cr_code.co_name == 'auto_login_with_sso'
    coroutine.close()
