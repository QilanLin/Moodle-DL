# macOS `._*` shadow files in your moodle-dl workspace

## TL;DR

**moodle-dl automatically strips macOS extended attributes
after each file write.** This prevents the OS from creating
`._*` AppleDouble / resource-fork shadow files on non-HFS+
filesystems. The fix is automatic; you do not need to do
anything special.

## What's the problem?

If you run moodle-dl on macOS, your download directory will
contain files that look like duplicates:

```
*05* some_lecture.webloc
._*05* some_lecture.webloc    <-- this one is "extra"
*10* another_url.webloc
._*10* another_url.webloc    <-- this one is "extra"
```

The `._*` files are **not** created by moodle-dl. They are
**macOS AppleDouble** (a.k.a. resource fork / "._" file) files
that the OS creates automatically when a file with extended
attributes (such as `com.apple.provenance`, a quarantine
flag, or a custom icon) is written to a non-HFS+ filesystem
(e.g. exFAT, NTFS, SMB share, FAT32, ext4, etc.).

## The fix

After every file write, moodle-dl calls `strip_macos_metadata`,
which uses `ctypes` to call macOS's `removexattr()` directly
(no subprocess overhead) to remove the OS-level xattrs that
trigger the shadow-file creation:

```python
strip_macos_metadata(dest_path)  # called in task.py after each write
```

The xattrs that are stripped:
- `com.apple.provenance`
- `com.apple.quarantine`
- `com.apple.metadata:kMDItemWhereFroms`
- `com.apple.metadata:kMDItemDownloadedDate`

These are macOS-internal attributes that moodle-dl doesn't need.
We do NOT touch user-level attributes like custom Finder tags.

## What if `._*` files still appear?

If you have old `._*` files from previous runs (with old
moodle-dl that didn't strip xattrs), you can clean them up
manually. moodle-dl also exposes `cleanup_macos_shadow_files()`
which you can call as a one-off:

```python
from moodle_dl.downloader.task import cleanup_macos_shadow_files
n = cleanup_macos_shadow_files('/path/to/your/workspace')
print(f'Removed {n} shadow files')
```

Or from the shell:

```bash
# Remove all ._ files recursively
find /path/to/workspace -name '._*' -type f -delete

# Or use the built-in macOS tool
dot_clean -m /path/to/workspace

# Strip the xattrs (the source of the problem)
xattr -cr /path/to/workspace
```

## Why does the OS do this?

The OS does this transparently to preserve file-level metadata
on non-HFS+ filesystems that don't natively support extended
attributes. The fix is to strip the xattrs after writing.

## Section ordering

moodle-dl trusts the Moodle server's section order
(`course_sections.section` column). It does NOT add its own
sort prefix to section directory names. On macOS Finder or
`ls`, multi-digit sections like `Week 1, Week 2, ..., Week 10`
will still appear in alphabetical order (`Week 1, Week 10,
Week 2, ...`). The in-section natural sort is provided by the
`*NN*` file prefix (`*01*`, `*02*`, ..., `*10*`) on each
file.

## See also

- [AppleDouble / Resource Forks on Wikipedia](https://en.wikipedia.org/wiki/AppleSingle_and_AppleDouble_formats)
- `man dot_clean` (macOS built-in cleanup tool)
- `man xattr` (macOS extended attribute manipulation)