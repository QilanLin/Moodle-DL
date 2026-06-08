# -*- coding: utf-8 -*-
"""
On-disk path repair for files affected by the workspace-isolation bug.

Background
----------
Before commit d1ae09d, 'resource', 'page', 'url', and 'label'
module files were saved flat at the section root (no
module_name subfolder). As a result, HTML files referencing
relative assets like 'assets/css/main.css' broke in the
browser — the browser resolved them relative to the HTML's
location and looked for a file in a non-existent 'assets/css/'
subfolder of the section root.

This tool repairs already-downloaded content in three steps:
  1. compute_correct_saved_to() — compute the path that
     gen_path() with the fix would have produced.
  2. move_buggy_files() — atomically move files from the
     buggy location to the corrected one (preserving content
     via shutil.move).
  3. (HTML rewriting is delegated to
     moodle_dl.downloader.html_localizer — same pipeline
     the downloader uses, see rewrite_html_links_to_local_paths
     + build_local_resource_map. This keeps the repair
     and download code paths DRY: a single bug fix
     benefits both flows.)

It also has find_buggy_files() for DB-driven discovery of
which files need to be repaired.
"""
import os
import re
import shutil
import sqlite3
from typing import Iterable, List, Tuple

from moodle_dl.downloader.task import Task
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools


def _build_file_for_gen_path(
    module_name: str,
    content_filepath: str,
    content_filename: str,
    module_modname: str = 'resource',
    section_name: str = 'Section',
    module_id: int = 1,
) -> File:
    """Build a minimal File object for use with Task.gen_path()."""
    return File(
        module_id=module_id,
        section_name=section_name,
        section_id=1,
        module_name=module_name,
        content_filepath=content_filepath,
        content_filename=content_filename,
        content_fileurl='https://example.com/' + content_filename,
        content_filesize=1,
        content_timemodified=1,
        module_modname=module_modname,
        content_type='file',
        content_isexternalfile=False,
    )


def compute_correct_saved_to(
    ws: str,
    course_fullname: str,
    section_name: str,
    module_name: str,
    content_filepath: str,
    buggy_filename: str,
) -> str:
    """Compute the path that gen_path() (with the d1ae09d fix)
    would have produced for this file.

    @param ws: workspace root (e.g. /Volumes/Untitled/...)
    @param course_fullname: course full name
    @param section_name: section name
    @param module_name: module name
    @param content_filepath: original file path within the module
    @param buggy_filename: file basename to attach to the path
    @return: absolute path the file SHOULD have been saved to
    """
    f = _build_file_for_gen_path(
        module_name=module_name,
        content_filepath=content_filepath,
        content_filename=buggy_filename,
        section_name=section_name,
    )
    course = Course(_id=0, fullname=course_fullname, files=[f])
    dest = Task.gen_path(ws, course, f)
    return os.path.join(dest, buggy_filename)


def move_buggy_files(
    ws: str,
    course_fullname: str,
    section_name: str,
    module_name: str,
    buggy_filenames_with_subdir: Iterable[Tuple[str, str]],
    # Maps buggy_filename -> its current content_filepath
    # (e.g. '/', '/images/', '/styles/'). We need this to
    # find the file's CURRENT location on disk because the
    # buggy state may have the file at
    # <section>/<content_filepath>/<basename> rather than
    # flat at <section>/<basename>.
    filenames_to_filepath: dict = None,
) -> List[Tuple[str, str, str]]:
    """Move a set of buggy files from their buggy on-disk
    location to the correct module-folder location.

    @param buggy_filenames_with_subdir: iterable of
       (buggy_basename, subdir_within_module) tuples.
       The subdir is prepended to the module folder; e.g.
       'assets/css' means the file should land at
       <ws>/<course>/<section>/<module_name>/assets/css/<basename>.
    @param filenames_to_filepath: optional dict mapping
       buggy_filename -> content_filepath. When set, we
       compute the on-disk buggy path as
       <section_dir>/<content_filepath>/<basename>. When
       not set, we assume flat at the section root.
    @return: list of (old_path, new_path, subdir) tuples that
             were successfully moved.
    """
    moves = []
    section_dir = os.path.join(
        ws,
        PathTools.to_valid_name(course_fullname, is_file=False),
        PathTools.to_valid_name(section_name, is_file=False),
    )
    if filenames_to_filepath is None:
        filenames_to_filepath = {}
    for entry in buggy_filenames_with_subdir:
        fname, subdir = entry
        # Find the buggy file's current on-disk location.
        # It could be at:
        #   <section>/<basename>           (flat)
        #   <section>/<cf>/<basename>      (in subdir)
        # where <cf> is the file's content_filepath.
        # We try flat first, then probe common subdirs.
        cfp = filenames_to_filepath.get(fname, '/')
        candidates = []
        if cfp and cfp != '/':
            candidates.append(
                os.path.join(section_dir, cfp.strip('/'), fname)
            )
        candidates.append(os.path.join(section_dir, fname))
        old = None
        for c in candidates:
            if os.path.exists(c):
                old = c
                break
        if old is None:
            continue
        # Build the content_filepath the file should have had.
        # '/assets/css/' + basename → content_filepath='/assets/css/'
        cf = '/' + subdir.strip('/') + '/' if subdir else '/'
        new = compute_correct_saved_to(
            ws=ws,
            course_fullname=course_fullname,
            section_name=section_name,
            module_name=module_name,
            content_filepath=cf,
            buggy_filename=fname,
        )
        new_dir = os.path.dirname(new)
        if not os.path.isdir(new_dir):
            os.makedirs(new_dir, exist_ok=True)
        if os.path.exists(new):
            # Already correct; just remove the buggy duplicate
            os.remove(old)
        else:
            shutil.move(old, new)
        moves.append((old, new, subdir))
    return moves


