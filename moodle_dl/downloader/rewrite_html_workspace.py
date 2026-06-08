"""
One-shot HTML rewrite pass for an existing workspace.

This is a small standalone tool that:
  1. Scans the workspace for all .html files
  2. Builds a resource map from the moodle_state.db
  3. Rewrites all <a href>, <link href>, <script src>, <img src>
     refs to local paths
  4. Useful when:
     - Files are already at correct locations (no moves needed)
     - HTML refs are still raw because of an old download
     - You don't want to re-download (rename collision risk)

Usage:
  python -m moodle_dl.downloader.rewrite_html_workspace \
      --db /path/to/moodle_state.db \
      --workspace /path/to/workspace
"""
import argparse
import os
import sqlite3
import sys

from moodle_dl.downloader.html_localizer import (
    build_local_resource_map,
    rewrite_html_links_to_local_paths,
)


class _MinimalFile:
    """Minimal stand-in for File that exposes just the
    fields build_local_resource_map reads: saved_to and
    content_fileurl."""
    pass


def find_all_html_files(workspace):
    """Return all .html files in the workspace (recursively)."""
    html_files = []
    for root, _, files in os.walk(workspace):
        if 'node_modules' in root or '._' in os.path.basename(root):
            continue
        for f in files:
            if not f.endswith('.html'):
                continue
            if f.startswith('._'):
                continue
            html_files.append(os.path.join(root, f))
    return html_files


def build_db_resource_map(db_path):
    """Read all files from the DB and return a
    build_local_resource_map result."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        'SELECT saved_to, content_fileurl FROM files '
        'WHERE download_status = ? AND saved_to IS NOT NULL '
        'AND saved_to != ""',
        ('success',),
    )
    rows = cur.fetchall()
    conn.close()
    files = []
    for saved_to, content_fileurl in rows:
        if not saved_to or not os.path.isfile(saved_to):
            continue
        f = _MinimalFile()
        f.saved_to = saved_to
        f.content_fileurl = content_fileurl or ''
        files.append(f)
    return build_local_resource_map(files)


def main():
    parser = argparse.ArgumentParser(
        description='One-shot HTML rewrite pass for an existing workspace',
    )
    parser.add_argument('--db', required=True,
                        help='Path to moodle_state.db')
    parser.add_argument('--workspace', required=True,
                        help='Path to the workspace root')
    parser.add_argument('--yes', action='store_true',
                        help='Skip the confirmation prompt')
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print(f'ERROR: DB not found: {args.db}')
        sys.exit(1)
    if not os.path.isdir(args.workspace):
        print(f'ERROR: workspace not found: {args.workspace}')
        sys.exit(1)

    print(f'Building resource map from {args.db}...', flush=True)
    local_resources = build_db_resource_map(args.db)
    print(f'  {len(local_resources)} resource entries', flush=True)

    print(f'Scanning {args.workspace} for HTML files...', flush=True)
    html_files = find_all_html_files(args.workspace)
    print(f'  {len(html_files)} HTML files found', flush=True)

    if not html_files:
        print('No HTML files to process.')
        return

    if not args.yes:
        resp = input(f'\nRewrite all {len(html_files)} HTML files? [y/N] ')
        if resp.lower() != 'y':
            print('Cancelled.')
            return

    total_rewrites = 0
    total_files_changed = 0
    for i, html_path in enumerate(sorted(html_files), 1):
        if i % 100 == 0 or i == 1:
            print(f'  [{i}/{len(html_files)}] processing {os.path.basename(html_path)[:60]}...',
                  flush=True)
        try:
            with open(html_path, 'r', encoding='utf-8', errors='replace') as fh:
                html_content = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        rewritten, n = rewrite_html_links_to_local_paths(
            html_content, html_path, local_resources,
        )
        if n:
            try:
                with open(html_path, 'w', encoding='utf-8', errors='replace', newline='') as fh:
                    fh.write(rewritten)
                total_files_changed += 1
                total_rewrites += n
            except OSError as e:
                print(f'  ERROR writing {html_path}: {e}', flush=True)

    print(f'\nDone! {total_files_changed} files changed, {total_rewrites} refs rewritten')


if __name__ == '__main__':
    main()
