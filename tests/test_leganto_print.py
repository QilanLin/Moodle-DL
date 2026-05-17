import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moodle_dl.downloader.leganto_print import (
    LegantoPdfPrinter,
    build_lti_launch_form,
    cookies_text_to_playwright,
    is_leganto_lti_launch_url,
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
        self.scrolled = False

    async def is_visible(self, **_kwargs):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def scroll_into_view_if_needed(self, **_kwargs):
        self.scrolled = True

    async def click(self, **kwargs):
        self.clicks.append(kwargs)

    async def wait_for(self, **_kwargs):
        if not self.visible:
            raise TimeoutError('not visible')

    async def evaluate(self, _script):
        return self.html or self.description

    async def inner_text(self, **_kwargs):
        return self.text


class FakeLegantoPage:
    def __init__(self):
        self.url = 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'
        self.keyboard = SimpleNamespace(press=AsyncMock())
        self.menu_open = False
        self.waited = []
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


def test_leganto_url_detection_is_specific_to_kcl_reading_lists():
    assert is_leganto_reading_list_url('https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML')
    assert is_leganto_lti_launch_url('https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1')

    assert not is_leganto_reading_list_url('https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1')
    assert not is_leganto_lti_launch_url('https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881')
    assert not is_leganto_reading_list_url('https://example.com/leganto/nui/lists/15085102330006881')


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
async def test_trigger_print_list_returns_popup_when_print_opens_new_page():
    page = FakeLegantoPage()
    page.print_item.visible = True
    popup = FakeElement()
    popup.wait_for_load_state = AsyncMock()

    class FakeContext:
        async def wait_for_event(self, event, timeout):
            assert event == 'page'
            assert timeout == 5_000
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

    assert page.print_item.clicks == [{}]


@pytest.mark.asyncio
async def test_trigger_print_list_accepts_current_page_when_window_print_runs():
    page = FakeLegantoPage()
    page.print_item.visible = True
    printer = LegantoPdfPrinter()
    printer._was_print_invoked = AsyncMock(return_value=True)

    class FakeContext:
        async def wait_for_event(self, *_args, **_kwargs):
            raise TimeoutError('no popup')

    result = await printer._trigger_print_list(FakeContext(), page)

    assert result is page
    assert page.print_item.clicks == [{}]
    printer._was_print_invoked.assert_awaited_once_with(page)


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
async def test_print_to_pdf_closes_browser_context_and_filters_launch_cookies(tmp_path, monkeypatch):
    class FakePdfPage:
        def __init__(self):
            self.pdf_kwargs = None

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
    printer._trigger_print_list.assert_awaited_once_with(context, context.page)
    assert context.page.pdf_kwargs == {
        'path': str(output),
        'format': 'A4',
        'print_background': True,
    }
    assert context.closed is True
    assert browser.closed is True