def find_buggy_files(conn: sqlite3.Connection,
                     workspace: str = None) -> List[dict]:
    """Query the DB and return all 'resource' / 'page' / 'url' /
    'label' files that are saved at a non-module-dir location
    on disk (i.e. saved_to does not contain the module_name
    as a directory component).

    If workspace is provided, ALSO cross-check disk: a file
    whose saved_to says 'section root' but whose content_filename
    (or any extension variant like .html vs .html.md) is found
    in the module dir on disk is NOT flagged as buggy (it has
    already been moved on disk; only the DB row needs updating).
    Files whose content_filename does not exist on disk
    anywhere are still buggy (likely 404).

    'Single-run completeness' contract: every file flagged
    as buggy must be either:
      - At a location that repair_paths can move
        (e.g. <section>/<basename> or <section>/<cf>/<basename>),
        OR
      - The DB row references a basename that does not exist
        anywhere on disk (i.e. the file was never downloaded,
        e.g. 404 from Moodle).
    The tool is NOT allowed to flag files as buggy that
    are already correctly placed on disk in the module
    dir (even with extension variation like .html vs
    .html.md).
    """
    from moodle_dl.utils import PathTools
    cur = conn.cursor()
    cur.execute("""
        SELECT
          file_id, course_id, course_fullname, section_id, section_name,
          module_id, module_name, module_modname,
          content_filepath, content_filename,
          download_status, saved_to
        FROM files
        WHERE (
            module_modname IN ('resource', 'page', 'url', 'label')
            OR module_modname LIKE '%url%'
            OR module_modname LIKE '%label%'
            OR module_modname LIKE '%description%'
        )
          AND download_status = 'success'
          AND module_name IS NOT NULL
          AND module_name != ''
          AND module_id != 0
    """)
    rows = cur.fetchall()
    buggy = []
    # Common file extensions on disk that moodle-dl may
    # append to the content_filename.
    extensions = ['.html.md', '.html', '.md', '.pdf', '.txt', '.zip',
                  '.css', '.js', '.gif', '.png', '.jpg', '.mp3',
                  '.mp4', '.webloc', '.json', '.docx', '.xlsx',
                  '.pptx', '.epub', '.csv', '.tsv']

    def _strip_ext(name):
        for ext in extensions:
            if name.endswith(ext):
                return name[:-len(ext)]
        return name

    for r in rows:
        (file_id, course_id, cname, section_id, section_name,
         module_id, mname, mmname, cfp, cfname, status, saved) = r
        # Normalize the module_name through to_valid_name
        # (used as a directory name) so we can compare
        # against saved_to which is the result of joining
        # the to_valid_name'd name into a path.
        norm_mname = PathTools.to_valid_name(mname, is_file=False) if mname else mname
        # The check needs to match a directory component
        # (e.g. '<section>/<module_name>/<filename>'), not
        # a substring match (e.g. '<module_name>.png' inside
        # a basename). The normalized module_name is used as
        # a directory; the next character after it in
        # saved_to should be a path separator.
        #
        # We need a true directory-component match. A
        # module_name can appear in the path in multiple
        # places (e.g. section="...Lecture 1..." and
        # module="Lecture 1" both contain the substring), so
        # we search for ALL occurrences and check which one
        # is followed by '/'. The first such match (or any
        # such match) means the file is correctly in module
        # dir; otherwise, the file is at a non-module-dir
        # location and is buggy.
        #
        # We do this case-INSENSITIVELY because some courses
        # use 'Title Case' for section/module names and
        # 'UPPER CASE' for content_filename (e.g. module
        # 'INTRODUCTION TO PART 2' with dir 'Introduction
        # to Part 2'). A case-sensitive check would
        # false-positive those files as buggy.
        if not norm_mname:
            continue
        saved_lower = (saved or '').lower()
        norm_mname_lower = norm_mname.lower()
        is_in_module_dir = False
        i = 0
        while True:
            idx = saved_lower.find(norm_mname_lower, i)
            if idx < 0:
                break
            after_idx = idx + len(norm_mname_lower)
            saved_l = saved or ''
            if after_idx == len(saved_l) or (after_idx < len(saved_l) and saved_l[after_idx] == '/'):
                is_in_module_dir = True
                break
            i = idx + 1
        if is_in_module_dir:
            continue

        # Cross-check disk: if the file is actually on disk
        # in the module dir (with any extension variation),
        # the file is correctly placed; only the DB row
        # needs updating. Not buggy.
        if workspace:
            # The disk paths are created using to_valid_name()
            # which converts '/' to '⧸' (U+29F8) and ':' to
            # '：' (fullwidth). The raw DB values keep the
            # original chars, so we must normalize the
            # course/section/module names AND the
            # content_filename the same way before comparing.
            from moodle_dl.utils import PathTools
            norm_cname = PathTools.to_valid_name(cname, is_file=False)
            norm_sname = PathTools.to_valid_name(section_name, is_file=False)
            norm_mname_dir = PathTools.to_valid_name(mname, is_file=False)
            norm_cfname = PathTools.to_valid_name(cfname, is_file=True)
            module_dir = os.path.join(
                workspace, norm_cname, norm_sname, norm_mname_dir,
            )
            if os.path.isdir(module_dir):
                cfname_stripped = _strip_ext(norm_cfname)
                for root, dirs, files in os.walk(module_dir):
                    for f in files:
                        # Strip the '*NN* ' position prefix that
                        # moodle-dl prepends to file basenames.
                        # e.g. '*01* main.css' -> 'main.css'.
                        f_stripped = _strip_ext(f)
                        if f_stripped.startswith('*') and ' ' in f_stripped:
                            f_stripped = f_stripped.split(' ', 1)[1]
                        if f == norm_cfname or f == cfname \
                                or f_stripped == cfname_stripped:
                            is_in_module_dir = True
                            break
                    if is_in_module_dir:
                        break
                if is_in_module_dir:
                    continue

        buggy.append({
            'file_id': file_id,
            'course_id': course_id,
            'course_fullname': cname,
            'section_id': section_id,
            'section_name': section_name,
            'module_id': module_id,
            'module_name': mname,
            'module_modname': mmname,
            'content_filepath': cfp,
            'content_filename': cfname,
            'download_status': status,
            'saved_to': saved,
        })
    return buggy


