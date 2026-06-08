# -*- coding: utf-8 -*-
import html
import os
import re
import urllib.parse
from typing import Dict, Iterable, Tuple

from moodle_dl.types import File


HTML_TAG_PATTERN = re.compile(
    r'<(?P<tag>[a-zA-Z][\w:-]*)(?P<attrs>[^<>]*)>',
    flags=re.IGNORECASE | re.DOTALL,
)
HTML_RESOURCE_ATTR_PATTERN = re.compile(
    r'(?P<prefix>\b(?P<attr>src|href|poster|data)\s*=\s*)'
    r'(?P<quote>["\'])'
    r'(?P<url>.*?)'
    r'(?P=quote)',
    flags=re.IGNORECASE | re.DOTALL,
)
LOCAL_RESOURCE_KEY_PREFIX = 'local:'
FILENAME_INDEX_PREFIX_PATTERN = re.compile(r'^\*\d+\*\s*')
IGNORED_HTML_URL_PREFIXES = ('#', 'data:', 'mailto:', 'javascript:', 'tel:')
EMBEDDED_RESOURCE_ATTRS = {
    'src': {'audio', 'embed', 'iframe', 'img', 'input', 'script', 'source', 'track', 'video'},
    'href': {'link'},
    'poster': {'video'},
    'data': {'object'},
}


def canonical_resource_url(url: str) -> str:
    """Return a stable key for matching HTML resource URLs to downloaded files."""
    prepared_url = _prepare_html_resource_url(url)
    if not prepared_url:
        return ''

    parsed = urllib.parse.urlsplit(prepared_url)
    if not parsed.netloc:
        return ''

    path = urllib.parse.unquote(parsed.path or '')
    path = path.replace('/webservice/pluginfile.php', '/pluginfile.php')
    path = _normalize_token_pluginfile_path(path)

    if '/pluginfile.php' in path:
        query = ''
    else:
        query = _stable_query(parsed.query)

    return urllib.parse.urlunsplit(('', parsed.netloc.lower(), path, query, ''))


def canonical_local_resource_url(url: str, html_dir: str) -> str:
    """Return a stable key for a relative HTML resource URL."""
    prepared_url = _prepare_html_resource_url(url)
    if not prepared_url:
        return ''

    parsed = urllib.parse.urlsplit(prepared_url)
    if parsed.scheme or parsed.netloc or parsed.path.startswith('/'):
        return ''

    relative_path = urllib.parse.unquote(parsed.path or '')
    if not relative_path:
        return ''

    local_path = os.path.abspath(os.path.join(html_dir, relative_path))
    return _local_resource_key(local_path)


def _prepare_html_resource_url(url: str) -> str:
    if not isinstance(url, str):
        return ''

    url = html.unescape(url).strip()
    if not url or url.lower().startswith(IGNORED_HTML_URL_PREFIXES):
        return ''

    url, _fragment = urllib.parse.urldefrag(url)
    return url


def _local_resource_key(path: str) -> str:
    normalized = os.path.normpath(os.path.abspath(path)).replace(os.sep, '/')
    return f'{LOCAL_RESOURCE_KEY_PREFIX}{normalized}'


def _strip_filename_index_prefix(filename: str) -> str:
    return FILENAME_INDEX_PREFIX_PATTERN.sub('', filename or '', count=1)


def _add_local_resource_alias(local_resources: Dict[str, str], local_path: str, saved_to: str) -> None:
    local_resources[_local_resource_key(local_path)] = saved_to


def _add_local_resource_aliases(local_resources: Dict[str, str], saved_to: str) -> None:
    saved_to_abs = os.path.abspath(saved_to)
    _add_local_resource_alias(local_resources, saved_to_abs, saved_to)

    stripped_filename = _strip_filename_index_prefix(os.path.basename(saved_to_abs))
    if stripped_filename and stripped_filename != os.path.basename(saved_to_abs):
        unprefixed_path = os.path.join(os.path.dirname(saved_to_abs), stripped_filename)
        _add_local_resource_alias(local_resources, unprefixed_path, saved_to)


def _normalize_token_pluginfile_path(path: str) -> str:
    token_match = re.match(r'(?P<prefix>.*/?)tokenpluginfile\.php/[^/]+(?P<rest>/.*)', path)
    if not token_match:
        return path
    return f'{token_match.group("prefix")}pluginfile.php{token_match.group("rest")}'


