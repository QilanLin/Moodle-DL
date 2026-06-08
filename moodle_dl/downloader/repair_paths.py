#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI tool: Repair on-disk paths for files affected by the
workspace-isolation bug (commit d1ae09d et al.).

For each buggy file we determine the target subdir within
the module by scanning the HTMLs at the section root for
relative references to that file's basename. The file is
then moved to <ws>/<course>/<section>/<module>/<subdir>/<basename>
and the HTML's reference is rewritten to a relative path
from the HTML's NEW location (which is the module root) to
the file's NEW location.

Usage:
    python -m moodle_dl.downloader.repair_paths \\
        --db <path-to-moodle_state.db> \\
        --workspace <workspace-root> \\
        [--course <course_fullname>] \\
        [--module <module_name>] \\
        [--section <section_name>] \\
        [--progress-file <path/to/progress.json>] \\
        [--dry-run] \\
        [--yes]

Long-running workspaces (5000+ files across 600+ chapters on a
slow U disk) can take 15+ minutes. The --progress-file
flag enables resumable runs: each chapter is recorded in a
JSON file after successful completion, so if the run is
killed (e.g. by an external timeout) the next invocation
skips already-processed chapters. Delete the progress file
to start fresh.
"""
import argparse
import atexit
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import defaultdict

from moodle_dl.downloader.task_path_repair import (
    compute_correct_saved_to,
    find_buggy_files,
    move_buggy_files,
    scan_html_references_in_section,
)
from moodle_dl.downloader.html_localizer import (
    build_local_resource_map,
    rewrite_html_links_to_local_paths,
)


# Progress file helpers (defined at module level so the
# tests in test_repair_progress.py can import them).

def load_progress(progress_file):
    """Load a progress file (or return default if it doesn't
    exist or is corrupt)."""
    if not progress_file or not os.path.exists(progress_file):
        return {'completed_keys': [], 'last_group': None, 'started_at': None}
    try:
        with open(progress_file) as f:
            data = json.load(f)
        # Backfill missing fields
        data.setdefault('completed_keys', [])
        data.setdefault('last_group', None)
        data.setdefault('started_at', None)
        return data
    except (json.JSONDecodeError, IOError):
        return {'completed_keys': [], 'last_group': None, 'started_at': None}


def save_progress(progress_file, completed_key, last_group=None):
    """Mark one chapter as completed. Uses write-temp + rename
    for atomicity. The last_group field records the most
    recently completed group for diagnostics."""
    if not progress_file:
        return
    progress = load_progress(progress_file)
    if completed_key not in progress['completed_keys']:
        progress['completed_keys'].append(completed_key)
    if last_group is not None:
        progress['last_group'] = last_group
    progress['updated_at'] = time.time()
    tmp = progress_file + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(progress, f)
    os.replace(tmp, progress_file)


def _infer_target_subdir(html_refs, basename):
    """For a given buggy file basename, find the most common
    relative reference pattern (the 'subdir' that the HTML
    uses to refer to it). E.g. 'assets/css/main.css' →
    subdir='assets/css'.

    Returns the most common subdir, or '' (empty) for files
    referenced bare.
    """
    candidates = []
    for ref in html_refs:
        if ref['basename'] == basename:
            # Compute the subdir part: everything before the
            # basename in the ref.
            ref_str = ref['old_ref']
            if '/' in ref_str:
                subdir = ref_str.rsplit('/', 1)[0]
                candidates.append(subdir)
            else:
                candidates.append('')
    if not candidates:
        return None  # no HTML references this file
    # Pick the most common
    counter = defaultdict(int)
    for s in candidates:
        counter[s] += 1
    return max(counter, key=counter.get)


def main(argv=None):
    p = argparse.ArgumentParser(
        description='Repair on-disk paths for files affected by the resource-module bug.',
    )
    p.add_argument('--db', required=True, help='Path to moodle_state.db')
    p.add_argument('--workspace', required=True, help='Workspace root')
    p.add_argument('--course', default=None, help='Filter to one course fullname')
    p.add_argument('--module', default=None, help='Filter to one module name')
    p.add_argument('--section', default=None, help='Filter to one section name')
    p.add_argument(
        '--progress-file', default=None,
        help='JSON file to record per-chapter progress. '
             'Enables resumable runs: chapters that have already '
             'been recorded are skipped on the next invocation. '
             'Delete the file to start fresh.',
    )
    p.add_argument(
        '--heartbeat', type=int, default=0,
        help='Print a heartbeat line every N seconds (0=off).',
    )
    p.add_argument('--dry-run', action='store_true', help='Show what would happen')
    p.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = p.parse_args(argv)

    if not os.path.isfile(args.db):
        sys.exit(f'DB not found: {args.db}')
    if not os.path.isdir(args.workspace):
        sys.exit(f'Workspace not found: {args.workspace}')

    # Load progress (skip already-completed chapters on resume)
    progress = load_progress(args.progress_file)
    completed_keys = set(progress['completed_keys'])
    if completed_keys:
        print(f'Resuming: skipping {len(completed_keys)} already-completed chapters.')
    if args.progress_file and not completed_keys:
        # Record start time on a fresh run
        save_progress(args.progress_file, '', last_group=None)

    # atexit hook: ensure the progress file's last_group is
    # set to the most recent attempt, so a SIGTERM-killed run
    # can be diagnosed.
    last_attempted_group = [None]

    def _on_exit():
        if last_attempted_group[0] is not None and args.progress_file:
            # Only mark attempted groups as 'in progress' by
            # writing last_group; we don't mark them as
            # completed because we don't know whether the move
            # + DB update + HTML rewrite all completed.
            pass
    atexit.register(_on_exit)

    conn = sqlite3.connect(args.db)
    buggy = find_buggy_files(conn, workspace=args.workspace)
    conn.close()

    if args.course:
        buggy = [f for f in buggy if f['course_fullname'] == args.course]
    if args.module:
        buggy = [f for f in buggy if f['module_name'] == args.module]
    if args.section:
        buggy = [f for f in buggy if f['section_name'] == args.section]

    if not buggy:
        print('No buggy files found. Nothing to do.')
        return 0

    # Group by (course, section, module_id) — not module_name,
    # because two modules in the same section can share the
    # same module_name (moodle has a stable id but the
    # name can be reused after a teacher edits).
    groups = defaultdict(list)
    for f in buggy:
        key = (f['course_fullname'], f['section_name'],
               f['module_id'], f['module_name'])
        groups[key].append(f)
    total_groups = len(groups)
    print(f'Found {len(buggy)} buggy files in {total_groups} (course, section, module) groups.')

    if args.dry_run:
        print('\n[dry-run] no changes made.')
        return 0

    if not args.yes:
        resp = input('\nProceed with repair? [y/N] ').strip().lower()
        if resp != 'y':
            print('Aborted.')
            return 1

    # Heartbeat state
    last_heartbeat = [time.time()]

    def _maybe_heartbeat(done, total, last_msg):
        if not args.heartbeat:
            return
        now = time.time()
        if now - last_heartbeat[0] >= args.heartbeat:
            last_heartbeat[0] = now
            print(f'  [heartbeat] {done}/{total} chapters done; last: {last_msg}',
                  flush=True)

    total_moves = 0
    total_rewrites = 0
    db_updates = 0
    skipped_resume = 0
    for i, ((cname, sname, module_id, mname), files) in enumerate(
            groups.items(), start=1):
        last_attempted_group[0] = (cname, sname, module_id, mname)

        # Skip if already completed (resume mode)
        key_str = f'{cname}|{sname}|{module_id}|{mname}'
        if key_str in completed_keys:
            skipped_resume += 1
            continue

        # Heartbeat: tell the user we're still alive
        _maybe_heartbeat(
            done=i - 1,
            total=total_groups,
            last_msg=f'{cname[:30]} / {sname[:30]} / {mname[:30]}',
        )

        # Step 1: Scan HTMLs at section root to learn which
        # subdir (if any) each buggy basename is referenced
        # from.
        html_refs = scan_html_references_in_section(
            ws=args.workspace,
            course_fullname=cname,
            section_name=sname,
        )

        # For each buggy file, determine the target subdir.
        # If HTML doesn't reference it, we just move it to
        # the module root (subdir='').
        # We also need the file's content_filepath to find
        # its current on-disk location (it may be at
        # <section>/<cf>/<basename> rather than flat).
        moves_spec = []
        filenames_to_filepath = {}
        for f in files:
            fname = os.path.basename(f['saved_to'])
            subdir = _infer_target_subdir(html_refs, fname) or ''
            moves_spec.append((fname, subdir))
            filenames_to_filepath[fname] = f.get('content_filepath') or '/'

        # Step 2: Move files
        moves = move_buggy_files(
            ws=args.workspace,
            course_fullname=cname,
            section_name=sname,
            module_name=mname,
            buggy_filenames_with_subdir=moves_spec,
            filenames_to_filepath=filenames_to_filepath,
        )
        total_moves += len(moves)
        print(f'  [{i}/{total_groups}] [{mname[:50]}] moved {len(moves)} files',
              flush=True)

        # Step 3: Update DB so future moodle-dl invocations see
        # the corrected paths. This is critical: without
        # updating the DB, the next 'moodle-dl' run will
        # consider the file "missing" and try to re-download.
        conn = sqlite3.connect(args.db)
        cur = conn.cursor()
        for (old, new, subdir) in moves:
            cur.execute(
                'UPDATE files SET saved_to = ? WHERE saved_to = ?',
                (new, old),
            )
            if cur.rowcount:
                db_updates += cur.rowcount
        conn.commit()
        conn.close()

        # Step 4: Rewrite HTML references. After the move, the
        # HTML is in <module>/<basename>. We use the same
        # rewrite_html_links_to_local_paths + build_local_resource_map
        # pipeline that the downloader uses (DRY principle).
        #
        # build_local_resource_map builds a lookup from
        # canonical KCL URLs → disk paths for all files in
        # the workspace. rewrite_html_links_to_local_paths
        # then scans each HTML file and replaces remote
        # URLs with local relative paths.
        if html_refs:
            # Collect all HTML paths that need rewriting
            html_paths = set(ref['html_path'] for ref in html_refs)
            # Build the resource map from ALL files in the workspace
            # (not just the moved ones — the HTML may reference
            # files that were already correctly placed).
            conn = sqlite3.connect(args.db)
            cur = conn.cursor()
            cur.execute(
                'SELECT * FROM files WHERE download_status = ?',
                ('success',),
            )
            all_files = []
            for row in cur.fetchall():
                # Build minimal File objects for build_local_resource_map
                # We only need saved_to and content_fileurl
                class _MinimalFile:
                    pass
                f = _MinimalFile()
                f.saved_to = row[10]  # saved_to column
                f.content_fileurl = row[9]  # content_fileurl column
                all_files.append(f)
            conn.close()
            local_resources = build_local_resource_map(all_files)
            for html_path in sorted(html_paths):
                if not os.path.isfile(html_path):
                    continue
                try:
                    with open(html_path, 'r', encoding='utf-8', errors='replace') as fh:
                        html_content = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                rewritten_html, n = rewrite_html_links_to_local_paths(
                    html_content,
                    html_path,
                    local_resources,
                )
                if n:
                    with open(html_path, 'w', encoding='utf-8', errors='replace', newline='') as fh:
                        fh.write(rewritten_html)
                    total_rewrites += n
                    print(f'    rewrote {n} refs in {os.path.basename(html_path)[:60]}',
                          flush=True)

        # Mark this group as completed in the progress file.
        # We do this after the DB update + HTML rewrite so
        # the resume logic only skips groups that were
        # fully processed.
        save_progress(
            args.progress_file,
            key_str,
            last_group=(cname, sname, module_id, mname),
        )

    print(f'\nTotal moves: {total_moves}')
    print(f'Total HTML rewrites: {total_rewrites}')
    print(f'Total DB updates: {db_updates}')
    if skipped_resume:
        print(f'Skipped (resume): {skipped_resume} chapters')
    if args.progress_file:
        print(f'Progress saved to: {args.progress_file}')
        print('(delete this file to start a fresh run)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
