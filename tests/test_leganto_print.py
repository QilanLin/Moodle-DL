import asyncio
import builtins
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moodle_dl.downloader.leganto_print import (
    LegantoPdfPrinter,
    build_leganto_print_url,
    build_lti_launch_form,
    cookies_text_to_playwright,
    is_leganto_lti_launch_url,
    is_leganto_print_url,
    is_leganto_reading_list_url,
    summarize_leganto_load_error,
)


class FakeLocator:
    def __init__(self, items=None):
        self.items = items or []

    @property
    def first(self):
        if self.items:
            return self.items[0]
        return FakeElement(visible=False)

    def nth(self, index):
        return self.items[index]

    def filter(self, **_kwargs):
        return self

    async def count(self):
        return len(self.items)

    async def inner_text(self, **kwargs):
        return await self.first.inner_text(**kwargs)

    async def evaluate(self, script):
        return await self.first.evaluate(script)


class FakeElement:
    def __init__(self, *, description='', visible=True, enabled=True, text='', html=''):
        self.description = description
        self.visible = visible
        self.enabled = enabled
        self.text = text
        self.html = html
        self.clicks = []
        self.evaluations = []
        self.presses = []
        self.scrolled = False
        self.focused = False

    async def is_visible(self, **_kwargs):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def scroll_into_view_if_needed(self, **_kwargs):
        self.scrolled = True

    async def click(self, **kwargs):
        self.clicks.append(kwargs)

    async def focus(self):
        self.focused = True

    async def press(self, key):
        self.presses.append(key)

    async def wait_for(self, **_kwargs):
        if not self.visible:
            raise TimeoutError('not visible')

    async def evaluate(self, _script):
        self.evaluations.append(_script)
        return self.html or self.description

    async def inner_text(self, **_kwargs):
        return self.text


class FakeLegantoPage:
    def __init__(self):
        self.url = 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'
        self.keyboard = SimpleNamespace(press=AsyncMock())
        self.menu_open = False
        self.waited = []
        self.goto_calls = []
        self.load_states = []
        self.screenshot_calls = []
        self.menu_button = FakeElement(
            description=(
                'lg-menu-button-list-menu-15085102330006881-more-info|'
                'List 6CCS3SAD menu|||mat-mdc-menu-trigger btn-menu|more_horiz'
            )
        )
        self.print_item = FakeElement(visible=False, text='Print list')

        async def open_menu(**kwargs):
            self.menu_button.clicks.append(kwargs)
            self.menu_open = True
            self.print_item.visible = True

        self.menu_button.click = open_menu

    def get_by_role(self, role, name=None):
        if role == 'button':
            return FakeLocator([self.menu_button])
        if role == 'menuitem':
            return FakeLocator([self.print_item])
        return FakeLocator()

    def get_by_text(self, *_args, **_kwargs):
        return FakeLocator([self.print_item] if self.print_item.visible else [])

    def locator(self, selector):
        if selector == '#lg-menu-action-print':
            return FakeLocator([self.print_item] if self.print_item.visible else [])
        if selector == 'body':
            return FakeLocator([FakeElement(text='List info View sections', html='<lg-root>ready</lg-root>')])
        return FakeLocator()

    async def wait_for_timeout(self, timeout):
        self.waited.append(timeout)

    async def wait_for_load_state(self, state, **kwargs):
        self.load_states.append((state, kwargs))

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = url

    async def screenshot(self, **kwargs):
        self.screenshot_calls.append(kwargs)
        path = kwargs.get('path')
        if path:
            with open(path, 'wb') as screenshot_file:
                screenshot_file.write(b'png')

    async def content(self):
        return '<html><body>debug</body></html>'


class FakePrintableLegantoPage(FakeLegantoPage):
    def __init__(self, url='https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'):
        super().__init__()
        self.url = url
        self.pdf_kwargs = None
        self.pdf_url = None
        self.media_kwargs = None

    async def pdf(self, **kwargs):
        self.pdf_kwargs = kwargs
        self.pdf_url = self.url

    async def emulate_media(self, **kwargs):
        self.media_kwargs = kwargs


def install_fake_playwright(monkeypatch, page):
    class FakeContext:
        def __init__(self):
            self.closed = False
            self.page = page
            self.request = getattr(page, 'request', None)
            page.context = self

        async def new_page(self):
            return self.page

        async def close(self):
            self.closed = True

    class FakeBrowser:
        def __init__(self):
            self.closed = False
            self.context = FakeContext()
            self.new_context_kwargs = None

        async def new_context(self, **kwargs):
            self.new_context_kwargs = kwargs
            return self.context

        async def close(self):
            self.closed = True

    class FakeChromium:
        def __init__(self):
            self.browser = FakeBrowser()
            self.launch_kwargs = None

        async def launch(self, **kwargs):
            self.launch_kwargs = kwargs
            return self.browser

    class FakePlaywrightManager:
        def __init__(self):
            self.chromium = FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    manager = FakePlaywrightManager()
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'playwright.async_api':
            return SimpleNamespace(async_playwright=lambda: manager)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    return manager


def test_leganto_url_detection_is_specific_to_kcl_reading_lists():
    assert is_leganto_reading_list_url('https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML')
    assert is_leganto_lti_launch_url('https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1')
    assert is_leganto_print_url('https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView')

    assert not is_leganto_reading_list_url('https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1')
    assert not is_leganto_lti_launch_url('https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881')
    assert not is_leganto_reading_list_url('https://example.com/leganto/nui/lists/15085102330006881')
    assert not is_leganto_print_url('https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881')


def test_build_leganto_print_url_uses_official_print_endpoint():
    assert build_leganto_print_url(
        'https://rl.kcl.ac.uk/leganto/nui/lists/12447222440006881?auth=SAML'
    ) == 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/12447222440006881/studentView'
    assert build_leganto_print_url(
        'https://rl.kcl.ac.uk/leganto/nui/lists/12447222440006881/?auth=SAML#section'
    ) == 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/12447222440006881/studentView'
    assert build_leganto_print_url(
        'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/12447222440006881/studentView'
    ) == 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/12447222440006881/studentView'
    assert build_leganto_print_url(
        'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/12447222440006881/studentView?download=1'
    ) == 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/12447222440006881/studentView'
    assert build_leganto_print_url('https://example.com/leganto/nui/lists/12447222440006881') is None


