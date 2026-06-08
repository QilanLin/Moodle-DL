# -*- coding: utf-8 -*-
import html
import logging
import re
import urllib.parse as urlparse
from typing import Optional

from moodle_dl.downloader.kaltura_patterns import (
    LTI_LAUNCH_PATH,
    CDN_HOST,
    ENTRY_ID_PATH_RE,
    extract_entry_id as _extract_entry_id_patterns,
)


class KalturaExtractionError(Exception):
    """Kaltura video URL extraction failed."""


class KalturaCDNError(Exception):
    """Kaltura CDN is unavailable or unreachable."""


class KalturaAuthenticationError(Exception):
    """Kaltura authentication failed, usually because cookies expired."""


class KalturaUrlBuilder:
    CDN_FALLBACKS = [
        CDN_HOST,
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

    # Use the more strictly-anchored entry_id pattern from
    # kaltura_patterns (which ends with [/?#]|$) to avoid
    # matching entry_id-like strings that are part of other
    # path components.
    REGEX_ENTRY_ID = ENTRY_ID_PATH_RE
    REGEX_UICONF_ID = re.compile(r'/(?:playerSkin|uiConfId|uiconf_id)/(\d+)', re.I)
    REGEX_KALTURA_PLAYLIST = re.compile(r'/isPlaylist/true(?:[/?#]|$)', re.I)
    REGEX_PARTNER_ID = re.compile(
        r'(?:partnerId|partner_id)["\']?\s*[:=]\s*["\']?(\d+)'
        r'|/p/(\d+)(?:/|$)'
        r'|/partner_id/(\d+)(?:[/?#]|$)'
    )
    REGEX_KALTURA_CDN = re.compile(r'https?://([^/]*kaltura\.com)/p/\d+/embed')

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
        if not (parsed.path or '').endswith(LTI_LAUNCH_PATH):
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
