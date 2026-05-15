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
                await self._add_cookies(context)
                page = await context.new_page()

                if launch_parameters is not None:
                    await self._launch_lti_form(page, url, launch_parameters)
                else:
                    await page.goto(url, wait_until='domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)

                await self._wait_for_leganto_ready(page)
                print_page = await self._trigger_print_list(context, page)
                await self._stabilize_page(print_page)
                await print_page.pdf(path=output_path, format='A4', print_background=True)
            finally:
                await context.close()
                await browser.close()

    async def _add_cookies(self, context) -> None:
        cookies = cookies_text_to_playwright(self.cookies_text)
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

    async def _launch_lti_form(self, page, endpoint: str, parameters: List[Dict[str, str]]) -> None:
        form_html = build_lti_launch_form(endpoint, parameters)
        await page.goto('about:blank')
        await page.set_content(form_html, wait_until='domcontentloaded')
        await page.wait_for_load_state('domcontentloaded', timeout=self.PRINT_TIMEOUT_MS)
        await self._stabilize_page(page)

    async def _wait_for_leganto_ready(self, page) -> None:
        try:
            await page.wait_for_url('**/leganto/nui/lists/**', timeout=self.PRINT_TIMEOUT_MS)
        except Exception:
            logging.debug('Leganto URL pattern was not reached before readiness checks')

        ready_selectors = [
            'text=List info',
            'text=View sections',
            'text=Print list',
            '[aria-label*="List" i]',
        ]
        for selector in ready_selectors:
            try:
                await page.locator(selector).first.wait_for(state='visible', timeout=5_000)
                return
            except Exception:
                continue

        current_url = page.url
        if '/leganto/' not in current_url:
            raise RuntimeError(f'Leganto reading list did not load; final URL was {current_url}')

    async def _trigger_print_list(self, context, page):
        if not await self._is_print_item_visible(page):
            await self._open_list_menu(page)

        print_item = page.get_by_text('Print list', exact=True).first
        await print_item.wait_for(state='visible', timeout=10_000)

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
        try:
            return await page.get_by_text('Print list', exact=True).first.is_visible(timeout=500)
        except TypeError:
            return await page.get_by_text('Print list', exact=True).first.is_visible()
        except Exception:
            return False

    async def _open_list_menu(self, page) -> None:
        candidates = [
            page.get_by_role('button', name=re.compile(r'(more|menu|actions|options)', re.I)),
            page.locator('button[aria-label*="More" i], button[title*="More" i]'),
            page.locator('button:has-text("more_horiz"), button:has-text("...")'),
            page.locator('button').filter(has=page.locator('mat-icon:has-text("more_horiz")')),
            page.locator('button').filter(has=page.locator('prm-icon')),
        ]

        for candidate in candidates:
            count = await candidate.count()
            for index in range(min(count, 10)):
                button = candidate.nth(index)
                try:
                    if await button.is_visible() and await button.is_enabled():
                        await button.click()
                        await page.wait_for_timeout(300)
                        if await self._is_print_item_visible(page):
                            return
                except Exception as exc:
                    logging.debug('Leganto menu candidate failed: %s', exc)

        raise RuntimeError('Could not open the Leganto list menu to find "Print list"')

    async def _stabilize_page(self, page) -> None:
        try:
            await page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            logging.debug('Leganto page did not reach networkidle before PDF export')
        await page.wait_for_timeout(750)
