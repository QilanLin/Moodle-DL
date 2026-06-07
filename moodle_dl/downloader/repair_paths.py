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
        [--dry-run] \\
        [--yes]
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict

from moodle_dl.downloader.task_path_repair import (
    compute_correct_saved_to,
    find_buggy_files,
    move_buggy_files,
    rewrite_html_references,
    scan_html_references_in_section,
)


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
    p.add_argument('--dry-run', action='store_true', help='Show what would happen')
    p.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = p.parse_args(argv)

    if not os.path.isfile(args.db):
        sys.exit(f'DB not found: {args.db}')
    if not os.path.isdir(args.workspace):
        sys.exit(f'Workspace not found: {args.workspace}')

    conn = sqlite3.connect(args.db)
    buggy = find_buggy_files(conn)
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
    print(f'Found {len(buggy)} buggy files in {len(groups)} (course, section, module) groups.')
    for key, files in groups.items():
        cname, sname, module_id, mname = key
        # Show only first 3 file basenames per group
        sample = sorted(set(os.path.basename(f['saved_to']) for f in files))[:3]
        print(f'  [{cname[:50]}] / [{sname[:50]}] / [{mname[:50]}] - {len(files)} files')
        for s in sample:
            print(f'    - {s}')

    if args.dry_run:
        print('\n[dry-run] no changes made.')
        return 0

    if not args.yes:
        resp = input('\nProceed with repair? [y/N] ').strip().lower()
        if resp != 'y':
            print('Aborted.')
            return 1

    total_moves = 0
    total_rewrites = 0
    db_updates = 0
    for (cname, sname, module_id, mname), files in groups.items():
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
        print(f'  [{mname[:50]}] moved {len(moves)} files')

        # Step 3: Update DB so future moodle-dl invocations see
        # the corrected paths. This is critical: without
        # updating the DB, the next 'moodle-dl' run will
        # consider the file "missing" and try to re-download.
        conn = sqlite3.connect(args.db)
        cur = conn.cursor()
        for (old, new, subdir) in moves:
            # Find the file_id(s) with the old saved_to and
            # update them to the new path.
            cur.execute(
                'UPDATE files SET saved_to = ? WHERE saved_to = ?',
                (new, old),
            )
            if cur.rowcount:
                db_updates += cur.rowcount
        conn.commit()
        conn.close()

        # Step 4: Rewrite HTML references. After the move, the
        # HTML is in <module>/<basename>. The new relative
        # path from there to the asset is just '<subdir>/<basename>'
        # or '<basename>' if subdir is empty.
        section_dir = os.path.join(
            args.workspace,
            cname,
            sname,
        )
        # Group refs by HTML path
        refs_by_html = defaultdict(list)
        for ref in html_refs:
            refs_by_html[ref['html_path']].append(ref)

        for html_path, refs in refs_by_html.items():
            old_to_new = {}
            for ref in refs:
                basename = ref['basename']
                # Find the move for this basename
                matching_move = None
                for (old, new, subdir) in moves:
                    if os.path.basename(old) == basename:
                        matching_move = (old, new, subdir)
                        break
                if not matching_move:
                    continue
                # New relative path from the HTML (in module dir) to the file
                # HTML is now at <module>/<html_basename>
                # File is at <module>/<subdir>/<basename>
                # Relative: <subdir>/<basename> (or ./<basename> if subdir empty)
                old, new, subdir = matching_move
                if subdir:
                    new_rel = f'{subdir}/{basename}'
                else:
                    new_rel = basename
                old_to_new[ref['old_ref']] = new_rel
            if old_to_new:
                n = rewrite_html_references(
                    html_path=html_path,
                    old_relative_paths=old_to_new.keys(),
                    new_relative_paths=old_to_new.values(),
                )
                total_rewrites += n
                if n:
                    print(f'    rewrote {n} refs in {os.path.basename(html_path)[:60]}')

    print(f'\nTotal moves: {total_moves}')
    print(f'Total HTML rewrites: {total_rewrites}')
    print(f'Total DB updates: {db_updates}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
