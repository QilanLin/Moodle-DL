# -*- coding: utf-8 -*-
"""
moodle-dl --list command.

Prints a natural-sort, macOS-shadow-file-filtered, DB-cross-referenced
listing of the workspace.

Background
----------
The user reported that the default directory listing
(``ls`` / Finder) shows ``._*`` shadow files alongside the real
moodle-dl output, doubling the file count and polluting the
listing. In addition, alphabetical sort puts ``Week 10`` between
``Week 1`` and ``Week 2``.

This module provides ``moodle-dl --list`` which:

  1. Reads the SQLite database
  2. Reads the workspace filesystem
  3. Cross-references the two
  4. Prints a natural-sort listing (no ._ files, multi-digit
     weeks in the correct order)
  5. Reports any DB↔FS inconsistencies

It does NOT download anything.
"""
import logging
import os
import re
import sqlite3
from collections import defaultdict
from typing import Dict, List, Tuple

from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts


def natural_sort_key(name: str):
    """Sort key that compares numeric parts numerically and
    everything else as case-insensitive string. So:
      'Week 1' < 'Week 2' < 'Week 10'
    """
    parts = re.split(r'(\d+)', name)
    return tuple(
        int(p) if p.isdigit() else p.lower()
        for p in parts
    )


def extract_numeric_prefix(name: str) -> int:
    """Extract the *NN* prefix from a moodle-dl filename. Returns
    9999 (a high sentinel) if no prefix is present.
    """
    m = re.match(r'\*(\d+)\*', name)
    return int(m.group(1)) if m else 9999


def is_macos_shadow(name: str) -> bool:
    """A macOS AppleDouble / resource-fork shadow file starts
    with ``._``. We filter them out by default.
    """
    return name.startswith('._')


def collect_db_files(db_path: str) -> Dict[str, Dict[int, str]]:
    """Return ``{course_fullname: {file_id: saved_to}}`` from the
    SQLite database. Returns empty dict on missing/corrupt DB.
    """
    result = defaultdict(dict)
    if not os.path.exists(db_path):
        return result
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # Use parameterized query (defensive even though we
        # control the values; we just don't want SQL injection
        # via tampered DB).
        cur.execute(
            'SELECT file_id, course_fullname, saved_to FROM files'
        )
        for file_id, course, saved_to in cur.fetchall():
            if saved_to and course:
                result[course][file_id] = saved_to
        conn.close()
    except sqlite3.Error as e:
        logging.error('Cannot read database %s: %s', db_path, e)
    return result


def collect_fs_files(root: str) -> Dict[str, List[str]]:
    """Walk the workspace filesystem and return
    ``{section_path: [filenames]}``. Filters out macOS
    ._ shadow files.
    """
    result = defaultdict(list)
    if not os.path.isdir(root):
        return result
    for dirpath, _dirnames, filenames in os.walk(root):
        # Skip the root dir
        if os.path.realpath(dirpath) == os.path.realpath(root):
            continue
        for fn in filenames:
            if is_macos_shadow(fn):
                continue
            result[dirpath].append(fn)
    return result


def find_mismatches(
    db_by_course: Dict[str, Dict[int, str]],
    root: str,
) -> Tuple[List[str], List[str]]:
    """Cross-reference DB against FS.

    Returns:
        (missing_on_disk, orphan_on_disk)
    """
    missing_on_disk = []  # in DB but not on disk
    seen_paths = set()

    for course, files in db_by_course.items():
        for file_id, saved_to in files.items():
            seen_paths.add(os.path.realpath(saved_to))
            if not os.path.exists(saved_to):
                missing_on_disk.append(
                    f'  {course}: file_id={file_id}\n    {saved_to}'
                )

    # Find orphan .part files
    orphan_on_disk = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith('.part'):
                orphan_on_disk.append(os.path.join(dirpath, fn))

    return missing_on_disk, orphan_on_disk