def test_build_leganto_print_url_supports_language_override():
    assert build_leganto_print_url(
        'https://rl.kcl.ac.uk/leganto/nui/lists/12447222440006881?auth=SAML',
        language='fr',
    ) == 'https://rl.kcl.ac.uk/leganto/rl/files/fr/print/list/12447222440006881/studentView'


def test_leganto_load_error_summary_detects_auth_and_lti_errors():
    assert summarize_leganto_load_error(
        'https://login.microsoftonline.com/tenant/saml2',
        'Pick an account',
    ) == 'Leganto redirected to Microsoft account sign-in'

    assert summarize_leganto_load_error(
        'https://rl.kcl.ac.uk/leganto/nui/error/login_error',
        'The user is not authorized',
    ) == 'Leganto reading list did not load: The user is not authorized'

    assert summarize_leganto_load_error(
        'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881',
        'List info View sections',
    ) is None


def test_leganto_menu_candidate_skip_only_filters_global_controls():
    assert LegantoPdfPrinter._should_skip_menu_candidate('top-panel-help|Help|help-btn')
    assert LegantoPdfPrinter._should_skip_menu_candidate('lg-menu-button-setting-menu|Settings')
    assert not LegantoPdfPrinter._should_skip_menu_candidate('list-action-menu|More actions|mat-mdc-menu-trigger')


def test_lti_launch_form_escapes_endpoint_and_parameters():
    form = build_lti_launch_form(
        'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1?x=<bad>',
        [
            {'name': 'id_token', 'value': 'abc&def'},
            {'name': '<ignored>', 'value': '"quoted"'},
        ],
    )

    assert 'action="https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1?x=&lt;bad&gt;"' in form
    assert 'name="id_token" value="abc&amp;def"' in form
    assert 'name="&lt;ignored&gt;" value="&quot;quoted&quot;"' in form
    assert 'document.getElementById("launchForm").submit()' in form


def test_cookies_text_to_playwright_converts_netscape_cookies():
    cookies_text = '\n'.join(
        [
            '# Netscape HTTP Cookie File',
            '.rl.kcl.ac.uk\tTRUE\t/\tTRUE\t1893456000\tSESSION\tabc123',
            'rl.kcl.ac.uk\tFALSE\t/leganto\tFALSE\t0\tPREF\txyz',
        ]
    )

    cookies = cookies_text_to_playwright(cookies_text)

    assert cookies[0] == {
        'name': 'SESSION',
        'value': 'abc123',
        'domain': '.rl.kcl.ac.uk',
        'path': '/',
        'secure': True,
        'httpOnly': False,
        'expires': 1893456000,
    }
    assert cookies[1] == {
        'name': 'PREF',
        'value': 'xyz',
        'domain': 'rl.kcl.ac.uk',
        'path': '/leganto',
        'secure': False,
        'httpOnly': False,
    }


def test_cookies_text_to_playwright_ignores_empty_input():
    assert cookies_text_to_playwright(None) == []
    assert cookies_text_to_playwright('') == []


@pytest.mark.asyncio
async def test_add_cookies_can_skip_stale_leganto_launch_cookies():
    cookies_text = '\n'.join(
        [
            '# Netscape HTTP Cookie File',
            '.rl.kcl.ac.uk\tTRUE\t/\tTRUE\t1893456000\tJSESSIONID\told-session',
            '.rl.kcl.ac.uk\tTRUE\t/\tTRUE\t1893456000\turm_se\t1000',
            '.rl.kcl.ac.uk\tTRUE\t/\tTRUE\t1893456000\tCUSTOM\tkeep-me',
            'keats.kcl.ac.uk\tFALSE\t/\tTRUE\t1893456000\tMoodleSession\tmoodle-session',
        ]
    )

    class FakeContext:
        def __init__(self):
            self.cookies = []

        async def add_cookies(self, cookies):
            self.cookies.extend(cookies)

    context = FakeContext()
    printer = LegantoPdfPrinter(cookies_text)

    await printer._add_cookies(context, skip_leganto_launch_cookies=True)

    assert [(cookie['domain'], cookie['name']) for cookie in context.cookies] == [
        ('.rl.kcl.ac.uk', 'CUSTOM'),
        ('keats.kcl.ac.uk', 'MoodleSession'),
    ]


@pytest.mark.asyncio
async def test_add_cookies_falls_back_to_individual_cookie_import():
    cookies_text = '\n'.join(
        [
            '# Netscape HTTP Cookie File',
            '.rl.kcl.ac.uk\tTRUE\t/\tTRUE\t1893456000\tGOOD\tgood',
            '.rl.kcl.ac.uk\tTRUE\t/\tTRUE\t1893456000\tBAD\tbad',
        ]
    )

    class FakeContext:
        def __init__(self):
            self.calls = []

        async def add_cookies(self, cookies):
            self.calls.append(cookies)
            if len(cookies) > 1:
                raise RuntimeError('bulk import failed')
            if cookies[0]['name'] == 'BAD':
                raise RuntimeError('single cookie failed')

    context = FakeContext()

    await LegantoPdfPrinter(cookies_text)._add_cookies(context)

    assert [len(call) for call in context.calls] == [2, 1, 1]
    assert context.calls[1][0]['name'] == 'GOOD'
    assert context.calls[2][0]['name'] == 'BAD'


@pytest.mark.asyncio
async def test_remove_onetrust_overlay_removes_blocking_elements():
    class FakePage:
        def __init__(self):
            self.script = ''
            self.waited = None

        async def evaluate(self, script):
            self.script = script
            return True

        async def wait_for_timeout(self, timeout):
            self.waited = timeout

    page = FakePage()

    await LegantoPdfPrinter()._remove_onetrust_overlay(page)

    assert '#onetrust-consent-sdk' in page.script
    assert '.onetrust-pc-dark-filter' in page.script
    assert page.waited == 250


