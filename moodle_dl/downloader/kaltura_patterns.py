"""
Single source of truth for Kaltura video URL patterns.

Multiple places in the codebase need to detect Kaltura
iframes in HTML, extract their entry_id, and build
a yt-dlp-friendly URL. Historically each call site
duplicated the same regex/URL fragments. This module
defines them once.

URL forms supported:

(A) KCL Moodle LTI wrapper:
    https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php
        ?...&source=https%3A%2F%2Fkaf.keats.kcl.ac.uk%2F
        browseandembed%2F...%2Fentryid%2F<entry_id>%2F...
    entry_id is in the URL-encoded 'source' parameter.

(B) Direct Kaltura embed (used in some book chapters
    and PCR practical HTML):
    https://cdnapisec.kaltura.com/p/<partner>/sp/<partner>00/
        embedIframeJs/uiconf_id/<uiconf>/partner_id/<partner>
        ?entry_id=<entry_id>&...
    entry_id is in the URL query string directly.
"""
import html
import re
import urllib.parse


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Path fragment identifying the KCL Moodle LTI wrapper
LTI_LAUNCH_PATH = '/filter/kaltura/lti_launch.php'

#: Host fragment identifying the direct Kaltura CDN embed
CDN_HOST = 'cdnapisec.kaltura.com'

#: Path fragment identifying the Kaltura embed iframe endpoint
EMBED_IFRAME_PATH = '/embedIframeJs/'

#: Moodle module modname for Kaltura cookie-mod videos (the
#: standalone kalvidres module type). Also used as the
#: module_modname on synthetic file rows so the downloader
#: routes them through the yt-dlp path.
MODULE_COOKIE_KALVIDRES = 'cookie_mod-kalvidres'

#: Moodle module modname for Helixmedia cookie-mod videos
MODULE_COOKIE_HELIXMEDIA = 'cookie_mod-helixmedia'

#: Tuple of all cookie-mod module modnames. Used to detect
#: "needs_moodle_cookies" downloads.
COOKIE_MOD_MODNAMES = (MODULE_COOKIE_KALVIDRES, MODULE_COOKIE_HELIXMEDIA)

#: Content type for Kaltura videos embedded in book chapters
#: (or in resource/label module HTML). Files with this type
#: are turned into cookie_mod-kalvidres files in the result
#: builder and routed through yt-dlp.
CONTENT_TYPE_KALVIDRES_EMBEDDED = 'kalvidres_embedded'

#: Synthetic content type for file rows created by the
#: extract_kaltura_from_html tool. Same as
#: CONTENT_TYPE_KALVIDRES_EMBEDDED for legacy reasons.
CONTENT_TYPE_COOKIE_MOD = 'cookie_mod'

#: Filename prefix used by extract_kaltura_from_html when
#: registering synthetic Kaltura video rows. The entry_id
#: is appended to make a unique filename that the downloader
#: can write to.
KALTURA_VIDEO_FILENAME_PREFIX = 'kaltura_video_'


# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

#: Combined Kaltura iframe extractor.
#: Captures the iframe src URL in group 1.
IFRAME_RE = re.compile(
    r'<iframe[^>]+src="'
    r'('
    # (A) lti_launch.php wrapper
    r'[^"]*' + re.escape(LTI_LAUNCH_PATH) + r'[^"]*'
    r'|'
    # (B) direct Kaltura CDN embed (with one or more path
    #     segments between the host and /embedIframeJs/)
    r'[^"]*' + re.escape(CDN_HOST) + r'/[^"]*?' + re.escape(EMBED_IFRAME_PATH) + r'[^"]*'
    r')'
    r'"',
    re.IGNORECASE,
)

#: Combined Kaltura iframe extractor that requires a specific
#: CSS class (e.g. `class="kaltura-player-iframe"`). Used by
#: the print book view which renders iframes with this class.
IFRAME_WITH_CLASS_RE = re.compile(
    r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src="'
    r'('
    r'[^"]*' + re.escape(LTI_LAUNCH_PATH) + r'[^"]*'
    r'|'
    r'[^"]*' + re.escape(CDN_HOST) + r'/[^"]*?' + re.escape(EMBED_IFRAME_PATH) + r'[^"]*'
    r')'
    r'[^>]*>',
    re.IGNORECASE | re.DOTALL,
)

#: Extracts entry_id from URL paths like
#: `/entryid/<id>/...` (used for lti_launch source URLs).
#: Stops at `/`, `"`, whitespace, or `>`. Case-insensitive
#: (matches both `entryid` and `entryId`).
ENTRY_ID_PATH_RE = re.compile(r'/entryid/([^/"\s>]+)', re.IGNORECASE)

#: Extracts entry_id from query strings like
#: `?entry_id=<id>&...` (used for direct embed URLs).
#: Case-insensitive.
ENTRY_ID_QUERY_RE = re.compile(r'[?&]entry_id=([^&]+)', re.IGNORECASE)

#: Extracts the `source` query parameter from a
#: lti_launch URL (URL-encoded).
LTI_SOURCE_RE = re.compile(r'[?&]source=([^&]+)')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_kaltura_url(url: str) -> bool:
    """Return True if the URL points to a Kaltura video
    (either lti_launch wrapper or direct embed)."""
    if not url:
        return False
    return (LTI_LAUNCH_PATH in url) or (
        CDN_HOST in url and EMBED_IFRAME_PATH in url
    )


