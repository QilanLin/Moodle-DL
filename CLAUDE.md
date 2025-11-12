# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Moodle-DL is a Python CLI application that downloads course content from Moodle learning management systems. This is a customized fork with Chinese localization and modified download behavior.

## Development Commands

### Installation
```bash
# Install from source (required for this fork)
pip install -e .
```

### Running
```bash
# First time setup
moodle-dl --init --sso

# Normal usage
moodle-dl

# With debug logging
moodle-dl --verbose --log-to-file

# Configuration wizard
moodle-dl --config
```

### Testing
```bash
# Unit tests (minimal coverage)
python tests/test_url_helper.py

# Debug/test scripts
python test_moodle_api.py          # API testing
python test_autologin.py           # SSO login testing
python test_generic_extractor.py   # Text extraction
```

### Code Formatting
```bash
# Format code (Black with 120 char line length)
black moodle_dl/ --line-length 120

# Sort imports
isort moodle_dl/ --profile black
```

## 🎯 Core Design Principle: Mobile API First

### The Principle

> **Use Moodle Mobile API whenever possible. For functionality not available in the API, other tools (like Playwright) can be used reasonably.**

This principle ensures:
- Clean architecture with clear data sources
- Stable, officially-supported API usage
- Graceful fallbacks for edge cases
- Maintainability and future compatibility

### Application in Moodle-DL

**All content should be obtained via Mobile API when available:**

| Content Type | API Source | Implementation | Notes |
|--------------|-----------|-----------------|-------|
| Course metadata | `core_course_get_contents` | Direct API call | ✅ Primary source |
| Module metadata | `core_course_get_contents` | Direct API call | ✅ Primary source |
| File content | `webservice/pluginfile.php` | Download via fileurl | ✅ API-provided URLs |
| HTML content | `content` field in API response | API return value | ✅ Direct from API |
| Videos (Kaltura, etc.) | Parse from HTML + convert | Extract from API HTML | ✅ Derived from API |
| Attachments | Parse from HTML + fileurl | Auto-extract by result_builder | ✅ Derived from API |

**Only use non-API tools when necessary:**

| Content Type | Tool | Reason | Notes |
|--------------|------|--------|-------|
| Print Book HTML | Playwright | Not in Mobile API | ⚠️ Modified immediately to link to local files |
| Browser-dependent content | Playwright | Requires browser session | ⚠️ Fallback only |
| SSO login flow | Playwright | API cannot handle redirects | ⚠️ Authentication only |

### Example: Book Module

The Book module exemplifies this principle perfectly:

```
Step 1: Get all chapters (Mobile API) ✅
  └─ core_course_get_contents → Chapter list + HTML + fileurl

Step 2: Download chapter files (Mobile API) ✅
  └─ Use fileurl (provided by API) to download chapter HTML
  └─ Extract videos from API-returned HTML
  └─ result_builder handles attachments automatically

Step 3: Get Print Book (Playwright - only if needed) ⚠️
  └─ Fetch Print Book HTML (not in API)
  └─ Modify to link to locally-downloaded files
  └─ Still depends on API downloads indirectly

Result: Everything depends on Mobile API, except one optional HTML file
```

See `BOOK_MODULE_IMPROVEMENTS_SUMMARY.md` (sections 2-62) for detailed implementation.

---

## Architecture

### High-Level Structure

The application follows a modular architecture with clear separation:

1. **Moodle API Client** (`moodle/`) - Handles Moodle Mobile API communication
2. **Download Service** (`downloader/`) - AsyncIO-based download orchestrator with parallel processing
3. **Module Handlers** (`moodle/mods/`) - 35+ specialized handlers for different Moodle module types
4. **State Management** (`database.py`) - SQLite-based file tracking and change detection
5. **Notification System** (`notifications/`) - Multi-channel notifications (Telegram, Discord, Email, XMPP, Ntfy)
6. **CLI Wizards** (`cli/`) - Interactive configuration management

### Critical Architectural Patterns

#### 1. Dual Authentication Model

- **Token Authentication**: For Moodle Mobile API access (long-lived tokens)
- **Cookie Authentication**: For HTML page access and browser-required resources
  - `MoodleSession` cookies auto-refresh via API
  - SSO cookies (buid, fpc) require browser export
  - Unified cookie manager at `cookie_manager.py` with auto-refresh

#### 2. Three-Tier Content Access Strategy

The application handles three types of Moodle content differently:

- **Type 1 - File Modules**: Direct download via API (resource, folder, assign files)
- **Type 2 - Page Modules**: Partial API support with description field (label, page)
- **Type 3 - External/LTI Modules**: Requires HTML scraping and custom extractors (kalvidres, helixmedia, lti)

#### 3. Modular Plugin System

Each Moodle module type has its own handler class inheriting from `MoodleMod`:

