"""
TaskUrlOps: URL-related operations extracted from Task.

The Task class had several methods dealing with URLs that
were unrelated to the "download a file" responsibility:

  * add_token_to_url: token injection for pluginfile URLs
  * is_filtered_external_domain: domain whitelist/blacklist
  * _is_drm_error: detect DRM-protected download errors

By extracting them into one cohesive class, the URL logic
becomes:
  * testable in isolation
  * reusable (other parts of the codebase can use the same
    URL manipulation rules)
  * documented in one place

The interface is intentionally focused on URL classification
and token injection:
    url_ops.add_token_to_url(url)
    url_ops.is_filtered_domain(domain, blacklist, whitelist)
    url_ops.is_drm_error(error_msg)
"""
import urllib.parse as urlparse
from typing import Iterable


class TaskUrlOps:
    """URL manipulation, domain filtering, and DRM detection.

    Stateless: every method takes the inputs explicitly so the
    class can be used as a module-level utility.
    """

    # DRM-protected download error indicators. Extracted from
    # the original DRM_KEYWORDS list on Task. These are the
    # exact same keywords that Task historically used.
    DRM_KEYWORDS: Iterable[str] = (
        'DRM',
        'protected',
        'widevine',
        'encrypted',
        'drm-protected',
        'WidevineDecryptor',
    )

    def add_token_to_url(self, url: str, token: str, moodle_base_url: str = '') -> str:
        """Adds the Moodle token to a URL.

        Based on official Moodle Mobile App's fixPluginfileURL
        (moodleapp/src/core/singletons/url.ts). Improvements:
          - Handles HTML escaping (&amp; → &)
          - Avoids duplicate token
          - Converts pluginfile.php → webservice/pluginfile.php
          - Adds offline=1 (required for external repos)

        @param url: The URL to add the token to.
        @param token: The Moodle session token.
        @param moodle_base_url: The base URL of the Moodle
            instance (for pluginfile URL construction).
        @return: The URL with the token added.
        """
        from moodle_dl.utils import UrlHelper

        fixed_url = UrlHelper.fix_pluginfile_url(
            url=url, token=token, moodle_base_url=moodle_base_url,
        )
        if fixed_url == url and not UrlHelper.is_pluginfile_url(url):
            # Not a pluginfile URL: use simple query-string injection
            if 'token=' in url:
                return url
            url_parts = list(urlparse.urlparse(url))
            query = dict(urlparse.parse_qsl(url_parts[4]))
            query.update({'token': token})
            url_parts[4] = urlparse.urlencode(query)
            return urlparse.urlunparse(url_parts)
        return fixed_url

    def is_filtered_domain(
        self,
        domain: str,
        blacklist: Iterable[str] = (),
        whitelist: Iterable[str] = (),
    ) -> bool:
        """Check if a domain is filtered out by the whitelist/blacklist.

        Returns True if the domain is filtered (i.e. should NOT
        be downloaded). Returns False if it's allowed.

        Logic:
          - If `domain` is None or empty: filtered.
          - If `domain` is in blacklist (or a subdomain of one): filtered.
          - If `whitelist` is empty: everything is allowed.
          - If `whitelist` is non-empty: only domains in it
            (or subdomains of those) are allowed.
        """
        blacklist_list = list(blacklist)
        whitelist_list = list(whitelist)
        if domain is None or domain == '':
            return True

        in_blacklist = False
        for entry in blacklist_list:
            if domain == entry or domain.endswith('.' + entry):
                in_blacklist = True
                break

        in_whitelist = len(whitelist_list) == 0
        for entry in whitelist_list:
            if domain == entry or domain.endswith('.' + entry):
                in_whitelist = True
                break

        return not in_whitelist or in_blacklist

    def is_drm_error(self, error_msg: str) -> bool:
        """Check if an error message indicates DRM protection.

        @param error_msg: The error message to check.
        @return: True if it appears to be a DRM-protected error.
        """
        error_lower = error_msg.lower()
        return any(kw.lower() in error_lower for kw in self.DRM_KEYWORDS)
