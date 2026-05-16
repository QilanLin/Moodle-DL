# -*- coding: utf-8 -*-
import asyncio
import html
import logging
import re
import urllib.parse as urlparse
from http.cookiejar import MozillaCookieJar
from io import StringIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from moodle_dl.utils import MoodleDLCookieJar


LEGANTO_LIST_PATH_RE = re.compile(r'^/leganto/nui/lists/[^/?#]+')
LEGANTO_ERROR_PATH_RE = re.compile(r'^/leganto/nui/error/')
LEGANTO_ERROR_MARKERS = (
    'Failed LTI',
    'Invalid token',
    'The user is not authorized',
    'Please contact your course instructor',
)
LEGANTO_LAUNCH_COOKIE_NAMES = {
    'JSESSIONID',
    'XSRF-TOKEN',
    'auth',
    'digitalDoc',
    'idpCode',
    'institute',
    'productCode',
    'urm_se',
    'urm_st',
}


def is_leganto_reading_list_url(url: str) -> bool:
    """Return True when the URL points at a Leganto reading-list page."""
    parsed = urlparse.urlparse(url or '')
    host = (parsed.hostname or '').lower()
    if not host:
        return False
    return host == 'rl.kcl.ac.uk' and bool(LEGANTO_LIST_PATH_RE.match(parsed.path or ''))


def is_leganto_lti_launch_url(url: str) -> bool:
    """Return True when the URL points at KCL's Leganto LTI launch endpoint."""
    parsed = urlparse.urlparse(url or '')
    host = (parsed.hostname or '').lower()
    return host == 'rl.kcl.ac.uk' and (parsed.path or '').startswith('/lti/')


def summarize_leganto_load_error(url: str, body_text: str = '') -> Optional[str]:
    """Return a concise load error when Leganto shows an auth/LTI error page."""
    parsed = urlparse.urlparse(url or '')
    host = (parsed.hostname or '').lower()
    path = parsed.path or ''

    if 'login.microsoftonline.com' in host:
        return 'Leganto redirected to Microsoft account sign-in'

    if host == 'rl.kcl.ac.uk' and LEGANTO_ERROR_PATH_RE.match(path):
        for marker in LEGANTO_ERROR_MARKERS:
            if marker in body_text:
                return f'Leganto reading list did not load: {marker}'
        return 'Leganto reading list did not load: Leganto error page'

    for marker in LEGANTO_ERROR_MARKERS:
        if marker in body_text:
            return f'Leganto reading list did not load: {marker}'

    return None


def build_lti_launch_form(endpoint: str, parameters: Iterable[Dict[str, str]]) -> str:
    """Build a minimal auto-submitting LTI launch form."""
    inputs = []
    for parameter in parameters or []:
        name = html.escape(str(parameter.get('name', '')), quote=True)
        value = html.escape(str(parameter.get('value', '')), quote=True)
        if name:
            inputs.append(f'<input type="hidden" name="{name}" value="{value}" />')

    inputs_html = '\n'.join(inputs)
    escaped_endpoint = html.escape(endpoint, quote=True)
    return f'''<!doctype html>
<html>
<head><meta charset="utf-8"><title>Launch Leganto</title></head>
<body>
<form id="launchForm" action="{escaped_endpoint}" method="post" enctype="application/x-www-form-urlencoded">
{inputs_html}
</form>
<script>document.getElementById("launchForm").submit();</script>
</body>
</html>
'''


def cookies_text_to_playwright(cookies_text: Optional[str]) -> List[Dict]:
    """Convert Netscape cookies.txt content to Playwright cookie dictionaries."""
    if not cookies_text:
        return []

    cookie_jar: MozillaCookieJar = MoodleDLCookieJar(StringIO(cookies_text))
    cookie_jar.load(ignore_discard=True, ignore_expires=True)

    cookies = []
    for cookie in cookie_jar:
        if not cookie.name or not cookie.domain or not cookie.path:
            continue

        playwright_cookie = {
            'name': str(cookie.name),
            'value': str(cookie.value),
            'domain': cookie.domain,
            'path': cookie.path,
            'secure': bool(cookie.secure),
            'httpOnly': bool(cookie.has_nonstandard_attr('HttpOnly') or cookie.has_nonstandard_attr('httponly')),
        }
        if cookie.expires is not None and cookie.expires > 0:
            playwright_cookie['expires'] = int(cookie.expires)
        cookies.append(playwright_cookie)

    return cookies


