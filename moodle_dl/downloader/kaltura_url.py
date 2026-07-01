# -*- coding: utf-8 -*-
import html
import logging
import re
import urllib.parse as urlparse

import requests
from typing import Optional


class KalturaExtractionError(Exception):
    """Kaltura video URL extraction failed."""


class KalturaCDNError(Exception):
    """Kaltura CDN is unavailable or unreachable."""


class KalturaAuthenticationError(Exception):
    """Kaltura authentication failed, usually because cookies expired."""


class KalturaUrlBuilder:
    CDN_FALLBACKS = [
        'cdnapisec.kaltura.com',
        'cdnbakmi.kaltura.com',
        'cdnakmi.kaltura.com',
        'cdnapi.kaltura.com',
    ]
    PARTNER_FALLBACKS_BY_HOST = {
        'kaf.kcl.ac.uk': '2368101',
        'kaf.keats.kcl.ac.uk': '2368101',
        'keats.kcl.ac.uk': '2368101',
        'media.kcl.ac.uk': '2368101',
    }
    UICONF_FALLBACKS_BY_HOST = {
        'keats.kcl.ac.uk': '50622292',
        'media.kcl.ac.uk': '50622292',
    }

    REGEX_ENTRY_ID = re.compile(r'/entryid/([^/?#]+)(?:[/?#]|$)', re.I)
    REGEX_UICONF_ID = re.compile(r'/(?:playerSkin|uiConfId|uiconf_id)/(\d+)', re.I)
    REGEX_KALTURA_PLAYLIST = re.compile(r'/isPlaylist/true(?:[/?#]|$)', re.I)
    REGEX_PARTNER_ID = re.compile(
        r'(?:partnerId|partner_id)["\']?\s*[:=]\s*["\']?(\d+)'
        r'|/p/(\d+)(?:/|$)'
        r'|/partner_id/(\d+)(?:[/?#]|$)'
    )
    REGEX_KALTURA_CDN = re.compile(r'https?://([^/]*kaltura\.com)/p/\d+/embed')
    REGEX_LTI_IFRAME = re.compile(r'<iframe[^>]+src="([^"]*lti_launch\.php[^"]*)"')
    REGEX_TARGET_LINK_URI = re.compile(r'name="target_link_uri"\s+value="([^"]+)"')
    REGEX_ACTIVITY_DESCRIPTION = re.compile(
        r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>', re.DOTALL,
    )
    REGEX_PAGE_TITLE = re.compile(r'<title>([^<]+)</title>')
    REGEX_H1 = re.compile(r'<h1[^>]*>(.*?)</h1>', re.DOTALL)

    REQUEST_TIMEOUT = 30
    RQ_HEADER = {
        'User-Agent': (
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    def __init__(self, task_id: int = 0):
        self.task_id = task_id

    def extract_entry_id(self, url: str) -> str:
        match = self.REGEX_ENTRY_ID.search(url)
        if not match:
            raise KalturaExtractionError('无法从 URL 中提取 entry ID')
        entry_id = match.group(1)
        logging.debug('[%d] ✓ Entry ID: %s', self.task_id, entry_id)
        return entry_id

    def extract_uiconf_id(self, url: str) -> str:
        match = self.REGEX_UICONF_ID.search(url)
        if not match:
            raise KalturaExtractionError('无法从 URL 中提取 uiconf_id')
        uiconf_id = match.group(1)
        logging.debug('[%d] ✓ UI 配置 ID: %s', self.task_id, uiconf_id)
        return uiconf_id

    def extract_partner_id(self, html_content: str) -> str:
        match = self.REGEX_PARTNER_ID.search(html_content)
        if not match:
            raise KalturaExtractionError('无法从页面中提取 partner ID')
        partner_id = next(group for group in match.groups() if group)
        logging.debug('[%d] ✓ Partner ID: %s', self.task_id, partner_id)
        return partner_id

    def infer_partner_id_from_browse_url(self, browseandembed_url: str) -> Optional[str]:
        host = urlparse.urlparse(browseandembed_url).hostname or ''
        partner_id = self.PARTNER_FALLBACKS_BY_HOST.get(host.lower())
        if partner_id:
            logging.info(
                '[%d] ℹ️  Using partner ID fallback for Kaltura host %s',
                self.task_id,
                host,
            )
        else:
            logging.debug(
                '[%d] No Kaltura partner ID fallback configured for host %s',
                self.task_id,
                host,
            )
        return partner_id

    def infer_uiconf_id_from_browse_url(self, browseandembed_url: str) -> Optional[str]:
        host = urlparse.urlparse(browseandembed_url).hostname or ''
        uiconf_id = self.UICONF_FALLBACKS_BY_HOST.get(host.lower())
        if uiconf_id:
            logging.info(
                '[%d] ℹ️  Using uiconf_id fallback for Kaltura host %s',
                self.task_id,
                host,
            )
        return uiconf_id

    @staticmethod
    def source_url_from_lti_launch(url: str) -> Optional[str]:
        parsed = urlparse.urlparse(url or '')
        if not (parsed.path or '').endswith('/filter/kaltura/lti_launch.php'):
            return None

        source_values = urlparse.parse_qs(parsed.query).get('source')
        if not source_values:
            return None

        return html.unescape(source_values[0])

    def build_from_known_embed_url(self, url: str) -> Optional[str]:
        source_url = self.source_url_from_lti_launch(url) or url
        partner_id = self.infer_partner_id_from_browse_url(source_url)
        if not partner_id:
            return None

        try:
            entry_id = self.extract_entry_id(source_url)
        except KalturaExtractionError:
            return None

        try:
            uiconf_id = self.extract_uiconf_id(source_url)
        except KalturaExtractionError:
            uiconf_id = self.infer_uiconf_id_from_browse_url(source_url)
            if not uiconf_id:
                return None

        if self.REGEX_KALTURA_PLAYLIST.search(source_url):
            return self.build_playlist_url(
                partner_id,
                uiconf_id,
                entry_id,
                self.CDN_FALLBACKS[0],
                source_url,
            )

        return self.build_url(
            partner_id,
            uiconf_id,
            entry_id,
            self.CDN_FALLBACKS[0],
        )

    def log_browseandembed_url(self, browseandembed_url: str) -> None:
        parsed = urlparse.urlparse(browseandembed_url)
        logging.debug(
            '[%d] Kaltura browseandembed target: host=%s path=%s',
            self.task_id,
            parsed.hostname or '',
            parsed.path or '',
        )

    def detect_cdn(self, html_content: str) -> Optional[str]:
        match = self.REGEX_KALTURA_CDN.search(html_content)
        if match:
            cdn = match.group(1)
            logging.debug('[%d] ✓ 从页面检测到 Kaltura CDN: %s', self.task_id, cdn)
            return cdn
        logging.debug('[%d] ℹ️  无法从页面检测到 CDN，将使用备用 CDN 列表', self.task_id)
        return None

    def build_url(self, partner_id: str, uiconf_id: str, entry_id: str, cdn: str) -> str:
        url = (
            f'https://{cdn}/p/{partner_id}/sp/{partner_id}00/embedIframeJs/'
            f'uiconf_id/{uiconf_id}/partner_id/{partner_id}?iframeembed=true&entry_id={entry_id}'
        )
        logging.debug('[%d] 🔗 构建 Kaltura URL (CDN: %s)', self.task_id, cdn)
        return url

    def build_playlist_url(
        self,
        partner_id: str,
        uiconf_id: str,
        playlist_id: str,
        cdn: str,
        source_url: str,
    ) -> str:
        source_host = urlparse.urlparse(source_url).hostname or ''
        params = {
            'wid': f'_{partner_id}',
            'iframeembed': 'true',
            'playerId': 'kaltura_player_',
            'flashvars[playlistAPI.kpl0Id]': playlist_id,
        }
        if source_host:
            params['flashvars[playlistAPI.playlistUrl]'] = (
                f'https://{source_host}/playlist/details/{{playlistAPI.kpl0Id}}'
            )
        url = (
            f'https://{cdn}/html5/html5lib/v2.101/mwEmbedFrame.php/'
            f'p/{partner_id}/uiconf_id/{uiconf_id}?{urlparse.urlencode(params)}'
        )
        logging.debug('[%d] 🔗 构建 Kaltura playlist URL (CDN: %s)', self.task_id, cdn)
        return url

    def resolve_view_php_to_cdn(
        self,
        view_php_url: str,
        session,
        verify_ssl: bool = True,
    ) -> Optional[str]:
        """
        Synchronously resolve a Moodle `mod/kalvidres/view.php?id=...` URL
        into the auth-free Kaltura CDN embed URL.

        Three HTTP GETs follow the production flow in
        task.py:extract_kalvidres_video_url but synchronously:

            view.php      ->  lti_launch iframe URL
            lti_launch    ->  browseandembed URL
            browseandembed -> entry_id / uiconf_id / partner_id / CDN

        On success returns e.g.
            https://cdnapisec.kaltura.com/p/2368101/sp/.../embedIframeJs/...

        Any failure (network, 4xx/5xx, missing regex match) returns None.

        @param view_php_url: URL of the kalvidres view.php page.
        @param session: requests.Session already populated with
                        Moodle cookies (e.g. via MoodleDLCookieJar).
        @param verify_ssl: pass False to skip TLS verification in
                           debugging environments only.
        @return: auth-free Kaltura CDN URL or None.
        """
        try:
            # 1. GET view.php
            try:
                resp = session.get(
                    view_php_url,
                    headers=self.RQ_HEADER,
                    verify=verify_ssl,
                    timeout=self.REQUEST_TIMEOUT,
                )
            except (requests.RequestException, TimeoutError) as e:
                logging.debug(
                    '[%d] view.php GET failed: %s', self.task_id, e,
                )
                return None

            if resp.status_code != 200:
                logging.debug(
                    '[%d] view.php HTTP %d — skipping resolve',
                    self.task_id, resp.status_code,
                )
                return None

            # 2. Find the lti_launch iframe inside the page HTML
            iframe_match = self.REGEX_LTI_IFRAME.search(resp.text)
            if not iframe_match:
                logging.debug(
                    '[%d] no lti_launch iframe in view.php HTML',
                    self.task_id,
                )
                return None
            lti_launch_url = iframe_match.group(1).replace('&amp;', '&')

            # 3. GET lti_launch.php
            try:
                lti_resp = session.get(
                    lti_launch_url,
                    headers=self.RQ_HEADER,
                    verify=verify_ssl,
                    timeout=self.REQUEST_TIMEOUT,
                )
            except (requests.RequestException, TimeoutError) as e:
                logging.debug(
                    '[%d] lti_launch GET failed: %s', self.task_id, e,
                )
                return None

            if lti_resp.status_code != 200:
                logging.debug(
                    '[%d] lti_launch HTTP %d — skipping resolve',
                    self.task_id, lti_resp.status_code,
                )
                return None

            # 4. Extract browseandembed URL from lti_launch HTML
            target_match = self.REGEX_TARGET_LINK_URI.search(lti_resp.text)
            if not target_match:
                logging.debug(
                    '[%d] no target_link_uri in lti_launch HTML',
                    self.task_id,
                )
                return None
            browseandembed_url = html.unescape(target_match.group(1))

            # 5. Extract entry_id / uiconf_id from browseandembed URL
            try:
                entry_id = self.extract_entry_id(browseandembed_url)
                uiconf_id = self.extract_uiconf_id(browseandembed_url)
            except KalturaExtractionError as e:
                logging.debug(
                    '[%d] extract_id failed: %s', self.task_id, e,
                )
                return None

            # 6. GET browseandembed to discover partner_id + CDN
            try:
                br_resp = session.get(
                    browseandembed_url,
                    headers=self.RQ_HEADER,
                    verify=verify_ssl,
                    timeout=self.REQUEST_TIMEOUT,
                )
            except (requests.RequestException, TimeoutError) as e:
                logging.debug(
                    '[%d] browseandembed GET failed: %s',
                    self.task_id, e,
                )
                return None

            if br_resp.status_code != 200:
                logging.debug(
                    '[%d] browseandembed HTTP %d — falling back to partner_id inference',
                    self.task_id, br_resp.status_code,
                )
                partner_id = self.infer_partner_id_from_browse_url(browseandembed_url)
                if not partner_id:
                    return None
                cdn = self.CDN_FALLBACKS[0]
            else:
                try:
                    partner_id = self.extract_partner_id(br_resp.text)
                except KalturaExtractionError:
                    partner_id = self.infer_partner_id_from_browse_url(browseandembed_url)
                    if not partner_id:
                        return None
                cdn = self.detect_cdn(br_resp.text) or self.CDN_FALLBACKS[0]

            cdn_url = self.build_url(partner_id, uiconf_id, entry_id, cdn)
            logging.debug(
                '[%d] ✓ resolved view.php → %s', self.task_id, cdn_url,
            )
            return cdn_url

        except Exception as e:  # noqa: BLE001
            logging.debug(
                '[%d] resolve_view_php_to_cdn unexpected error: %s',
                self.task_id, e,
            )
            return None

    def extract_view_php_text(
        self,
        view_php_url: str,
        session,
        save_path: str,
        verify_ssl: bool = True,
    ) -> bool:
        """
        Synchronous equivalent of task.py:extract_kalvidres_text for use
        from the halt-videos partition step. Fetches the view.php page,
        extracts title / h1 / activity-description, formats them as
        Markdown, and writes to `save_path`.

        Cookies are required because view.php is login-gated.

        @return: True if a non-empty Markdown was written, False otherwise.
        """
        try:
            try:
                resp = session.get(
                    view_php_url,
                    headers=self.RQ_HEADER,
                    verify=verify_ssl,
                    timeout=self.REQUEST_TIMEOUT,
                )
            except (requests.RequestException, TimeoutError) as e:
                logging.debug(
                    '[%d] view.php GET for text extraction failed: %s',
                    self.task_id, e,
                )
                return False

            if resp.status_code != 200:
                logging.debug(
                    '[%d] view.php HTTP %d — skipping text extraction',
                    self.task_id, resp.status_code,
                )
                return False

            html_content = resp.text
            text_data = parse_kalvidres_html(html_content)

            if not text_data:
                logging.debug(
                    '[%d] No text content found in view.php HTML',
                    self.task_id,
                )
                return False

            markdown = format_kalvidres_text(text_data)
            import os

            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(markdown)
            logging.debug(
                '[%d] ✓ wrote view.php _notes.md to %s',
                self.task_id, save_path,
            )
            return True

        except Exception as e:  # noqa: BLE001
            logging.debug(
                '[%d] extract_view_php_text unexpected error: %s',
                self.task_id, e,
            )
            return False


# Module-level helpers — shared between Task (async) and halt-videos
# partition (sync). They are pure-string transforms so they can live
# outside the KalturaUrlBuilder class.

def _convert_line_breaks(html_text: str) -> str:
    return re.sub(r'<br\s*/?>', '\n', html_text)


def _convert_paragraphs(html_text: str) -> str:
    text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', html_text)
    text = re.sub(r'</?p[^>]*>', '\n', text)
    return text


def _convert_lists(html_text: str) -> str:
    text = re.sub(r'<li[^>]*>', '\n• ', html_text)
    text = re.sub(r'</li>', '', text)
    text = re.sub(r'</?ul[^>]*>', '\n', text)
    text = re.sub(r'</?ol[^>]*>', '\n', text)
    return text


def _convert_formatting(html_text: str) -> str:
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', html_text, flags=re.DOTALL)
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL)
    return text


def _convert_links(html_text: str) -> str:
    return re.sub(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r'[\2](\1)',
        html_text,
        flags=re.DOTALL,
    )


def _remove_html_tags(html_text: str) -> str:
    return re.sub(r'<[^>]+>', '', html_text)


def _decode_html_entities(html_text: str) -> str:
    return html.unescape(html_text)


def _clean_whitespace(html_text: str) -> str:
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', html_text)
    text = re.sub(r' +', ' ', text)
    return text.strip()


def clean_html_simple(html_text: str) -> str:
    """Strip all HTML tags and return plain text. Used for H1 inside
    parse_kalvidres_html."""
    if not html_text:
        return ''
    text = re.sub(r'<br\s*/?>', '\n', html_text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def clean_html_preserve_structure(html_text: str) -> str:
    """Convert HTML to Markdown, preserving list / link / formatting
    structure. Used for the activity-description block."""
    if not html_text:
        return ''
    text = _convert_line_breaks(html_text)
    text = _convert_paragraphs(text)
    text = _convert_lists(text)
    text = _convert_formatting(text)
    text = _convert_links(text)
    text = _remove_html_tags(text)
    text = _decode_html_entities(text)
    text = _clean_whitespace(text)
    return text


def parse_kalvidres_html(html_content: str) -> dict:
    """Parse a view.php page HTML into {page_title, module_name,
    activity_description}. Same logic as task.py:extract_kalvidres_text
    step 1-3 — extracted as a sync helper so the halt-videos partition
    can write _notes.md without going through the async Task path."""
    text_data = {}

    title_match = KalturaUrlBuilder.REGEX_PAGE_TITLE.search(html_content)
    if title_match:
        text_data['page_title'] = html.unescape(title_match.group(1).strip())

    h1_match = KalturaUrlBuilder.REGEX_H1.search(html_content)
    if h1_match:
        h1_text = clean_html_simple(h1_match.group(1))
        if h1_text:
            text_data['module_name'] = h1_text

    activity_match = KalturaUrlBuilder.REGEX_ACTIVITY_DESCRIPTION.search(html_content)
    if activity_match:
        content_html = activity_match.group(1)
        text_data['activity_description'] = clean_html_preserve_structure(content_html)

    return text_data


def format_kalvidres_text(text_data: dict) -> str:
    """Render the {page_title, module_name, activity_description} dict
    as a Markdown document. Same shape as task.py:_save_kalvidres_text
    but without the async / I/O wrapper."""
    lines = []
    if text_data.get('page_title'):
        lines.append(f"# {text_data['page_title']}")
        lines.append('')
    if text_data.get('module_name'):
        lines.append(f"## {text_data['module_name']}")
        lines.append('')
    if text_data.get('activity_description'):
        lines.append(text_data['activity_description'])
        lines.append('')
    return '\n'.join(lines)