def print_workspace_listing(config: ConfigHelper, opts: MoodleDlOpts):
    """Entry point for ``moodle-dl --list``.

    Prints a natural-sort listing of the workspace, filters
    macOS shadow files, and reports DB↔FS inconsistencies.
    """
    download_path = opts.path
    db_path = os.path.join(download_path, 'moodle_state.db')

    print('=' * 78)
    print('moodle-dl --list: workspace listing')
    print('=' * 78)
    print(f'  Workspace: {download_path}')
    print(f'  Database:  {db_path}  ({"exists" if os.path.exists(db_path) else "MISSING"})')
    print()

    if not os.path.isdir(download_path):
        print(f'ERROR: workspace does not exist: {download_path}')
        return

    # 1. List top-level courses (natural sort)
    print('--- Courses (natural sort) ---')
    top = sorted(
        (
            d for d in os.listdir(download_path)
            if os.path.isdir(os.path.join(download_path, d))
            and not d.startswith('.')  # skip hidden dirs
        ),
        key=natural_sort_key,
    )
    for d in top:
        print(f'  {d}')

    if not top:
        print('  (no courses found)')
        return

    # 2. List sections of each course (natural sort)
    print()
    print('--- Sections per course (natural sort) ---')
    for course in top:
        course_path = os.path.join(download_path, course)
        if not os.path.isdir(course_path):
            continue
        sections = sorted(
            (
                d for d in os.listdir(course_path)
                if os.path.isdir(os.path.join(course_path, d))
                and not d.startswith('.')
            ),
            key=natural_sort_key,
        )
        print(f'\n  [{course}]')
        for s in sections:
            print(f'    {s}')

    # 3. Per section, list files (natural sort, *NN* prefix first)
    print()
    print('--- Files per section (natural sort, *NN* prefix first) ---')
    for course in top:
        course_path = os.path.join(download_path, course)
        if not os.path.isdir(course_path):
            continue
        sections = sorted(
            (
                d for d in os.listdir(course_path)
                if os.path.isdir(os.path.join(course_path, d))
                and not d.startswith('.')
            ),
            key=natural_sort_key,
        )
        print(f'\n  [{course}]')
        for section in sections:
            section_path = os.path.join(course_path, section)
            files_in_section = [
                f for f in os.listdir(section_path)
                if not f.startswith('._')
                and not f.startswith('.')
            ]
            # Sort: *NN* prefix first by numeric, then natural
            # sort for everything else (Week 10 after Week 9)
            files_in_section.sort(
                key=lambda f: (
                    extract_numeric_prefix(f),
                    natural_sort_key(f),
                ),
            )
            print(f'    [{section}] ({len(files_in_section)} files)')
            for f in files_in_section:
                print(f'      {f}')

    # 4. DB↔FS cross-reference
    print()
    print('--- DB ↔ FS cross-reference ---')
    db_by_course = collect_db_files(db_path)
    missing, orphans = find_mismatches(db_by_course, download_path)

    if not db_by_course:
        print('  (no database or empty)')

    if missing:
        print(f'\n  ⚠️  {len(missing)} files in DB but NOT on disk:')
        for m in missing[:20]:  # cap output
            print(m)
        if len(missing) > 20:
            print(f'  ... and {len(missing) - 20} more')

    if orphans:
        print(f'\n  ⚠️  {len(orphans)} .part files on disk (orphans):')
        for o in orphans[:20]:
            print(f'    {o}')
        if len(orphans) > 20:
            print(f'  ... and {len(orphans) - 20} more')

    if not missing and not orphans:
        total_db = sum(len(v) for v in db_by_course.values())
        print(f'  ✓ All {total_db} DB records consistent with FS')

    # 5. Stat the workspace
    print()
    print('--- Workspace stats ---')
    total_files = 0
    total_size = 0
    shadow_count = 0
    for dirpath, _dirs, filenames in os.walk(download_path):
        for fn in filenames:
            if is_macos_shadow(fn):
                shadow_count += 1
            else:
                total_files += 1
                try:
                    total_size += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
    print(f'  Total moodle-dl files: {total_files}')
    print(f'  macOS ._ shadow files:  {shadow_count}  (hidden, see docs/macos-shadow-files.md)')
    print(f'  Total size: {total_size / 1024 / 1024:.1f} MiB')
    print()
    print('Done.')