class LegantoPdfPrinter:
    """Open a Leganto reading list, trigger Print list, and save the page as PDF."""

    PRINT_TIMEOUT_MS = 60_000

    def __init__(
        self,
        cookies_text: Optional[str] = None,
        *,
        skip_cert_verify: bool = False,
        headless: bool = True,
    ):
        self.cookies_text = cookies_text
        self.skip_cert_verify = skip_cert_verify
        self.headless = headless

    async def print_to_pdf(
        self,
        url: str,
        output_path: str,
        *,
        launch_parameters: Optional[List[Dict[str, str]]] = None,
        course_url: Optional[str] = None,
    ) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                'Playwright is required to save Leganto reading lists as PDF. '
                'Install it with: python3 -m pip install playwright && python3 -m playwright install chromium'
            ) from exc

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=self.headless)
            except Exception as exc:
                raise RuntimeError(
                    'Chromium is required to save Leganto reading lists as PDF. '
                    'Install it with: python3 -m playwright install chromium'
                ) from exc

            context = await browser.new_context(ignore_https_errors=self.skip_cert_verify)
            try:
                await self._add_cookies(
                    context,
                    skip_leganto_launch_cookies=bool(launch_parameters is not None or course_url),
                )
                page = await context.new_page()

                if launch_parameters is not None:
                    await self._launch_lti_form(page, url, launch_parameters)
                elif course_url:
                    page = await self._open_from_moodle_course(context, page, course_url)
                else:
                    await page.goto(url, wait_until='domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)

                await self._wait_for_leganto_ready(page)
                await self._dismiss_cookie_banner(page)
                print_page = await self._trigger_print_list(context, page)
                await self._stabilize_page(print_page)
                await print_page.pdf(path=output_path, format='A4', print_background=True)
            finally:
                await context.close()
                await browser.close()

    async def _add_cookies(self, context, *, skip_leganto_launch_cookies: bool = False) -> None:
        cookies = cookies_text_to_playwright(self.cookies_text)
        if skip_leganto_launch_cookies:
            cookies = [
                cookie for cookie in cookies
                if not self._is_leganto_launch_cookie(cookie)
            ]
        if not cookies:
            return

        try:
            await context.add_cookies(cookies)
            return
        except Exception as exc:
            logging.debug('Bulk Playwright cookie import failed for Leganto PDF export: %s', exc)

        for cookie in cookies:
            try:
                await context.add_cookies([cookie])
            except Exception as exc:
                logging.debug(
                    'Skipping cookie during Leganto PDF export: domain=%s name=%s error=%s',
                    cookie.get('domain'),
                    cookie.get('name'),
                    exc,
                )

    @staticmethod
    def _is_leganto_launch_cookie(cookie: Dict) -> bool:
        domain = str(cookie.get('domain') or '').lstrip('.').lower()
        name = str(cookie.get('name') or '')
        return domain == 'rl.kcl.ac.uk' and name in LEGANTO_LAUNCH_COOKIE_NAMES

    async def _launch_lti_form(self, page, endpoint: str, parameters: List[Dict[str, str]]) -> None:
        form_html = build_lti_launch_form(endpoint, parameters)
        await page.goto('about:blank')
        await page.set_content(form_html, wait_until='domcontentloaded')
        await page.wait_for_load_state('domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)
        await self._stabilize_page(page)

    async def _open_from_moodle_course(self, context, page, course_url: str):
        await page.goto(course_url, wait_until='domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)
        await self._stabilize_page(page)

        link = await self._find_moodle_reading_list_link(page)
        popup_task = asyncio.create_task(context.wait_for_event('page', timeout=8_000))
        try:
            await link.scroll_into_view_if_needed(timeout=3_000)
        except Exception:
            pass

        try:
            await link.click(timeout=10_000)
        except Exception:
            if not popup_task.done():
                popup_task.cancel()
            raise

        try:
            popup = await popup_task
            await popup.wait_for_load_state('domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)
            return popup
        except Exception:
            if not popup_task.done():
                popup_task.cancel()
            await page.wait_for_load_state('domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)
            return page

    async def _find_moodle_reading_list_link(self, page):
        candidates = [
            page.locator('a[href*="/mod/lti/view.php"]').filter(has_text=re.compile(r'\bReading List\b', re.I)),
            page.get_by_role('link', name=re.compile(r'\bReading List\b', re.I)),
            page.locator('a').filter(has_text=re.compile(r'\bReading List\b', re.I)),
            page.locator('a[href*="rl.kcl.ac.uk"], a[href*="leganto"]'),
        ]

        for candidate in candidates:
            count = await candidate.count()
            for index in range(min(count, 10)):
                link = candidate.nth(index)
                try:
                    if await link.is_visible() and await link.is_enabled():
                        return link
                except Exception:
                    continue

        raise RuntimeError('Could not find a Moodle Reading List link on the course page')

    async def _wait_for_leganto_ready(self, page) -> None:
        try:
            await page.wait_for_url('**/leganto/nui/lists/**', timeout=self.PRINT_TIMEOUT_MS)
        except Exception:
            logging.debug('Leganto URL pattern was not reached before readiness checks')

        load_error = await self._get_load_error(page)
        if load_error:
            raise RuntimeError(load_error)

        try:
            await page.wait_for_function(
                """() => {
                    const text = document.body ? document.body.innerText || '' : '';
                    return /\\b(List info|View sections|Print list|Filter|Search)\\b/i.test(text);
                }""",
                timeout=self.PRINT_TIMEOUT_MS,
            )
            return
        except Exception:
            logging.debug('Leganto rendered-content readiness check timed out')

        load_error = await self._get_load_error(page)
        if load_error:
            raise RuntimeError(load_error)

        current_url = page.url
        if '/leganto/' not in current_url:
            raise RuntimeError(f'Leganto reading list did not load; final URL was {current_url}')

        body_text = await self._body_text(page)
        root_html = await self._root_html(page)
        if not body_text.strip() and '<lg-root' in root_html:
            raise RuntimeError(
                'Leganto reading list did not render; stored Leganto session cookies may be expired'
            )

    async def _body_text(self, page) -> str:
        try:
            return await page.locator('body').inner_text(timeout=1_000)
        except TypeError:
            try:
                return await page.locator('body').inner_text()
            except Exception:
                return ''
        except Exception:
            return ''

    async def _root_html(self, page) -> str:
        try:
            return await page.locator('body').evaluate('el => el.innerHTML')
        except Exception:
            return ''

    async def _get_load_error(self, page) -> Optional[str]:
        body_text = await self._body_text(page)
        return summarize_leganto_load_error(page.url, body_text)

    async def _trigger_print_list(self, context, page):
        if not await self._is_print_item_visible(page):
            await self._open_list_menu(page)

        print_item = await self._wait_for_visible_print_item(page, timeout=10_000)
        if print_item is None:
            raise RuntimeError('Could not find the Leganto "Print list" menu item')

        popup_task = asyncio.create_task(context.wait_for_event('page', timeout=5_000))
        await print_item.click()

        try:
            popup = await popup_task
            await popup.wait_for_load_state('domcontentloaded', timeout=15_000)
            return popup
        except Exception:
            if not popup_task.done():
                popup_task.cancel()
            return page

    async def _is_print_item_visible(self, page) -> bool:
        return await self._visible_print_item(page) is not None

    async def _wait_for_visible_print_item(self, page, timeout: int = 3_000):
        for candidate in self._print_item_candidates(page):
            try:
                item = candidate.first
                await item.wait_for(state='visible', timeout=timeout)
                return item
            except Exception:
                continue

        return None

    async def _visible_print_item(self, page):
        for candidate in self._print_item_candidates(page):
            count = await candidate.count()
            for index in range(min(count, 5)):
                item = candidate.nth(index)
                try:
                    if await item.is_visible(timeout=500):
                        return item
                except TypeError:
                    try:
                        if await item.is_visible():
                            return item
                    except Exception:
                        continue
                except Exception:
                    continue

        return None

    def _print_item_candidates(self, page):
        return [
            page.locator('#lg-menu-action-print'),
            page.get_by_role('menuitem', name=re.compile(r'Print list', re.I)),
            page.locator('button:has-text("Print list")'),
            page.get_by_text(re.compile(r'\bPrint list\b', re.I)),
        ]

    async def _dismiss_cookie_banner(self, page) -> None:
        selectors = [
            '#onetrust-reject-all-handler',
            '#onetrust-accept-btn-handler',
            'button:has-text("Reject all")',
            'button:has-text("Accept all")',
        ]

        for selector in selectors:
            button = page.locator(selector).first
            try:
                if await button.count() == 0:
                    continue
                try:
                    await button.click(timeout=2_000, force=True)
                except Exception:
                    await button.evaluate('el => el.click()')
                await page.wait_for_timeout(750)
                return
            except Exception:
                continue

        await self._remove_onetrust_overlay(page)

    async def _remove_onetrust_overlay(self, page) -> None:
        try:
            removed = await page.evaluate(
                """() => {
                    let removed = false;
                    for (const selector of [
                        '#onetrust-consent-sdk',
                        '#onetrust-banner-sdk',
                        '.onetrust-pc-dark-filter',
                        '.ot-fade-in',
                    ]) {
                        document.querySelectorAll(selector).forEach((element) => {
                            element.remove();
                            removed = true;
                        });
                    }
                    document.documentElement.classList.remove('ot-sdk-show-settings');
                    document.body.classList.remove('ot-sdk-show-settings');
                    return removed;
                }"""
            )
            if removed:
                await page.wait_for_timeout(250)
        except Exception:
            logging.debug('Could not remove OneTrust overlay during Leganto PDF export', exc_info=True)

    async def _open_list_menu(self, page) -> None:
        await self._dismiss_cookie_banner(page)
        candidates = [
            page.get_by_role('button', name=re.compile(r'(more|menu|actions|options)', re.I)),
            page.locator(
                'button[aria-label*="More" i], button[title*="More" i], '
                'button[aria-label*="Action" i], button[title*="Action" i], '
                'button[aria-label*="Option" i], button[title*="Option" i]'
            ),
            page.locator('button[id*="menu" i], button[id*="action" i]'),
            page.locator('button.mat-mdc-menu-trigger, button[aria-haspopup="menu"], button[aria-controls*="mat-menu"]'),
            page.locator('button:has-text("more_horiz"), button:has-text("more_vert"), button:has-text("...")'),
            page.locator('button').filter(has=page.locator('mat-icon:has-text("more_horiz"), mat-icon:has-text("more_vert")')),
            page.locator('button').filter(has=page.locator('mat-icon:has-text("more_horiz")')),
            page.locator('button').filter(has=page.locator('prm-icon')),
        ]

        seen = set()
        for candidate in candidates:
            count = await candidate.count()
            for index in range(min(count, 50)):
                button = candidate.nth(index)
                try:
                    description = await self._describe_button(button)
                    if description in seen:
                        continue
                    seen.add(description)
                    if self._should_skip_menu_candidate(description):
                        continue
                    if await button.is_visible() and await button.is_enabled():
                        try:
                            await button.scroll_into_view_if_needed(timeout=1_000)
                        except Exception:
                            pass
                        try:
                            await button.click(timeout=5_000)
                        except Exception:
                            await self._dismiss_cookie_banner(page)
                            await button.click(timeout=3_000, force=True)
                        await page.wait_for_timeout(500)
                        if await self._wait_for_visible_print_item(page, timeout=3_000):
                            return
                        await self._dismiss_open_menu(page)
                except Exception as exc:
                    logging.debug('Leganto menu candidate failed: %s', exc)
                    await self._dismiss_open_menu(page)

        raise RuntimeError('Could not open the Leganto list menu to find "Print list"')

    async def _describe_button(self, button) -> str:
        try:
            return await button.evaluate(
                """el => [
                    el.id || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('aria-controls') || '',
                    typeof el.className === 'string' ? el.className : '',
                    (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ')
                ].join('|').slice(0, 1000)"""
            )
        except Exception:
            return ''

    @staticmethod
    def _should_skip_menu_candidate(description: str) -> bool:
        lower = (description or '').lower()
        if any(fragment in lower for fragment in ('top-panel-help', 'setting-menu', 'notification')):
            return True

        has_menu_hint = any(fragment in lower for fragment in ('more', 'menu', 'action', 'option', 'mat-mdc-menu-trigger'))
        return not has_menu_hint and any(word in lower for word in ('help', 'settings', 'log in'))

    async def _dismiss_open_menu(self, page) -> None:
        try:
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(100)
        except Exception:
            pass

    async def _stabilize_page(self, page) -> None:
        try:
            await page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            logging.debug('Leganto page did not reach networkidle before PDF export')
        await page.wait_for_timeout(750)
