# -*- coding: utf-8 -*-
"""
Pure-function helpers for extracting URLs from Moodle description HTML.

Extracted from ResultBuilder._find_all_urls in June 2026. The original
function was 100+ lines doing five things at once:
  1. Regex extraction of href / src / data / anchor-text URLs
  2. The "should this URL be dropped?" multi-condition decision
  3. The module_modname dispatch (kaltura / helixmedia / /webservice/ branch)
  4. filename derivation for data:/kaltura/plain URL types
  5. The File(...) assembly

This module pulls out the first three as pure functions that can be
unit-tested without a ResultBuilder instance, without a database,
and without a network. The original _find_all_urls is now a thin
orchestrator that calls these helpers and assembles the File.

WHY three separate functions (not one):
  * Each function corresponds to ONE decision the production code
    makes per URL.
  * should_skip_url() has a name that reads as a sentence
    ("should this URL be skipped?"); the original 11-line boolean
    chain did not.
  * assign_modname_for_url() collapses 3 nested if/elif/elif branches
    into a single decision point with clear precedence rules.
  * Pure functions are pin-fast to test (no fixtures) and easy to
    reason about in isolation.

USAGE:
    from moodle_dl.moodle.description_url_extractor import (
        extract_urls_from_html,
        should_skip_url,
        assign_modname_for_url,
    )

    # In ResultBuilder._find_all_urls:
    raw_urls = extract_urls_from_html(content_html)
    for raw_url in raw_urls:
        url, parts = self._normalize_extracted_html_url(raw_url)
        if not url:
            continue
        is_kaltura = self._is_kaltura_url_candidate(url, parts)
        is_helix = self._is_helixmedia_url_candidate(url, parts)
        if should_skip_url(
            url=url,
            url_parts=parts,
            moodle_domain=self.moodle_domain,
            original_modname=original_module_modname,
            is_embedded_media=is_kaltura or is_helix,
        ):
            continue
        location['module_modname'] = assign_modname_for_url(
            url=url,
            url_parts=parts,
            moodle_domain=self.moodle_domain,
            original_modname=original_module_modname,
            is_kaltura=is_kaltura,
            is_helixmedia=is_helix,
        )
        ...

TESTS: tests/test_description_url_extractor.py pins the contract
for all three functions.
"""
import re
from typing import List
from urllib.parse import ParseResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: pluginfile.php is the Moodle endpoint for serving resource attachments
#: (PDF/ZIP/PNG/DOCX/MP3/...) from a description page. The host part of
#: the URL is always the Moodle domain, but the path identifies the URL
#: as a downloadable file rather than an internal page link.
PLUGINFILE_PATH_FRAGMENT = 'pluginfile.php'

#: The `book` modname is special: its chapter images are rewritten by
#: the HTML localizer after the chapter is downloaded as a single HTML
#: file. Creating a File entry for each chapter image would cause a
#: duplicate download. This constant pins that exception.
BOOK_MODNAME = 'book'

#: URL paths that match /webservice/ on the Moodle domain are dispatched
#: to the index_mod-description-* branch (the file is downloaded via
#: the webservice/pluginfile.php endpoint with token-aware auth).
WEBSERVICE_PATH_FRAGMENT = '/webservice/'

#: Modname prefixes used by the original _find_all_urls dispatch.
#: Pin the strings so a typo in one place doesn't cause silent breakage.
MODNAME_PREFIX_URL_DESCRIPTION = 'url-description-'
MODNAME_PREFIX_INDEX_MOD_DESCRIPTION = 'index_mod-description-'
MODNAME_PREFIX_COOKIE_MOD_DESCRIPTION = 'cookie_mod-description-'


# ---------------------------------------------------------------------------
# 1. extract_urls_from_html
# ---------------------------------------------------------------------------

# Four regex sources, mirroring the original production code so behavior
# is preserved exactly. Each captures the URL value inside the relevant
# HTML attribute. Pin the regex set; the test file verifies the contract.
_URL_REGEXES = (
    # href="..."  /  href='...'  /  href=...
    re.compile(r'href=[\'"]?([^\'" >]+)', re.IGNORECASE),
    # <a>URL</a>  — bare URL as anchor text
    re.compile(r'<a[^>]*>(http[^<]*)</a>', re.IGNORECASE),
    # src="..."
    re.compile(r'src=[\'"]?([^\'" >]+)', re.IGNORECASE),
    # data="..."  (e.g. <object data="...">)
    re.compile(r'data=[\'"]?([^\'" >]+)', re.IGNORECASE),
)


def extract_urls_from_html(content_html: str) -> List[str]:
    """Pull raw URLs out of an HTML description string.

    Mirrors the regex set in the original ResultBuilder._find_all_urls
    (lines 515-518). Returns a deduplicated list preserving the order
    in which URLs were first seen.

    @param content_html: HTML string (the description body of a Moodle
        resource / page / label module). May be empty, whitespace-only,
        or contain no URLs at all — all are handled gracefully.
    @return: List of unique URL strings, in first-seen order.
    """
    if not content_html:
        return []

    seen = set()
    result = []
    for regex in _URL_REGEXES:
        for match in regex.findall(content_html):
            if match and match not in seen:
                seen.add(match)
                result.append(match)
    return result


# ---------------------------------------------------------------------------
# 2. should_skip_url
# ---------------------------------------------------------------------------