def is_lti_launch_url(url: str) -> bool:
    """Return True if the URL is the lti_launch wrapper form."""
    return LTI_LAUNCH_PATH in (url or '')


def is_direct_embed_url(url: str) -> bool:
    """Return True if the URL is the direct Kaltura CDN embed
    form (cdnapisec.kaltura.com/.../embedIframeJs/...)."""
    return (
        CDN_HOST in (url or '')
        and EMBED_IFRAME_PATH in (url or '')
    )


def extract_entry_id(url: str) -> str:
    """Extract the Kaltura entry_id from a Kaltura video URL.

    Returns the entry_id (e.g. '1_bn1vhn06') or an empty
    string if it can't be extracted.

    Handles both URL forms:
      (A) lti_launch wrapper: decodes the 'source' parameter
          and pulls entry_id from the path. The source param
          may be double-encoded (e.g. %252F for %2F) — we
          decode twice if needed to handle this case.
      (B) direct embed: pulls entry_id from the query string

    The function is robust to:
      - HTML entities in the URL (e.g. &amp; instead of &)
      - URL-encoded slashes (entryid%2F or entryid/)
      - Double URL-encoded slashes (entryid%252F)
    """
    if not url:
        return ''

    # Unescape HTML entities first (handles &amp; → &, etc.)
    url = html.unescape(url)

    # Try direct extraction first (works for both URL forms
    # when the URL doesn't have HTML entity separators).
    # Match either entryid/ (after URL decoding) or entryid%2F
    # (without decoding). The unescape above may have converted
    # &amp; to &, so the source param extraction can now find it.
    if is_lti_launch_url(url):
        m = LTI_SOURCE_RE.search(url)
        if m:
            kaltura_source = urllib.parse.unquote(m.group(1))
            m2 = ENTRY_ID_PATH_RE.search(kaltura_source)
            if m2:
                return urllib.parse.unquote(m2.group(1))
            # Try double-decode
            kaltura_source = urllib.parse.unquote(kaltura_source)
            m3 = ENTRY_ID_PATH_RE.search(kaltura_source)
            if m3:
                return urllib.parse.unquote(m3.group(1))

    # Fallback: search the whole URL (after HTML-unescape) for
    # entryid/ or entryid%2F. This handles edge cases where the
    # source param can't be cleanly extracted (e.g. when &amp;
    # is not converted to &). We URL-decode the whole URL
    # before matching so that entryid%2F is found via entryid/.
    decoded = urllib.parse.unquote(url)
    m = re.search(r'entryid/([^/"\s>]+)', decoded, re.IGNORECASE)
    if m:
        return urllib.parse.unquote(m.group(1))
    # Also try without decoding (in case the entryid was already
    # in the URL in its plain form)
    m = re.search(r'entryid%2F([^/"\s>]+)', url, re.IGNORECASE)
    if m:
        return urllib.parse.unquote(m.group(1))

    # Path B: direct embed
    m = ENTRY_ID_QUERY_RE.search(url)
    if m:
        return urllib.parse.unquote(m.group(1))
    return ''


def reconstruct_url_from_entry_id(entry_id: str) -> str:
    """Reconstruct a Kaltura direct-embed URL from an entry_id.

    Used when the original URL was discarded (e.g. when an
    iframe was previously replaced with a <video> tag and
    only the entry_id is recoverable from the filename).

    The reconstructed URL uses the standard KCL Kaltura CDN
    pattern with the known KAF partner_id (2368101) and
    uiconf_id (42864872).
    """
    return (
        f'https://{CDN_HOST}/p/2368101/sp/236810100/embedIframeJs/'
        f'uiconf_id/42864872/partner_id/2368101?entry_id={entry_id}'
    )


#: Filename regex for matching the synthetic Kaltura video
#: filenames written by extract_kaltura_from_html.py and the
#: downloader. Captures the entry_id in group 1.
KALTURA_VIDEO_FILENAME_RE = re.compile(
    re.escape(KALTURA_VIDEO_FILENAME_PREFIX) + r'([^."]+)\.mp4',
    re.IGNORECASE,
)


def kaltura_video_filename(entry_id: str) -> str:
    """Build the synthetic local filename for a Kaltura video.

    Example: entry_id='1_a' → 'kaltura_video_1_a.mp4'

    The downloader writes the actual .mp4 file using this
    name when yt-dlp extracts the video.
    """
    return f'{KALTURA_VIDEO_FILENAME_PREFIX}{entry_id}.mp4'


def entry_id_from_filename(filename: str) -> str:
    """Reverse of kaltura_video_filename: extract the entry_id
    from a synthetic Kaltura video filename.

    Returns the entry_id or an empty string if the filename
    doesn't match the pattern.
    """
    m = KALTURA_VIDEO_FILENAME_RE.search(filename or '')
    return m.group(1) if m else ''


def is_kaltura_synthetic_filename(filename: str) -> bool:
    """Return True if the filename is a synthetic Kaltura
    video filename (e.g. 'kaltura_video_1_a.mp4')."""
    return bool(KALTURA_VIDEO_FILENAME_RE.fullmatch(filename or ''))