- Located in `moodle_dl/moodle/mods/`
- Each implements `real_fetch_mod_entries()` (async fetch) and `download_condition()` (filtering)
- Auto-discovered via `ALL_MODS` list
- Examples: `BookMod`, `AssignMod`, `QuizMod`, `ForumMod`, `WikiMod`, etc.

#### 4. Result Building Flow

**IMPORTANT**: Understanding how files flow through the system is critical:

1. **API Fetch Phase**: `moodle_service.py` calls module handlers to fetch content
2. **Result Building Phase**: `result_builder.py` combines API data with module-specific data
3. **Two Processing Paths**:
   - `_get_files_in_modules()`: Processes modules listed in Mobile API sections
   - `_get_files_not_on_main_page()`: Processes modules NOT in sections (like books, quizzes)

**Key files are processed at `result_builder.py:163-197` via `_get_files_not_on_main_page()`**, not through the section modules list.

#### 5. Book Module Dual-API Approach

The Book module uses a sophisticated dual approach:

- **Mobile API** (`core_course_get_contents`): Fetches chapter metadata and file URLs
- **Print Book API** (Playwright): Fetches merged single-page HTML with complete content
- **Chapter HTML Fetching**: Downloads individual chapter HTML from fileurl with token auth
- **Video Extraction**: Extracts embedded Kaltura/HelixMedia videos from chapter HTML
- **Type Setting**: Chapter content set as `type='html'` to trigger URL extraction

**Critical implementation details in `moodle_dl/moodle/mods/book.py:137-163`:**
- Mobile API's `content` field only has short snippets (e.g., "Application Types")
- Must fetch full HTML from `fileurl` using aiohttp with token to extract embedded videos
- Videos are processed through standard URL extraction pipeline

### Key Abstractions

#### File Object (`types.py`)
Represents a downloadable resource with:
- Metadata: `module_id`, `section_name`, `content_fileurl`, `content_filename`
- State flags: `modified`, `deleted`, `moved`, `notified`
- Content variants: `html_content`, `text_content`, `content`
- Download info: `content_type`, `content_filesize`, `saved_to`, `time_stamp`

#### MoodleMod Base Class
Template for module handlers:
- `MOD_NAME`, `MOD_PLURAL_NAME` constants
- `real_fetch_mod_entries()` - async fetch from API
- `download_condition()` - config-based filtering
- `get_module_in_core_contents()` - helper for API data extraction

### Important Technical Constraints

#### Moodle Mobile API Limitations

The Mobile API (`core_course_get_contents`) has significant limitations:

1. **LTI/External modules**: Only provides launch URL, not actual content
   - Kalvidres (Kaltura videos): Requires HTML scraping + custom extractor
   - HelixMedia videos: Requires HTML scraping + custom extractor
   - External LTI tools: May not provide content URLs

2. **Activity descriptions**: Not included in API responses
   - Must parse HTML from description fields
   - Requires cookie authentication for embedded content

3. **Book modules**: Metadata only
   - API returns chapter titles and file URLs
   - Full HTML content requires separate fetching from fileurl
   - Print Book requires Playwright for merged HTML

4. **Dynamic content**: Not accessible via API
   - Quiz questions/answers
   - Forum posts
   - Assignment submissions (requires separate API calls)

See `MOODLE_API_LIMITATIONS.md` for comprehensive details.

#### Cookie Management

- **Path**: `~/.moodle-dl/Cookies.txt` (Netscape format)
- **Auto-refresh**: MoodleSession cookies refresh via `tool_mobile_get_autologin_key` API
- **Manual SSO cookies**: Microsoft/SSO cookies (buid, fpc) require browser export
- **Export tool**: `python3 export_browser_cookies.py` for browser cookie extraction
- **Integration**: `CookieManager` class handles auto-retry on cookie expiration

### Configuration Files

**Main config**: `~/.moodle-dl/config.json`
```json
{
  "moodle_url": "https://...",
  "token": "...",                    // Mobile API token
  "privatetoken": "...",             // Alternative token
  "download_course_ids": [...],      // Whitelist mode
  "download_books": true,            // Per-module toggles
  "download_submissions": true,
  "download_also_with_cookie": true,
  "preferred_browser": "firefox",
  ...
}
```

**Database**: `~/.moodle-dl/moodle_state.db` (SQLite)
- Tracks files by `course_id`, `module_id`, `content_fileurl`
- Change detection via hash/timemodified comparison
- Supports states: new, modified, moved, deleted

## Common Development Tasks

### Adding a New Module Handler

1. Create a new file in `moodle_dl/moodle/mods/` (e.g., `newmod.py`)
2. Inherit from `MoodleMod` base class
3. Define `MOD_NAME` and `MOD_PLURAL_NAME` constants
4. Implement `real_fetch_mod_entries()` for API fetching
5. Implement `download_condition()` for config-based filtering
6. Module will be auto-discovered via `ALL_MODS` list in `moodle_dl/moodle/mods/__init__.py`

