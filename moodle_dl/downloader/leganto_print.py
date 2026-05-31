# -*- coding: utf-8 -*-
import asyncio
import html
import logging
import os
import re
import time
import urllib.parse as urlparse
from contextlib import suppress
from http.cookiejar import MozillaCookieJar
from io import StringIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from moodle_dl.utils import MoodleDLCookieJar


LEGANTO_LIST_PATH_RE = re.compile(r'^/leganto/nui/lists/[^/?#]+')
LEGANTO_PRINT_PATH_RE = re.compile(r'^/leganto/rl/files/[^/]+/print/list/[^/?#]+/studentView/?$')
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


def is_leganto_print_url(url: str) -> bool:
    """Return True when the URL points at Leganto's printable list view."""
    parsed = urlparse.urlparse(url or '')
    host = (parsed.hostname or '').lower()
    return host == 'rl.kcl.ac.uk' and bool(LEGANTO_PRINT_PATH_RE.match(parsed.path or ''))


def build_leganto_print_url(url: str, language: str = 'en') -> Optional[str]:
    """Build Leganto's printable-list URL from a normal reading-list URL."""
    parsed = urlparse.urlparse(url or '')
    host = (parsed.hostname or '').lower()
    if host != 'rl.kcl.ac.uk':
        return None

    path = parsed.path or ''
    if LEGANTO_PRINT_PATH_RE.match(path):
        return urlparse.urlunparse((parsed.scheme or 'https', parsed.netloc, path, '', '', ''))

    if not LEGANTO_LIST_PATH_RE.match(path):
        return None

    list_id = path.rstrip('/').split('/')[-1]
    if not list_id:
        return None

    safe_list_id = urlparse.quote(list_id, safe='')
    safe_language = urlparse.quote(language or 'en', safe='')
    print_path = f'/leganto/rl/files/{safe_language}/print/list/{safe_list_id}/studentView'
    return urlparse.urlunparse((parsed.scheme or 'https', parsed.netloc, print_path, '', '', ''))


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
    # 单份 Reading List 的总 wall-clock 预算。打开浏览器、LTI launch、Leganto
    # 自身渲染、Print list 弹菜单、page.pdf —— 任何一个阶段卡住都不应让整个
    # 下载 pipeline 卡几分钟。
    # 设 90s 是经验值：正常 Leganto 30s 内就绪；超过 90s 一般是 session/LTI
    # 已经死了，重试也救不回，不如赶紧放弃让 pipeline 往下走。
    TOTAL_BUDGET_S = 90

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
        moodle_launch_url: Optional[str] = None,
        course_url: Optional[str] = None,
    ) -> None:
        """打印 Leganto Reading List 为 PDF，整个流程 wall-clock 受 TOTAL_BUDGET_S 限制。

        各内部 stage 自己也有 PRINT_TIMEOUT_MS 超时，但多个 stage 串联起来可以累
        计出几分钟的死等（实测见过 5 分钟）。外层 wall-clock 是兜底——超过预算
        立即抛 RuntimeError，让 pipeline 继续处理下一个文件。
        """
        try:
            await asyncio.wait_for(
                self._print_to_pdf_unbounded(
                    url, output_path,
                    launch_parameters=launch_parameters,
                    moodle_launch_url=moodle_launch_url,
                    course_url=course_url,
                ),
                timeout=self.TOTAL_BUDGET_S,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f'Leganto reading list 渲染超过 {self.TOTAL_BUDGET_S}s 预算 '
                f'（session/LTI 可能已失效，跳过此文件）'
            ) from exc

    async def _print_to_pdf_unbounded(
        self,
        url: str,
        output_path: str,
        *,
        launch_parameters: Optional[List[Dict[str, str]]] = None,
        moodle_launch_url: Optional[str] = None,
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
                elif moodle_launch_url:
                    page = await self._open_moodle_lti_launch(context, page, moodle_launch_url)
                elif course_url:
                    page = await self._open_from_moodle_course(context, page, course_url)
                else:
                    await page.goto(url, wait_until='domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)

                await self._wait_for_leganto_ready(page)
                await self._dismiss_cookie_banner(page)
                if await self._download_direct_print_pdf(page, getattr(page, 'url', ''), output_path):
                    return
                print_page = await self._open_direct_print_url(page, getattr(page, 'url', ''))
                if print_page is None:
                    print_page = await self._trigger_print_list(context, page)
                await self._prepare_print_media(print_page)
                await self._stabilize_page(print_page)
                await print_page.pdf(path=output_path, format='A4', print_background=True)
            finally:
                # cleanup 一定要包 try/except——TargetClosedError / 取消导致的
                # 二次异常会吞掉 _wait_for_leganto_ready 抛出的真正诊断信息。
                with suppress(Exception):
                    await context.close()
                with suppress(Exception):
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

    async def _open_moodle_lti_launch(self, context, page, launch_url: str):
        popup_task = asyncio.create_task(context.wait_for_event('page', timeout=8_000))
        try:
            await page.goto(launch_url, wait_until='domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)
            await self._stabilize_page(page)
            leganto_page = await self._wait_for_leganto_page(context, page, timeout_ms=10_000)
            if leganto_page is not None:
                return leganto_page

            if not popup_task.done():
                popup_task.cancel()
            popup_task = asyncio.create_task(context.wait_for_event('page', timeout=8_000))
            submitted = await self._submit_visible_lti_form(page)
            if submitted:
                await self._stabilize_page(page)
                leganto_page = await self._wait_for_leganto_page(context, page, timeout_ms=10_000)
                return leganto_page or await self._maybe_get_popup(popup_task, timeout_ms=1_000) or page
            if not popup_task.done():
                popup_task.cancel()

            launch_control = await self._find_lti_launch_control(page)
            if launch_control is not None:
                if not popup_task.done():
                    popup_task.cancel()
                popup_task = asyncio.create_task(context.wait_for_event('page', timeout=8_000))
                try:
                    await launch_control.click(timeout=10_000)
                except Exception:
                    await launch_control.click(timeout=10_000, force=True)
                leganto_page = await self._wait_for_leganto_page(context, page, timeout_ms=10_000)
                if leganto_page is not None:
                    return leganto_page
                popup = await self._maybe_get_popup(popup_task, timeout_ms=1_000)
                if popup is not None and self._is_leganto_page_url(popup.url):
                    return popup
                await page.wait_for_load_state('domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)

            return page
        finally:
            if not popup_task.done():
                popup_task.cancel()

    async def _wait_for_leganto_page(self, context, current_page, *, timeout_ms: int):
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            for candidate in context.pages:
                if self._is_leganto_page_url(candidate.url):
                    try:
                        await candidate.wait_for_load_state('domcontentloaded', timeout=1_000)
                    except Exception:
                        pass
                    return candidate
            await current_page.wait_for_timeout(250)
        return None

    @staticmethod
    def _is_leganto_page_url(url: str) -> bool:
        parsed = urlparse.urlparse(url or '')
        return (parsed.hostname or '').lower() == 'rl.kcl.ac.uk' and '/leganto/' in (parsed.path or '')

    async def _maybe_get_popup(self, popup_task, *, timeout_ms: int):
        try:
            popup = await asyncio.wait_for(asyncio.shield(popup_task), timeout=timeout_ms / 1000)
            await popup.wait_for_load_state('domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)
            return popup
        except Exception:
            if not popup_task.done():
                popup_task.cancel()
            return None

    async def _submit_visible_lti_form(self, page) -> bool:
        forms = page.locator('form[action*="rl.kcl.ac.uk"], form[action*="/lti/"]')
        count = await forms.count()
        for index in range(min(count, 5)):
            form = forms.nth(index)
            try:
                await form.evaluate('form => form.submit()')
                return True
            except Exception:
                continue
        return False

    async def _find_lti_launch_control(self, page):
        candidates = [
            page.get_by_role('button', name=re.compile(r'(launch|open|continue|reading list)', re.I)),
            page.get_by_role('link', name=re.compile(r'(launch|open|continue|reading list)', re.I)),
            page.locator('input[type="submit"], button[type="submit"]'),
            page.locator('a[href*="/mod/lti/view.php"], a[href*="rl.kcl.ac.uk"], a[href*="leganto"]'),
        ]

        for candidate in candidates:
            count = await candidate.count()
            for index in range(min(count, 10)):
                control = candidate.nth(index)
                try:
                    if await control.is_visible() and await control.is_enabled():
                        return control
                except Exception:
                    continue
        return None

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

        original_url = page.url
        await self._install_print_invocation_marker(page)

        activations = ('click', 'press_enter', 'press_space', 'force_click', 'parent_click')
        for activation in activations:
            if not await self._is_print_item_visible(page):
                await self._open_list_menu(page)

            print_item = await self._wait_for_visible_print_item(page, timeout=10_000)
            if print_item is None:
                raise RuntimeError('Could not find the Leganto "Print list" menu item')
            print_item_description = await self._describe_print_item(print_item)
            if os.environ.get('MOODLE_DL_LEGANTO_DEBUG'):
                logging.debug(
                    'Leganto Print list target for %s: %s',
                    activation,
                    print_item_description,
                )

            popup_task = asyncio.create_task(context.wait_for_event('page', timeout=3_000))
            try:
                await self._activate_print_item(print_item, activation)
                print_page = await self._wait_for_print_activation_result(
                    popup_task,
                    page,
                    original_url,
                )
                if print_page is not None:
                    return print_page
                if activation == activations[-1] and self._is_verified_print_menu_item(print_item_description):
                    logging.debug(
                        'Leganto Print list did not emit an automation-visible signal; '
                        'opening the printable Leganto endpoint directly'
                    )
                    direct_print_page = await self._open_direct_print_url(page, original_url)
                    if direct_print_page is not None:
                        return direct_print_page
            finally:
                if not popup_task.done():
                    popup_task.cancel()

            logging.debug('Leganto Print list activation via %s did not produce a printable view', activation)
            await self._dismiss_open_menu(page)

        await self._dump_debug_artifacts(page, 'print-list-not-triggered')
        raise RuntimeError(
            'Leganto "Print list" did not open a printable view; '
            'refusing to save the regular reading-list page as PDF'
        )

    async def _open_direct_print_url(self, page, source_url: str):
        print_url = build_leganto_print_url(source_url)
        if not print_url:
            return None

        if is_leganto_print_url(getattr(page, 'url', '')):
            return page

        logging.debug('Opening Leganto printable list URL: %s', print_url)
        await page.goto(print_url, wait_until='domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)
        try:
            await page.wait_for_load_state('domcontentloaded', timeout=15_000)
        except Exception:
            pass
        return page

    async def _download_direct_print_pdf(self, page, source_url: str, output_path: str) -> bool:
        print_url = build_leganto_print_url(source_url)
        if not print_url:
            return False

        request_context = getattr(getattr(page, 'context', None), 'request', None)
        if request_context is None:
            return False

        headers = {'Accept': 'application/pdf,*/*'}
        referer = getattr(page, 'url', '')
        if referer:
            headers['Referer'] = referer

        try:
            response = await request_context.get(
                print_url,
                headers=headers,
                timeout=self.PRINT_TIMEOUT_MS,
                fail_on_status_code=False,
            )
            body = await response.body()
        except Exception as exc:
            logging.debug('Leganto direct printable PDF request failed: %s', exc)
            return False

        status = getattr(response, 'status', 0)
        headers_map = getattr(response, 'headers', {}) or {}
        content_type = str(headers_map.get('content-type') or headers_map.get('Content-Type') or '').lower()

        if 200 <= status < 300 and body and (body.startswith(b'%PDF') or 'pdf' in content_type):
            Path(output_path).write_bytes(body)
            logging.debug('Saved Leganto printable PDF from direct endpoint: %s', print_url)
            return True

        preview = ''
        if body:
            preview = body[:2000].decode('utf-8', errors='ignore')
        load_error = summarize_leganto_load_error(print_url, preview)
        if status in (401, 403) or load_error:
            reason = load_error or f'HTTP {status}'
            raise RuntimeError(f'Leganto printable PDF request failed: {reason}')

        logging.debug(
            'Leganto direct print URL did not return a PDF (status=%s, content-type=%s); falling back to page PDF',
            status,
            content_type,
        )
        return False

    async def _activate_print_item(self, print_item, activation: str) -> None:
        if activation == 'click':
            await print_item.click()
            return
        if activation == 'force_click':
            await print_item.click(timeout=3_000, force=True)
            return
        if activation == 'press_enter':
            try:
                await print_item.focus()
            except Exception:
                pass
            await print_item.press('Enter')
            return
        if activation == 'press_space':
            try:
                await print_item.focus()
            except Exception:
                pass
            await print_item.press('Space')
            return

        await print_item.evaluate(
            """element => {
                const target = element.closest(
                    'button, [role="menuitem"], .mat-mdc-menu-item, .mat-menu-item, a'
                ) || element;
                target.dispatchEvent(new MouseEvent('click', {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                }));
            }"""
        )

    async def _describe_print_item(self, print_item) -> str:
        try:
            return await print_item.evaluate(
                """element => {
                    const target = element.closest(
                        'button, [role="menuitem"], .mat-mdc-menu-item, .mat-menu-item, a'
                    ) || element;
                    const describe = (node) => ({
                        tag: node.tagName,
                        id: node.id || '',
                        role: node.getAttribute('role') || '',
                        ariaLabel: node.getAttribute('aria-label') || '',
                        ariaDisabled: node.getAttribute('aria-disabled') || '',
                        disabled: Boolean(node.disabled),
                        classes: typeof node.className === 'string' ? node.className : '',
                        text: (node.innerText || node.textContent || '').trim().replace(/\\s+/g, ' '),
                    });
                    return JSON.stringify({
                        element: describe(element),
                        target: describe(target),
                        targetHtml: target.outerHTML.slice(0, 1000),
                    });
                }"""
            )
        except Exception as exc:
            return f'<unavailable: {exc}>'

    async def _wait_for_print_activation_result(self, popup_task, page, original_url: str):
        try:
            popup = await asyncio.wait_for(asyncio.shield(popup_task), timeout=3)
            await popup.wait_for_load_state('domcontentloaded', timeout=15_000)
            logging.debug('Leganto Print list opened a printable popup: %s', getattr(popup, 'url', ''))
            return popup
        except Exception:
            pass

        try:
            await page.wait_for_load_state('domcontentloaded', timeout=1_000)
        except Exception:
            pass

        if await self._was_print_invoked(page):
            logging.debug('Leganto Print list triggered browser print on the current page')
            direct_print_page = await self._open_direct_print_url(page, original_url)
            if direct_print_page is not None:
                return direct_print_page
            return page

        if not self._is_same_leganto_list_url(original_url, page.url):
            logging.debug('Leganto Print list changed page URL from %s to %s', original_url, page.url)
            return page

        return None

    @staticmethod
    def _is_verified_print_menu_item(description: str) -> bool:
        normalized = description or ''
        return 'lg-menu-action-print' in normalized and 'Print list' in normalized

    async def _dispatch_before_print(self, page) -> None:
        try:
            await page.evaluate(
                """() => {
                    window.__moodleDlPrintInvoked = true;
                    try {
                        window.dispatchEvent(new Event('beforeprint'));
                    } catch (_error) {
                        // Best effort: Playwright PDF generation will still use print media.
                    }
                }"""
            )
        except Exception:
            logging.debug('Could not dispatch Leganto beforeprint event', exc_info=True)

    async def _install_print_invocation_marker(self, page) -> None:
        try:
            await page.evaluate(
                """() => {
                    window.__moodleDlPrintInvoked = false;
                    const markPrintInvoked = () => {
                        window.__moodleDlPrintInvoked = true;
                    };
                    window.addEventListener('beforeprint', markPrintInvoked, true);
                    window.addEventListener('afterprint', markPrintInvoked, true);
                    if (!window.__moodleDlOriginalPrint) {
                        window.__moodleDlOriginalPrint = window.print;
                    }
                    window.print = () => {
                        markPrintInvoked();
                        try {
                            window.dispatchEvent(new Event('beforeprint'));
                        } catch (_error) {
                            // The marker above is sufficient; synthetic events are best effort.
                        }
                    };
                    if (document.execCommand && !document.__moodleDlOriginalExecCommand) {
                        document.__moodleDlOriginalExecCommand = document.execCommand.bind(document);
                    }
                    if (document.__moodleDlOriginalExecCommand) {
                        document.execCommand = (command, ...args) => {
                            if (String(command || '').toLowerCase() === 'print') {
                                markPrintInvoked();
                                return true;
                            }
                            return document.__moodleDlOriginalExecCommand(command, ...args);
                        };
                    };
                }"""
            )
        except Exception:
            logging.debug('Could not install Leganto print invocation marker', exc_info=True)

    async def _prepare_print_media(self, page) -> None:
        try:
            await page.emulate_media(media='print')
        except Exception:
            logging.debug('Could not force print media before Leganto PDF export', exc_info=True)

    async def _was_print_invoked(self, page) -> bool:
        try:
            return bool(await page.evaluate('() => Boolean(window.__moodleDlPrintInvoked)'))
        except Exception:
            return False

    async def _dump_debug_artifacts(self, page, label: str) -> None:
        if not os.environ.get('MOODLE_DL_LEGANTO_DEBUG'):
            return

        timestamp = int(time.time())
        safe_label = re.sub(r'[^a-zA-Z0-9_-]+', '-', label).strip('-') or 'debug'
        base_path = Path('/tmp') / f'moodle_dl_leganto_{safe_label}_{timestamp}'
        try:
            await page.screenshot(path=str(base_path.with_suffix('.png')), full_page=True)
        except Exception:
            logging.debug('Could not save Leganto debug screenshot', exc_info=True)
        try:
            html_text = await page.content()
            base_path.with_suffix('.html').write_text(html_text, encoding='utf-8')
        except Exception:
            logging.debug('Could not save Leganto debug HTML', exc_info=True)
        logging.debug('Leganto debug artifacts saved with prefix: %s (url=%s)', base_path, page.url)

    @staticmethod
    def _is_same_leganto_list_url(left: str, right: str) -> bool:
        left_parsed = urlparse.urlparse(left or '')
        right_parsed = urlparse.urlparse(right or '')
        return (
            (left_parsed.hostname or '').lower() == (right_parsed.hostname or '').lower()
            and (left_parsed.path or '') == (right_parsed.path or '')
            and bool(LEGANTO_LIST_PATH_RE.match(left_parsed.path or ''))
            and bool(LEGANTO_LIST_PATH_RE.match(right_parsed.path or ''))
        )

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

        await self._dump_debug_artifacts(page, 'print-menu-not-found')
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
