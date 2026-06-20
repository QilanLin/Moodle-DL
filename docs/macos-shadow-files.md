# macOS `._*` shadow files in your moodle-dl workspace

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
that Finder (and other macOS apps) automatically create whenever
a regular file is written to a non-HFS+ filesystem (e.g. exFAT,
NTFS, SMB share, FAT32, ext4, etc.).

## Why does this happen?

When macOS writes a file with extended attributes (such as
`com.apple.provenance` for download provenance, or a custom
icon, or a quarantine flag), it stores the attributes in a
**second file** named `._<original-filename>`. The OS does
this transparently — Finder hides them in the GUI, but `ls`
shows them.

## Are they a problem?

Only visually:

- They **double the file count** in `ls` / `moodle-dl --list`
- They make `git status` noisy if the workspace is in a repo
- They confuse the natural-sort of a directory listing
  (e.g. `Week 10` sorting between `Week 1` and `Week 2` is a
  separate issue, not caused by `._*` files, but the `._*`
  pollution makes the listing look worse)

The `._*` files are **harmless** — they do not contain
duplicates of your moodle content. They are 4KB of metadata
per file.

## Solutions

### 1. Use `moodle-dl --list` (recommended)

```bash
moodle-dl --list
```

This command:
- filters out `._*` files automatically
- sorts entries naturally (`Week 1`, `Week 2`, ..., `Week 10`)
- cross-references the SQLite database and the filesystem
- reports any DB↔FS inconsistencies

### 2. Hide in Finder

In Finder, go to **View → Show View Options → Show these items on
the Desktop** and uncheck "Show External Disks" / uncheck
"Show all filename extensions" — actually the right setting is
**View → Hide Resource Forks** (only available in some Finder
versions).

Or run in Terminal:
```bash
defaults write com.apple.finder AppleShowAllFiles -bool NO
killall Finder
```

### 3. Remove them

```bash
# Remove all ._ files recursively
find /path/to/workspace -name '._*' -type f -delete

# Or use the built-in macOS tool
dot_clean -m /path/to/workspace
```

### 4. Strip the extended attributes that cause them

```bash
# Strip the xattrs that trigger the ._ file creation
xattr -cr /path/to/workspace
```

This is the most thorough fix — it removes the original cause.

### 5. Use a native macOS filesystem (HFS+/APFS)

If you use a macOS-native filesystem (HFS+ or APFS), no `._*`
files are created at all. This is the best option if you have
control over the storage.

## Does moodle-dl plan to fix this?

**No.** The `._*` files are an OS-level behavior and removing
them would:

1. Strip legitimate macOS metadata (download provenance,
   custom icons, etc.)
2. Hide a real problem (you may have legitimately wanted those
   attributes for the file)

Instead, moodle-dl provides **`--list`** to give you a clean
view, and this doc explains how to clean up if you want.

## References

- [AppleDouble / Resource Forks on Wikipedia](https://en.wikipedia.org/wiki/AppleSingle_and_AppleDouble_formats)
- `man dot_clean` (macOS built-in cleanup tool)
- `man xattr` (macOS extended attribute manipulation)