@pytest.mark.asyncio
async def test_launch_lti_form_sets_autosubmit_page():
    class FakePage:
        def __init__(self):
            self.goto_calls = []
            self.content = None
            self.load_states = []
            self.waited = []

        async def goto(self, url):
            self.goto_calls.append(url)

        async def set_content(self, content, **kwargs):
            self.content = content
            self.content_kwargs = kwargs

        async def wait_for_load_state(self, state, **kwargs):
            self.load_states.append((state, kwargs))

        async def wait_for_timeout(self, timeout):
            self.waited.append(timeout)

    page = FakePage()

    await LegantoPdfPrinter()._launch_lti_form(
        page,
        'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        [{'name': 'id_token', 'value': 'signed'}],
    )

    assert page.goto_calls == ['about:blank']
    assert 'id="launchForm"' in page.content
    assert 'name="id_token" value="signed"' in page.content
    assert page.content_kwargs == {'wait_until': 'domcontentloaded'}
    assert page.load_states == [
        ('domcontentloaded', {'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS}),
        ('networkidle', {'timeout': 15_000}),
    ]
    assert page.waited == [750]


@pytest.mark.asyncio
async def test_wait_for_leganto_ready_accepts_rendered_list():
    class ReadyPage:
        url = 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'

        def __init__(self):
            self.waited_for_function = False

        async def wait_for_url(self, *_args, **_kwargs):
            return None

        async def wait_for_function(self, *_args, **_kwargs):
            self.waited_for_function = True

        def locator(self, selector):
            assert selector == 'body'
            return FakeLocator([FakeElement(text='List info View sections', html='<lg-root>ready</lg-root>')])

    page = ReadyPage()

    await LegantoPdfPrinter()._wait_for_leganto_ready(page)

    assert page.waited_for_function is True


@pytest.mark.asyncio
async def test_wait_for_leganto_ready_reports_blank_angular_shell():
    class BlankPage:
        url = 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'

        async def wait_for_url(self, *_args, **_kwargs):
            return None

        async def wait_for_function(self, *_args, **_kwargs):
            raise TimeoutError('not rendered')

        def locator(self, selector):
            assert selector == 'body'
            return FakeLocator([FakeElement(text='', html='<lg-root></lg-root>')])

    with pytest.raises(RuntimeError, match='did not render'):
        await LegantoPdfPrinter()._wait_for_leganto_ready(BlankPage())


@pytest.mark.asyncio
async def test_wait_for_leganto_ready_reports_non_leganto_final_url():
    class LoginPage:
        url = 'https://login.microsoftonline.com/tenant/oauth2'

        async def wait_for_url(self, *_args, **_kwargs):
            raise TimeoutError('still on login page')

        async def wait_for_function(self, *_args, **_kwargs):
            raise TimeoutError('not rendered')

        def locator(self, selector):
            assert selector == 'body'
            return FakeLocator([FakeElement(text='Pick an account', html='<body>login</body>')])

    with pytest.raises(RuntimeError, match='Microsoft account sign-in'):
        await LegantoPdfPrinter()._wait_for_leganto_ready(LoginPage())


@pytest.mark.asyncio
async def test_body_text_falls_back_without_timeout_argument_and_root_html_handles_errors():
    class BodyElement:
        async def inner_text(self, **kwargs):
            if kwargs:
                raise TypeError('timeout unsupported')
            return 'fallback text'

        async def evaluate(self, *_args):
            raise RuntimeError('body html unavailable')

    class Page:
        def locator(self, selector):
            assert selector == 'body'
            return FakeLocator([BodyElement()])

    printer = LegantoPdfPrinter()

    assert await printer._body_text(Page()) == 'fallback text'
    assert await printer._root_html(Page()) == ''


@pytest.mark.asyncio
async def test_open_list_menu_waits_for_print_item_after_menu_click():
    page = FakeLegantoPage()
    printer = LegantoPdfPrinter()
    printer._dismiss_cookie_banner = AsyncMock()

    await printer._open_list_menu(page)

    printer._dismiss_cookie_banner.assert_awaited_once()
    assert page.menu_open is True
    assert page.print_item.visible is True
    assert page.menu_button.scrolled is True


@pytest.mark.asyncio
async def test_open_list_menu_force_clicks_after_cookie_overlay_intercepts():
    page = FakeLegantoPage()
    original_click = page.menu_button.click

    async def click_with_overlay(**kwargs):
        page.menu_button.clicks.append(kwargs)
        if not kwargs.get('force'):
            raise RuntimeError('overlay intercepts pointer events')
        await original_click(**kwargs)

    page.menu_button.click = click_with_overlay
    printer = LegantoPdfPrinter()
    printer._dismiss_cookie_banner = AsyncMock()

    await printer._open_list_menu(page)

    assert printer._dismiss_cookie_banner.await_count == 2
    assert page.menu_open is True
    assert {'timeout': 3_000, 'force': True} in page.menu_button.clicks


@pytest.mark.asyncio
async def test_open_direct_print_url_navigates_from_list_to_print_endpoint():
    page = FakeLegantoPage()

    result = await LegantoPdfPrinter()._open_direct_print_url(page, page.url)

    assert result is page
    assert page.url == 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView'
    assert page.goto_calls == [(
        'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView',
        {'wait_until': 'domcontentloaded', 'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS},
    )]
    assert page.load_states == [('domcontentloaded', {'timeout': 15_000})]


@pytest.mark.asyncio
async def test_open_direct_print_url_reuses_existing_print_endpoint_page():
    page = FakeLegantoPage()
    page.url = 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView'

    result = await LegantoPdfPrinter()._open_direct_print_url(page, page.url)

    assert result is page
    assert page.goto_calls == []
    assert page.load_states == []


@pytest.mark.asyncio
async def test_download_direct_print_pdf_saves_pdf_response(tmp_path):
    class FakeResponse:
        status = 200
        headers = {'content-type': 'application/pdf'}

        async def body(self):
            return b'%PDF-1.4\nprint-list'

    class FakeRequestContext:
        def __init__(self):
            self.calls = []

        async def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse()

    page = FakeLegantoPage()
    page.context = SimpleNamespace(request=FakeRequestContext())
    output = tmp_path / 'Reading List.pdf'

    result = await LegantoPdfPrinter()._download_direct_print_pdf(page, page.url, str(output))

    assert result is True
    assert output.read_bytes() == b'%PDF-1.4\nprint-list'
    assert page.context.request.calls == [(
        'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView',
        {
            'headers': {
                'Accept': 'application/pdf,*/*',
                'Referer': 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
            },
            'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS,
            'fail_on_status_code': False,
        },
    )]


@pytest.mark.asyncio
async def test_download_direct_print_pdf_accepts_pdf_magic_without_pdf_content_type(tmp_path):
    class FakeResponse:
        status = 200
        headers = {'content-type': 'application/octet-stream'}

        async def body(self):
            return b'%PDF-1.4\nprint-list'

    class FakeRequestContext:
        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    page = FakeLegantoPage()
    page.context = SimpleNamespace(request=FakeRequestContext())
    output = tmp_path / 'Reading List.pdf'

    result = await LegantoPdfPrinter()._download_direct_print_pdf(page, page.url, str(output))

    assert result is True
    assert output.read_bytes() == b'%PDF-1.4\nprint-list'


@pytest.mark.asyncio
async def test_download_direct_print_pdf_falls_back_for_successful_html_response(tmp_path):
    class FakeResponse:
        status = 200
        headers = {'content-type': 'text/html'}

        async def body(self):
            return b'<html><body>Leganto print view is still rendering</body></html>'

    class FakeRequestContext:
        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    page = FakeLegantoPage()
    page.context = SimpleNamespace(request=FakeRequestContext())
    output = tmp_path / 'Reading List.pdf'

    result = await LegantoPdfPrinter()._download_direct_print_pdf(page, page.url, str(output))

    assert result is False
    assert not output.exists()


@pytest.mark.asyncio
async def test_download_direct_print_pdf_falls_back_when_request_context_unavailable(tmp_path):
    page = FakeLegantoPage()
    output = tmp_path / 'Reading List.pdf'

    result = await LegantoPdfPrinter()._download_direct_print_pdf(page, page.url, str(output))

    assert result is False
    assert not output.exists()


@pytest.mark.asyncio
async def test_download_direct_print_pdf_returns_false_for_non_leganto_source(tmp_path):
    page = FakeLegantoPage()
    output = tmp_path / 'Reading List.pdf'

    result = await LegantoPdfPrinter()._download_direct_print_pdf(
        page,
        'https://example.test/not-leganto',
        str(output),
    )

    assert result is False
    assert not output.exists()


@pytest.mark.asyncio
async def test_download_direct_print_pdf_falls_back_when_request_raises(tmp_path):
    class FakeRequestContext:
        async def get(self, *_args, **_kwargs):
            raise RuntimeError('network down')

    page = FakeLegantoPage()
    page.context = SimpleNamespace(request=FakeRequestContext())
    output = tmp_path / 'Reading List.pdf'

    result = await LegantoPdfPrinter()._download_direct_print_pdf(page, page.url, str(output))

    assert result is False
    assert not output.exists()


@pytest.mark.asyncio
async def test_download_direct_print_pdf_rejects_auth_error(tmp_path):
    class FakeResponse:
        status = 401
        headers = {'content-type': 'text/html'}

        async def body(self):
            return b'HTTP Status 401 - Unauthorized'

    class FakeRequestContext:
        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    page = FakeLegantoPage()
    page.context = SimpleNamespace(request=FakeRequestContext())
    output = tmp_path / 'Reading List.pdf'
    output.write_bytes(b'%PDF-1.4\nprevious-good-copy')

    with pytest.raises(RuntimeError, match='HTTP 401'):
        await LegantoPdfPrinter()._download_direct_print_pdf(page, page.url, str(output))

    assert output.read_bytes() == b'%PDF-1.4\nprevious-good-copy'


@pytest.mark.asyncio
async def test_download_direct_print_pdf_rejects_leganto_auth_error_body_without_overwrite(tmp_path):
    class FakeResponse:
        status = 200
        headers = {'content-type': 'text/html'}

        async def body(self):
            return b'<html><body>The user is not authorized</body></html>'

    class FakeRequestContext:
        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    page = FakeLegantoPage()
    page.context = SimpleNamespace(request=FakeRequestContext())
    output = tmp_path / 'Reading List.pdf'
    output.write_bytes(b'%PDF-1.4\nprevious-good-copy')

    with pytest.raises(RuntimeError, match='The user is not authorized'):
        await LegantoPdfPrinter()._download_direct_print_pdf(page, page.url, str(output))

    assert output.read_bytes() == b'%PDF-1.4\nprevious-good-copy'


@pytest.mark.asyncio
async def test_trigger_print_list_returns_popup_when_print_opens_new_page():
    page = FakeLegantoPage()
    page.print_item.visible = True
    popup = FakeElement()
    popup.wait_for_load_state = AsyncMock()

    class FakeContext:
        async def wait_for_event(self, event, timeout):
            assert event == 'page'
            assert timeout == 3_000
            return popup

    result = await LegantoPdfPrinter()._trigger_print_list(FakeContext(), page)

    assert result is popup
    popup.wait_for_load_state.assert_awaited_once_with('domcontentloaded', timeout=15_000)
    assert page.print_item.clicks == [{}]


@pytest.mark.asyncio
async def test_trigger_print_list_refuses_regular_page_when_no_print_action_opens():
    page = FakeLegantoPage()
    page.print_item.visible = True

    class FakeContext:
        async def wait_for_event(self, *_args, **_kwargs):
            raise TimeoutError('no popup')

    with pytest.raises(RuntimeError, match='refusing to save the regular reading-list page'):
        await LegantoPdfPrinter()._trigger_print_list(FakeContext(), page)

    assert page.print_item.clicks == [{}, {'timeout': 3_000, 'force': True}]
    assert any('closest' in script for script in page.print_item.evaluations)


@pytest.mark.asyncio
async def test_trigger_print_list_opens_print_endpoint_when_window_print_runs():
    page = FakeLegantoPage()
    page.print_item.visible = True
    printer = LegantoPdfPrinter()
    printer._was_print_invoked = AsyncMock(return_value=True)

    class FakeContext:
        async def wait_for_event(self, *_args, **_kwargs):
            raise TimeoutError('no popup')

    result = await printer._trigger_print_list(FakeContext(), page)

    assert result is page
    assert page.url == 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView'
    assert page.goto_calls == [(
        'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView',
        {'wait_until': 'domcontentloaded', 'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS},
    )]
    assert page.print_item.clicks == [{}]
    printer._was_print_invoked.assert_awaited_once_with(page)


@pytest.mark.asyncio
async def test_trigger_print_list_accepts_verified_print_menu_item_without_popup_signal():
    page = FakeLegantoPage()
    page.print_item.visible = True
    page.print_item.description = 'id=lg-menu-action-print text=Print list'

    class FakeContext:
        async def wait_for_event(self, *_args, **_kwargs):
            raise TimeoutError('no popup')

    result = await LegantoPdfPrinter()._trigger_print_list(FakeContext(), page)

    assert result is page
    assert page.url == 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView'
    assert page.goto_calls == [(
        'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView',
        {'wait_until': 'domcontentloaded', 'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS},
    )]
    assert page.print_item.clicks == [{}, {'timeout': 3_000, 'force': True}]
    assert page.print_item.presses == ['Enter', 'Space']


@pytest.mark.asyncio
async def test_install_print_invocation_marker_covers_browser_print_paths():
    class FakePage:
        def __init__(self):
            self.script = ''

        async def evaluate(self, script):
            self.script = script

    page = FakePage()

    await LegantoPdfPrinter()._install_print_invocation_marker(page)

    assert 'beforeprint' in page.script
    assert 'afterprint' in page.script
    assert 'window.print' in page.script
    assert 'execCommand' in page.script


@pytest.mark.asyncio
async def test_dispatch_before_print_and_was_print_invoked_handle_page_evaluate_paths():
    class FakePage:
        def __init__(self):
            self.scripts = []

        async def evaluate(self, script):
            self.scripts.append(script)
            return True

    page = FakePage()
    printer = LegantoPdfPrinter()

    await printer._dispatch_before_print(page)
    assert await printer._was_print_invoked(page) is True
    assert 'beforeprint' in page.scripts[0]
    assert 'window.__moodleDlPrintInvoked' in page.scripts[0]


@pytest.mark.asyncio
async def test_describe_print_item_returns_placeholder_when_evaluate_fails():
    class BrokenElement:
        async def evaluate(self, *_args):
            raise RuntimeError('detached')

    description = await LegantoPdfPrinter()._describe_print_item(BrokenElement())

    assert description == '<unavailable: detached>'


@pytest.mark.asyncio
async def test_prepare_print_media_forces_print_css():
    class FakePage:
        def __init__(self):
            self.media_kwargs = None

        async def emulate_media(self, **kwargs):
            self.media_kwargs = kwargs

    page = FakePage()

    await LegantoPdfPrinter()._prepare_print_media(page)

    assert page.media_kwargs == {'media': 'print'}


@pytest.mark.asyncio
async def test_prepare_print_media_ignores_emulation_failure():
    class BrokenPage:
        async def emulate_media(self, **_kwargs):
            raise RuntimeError('unsupported')

    await LegantoPdfPrinter()._prepare_print_media(BrokenPage())


@pytest.mark.asyncio
async def test_dump_debug_artifacts_is_disabled_by_default(monkeypatch):
    page = FakeLegantoPage()
    monkeypatch.delenv('MOODLE_DL_LEGANTO_DEBUG', raising=False)

    await LegantoPdfPrinter()._dump_debug_artifacts(page, 'print list failed')

    assert page.screenshot_calls == []


@pytest.mark.asyncio
async def test_dump_debug_artifacts_writes_tmp_files_when_enabled(monkeypatch):
    page = FakeLegantoPage()
    monkeypatch.setenv('MOODLE_DL_LEGANTO_DEBUG', '1')
    monkeypatch.setattr(time, 'time', lambda: 1234567890)
    png_path = '/tmp/moodle_dl_leganto_print-list-failed_1234567890.png'
    html_path = '/tmp/moodle_dl_leganto_print-list-failed_1234567890.html'

    try:
        await LegantoPdfPrinter()._dump_debug_artifacts(page, 'print list failed')

        assert page.screenshot_calls == [{'path': png_path, 'full_page': True}]
        with open(png_path, 'rb') as screenshot_file:
            assert screenshot_file.read() == b'png'
        with open(html_path, encoding='utf-8') as html_file:
            assert html_file.read() == '<html><body>debug</body></html>'
    finally:
        for path in (png_path, html_path):
            try:
                import os
                os.remove(path)
            except FileNotFoundError:
                pass


@pytest.mark.asyncio
async def test_wait_for_leganto_page_finds_existing_popup():
    class FakePage:
        def __init__(self, url):
            self.url = url
            self.waits = 0

        async def wait_for_load_state(self, *_args, **_kwargs):
            self.waits += 1

        async def wait_for_timeout(self, *_args):
            raise AssertionError('Leganto popup should be found without polling timeout')

    moodle_page = FakePage('https://keats.kcl.ac.uk/mod/lti/view.php?id=1')
    leganto_page = FakePage('https://rl.kcl.ac.uk/leganto/nui/lists/123?auth=SAML')
    context = SimpleNamespace(pages=[moodle_page, leganto_page])

    result = await LegantoPdfPrinter()._wait_for_leganto_page(context, moodle_page, timeout_ms=1000)

    assert result is leganto_page
    assert leganto_page.waits == 1


@pytest.mark.asyncio
async def test_maybe_get_popup_returns_popup_and_waits_for_load_state():
    popup = FakeLegantoPage()

    async def popup_task():
        return popup

    task = asyncio.create_task(popup_task())
    result = await LegantoPdfPrinter()._maybe_get_popup(task, timeout_ms=1_000)

    assert result is popup
    assert popup.load_states == [('domcontentloaded', {'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS})]
    assert task.done() is True


@pytest.mark.asyncio
async def test_maybe_get_popup_cancels_unresolved_popup_task():
    async def never_returns():
        await asyncio.sleep(10)

    task = asyncio.create_task(never_returns())

    result = await LegantoPdfPrinter()._maybe_get_popup(task, timeout_ms=1)
    await asyncio.sleep(0)

    assert result is None
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_open_moodle_lti_launch_returns_existing_leganto_page():
    page = FakeLegantoPage()
    page.url = 'https://keats.kcl.ac.uk/mod/lti/view.php?id=99'
    leganto_page = FakeLegantoPage()

    class FakeContext:
        def __init__(self):
            self.pages = [page, leganto_page]

        async def wait_for_event(self, *_args, **_kwargs):
            await asyncio.sleep(10)

    printer = LegantoPdfPrinter()
    printer._stabilize_page = AsyncMock()

    result = await printer._open_moodle_lti_launch(
        FakeContext(),
        page,
        'https://keats.kcl.ac.uk/mod/lti/view.php?id=99',
    )

    assert result is leganto_page
    assert page.goto_calls == [(
        'https://keats.kcl.ac.uk/mod/lti/view.php?id=99',
        {'wait_until': 'domcontentloaded', 'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS},
    )]


@pytest.mark.asyncio
async def test_open_moodle_lti_launch_submits_visible_form_and_uses_popup():
    page = FakeLegantoPage()
    page.url = 'https://keats.kcl.ac.uk/mod/lti/view.php?id=99'
    popup = FakeLegantoPage()

    class FakeContext:
        pages = []

        async def wait_for_event(self, *_args, **_kwargs):
            await asyncio.sleep(10)

    printer = LegantoPdfPrinter()
    printer._stabilize_page = AsyncMock()
    printer._wait_for_leganto_page = AsyncMock(return_value=None)
    printer._submit_visible_lti_form = AsyncMock(return_value=True)
    printer._maybe_get_popup = AsyncMock(return_value=popup)

    result = await printer._open_moodle_lti_launch(
        FakeContext(),
        page,
        'https://keats.kcl.ac.uk/mod/lti/view.php?id=99',
    )

    assert result is popup
    assert printer._wait_for_leganto_page.await_count == 2
    printer._submit_visible_lti_form.assert_awaited_once_with(page)
    printer._maybe_get_popup.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_moodle_lti_launch_clicks_launch_control_with_force_after_failure():
    page = FakeLegantoPage()
    page.url = 'https://keats.kcl.ac.uk/mod/lti/view.php?id=99'
    popup = FakeLegantoPage()
    launch_control = FakeElement(visible=True, enabled=True)

    async def click_launch(**kwargs):
        launch_control.clicks.append(kwargs)
        if not kwargs.get('force'):
            raise RuntimeError('overlay')

    launch_control.click = click_launch

    class FakeContext:
        pages = []

        async def wait_for_event(self, *_args, **_kwargs):
            await asyncio.sleep(10)

    printer = LegantoPdfPrinter()
    printer._stabilize_page = AsyncMock()
    printer._wait_for_leganto_page = AsyncMock(return_value=None)
    printer._submit_visible_lti_form = AsyncMock(return_value=False)
    printer._find_lti_launch_control = AsyncMock(return_value=launch_control)
    printer._maybe_get_popup = AsyncMock(return_value=popup)

    result = await printer._open_moodle_lti_launch(
        FakeContext(),
        page,
        'https://keats.kcl.ac.uk/mod/lti/view.php?id=99',
    )

    assert result is popup
    assert launch_control.clicks == [{'timeout': 10_000}, {'timeout': 10_000, 'force': True}]
    printer._find_lti_launch_control.assert_awaited_once_with(page)


@pytest.mark.asyncio
async def test_open_from_moodle_course_returns_popup_from_reading_list_link():
    page = FakeLegantoPage()
    popup = FakeLegantoPage()
    popup.url = 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'
    link = FakeElement(visible=True, enabled=True)

    class FakeContext:
        async def wait_for_event(self, event, timeout):
            assert event == 'page'
            assert timeout == 8_000
            return popup

    printer = LegantoPdfPrinter()
    printer._stabilize_page = AsyncMock()
    printer._find_moodle_reading_list_link = AsyncMock(return_value=link)

    result = await printer._open_from_moodle_course(
        FakeContext(),
        page,
        'https://keats.kcl.ac.uk/course/view.php?id=134658',
    )

    assert result is popup
    assert page.goto_calls == [(
        'https://keats.kcl.ac.uk/course/view.php?id=134658',
        {'wait_until': 'domcontentloaded', 'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS},
    )]
    assert link.scrolled is True
    assert link.clicks == [{'timeout': 10_000}]
    assert popup.load_states == [('domcontentloaded', {'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS})]


@pytest.mark.asyncio
async def test_open_from_moodle_course_uses_same_page_when_no_popup_opens():
    page = FakeLegantoPage()
    page.url = 'https://keats.kcl.ac.uk/course/view.php?id=134658'
    link = FakeElement(visible=True, enabled=True)

    class FakeContext:
        async def wait_for_event(self, *_args, **_kwargs):
            raise TimeoutError('no popup')

    printer = LegantoPdfPrinter()
    printer._stabilize_page = AsyncMock()
    printer._find_moodle_reading_list_link = AsyncMock(return_value=link)

    result = await printer._open_from_moodle_course(
        FakeContext(),
        page,
        'https://keats.kcl.ac.uk/course/view.php?id=134658',
    )

    assert result is page
    assert page.load_states == [('domcontentloaded', {'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS})]
    assert link.clicks == [{'timeout': 10_000}]


@pytest.mark.asyncio
async def test_print_to_pdf_closes_browser_context_and_filters_launch_cookies(tmp_path, monkeypatch):
    class FakePdfPage:
        def __init__(self):
            self.pdf_kwargs = None
            self.media_kwargs = None

        async def emulate_media(self, **kwargs):
            self.media_kwargs = kwargs

        async def pdf(self, **kwargs):
            self.pdf_kwargs = kwargs

    class FakeContext:
        def __init__(self):
            self.closed = False
            self.page = FakePdfPage()

        async def new_page(self):
            return self.page

        async def close(self):
            self.closed = True

    class FakeBrowser:
        def __init__(self):
            self.closed = False
            self.context = FakeContext()
            self.new_context_kwargs = None

        async def new_context(self, **kwargs):
            self.new_context_kwargs = kwargs
            return self.context

        async def close(self):
            self.closed = True

    class FakeChromium:
        def __init__(self):
            self.browser = FakeBrowser()
            self.launch_kwargs = None

        async def launch(self, **kwargs):
            self.launch_kwargs = kwargs
            return self.browser

    class FakePlaywrightManager:
        def __init__(self):
            self.chromium = FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    manager = FakePlaywrightManager()
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'playwright.async_api':
            return SimpleNamespace(async_playwright=lambda: manager)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', fake_import)

    output = tmp_path / 'reading-list.pdf'
    printer = LegantoPdfPrinter('cookie-data', skip_cert_verify=True, headless=False)
    printer._add_cookies = AsyncMock()
    printer._launch_lti_form = AsyncMock()
    printer._wait_for_leganto_ready = AsyncMock()
    printer._dismiss_cookie_banner = AsyncMock()
    printer._download_direct_print_pdf = AsyncMock(return_value=False)
    printer._trigger_print_list = AsyncMock(return_value=manager.chromium.browser.context.page)
    printer._stabilize_page = AsyncMock()

    await printer.print_to_pdf(
        'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        str(output),
        launch_parameters=[{'name': 'id_token', 'value': 'signed'}],
    )

    browser = manager.chromium.browser
    context = browser.context
    assert manager.chromium.launch_kwargs == {'headless': False}
    assert browser.new_context_kwargs == {'ignore_https_errors': True}
    printer._add_cookies.assert_awaited_once_with(context, skip_leganto_launch_cookies=True)
    printer._launch_lti_form.assert_awaited_once()
    printer._download_direct_print_pdf.assert_awaited_once_with(context.page, '', str(output))
    printer._trigger_print_list.assert_awaited_once_with(context, context.page)
    assert context.page.media_kwargs == {'media': 'print'}
    assert context.page.pdf_kwargs == {
        'path': str(output),
        'format': 'A4',
        'print_background': True,
    }
    assert context.closed is True
    assert browser.closed is True


@pytest.mark.asyncio
async def test_print_to_pdf_reports_missing_playwright_dependency(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'playwright.async_api':
            raise ImportError('playwright missing')
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', fake_import)

    with pytest.raises(RuntimeError, match='Playwright is required'):
        await LegantoPdfPrinter().print_to_pdf(
            'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
            str(tmp_path / 'reading-list.pdf'),
        )


@pytest.mark.asyncio
async def test_print_to_pdf_reports_missing_chromium(tmp_path, monkeypatch):
    class BrokenChromium:
        async def launch(self, **_kwargs):
            raise RuntimeError('browser executable missing')

    class FakePlaywrightManager:
        chromium = BrokenChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'playwright.async_api':
            return SimpleNamespace(async_playwright=lambda: FakePlaywrightManager())
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', fake_import)

    with pytest.raises(RuntimeError, match='Chromium is required'):
        await LegantoPdfPrinter().print_to_pdf(
            'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
            str(tmp_path / 'reading-list.pdf'),
        )


@pytest.mark.asyncio
async def test_print_to_pdf_opens_official_print_endpoint_before_page_pdf(tmp_path, monkeypatch):
    page = FakePrintableLegantoPage()
    manager = install_fake_playwright(monkeypatch, page)
    output = tmp_path / 'reading-list.pdf'
    printer = LegantoPdfPrinter('cookie-data')
    printer._add_cookies = AsyncMock()
    printer._wait_for_leganto_ready = AsyncMock()
    printer._dismiss_cookie_banner = AsyncMock()
    printer._download_direct_print_pdf = AsyncMock(return_value=False)
    printer._trigger_print_list = AsyncMock()
    printer._stabilize_page = AsyncMock()

    await printer.print_to_pdf(page.url, str(output))

    print_url = 'https://rl.kcl.ac.uk/leganto/rl/files/en/print/list/15085102330006881/studentView'
    assert page.goto_calls == [
        (
            'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
            {'wait_until': 'domcontentloaded', 'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS},
        ),
        (
            print_url,
            {'wait_until': 'domcontentloaded', 'timeout': LegantoPdfPrinter.PRINT_TIMEOUT_MS},
        ),
    ]
    printer._download_direct_print_pdf.assert_awaited_once_with(
        page,
        'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
        str(output),
    )
    printer._trigger_print_list.assert_not_awaited()
    assert page.media_kwargs == {'media': 'print'}
    assert page.pdf_url == print_url
    assert page.pdf_kwargs == {'path': str(output), 'format': 'A4', 'print_background': True}
    assert manager.chromium.browser.context.closed is True
    assert manager.chromium.browser.closed is True


@pytest.mark.asyncio
async def test_print_to_pdf_moodle_launch_url_uses_lti_flow_and_keeps_launch_cookies(tmp_path, monkeypatch):
    page = FakePrintableLegantoPage('https://keats.kcl.ac.uk/mod/lti/view.php?id=99')
    leganto_page = FakePrintableLegantoPage()
    manager = install_fake_playwright(monkeypatch, page)
    output = tmp_path / 'reading-list.pdf'
    printer = LegantoPdfPrinter('cookie-data')
    printer._add_cookies = AsyncMock()
    printer._open_moodle_lti_launch = AsyncMock(return_value=leganto_page)
    printer._wait_for_leganto_ready = AsyncMock()
    printer._dismiss_cookie_banner = AsyncMock()
    printer._download_direct_print_pdf = AsyncMock(return_value=True)

    await printer.print_to_pdf(
        'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
        str(output),
        moodle_launch_url='https://keats.kcl.ac.uk/mod/lti/view.php?id=99',
    )

    context = manager.chromium.browser.context
    printer._add_cookies.assert_awaited_once_with(context, skip_leganto_launch_cookies=False)
    printer._open_moodle_lti_launch.assert_awaited_once_with(
        context,
        page,
        'https://keats.kcl.ac.uk/mod/lti/view.php?id=99',
    )
    printer._download_direct_print_pdf.assert_awaited_once_with(leganto_page, leganto_page.url, str(output))
    assert context.closed is True
    assert manager.chromium.browser.closed is True


@pytest.mark.asyncio
async def test_print_to_pdf_course_url_uses_course_link_flow_and_filters_launch_cookies(tmp_path, monkeypatch):
    page = FakePrintableLegantoPage('about:blank')
    leganto_page = FakePrintableLegantoPage()
    manager = install_fake_playwright(monkeypatch, page)
    output = tmp_path / 'reading-list.pdf'
    printer = LegantoPdfPrinter('cookie-data')
    printer._add_cookies = AsyncMock()
    printer._open_from_moodle_course = AsyncMock(return_value=leganto_page)
    printer._wait_for_leganto_ready = AsyncMock()
    printer._dismiss_cookie_banner = AsyncMock()
    printer._download_direct_print_pdf = AsyncMock(return_value=True)

    await printer.print_to_pdf(
        'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
        str(output),
        course_url='https://keats.kcl.ac.uk/course/view.php?id=134658',
    )

    context = manager.chromium.browser.context
    printer._add_cookies.assert_awaited_once_with(context, skip_leganto_launch_cookies=True)
    printer._open_from_moodle_course.assert_awaited_once_with(
        context,
        page,
        'https://keats.kcl.ac.uk/course/view.php?id=134658',
    )
    printer._download_direct_print_pdf.assert_awaited_once_with(leganto_page, leganto_page.url, str(output))
    assert context.closed is True
    assert manager.chromium.browser.closed is True


@pytest.mark.asyncio
async def test_print_to_pdf_short_circuits_when_direct_print_pdf_download_succeeds(tmp_path, monkeypatch):
    class FakePdfPage:
        url = 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'

        async def pdf(self, **_kwargs):
            raise AssertionError('page.pdf should not run when direct PDF download succeeds')

    class FakeContext:
        def __init__(self):
            self.closed = False
            self.page = FakePdfPage()

        async def new_page(self):
            return self.page

        async def close(self):
            self.closed = True

    class FakeBrowser:
        def __init__(self):
            self.closed = False
            self.context = FakeContext()

        async def new_context(self, **_kwargs):
            return self.context

        async def close(self):
            self.closed = True

    class FakeChromium:
        def __init__(self):
            self.browser = FakeBrowser()

        async def launch(self, **_kwargs):
            return self.browser

    class FakePlaywrightManager:
        def __init__(self):
            self.chromium = FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    manager = FakePlaywrightManager()
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'playwright.async_api':
            return SimpleNamespace(async_playwright=lambda: manager)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', fake_import)

    output = tmp_path / 'reading-list.pdf'
    printer = LegantoPdfPrinter('cookie-data')
    printer._add_cookies = AsyncMock()
    printer._launch_lti_form = AsyncMock()
    printer._wait_for_leganto_ready = AsyncMock()
    printer._dismiss_cookie_banner = AsyncMock()
    printer._download_direct_print_pdf = AsyncMock(return_value=True)
    printer._trigger_print_list = AsyncMock()
    printer._prepare_print_media = AsyncMock()
    printer._stabilize_page = AsyncMock()

    await printer.print_to_pdf(
        'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        str(output),
        launch_parameters=[{'name': 'id_token', 'value': 'signed'}],
    )

    context = manager.chromium.browser.context
    printer._download_direct_print_pdf.assert_awaited_once_with(context.page, context.page.url, str(output))
    printer._trigger_print_list.assert_not_awaited()
    printer._prepare_print_media.assert_not_awaited()
    printer._stabilize_page.assert_not_awaited()
    assert context.closed is True
    assert manager.chromium.browser.closed is True


# ---- Wall-clock budget --------------------------------------------------
#
# 真实场景：Leganto + LTI + KCL SSO 偶尔会让 print_to_pdf 卡 5+ 分钟（多个
# 60s 阶段串联），阻塞整个 retry pipeline。一旦超过 TOTAL_BUDGET_S，必须放
# 弃这份 Reading List 让 pipeline 继续。

@pytest.mark.asyncio
async def test_print_to_pdf_raises_on_wall_clock_timeout(monkeypatch, tmp_path):
    printer = LegantoPdfPrinter('cookie-data')
    printer.TOTAL_BUDGET_S = 0.2  # 缩短到 200ms，测试本身不能跑半天

    async def hang_forever(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(printer, '_print_to_pdf_unbounded', hang_forever)

    with pytest.raises(RuntimeError, match=r'渲染超过 0\.2s 预算'):
        await printer.print_to_pdf(
            'https://rl.kcl.ac.uk/leganto/nui/lists/1',
            str(tmp_path / 'out.pdf'),
        )


@pytest.mark.asyncio
async def test_print_to_pdf_propagates_other_errors_unchanged(monkeypatch, tmp_path):
    """wall-clock 包装不应吞掉真正的内部错误。"""
    printer = LegantoPdfPrinter('cookie-data')
    printer.TOTAL_BUDGET_S = 60

    async def fail(*args, **kwargs):
        raise RuntimeError('Leganto reading list did not load: Leganto error page')

    monkeypatch.setattr(printer, '_print_to_pdf_unbounded', fail)

    with pytest.raises(RuntimeError, match='Leganto error page'):
        await printer.print_to_pdf(
            'https://rl.kcl.ac.uk/leganto/nui/lists/1',
            str(tmp_path / 'out.pdf'),
        )


@pytest.mark.asyncio
async def test_context_close_failure_does_not_mask_real_error(monkeypatch, tmp_path):
    """context.close() / browser.close() 失败不应吞掉 _wait_for_leganto_ready 的诊断信息。

    历史 bug：5 分钟卡死后真错被 TargetClosedError(BrowserContext.close) 吞了，
    用户只看到无关的关闭错误，看不到 'Leganto error page' 之类的根因。
    """
    page = SimpleNamespace(
        url='https://rl.kcl.ac.uk/leganto/nui/lists/1',
        context=None,
        goto=AsyncMock(),
    )

    class ExplodingContext:
        def __init__(self):
            self.page = page
            page.context = self

        async def new_page(self):
            return self.page

        async def add_cookies(self, _cookies):
            pass

        async def close(self):
            raise RuntimeError('TargetClosedError: BrowserContext already closed')

    class ExplodingBrowser:
        def __init__(self):
            self.context = ExplodingContext()

        async def new_context(self, **_):
            return self.context

        async def close(self):
            raise RuntimeError('Browser close also fails')

    class ExplodingChromium:
        def __init__(self):
            self.browser = ExplodingBrowser()

        async def launch(self, **_):
            return self.browser

    class FakeManager:
        chromium = ExplodingChromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False

    manager = FakeManager()
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'playwright.async_api':
            return SimpleNamespace(async_playwright=lambda: manager)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)

    printer = LegantoPdfPrinter('cookie-data')
    printer._add_cookies = AsyncMock()
    # 真正的诊断错误从内部抛出
    printer._wait_for_leganto_ready = AsyncMock(
        side_effect=RuntimeError('Leganto reading list did not load: Leganto error page')
    )

    with pytest.raises(RuntimeError, match='Leganto error page'):
        await printer.print_to_pdf(
            'https://rl.kcl.ac.uk/leganto/nui/lists/1',
            str(tmp_path / 'out.pdf'),
        )
    # 关键：用户看到的是 'Leganto error page'，不是 close 的二次异常


@pytest.mark.asyncio
async def test_wall_clock_budget_is_a_class_constant(monkeypatch, tmp_path):
    """TOTAL_BUDGET_S 默认 90s，但允许 subclass 或测试覆盖。"""
    assert LegantoPdfPrinter.TOTAL_BUDGET_S == 90


@pytest.mark.asyncio
async def test_print_to_pdf_propagates_cancellation_without_conversion(monkeypatch, tmp_path):
    """用户 Ctrl+C → CancelledError 必须原样穿透，不能被转成 timeout RuntimeError。

    asyncio.wait_for 在内部 task 被外部取消时抛 CancelledError（不是 TimeoutError）。
    如果有人把 'except asyncio.TimeoutError' 简化成 'except Exception'，Ctrl+C 就会
    变成 '渲染超过 90s 预算' 这种误导性错误，调用栈也会被截断。
    """
    printer = LegantoPdfPrinter('cookie-data')
    printer.TOTAL_BUDGET_S = 60

    async def hang_forever(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(printer, '_print_to_pdf_unbounded', hang_forever)

    task = asyncio.create_task(printer.print_to_pdf(
        'https://rl.kcl.ac.uk/leganto/nui/lists/1',
        str(tmp_path / 'out.pdf'),
    ))
    # 让 wait_for 开始等
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