def _is_moodle_domain_url(url_parts: ParseResult, moodle_domain: str) -> bool:
    """Return True if the URL's host is the configured Moodle domain.

    Encapsulates the hostname/netloc comparison the original code did
    inline: ``url_parts.hostname == self.moodle_domain or
    url_parts.netloc == self.moodle_domain``. A pure function form
    makes the comparison testable in isolation.
    """
    return url_parts.hostname == moodle_domain or url_parts.netloc == moodle_domain


def should_skip_url(
    *,
    url: str,
    url_parts: ParseResult,
    moodle_domain: str,
    original_modname: str,
    is_embedded_media: bool,
) -> bool:
    """Decide whether a URL extracted from description HTML should be dropped.

    Encapsulates the 11-line boolean chain in the original
    _find_all_urls. The function returns True to DROP the URL (do
    not create a File entry), False to KEEP it.

    The decision rules, in order of precedence:

    1. Embedded media (Kaltura / Helixmedia) launch URLs are always
       kept, even when on the Moodle domain.
    2. pluginfile.php URLs on the Moodle domain are kept for
       non-book modnames. This is the fix for the KLaSS Research
       Data + 6CCS3ML1 regression (June 2026): without this
       exemption, the description-page → markdown-only bug silently
       dropped every real attachment URL.
    3. pluginfile.php URLs on the Moodle domain in a `book` module
       are SKIPPED — book chapter images are handled by the HTML
       localizer, and creating a File entry here would cause a
       duplicate download.
    4. Other Moodle-domain URLs are skipped (no shortcut files for
       ordinary internal Moodle links, the historical contract).
    5. External URLs (any other host) are always kept.

    @param url: The URL string (for documentation / debugging only;
        the decision uses url_parts, not the raw string).
    @param url_parts: Pre-parsed urlparse result for the URL.
    @param moodle_domain: The configured Moodle domain, e.g.
        'keats.kcl.ac.uk'. Used for the is_moodle_domain check.
    @param original_modname: The original module_modname of the
        description-bearing module. The `book` modname is special.
    @param is_embedded_media: True if the URL is a Kaltura launch
        URL or a Helixmedia URL. Such URLs are always kept.
    @return: True if the URL should be skipped (no File entry).
    """
    is_moodle_url = _is_moodle_domain_url(url_parts, moodle_domain)
    if is_moodle_url and not is_embedded_media:
        # The pluginfile.php exemption only applies outside the `book`
        # modname (book chapter images are handled by the HTML localizer).
        is_pluginfile_url = PLUGINFILE_PATH_FRAGMENT in url_parts.path
        if not (is_pluginfile_url and original_modname != BOOK_MODNAME):
            # Keep the historical behavior: no shortcut files for
            # ordinary internal Moodle links.
            return True
    return False


# ---------------------------------------------------------------------------
# 3. assign_modname_for_url
# ---------------------------------------------------------------------------

#: The kaltura and helixmedia modname constants are imported from the
#: kaltura_patterns SSOT module (the project convention: kaltura_patterns
#: is the single source of truth for cross-module constants).
try:
    # Lazy import: the kaltura_patterns module pulls in yt-dlp and other
    # heavy dependencies, so we keep it lazy for unit tests that don't
    # need it. Tests that need these constants can import them directly.
    from moodle_dl.downloader.kaltura_patterns import (  # type: ignore
        MODULE_COOKIE_KALVIDRES,
        MODULE_COOKIE_HELIXMEDIA,
    )
except ImportError:  # pragma: no cover — kaltura_patterns may be missing in minimal test envs
    MODULE_COOKIE_KALVIDRES = 'cookie_mod-kalvidres'
    MODULE_COOKIE_HELIXMEDIA = 'cookie_mod-helixmedia'


def assign_modname_for_url(
    *,
    url: str,
    url_parts: ParseResult,
    moodle_domain: str,
    original_modname: str,
    is_kaltura: bool,
    is_helixmedia: bool,
) -> str:
    """Assign a module_modname to a URL extracted from description HTML.

    Encapsulates the 3-branch modname dispatch in the original
    _find_all_urls. The dispatch precedence is:

    1. Kaltura URL  → ``cookie_mod-kalvidres``  (overrides everything)
    2. Helixmedia URL  → ``cookie_mod-helixmedia``
    3. Moodle-domain /webservice/ URL  → ``index_mod-description-<original>``
       (used for pluginfile.php downloads, which go through the
       webservice endpoint with token-aware auth)
    4. Other Moodle-domain URL  → ``cookie_mod-description-<original>``
       (Kaltura/Helixmedia launch URLs, internal page links that
       survived the skip filter, etc.)
    5. External URL  → ``url-description-<original>``

    @param url: The URL string (for documentation; the decision uses
        url_parts.path for path-based checks).
    @param url_parts: Pre-parsed urlparse result.
    @param moodle_domain: The configured Moodle domain.
    @param original_modname: The original module_modname of the
        description-bearing module; preserved as a suffix so the
        downloader can route the file to the right folder.
    @param is_kaltura: True if the URL is a Kaltura launch URL.
    @param is_helixmedia: True if the URL is a Helixmedia URL.
    @return: The module_modname to assign to the File entry.
    """
    if is_kaltura:
        return MODULE_COOKIE_KALVIDRES
    if is_helixmedia:
        return MODULE_COOKIE_HELIXMEDIA
    if _is_moodle_domain_url(url_parts, moodle_domain):
        if WEBSERVICE_PATH_FRAGMENT in url_parts.path:
            return MODNAME_PREFIX_INDEX_MOD_DESCRIPTION + original_modname
        return MODNAME_PREFIX_COOKIE_MOD_DESCRIPTION + original_modname
    return MODNAME_PREFIX_URL_DESCRIPTION + original_modname
