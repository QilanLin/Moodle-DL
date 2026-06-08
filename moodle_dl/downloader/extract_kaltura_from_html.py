"""
Extract Kaltura video iframes from existing HTML files in a
workspace and register them as downloadable files in the
moodle_state.db.

Use case: Some HTML files (e.g. PCR Practical2.html)
embed Kaltura videos with the direct
'cdnapisec.kaltura.com/.../embedIframeJs/...' URL form.
The new book.py extractor only handles book modules, so
HTML files in resource/label modules still have inline
Kaltura iframes that don't get downloaded.

This tool:
  1. Scans the workspace for .html files
  2. Detects Kaltura iframes (both lti_launch and direct embed)
  3. Inserts synthetic file rows into the DB with type
     'kalvidres_embedded' so the downloader picks them up
     via the same yt-dlp path as real kalvidres modules
  4. Replaces the iframe with a <video> tag pointing to the
     local file path

Usage:
  python -m moodle_dl.downloader.extract_kaltura_from_html \
      --db /path/to/moodle_state.db \
      --workspace /path/to/workspace \
      --yes
"""
import argparse
import os
import re
import sqlite3
import sys
import urllib.parse
from typing import List

from moodle_dl.moodle.mods.book import BookMod


# Reuse the patterns from book.py
KALTURA_LAUNCH_RE = re.compile(
    r'[^"]*filter/kaltura/lti_launch\.php[^"]*',
    re.IGNORECASE,
)
KALTURA_DIRECT_RE = re.compile(
    r'[^"]*cdnapisec\.kaltura\.com/[^"]*?/embedIframeJs/[^"]*',
    re.IGNORECASE,
)
IFRAME_RE = re.compile(
    r'<iframe[^>]+src="(?P<url>[^"]*(?:filter/kaltura/lti_launch\.php|cdnapisec\.kaltura\.com/[^"]*?/embedIframeJs/)[^"]*)"[^>]*>',
    re.IGNORECASE,
)


def extract_kaltura_entry_id(iframe_src: str) -> str:
    """Extract Kaltura entry_id from either URL form."""
    if 'filter/kaltura/lti_launch.php' in iframe_src:
        source_match = re.search(r'[?&]source=([^&]+)', iframe_src)
        if not source_match:
            return ''
        kaltura_source = urllib.parse.unquote(source_match.group(1))
        m = re.search(r'/entryid/([^/]+)', kaltura_source)
        return m.group(1) if m else ''
    else:
        m = re.search(r'[?&]entry_id=([^&]+)', iframe_src)
        if m:
            return urllib.parse.unquote(m.group(1))
        return ''


def find_kaltura_iframes(html_content: str) -> List[dict]:
    """Return list of {'iframe_src': str, 'entry_id': str,
    'iframe_tag': str, 'position': int} for each Kaltura
    iframe found in html_content.

    Also detects iframes that have already been replaced
    with a <video> tag pointing to a local kaltura_video
    file. For already-replaced iframes, the iframe_src is
    reconstructed from the entry_id (since the original
    Kaltura URL was discarded during replacement).
    """
    results = []
    for m in IFRAME_RE.finditer(html_content):
        iframe_src = m.group('url')
        entry_id = extract_kaltura_entry_id(iframe_src)
        if entry_id:
            results.append({
                'iframe_src': iframe_src,
                'entry_id': entry_id,
                'iframe_tag': m.group(0),
                'position': m.start(),
            })

    # Detect already-replaced iframes: <video>...<source src="kaltura_video_<entry_id>.mp4">
    replaced_re = re.compile(
        r'<video[^>]*>\s*<source\s+src="kaltura_video_([^."]+)\.mp4"[^>]*>',
        re.IGNORECASE,
    )
    for m in replaced_re.finditer(html_content):
        entry_id = m.group(1)
        results.append({
            'iframe_src': None,  # Was discarded on previous run
            'entry_id': entry_id,
            'iframe_tag': m.group(0),
            'position': m.start(),
            'already_replaced': True,
        })

    return results


def find_all_html_files(workspace: str) -> List[str]:
    out = []
    for root, _, files in os.walk(workspace):
        if '._' in os.path.basename(root) or 'node_modules' in root:
            continue
        for f in files:
            if f.endswith('.html') and not f.startswith('._'):
                out.append(os.path.join(root, f))
    return out