def _stable_query(query: str) -> str:
    if not query:
        return ''

    volatile_keys = {'token', 'offline'}
    pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True)
        if key.lower() not in volatile_keys
    ]
    return urllib.parse.urlencode(pairs)


def build_local_resource_map(files: Iterable[File]) -> Dict[str, str]:
    """Map downloaded remote resource URLs to local filesystem paths."""
    local_resources: Dict[str, str] = {}
    for file in files:
        saved_to = getattr(file, 'saved_to', '') or ''
        if not saved_to or not os.path.isfile(saved_to):
            continue
        if saved_to.lower().endswith(('.webloc', '.url', '.desktop')):
            continue

        key = canonical_resource_url(getattr(file, 'content_fileurl', '') or '')
        if key:
            local_resources[key] = saved_to

        _add_local_resource_aliases(local_resources, saved_to)

        # 🆕 When moodle-dl renames a file to the module display
        # name (e.g. 'main.css' → 'Interactive Virtual...css'),
        # the HTML still references the original filename
        # (e.g. 'assets/css/main.css'). Add an alias that maps
        # the original filename at the expected path to the
        # actual disk path so the HTML rewrite can find it.
        content_fileurl = getattr(file, 'content_fileurl', '') or ''
        original_filename = _extract_original_filename_from_url(content_fileurl)
        if original_filename:
            original_path = os.path.join(
                os.path.dirname(os.path.abspath(saved_to)),
                original_filename,
            )
            if original_path != os.path.abspath(saved_to):
                _add_local_resource_alias(local_resources, original_path, saved_to)

    return local_resources


def _extract_original_filename_from_url(url: str) -> str:
    """Extract the last path component (original filename) from a
    KCL pluginfile URL, ignoring query parameters.

    Example:
        '.../pluginfile.php/.../content/0/assets/css/main.css?forcedownload=1'
        → 'main.css'
    """
    if not url:
        return ''
    # Strip query string and fragment
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(parsed.path or '')
    if not path:
        return ''
    filename = os.path.basename(path)
    return filename if filename else ''


def rewrite_html_links_to_local_paths(
    html_content: str,
    html_file_path: str,
    local_resources: Dict[str, str],
) -> Tuple[str, int]:
    """Rewrite HTML resource attributes to local relative paths when available."""
    if not html_content or not local_resources:
        return html_content, 0

    html_dir = os.path.dirname(os.path.abspath(html_file_path))
    replacements = 0

    def replace_tag(tag_match: re.Match) -> str:
        nonlocal replacements

        tag = tag_match.group('tag').lower()
        attrs = tag_match.group('attrs')

        def replace_attribute(attr_match: re.Match) -> str:
            nonlocal replacements

            attr = attr_match.group('attr').lower()
            if tag not in EMBEDDED_RESOURCE_ATTRS.get(attr, set()):
                return attr_match.group(0)

            url = attr_match.group('url')
            local_path = _find_local_resource_path(url, html_dir, local_resources)
            if not local_path:
                return attr_match.group(0)

            local_path = os.path.abspath(local_path)
            if local_path == os.path.abspath(html_file_path):
                return attr_match.group(0)

            relative_path = os.path.relpath(local_path, html_dir).replace(os.sep, '/')
            escaped_relative_path = html.escape(relative_path, quote=True)
            if escaped_relative_path == url:
                return attr_match.group(0)

            replacements += 1
            return (
                f'{attr_match.group("prefix")}{attr_match.group("quote")}'
                f'{escaped_relative_path}{attr_match.group("quote")}'
            )

        rewritten_attrs = HTML_RESOURCE_ATTR_PATTERN.sub(replace_attribute, attrs)
        return f'<{tag_match.group("tag")}{rewritten_attrs}>'

    return HTML_TAG_PATTERN.sub(replace_tag, html_content), replacements


def _find_local_resource_path(url: str, html_dir: str, local_resources: Dict[str, str]) -> str:
    remote_key = canonical_resource_url(url)
    if remote_key and remote_key in local_resources:
        return local_resources[remote_key]

    local_key = canonical_local_resource_url(url, html_dir)
    if local_key and local_key in local_resources:
        return local_resources[local_key]

    return ''
