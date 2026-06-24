# -*- coding: utf-8 -*-
import json
import os
from ipaddress import ip_address
from typing import Dict, List
from urllib.parse import urlparse

from requests.exceptions import RequestException

from moodle_dl.utils import SslHelper


def _is_ssrf_risky(url: str) -> bool:
    """Return True if the URL targets a private/loopback/link-local
    address that could be used for SSRF.

    Discords webhook URLs are user-supplied. By default we
    **block** URLs that target:
      - Private IP ranges (RFC 1918)
      - Loopback (127.0.0.0/8, ::1)
      - Link-local (169.254.0.0/16, fe80::/10)
      - Multicast, reserved, unspecified
      - file://, ftp://, gopher://, dict://, etc. (non-http)
      - Userinfo (user:pass@host) — phishing risk
      - Localhost names (localhost, *.local)

    Opt-out:
      Set MOODLE_DL_ALLOW_PRIVATE_WEBHOOK=1 to disable SSRF
      protection (for self-hosted webhooks on a private network).
    """
    # Opt-out check
    if os.environ.get('MOODLE_DL_ALLOW_PRIVATE_WEBHOOK', '').strip() in (
        '1', 'true', 'yes',
    ):
        return False

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return True  # Unparseable → treat as risky

    scheme = (parsed.scheme or '').lower()
    if scheme not in ('http', 'https'):
        return True  # file://, ftp://, gopher://, etc.

    # Userinfo (user:pass@host) is a phishing vector
    if parsed.username or parsed.password:
        return True

    hostname = (parsed.hostname or '').lower()
    if not hostname:
        return True

    # Hostname-based local checks
    if hostname in ('localhost', 'localhost.localdomain'):
        return True
    if hostname.endswith('.local') or hostname.endswith('.internal'):
        return True
    if hostname.endswith('.localhost'):
        return True

    # IP-based checks
    try:
        # Strip brackets for IPv6
        ip_str = hostname.strip('[]')
        ip = ip_address(ip_str)
        # Block private, loopback, link-local, multicast, reserved
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    except ValueError:
        # 🔧 Fix (audit): some HTTP clients and DNS resolvers
        # accept non-canonical IPv4 forms (decimal, octal, hex,
        # short-form) that resolve to loopback or private
        # addresses. The stdlib `ip_address()` rejects these,
        # which would let them bypass the check as "DNS names".
        # Examples that resolve to loopback or unspecified:
        #   2130706433       → 127.0.0.1
        #   0177.0.0.1       → 127.0.0.1
        #   0x7f000001       → 127.0.0.1
        #   127.1            → 127.0.0.1
        #   0                → 0.0.0.0 (any-address)
        #   16843009         → 10.0.0.1
        # Strategy: if the hostname is **numeric** (digits only,
        # possibly with one or more dots), it's a non-canonical
        # IP literal and we MUST resolve it to verify. If it's a
        # DNS name (has letters), we skip the resolution (avoids
        # adding DNS latency and avoids breaking users who are
        # offline).
        import socket as _socket
        # Detect non-canonical IPv4 forms:
        #   - Pure digits (with optional dots): 2130706433, 127.1, 0177.0.0.1
        #   - Hex prefix '0x' (no dots):          0x7f000001
        #   - Just '0' (any-address):             0 → 0.0.0.0
        # All of these resolve to loopback or unspecified addresses
        # via socket.gethostbyname / getaddrinfo, but are rejected
        # by ip_address(). Resolve them and re-check.
        is_numeric_ip_form = (
            (hostname.replace('.', '').isdigit() and hostname not in ('','.'))
            or hostname.startswith('0x')
            or hostname == '0'
        )
        if is_numeric_ip_form:
            try:
                resolved = _socket.gethostbyname(hostname)
                try:
                    ip = ip_address(resolved)
                    if (
                        ip.is_private
                        or ip.is_loopback
                        or ip.is_link_local
                        or ip.is_multicast
                        or ip.is_reserved
                        or ip.is_unspecified
                    ):
                        return True
                except ValueError:
                    return True  # Unparseable resolved IP.
            except (OSError, UnicodeError, ValueError):
                # DNS lookup failed for a numeric hostname —
                # probably a network problem. Treat as risky
                # (fail closed) so we don't allow a malicious
                # bypass through DNS.
                return True

    # DNS rebinding mitigation: resolve hostname and check the
    # resolved IPs too. But this is async — we skip it for
    # performance (the IP block list covers most attacks).

    return False


class DiscordShooter:
    RQ_HEADER = {
        'User-Agent': (
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.108 Safari/537.36'
        ),
        'Content-Type': 'application/json',
    }

    def __init__(self, discord_webhooks: List[str]):
        # 🔧 SSRF protection: validate webhook URLs at construction
        # time. By default we reject URLs that target private IPs,
        # loopback, file://, etc. To allow self-hosted Discord
        # webhooks on private networks, set the env var
        # MOODLE_DL_ALLOW_PRIVATE_WEBHOOK=1.
        risky = [u for u in discord_webhooks if _is_ssrf_risky(u)]
        if risky:
            raise ValueError(
                f'Discord webhook URL(s) appear to target private/loopback addresses, '
                f'which is a security risk (SSRF): {risky}. '
                f'If you are running a self-hosted Discord webhook on a private network, '
                f'set the env var MOODLE_DL_ALLOW_PRIVATE_WEBHOOK=1 to disable this check.'
            )
        self.discord_webhooks = discord_webhooks

    def send_msg(self, text):
        self.send_data(
            {
                'content': text,
                'username': 'Moodle Notifications',
                'avatar_url': 'https://i.imgur.com/J3Pxl41.png',
            }
        )

    def send(self, embeds: List):
        self.send_data(
            {
                'embeds': embeds,
                'username': 'Moodle Notifications',
                'avatar_url': 'https://i.imgur.com/J3Pxl41.png',
            }
        )

    def send_data(self, data: Dict):

        session = SslHelper.custom_requests_session(
            skip_cert_verify=False, allow_insecure_ssl=False, use_all_ciphers=False
        )
        for webhook_url in self.discord_webhooks:
            try:
                response = session.post(webhook_url, data=json.dumps(data), headers=self.RQ_HEADER, timeout=60)
                self._check_response_code(response)
            except RequestException as error:
                raise ConnectionError(f"Connection error: {str(error)}") from None

    @staticmethod
    def _check_response_code(response):
        # Normally Discord answers with response 204
        if response.status_code not in [200, 204, 400]:
            raise RuntimeError(
                'An unexpected error happened on'
                + " Discord's servers."
                + f' Status code: {str(response.status_code)}'
                + f'\nHeader: {response.headers}'
                + f'\nResponse: {response.text}'
            )