def main():
    parser = argparse.ArgumentParser(
        description='Extract Kaltura videos from existing HTML files and register them in the DB',
    )
    parser.add_argument('--db', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print(f'ERROR: DB not found: {args.db}')
        sys.exit(1)
    if not os.path.isdir(args.workspace):
        print(f'ERROR: workspace not found: {args.workspace}')
        sys.exit(1)

    html_files = find_all_html_files(args.workspace)
    print(f'Found {len(html_files)} HTML files in {args.workspace}', flush=True)

    # Load DB
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # Schema check
    cur.execute('PRAGMA table_info(files)')
    columns = {row[1] for row in cur.fetchall()}
    print(f'DB columns: {sorted(columns)[:10]}...', flush=True)

    # Look up file_id max
    cur.execute('SELECT MAX(file_id) FROM files')
    max_file_id = cur.fetchone()[0] or 0
    print(f'Max file_id in DB: {max_file_id}', flush=True)

    total_inserts = 0
    total_html_modifications = 0
    for html_path in sorted(html_files):
        try:
            with open(html_path, 'r', encoding='utf-8', errors='replace') as f:
                c = f.read()
        except (OSError, UnicodeDecodeError):
            continue

        iframes = find_kaltura_iframes(c)
        if not iframes:
            continue

        # Get the module_id, course_id, and other context for this HTML file.
        # We need course_id to be set (not 0) so that when the downloader
        # sees this row, it correctly associates the video with the
        # course. Without course_id, the downloader might skip it.
        cur.execute(
            'SELECT module_id, module_name, course_id, course_fullname, '
            '       section_id, section_name '
            'FROM files WHERE saved_to = ? LIMIT 1',
            (html_path,),
        )
        row = cur.fetchone()
        if not row:
            continue
        (orig_module_id, orig_module_name, orig_course_id,
         orig_course_fullname, orig_section_id, orig_section_name) = row

        print(f'\n[{html_files.index(html_path) + 1}/{len(html_files)}] {os.path.basename(html_path)[:60]}',
              flush=True)
        print(f'  module_id={orig_module_id}  module_name="{orig_module_name[:50]}"',
              flush=True)
        print(f'  found {len(iframes)} Kaltura iframe(s)', flush=True)

        new_html = c
        for i, info in enumerate(iframes, 1):
            entry_id = info['entry_id']
            iframe_src = info.get('iframe_src')
            if not iframe_src:
                # The Kaltura URL was already discarded on a
                # previous run. We need to reconstruct it so the
                # downloader can fetch it. We use the standard
                # KCL Kaltura CDN URL format that yt-dlp can
                # parse directly.
                iframe_src = (
                    f'https://cdnapisec.kaltura.com/p/2368101/'
                    f'sp/236810100/embedIframeJs/'
                    f'uiconf_id/42864872/partner_id/2368101'
                    f'?entry_id={entry_id}'
                )
            # Use the lti_launch URL (preferred for cookie_mod processing)
            # or the direct embed URL as the fileurl
            video_filename = f'kaltura_video_{entry_id}.mp4'

            # Check if this iframe_src is already registered (idempotent).
            cur.execute(
                'SELECT file_id FROM files '
                'WHERE module_id = ? AND content_fileurl = ? '
                'LIMIT 1',
                (orig_module_id, iframe_src),
            )
            existing = cur.fetchone()
            if existing:
                print(f'    [=] already registered file_id={existing[0]} entry_id={entry_id}',
                      flush=True)
                continue

            max_file_id += 1
            # 🆕 Set module_modname to 'cookie_mod-kalvidres' so the
            # downloader (task.py:475) routes the file through the
            # yt-dlp path. The existing 'resource' module_modname
            # would treat the file as a regular resource (HTML download
            # path) and miss the Kaltura extraction.
            cur.execute(
                '''INSERT INTO files (
                    file_id, course_id, course_fullname, section_name,
                    section_id, module_id, module_name, module_modname,
                    content_filepath, content_filename, content_fileurl,
                    content_filesize, content_timemodified, content_type,
                    content_isexternalfile, saved_to, time_stamp, modified,
                    moved, deleted, notified, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'cookie_mod-kalvidres',
                          '/', ?, ?, 0, ?, 'cookie_mod',
                          1, '', 0, 0, 0, 0, 0, NULL)
                ''',
                (max_file_id, orig_course_id, orig_course_fullname,
                 orig_section_name, orig_section_id, orig_module_id,
                 orig_module_name[:200], video_filename, iframe_src,
                 int(0)),
            )
            total_inserts += 1

            # Replace the iframe with a <video> tag in the HTML
            video_tag = (
                f'<video controls preload="metadata" '
                f'style="width:100%;max-width:608px;height:auto;">'
                f'<source src="{video_filename}" type="video/mp4">'
                f'<p>Kaltura video (entry_id: {entry_id}). '
                f'Re-download required for offline access.</p>'
                f'</video>'
            )
            new_html = new_html.replace(info['iframe_tag'], video_tag, 1)
            print(f'    [+] inserted file_id={max_file_id} entry_id={entry_id}', flush=True)

        # Write the modified HTML back
        if new_html != c:
            with open(html_path, 'w', encoding='utf-8', errors='replace', newline='') as f:
                f.write(new_html)
            total_html_modifications += 1

    conn.commit()
    conn.close()
    print(f'\nDone! {total_inserts} Kaltura videos registered, '
          f'{total_html_modifications} HTML files modified.')


if __name__ == '__main__':
    main()