### Debugging API Issues

1. Enable verbose logging: `moodle-dl --verbose --log-to-file`
2. Check logs in `~/.moodle-dl/MoodleDL.log`
3. Use test script: `python test_moodle_api.py`
4. Enable response logging: `--log-responses` (creates `responses.log`)
5. For Playwright issues: Debug HTML saved to `/tmp/`

### Debugging Download Issues

1. Check download task execution in `moodle_dl/downloader/task.py`
2. Verify file state in database: `sqlite3 ~/.moodle-dl/moodle_state.db`
3. Check cookie validity: `cat ~/.moodle-dl/Cookies.txt`
4. Test cookie extraction: `python export_browser_cookies.py`

### Working with Kaltura/HelixMedia Videos

Videos embedded in book chapters or descriptions use LTI (Learning Tools Interoperability):

1. **Detection**: Look for `/filter/kaltura/lti_launch.php` or `/filter/helixmedia/lti_launch.php` URLs
2. **Extraction**: Entry ID extracted from URL using regex: `r'entryid[/%]([^/%&]+)'`
3. **Conversion**: Convert to direct URL format:
   - Kaltura: `https://{domain}/browseandembed/index/media/entryid/{entry_id}`
   - HelixMedia: `https://{domain}/mod/helixmedia/view.php?id={entry_id}`
4. **Module type**: Set `module_modname` to `cookie_mod-kalvidres` or `cookie_mod-helixmedia`
5. **Custom extractors**: Located in `moodle_dl/downloader/extractors/`

**Critical code location**: `result_builder.py:292-335` handles Kaltura LTI URL conversion.

## Fork-Specific Modifications

This fork includes several customizations:

1. **Chinese Localization**: All UI strings translated to Chinese
2. **Disabled yt-dlp**: Only enabled for `cookie_mod` files (bandwidth optimization)
3. **Sequential Downloads**: Parallel downloads disabled with delays between requests
4. **HTML Shortcuts**: Creates shortcuts for HTML pages instead of downloading source
5. **Improved Wizards**: Back/forward navigation, different checkbox symbols

## Important File Locations

### Entry Points
- `moodle-dl` - Main executable script
- `moodle_dl/main.py` - Application entry point

### Core Logic
- `moodle_dl/moodle/moodle_service.py` - Main orchestration
- `moodle_dl/moodle/result_builder.py` - File aggregation and URL extraction
- `moodle_dl/moodle/core_handler.py` - Core API calls
- `moodle_dl/downloader/download_service.py` - Async download orchestrator
- `moodle_dl/downloader/task.py` - Individual download task execution

### Module Handlers
- `moodle_dl/moodle/mods/book.py` - Book module (Mobile API-first approach with improved directory structure)
  - **Recently Improved** (Nov 2025): Complete redesign following "Mobile API-first" principle
  - **Design Principle**: All chapter files and content → Mobile API. Only Print Book HTML → Playwright (not in API)
  - Chapters organized by title instead of ID (e.g., `01 - Chapter 1 - Introduction/`)
  - All chapter content (HTML, videos, attachments) downloaded via core_course_get_contents API
  - Kaltura videos extracted from API-returned HTML and converted to standard format
  - Print Book HTML fetched via Playwright but modified to link to local API-downloaded files
  - See documentation: `BOOK_MODULE_IMPROVEMENTS_SUMMARY.md`
- `moodle_dl/moodle/mods/assign.py` - Assignments with submissions
- `moodle_dl/moodle/mods/forum.py` - Forum posts and attachments
- `moodle_dl/moodle/mods/quiz.py` - Quiz questions and feedback

### Documentation
- `README.md` - Project overview and installation
- `DOCUMENTATION_INDEX.md` - Navigation to all docs
- `AUTHENTICATION.md` - Detailed auth guide
- `MOODLE_API_LIMITATIONS.md` - API constraints
- `MODULE_TYPE_SUPPORT.md` - Module handling details
- **Book Module Documentation** (Nov 2025):
  - `BOOK_MODULE_IMPROVEMENT_PLAN.md` - Design and rationale
  - `BOOK_MODULE_IMPROVEMENTS_SUMMARY.md` - Complete implementation details
  - `BOOK_MODULE_TESTING_GUIDE.md` - Testing and verification steps
  - `BOOK_MODULE_QUICK_REFERENCE.md` - Quick lookup guide

## Testing Notes

- Test directory: `tests/` (minimal coverage currently)
- Test scripts in root for manual testing
- No automated CI/CD configured
- Manual testing workflow: modify code → reinstall → run with `--verbose` → check logs