def scan_html_references_in_section(
    ws: str,
    course_fullname: str,
    section_name: str,
) -> List[dict]:
    """For each HTML file at the buggy section root, scan its
    href/src attributes and return the list of (html_path,
    old_relative_ref) pairs that need to be rewritten.

    @return: list of dicts:
       {'html_path': ..., 'old_ref': 'assets/css/main.css',
        'filename': 'main.css'}
    """
    section_dir = os.path.join(
        ws,
        PathTools.to_valid_name(course_fullname, is_file=False),
        PathTools.to_valid_name(section_name, is_file=False),
    )
    if not os.path.isdir(section_dir):
        return []
    refs = []
    ref_pattern = re.compile(
        r'\b(?:href|src|poster|data)\s*=\s*(["\'])([^\"\']*?)\1',
        flags=re.IGNORECASE,
    )
    for entry in os.listdir(section_dir):
        if not entry.lower().endswith(('.html', '.htm')):
            continue
        html_path = os.path.join(section_dir, entry)
        if not os.path.isfile(html_path):
            continue
        with open(html_path, 'r', encoding='utf-8', errors='replace') as fp:
            html = fp.read()
        for m in ref_pattern.finditer(html):
            ref = m.group(2)
            # Skip absolute URLs and anchors
            if (ref.startswith(('#', 'http://', 'https://',
                                'data:', 'mailto:', 'javascript:'))
                    or not ref):
                continue
            # Get just the basename
            basename = ref.rsplit('/', 1)[-1]
            if not basename:
                continue
            refs.append({
                'html_path': html_path,
                'old_ref': ref,
                'basename': basename,
            })
    return refs
