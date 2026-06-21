# macOS `._*` shadow files: side-effect analysis

## What does moodle-dl actually do?

moodle-dl performs **two** operations on macOS to keep your
download directory clean:

### 1. `strip_macos_metadata(path)` — automatic, per-write

**Called automatically** after every file write. Uses `ctypes`
to call macOS's `removexattr()` directly. Removes the following
extended attributes that cause macOS to create `._*` shadow
files:

| xattr name | What it is | Side effect of stripping |
|---|---|---|
| `com.apple.provenance` | macOS internal — tracks which app wrote the file | None (moodle-dl doesn't use it) |
| `com.apple.quarantine` | macOS Gatekeeper — "this file came from the web" flag | After stripping, macOS won't show the "this file was downloaded from the Internet" dialog when opening. **moodle-dl creates new files, so this never applied anyway.** |
| `com.apple.metadata:kMDItemWhereFroms` | Spotlight metadata — where the file came from | Spotlight will re-scan and re-create this when the file is opened in Finder |
| `com.apple.metadata:kMDItemDownloadedDate` | Spotlight metadata — when the file was downloaded | Same as above — Spotlight re-creates |

**Does NOT touch:**
- `com.apple.fileprovider.ignore#P` — Finder-only attributes
- Custom user tags / Spotlight comments
- File permissions / owner / mode

**Net effect on the file:** the file's content, name, size,
and timestamps are unchanged. The file's `._*` shadow is
*prevented from being created* (not deleted after the fact).

### 2. `cleanup_macos_shadow_files(directory)` — manual only

**NOT called automatically.** It's exposed as a Python API
that the user can call manually to clean up `._*` files left
from previous runs (e.g. before this fix was added).

Returns the count of files removed. The function only deletes
files whose name starts with `._` — never touches real moodle-dl
files.

## What about non-macOS platforms?

Both functions are no-ops on Linux/Windows:
- `strip_macos_metadata` returns immediately if
  `sys.platform != 'darwin'`
- `cleanup_macos_shadow_files` returns 0 if
  `sys.platform != 'darwin'`

So Linux/Windows users see no behavior change.

## Could stripping break anything?

In practice: **no**. The four xattrs we strip are:
- Internal to macOS (re-created automatically when needed)
- Not used by moodle-dl
- Not used by Finder for display purposes
- Not used by Spotlight for search

The user's **own** xattrs (custom Finder tags, Spotlight comments,
third-party metadata) are NOT touched.

## What if the user WANTS the metadata?

If for some reason the user wants to keep `com.apple.provenance`
on the downloaded files (e.g. they're tracking downloads in a
script that reads this xattr), they can set the env var:

```bash
MOODLE_DL_KEEP_MACOS_XATTRS=1 moodle-dl
```

In that case, `strip_macos_metadata` becomes a no-op. The macOS
shadow file pollution will return, but the user's xattrs are
preserved.

## What about `cleanup_macos_shadow_files` — is it safe?

**Yes**, but ONLY if called manually. It only deletes files
matching `fn.startswith('._')` — never touches real files.
Errors are silently swallowed (best-effort cleanup).

## Summary

| Operation | When | Side effect |
|---|---|---|
| `strip_macos_metadata` | After every file write (macOS only) | Removes 4 macOS-internal xattrs, prevents `._*` creation. No user-visible change. |
| `cleanup_macos_shadow_files` | Never (manual only) | Deletes files matching `._*` (best-effort). User must call it. |
| Both on Linux/Windows | — | No-op. |
