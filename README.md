<div align="center">
    <br>
    <h2>Moodle-DL</h2>
    A command-line tool for batch-downloading Moodle course materials.
    <br>
    This maintenance fork supports Chinese / English initialization, uses headful SSO login by default, and includes several download-behavior adjustments.
</div>

---

`moodle-dl` is a console application for downloading the day-to-day course content you need from Moodle. It supports incremental downloads, notifications, course filtering, many Moodle activity modules, and external links or files. During first-time initialization, it asks you to choose Chinese or English and then shows the initialization prompts in that language.

## Features

- Downloads course files, assignments, assignment submissions, Book content, calendar events, forums, Workshop, Lesson, Quiz, descriptions, and more.
- Handles external links and files such as OpenCast, YouTube, Sciebo, OwnCloud, Kaltura, Helixmedia, and Google Drive.
- Supports incremental downloads: later runs only download new or changed content.
- Supports notifications via Telegram, Discord, XMPP, email, and other services. Notification setup is no longer shown during first-time initialization; configure it separately when needed.
- Provides a Chinese / English initialization wizard and CLI-based configuration tools.
- Uses a visible browser window for SSO by default, which helps with account selection, MFA, and browser-cookie reuse.
- Saves KCL Leganto reading lists as PDF by opening the list menu and using the page's `Print list` action.
- Downloads courses you are enrolled in, plus public courses visible to your account.

Fork source: <https://github.com/QilanLin/Moodle-DL>. Upstream project and historical discussions: <https://github.com/C0D3D3V/Moodle-DL/issues>

## Installation

> Note: this is a source-maintained fork with bilingual initialization and behavior changes. Install it from source instead of relying on PyPI.

### Requirements

- Python `>= 3.7`
- Use the same Python interpreter that you plan to run `moodle-dl` with, for example `python3 -m pip ...`.

### Install From Source

```bash
git clone https://github.com/QilanLin/Moodle-DL.git
cd Moodle-DL
python3 -m pip install -e .
```

This uses an editable install:

- After `git pull`, you usually do not need to reinstall the package.
- Local code changes take effect immediately.

### SSO Prerequisites

You can skip this section if you only use normal username/password login or a manually copied token. If you want to use `--sso`, browser-cookie import, or automatic API-token extraction, install the browser-automation dependencies first:

```bash
python3 -m pip install playwright browser-cookie3
```

Then install the Playwright browser runtime. For Firefox:

```bash
python3 -m playwright install firefox
```

If you mainly use Chrome / Edge, install the Chromium runtime:

```bash
python3 -m playwright install chromium
```

`--init --sso` opens a visible browser window by default. Only use `MOODLE_DL_HEADLESS=1` explicitly for servers, CI, or other environments without a desktop session.

Leganto reading-list PDF export uses Playwright's Chromium PDF support, so install Chromium even if you normally use Firefox for SSO.

### Reliable Ways To Run

Some machines can install the package successfully but still fail to find the `moodle-dl` command because of `PATH`, conda, virtualenv, or user script directory settings.

This repository includes an executable `moodle-dl` script, so the most reliable way to run it from the project directory is:

```bash
./moodle-dl --help
```

To bypass `PATH` entirely, run the module directly:

```bash
python3 -m moodle_dl.main --help
```

If the package is installed but the command is still not found, check:

```bash
which python3
python3 -m pip --version
python3 -m pip show moodle-dl
python3 -c "import shutil; print(shutil.which('moodle-dl'))"
```

Common causes:

- `pip` installed the package into a different Python environment.
- The script was installed into `~/.local/bin` or a virtualenv `bin/` directory that is not in `PATH`.
- You typed `moodle-dl` inside the project directory, but your shell does not search the current directory by default. Use `./moodle-dl` instead.

### Windows Notes

On Windows, use PowerShell or CMD. Avoid `mintty`, `MINGW`, and similar terminals.

If dependency compilation fails, you may need to install Visual C++ Build Tools.

## Quick Start

### Initialize Configuration

`--init` and `--init --sso` first show a language picker. Choose Chinese or English.

Normal login:

```bash
./moodle-dl --init
```

If your institution uses SSO, the default flow opens a visible browser window so you can select the right account, complete MFA, and reuse browser cookies:

```bash
./moodle-dl --init --sso
```

You can also use the `python -m` form:

```bash
python3 -m moodle_dl.main --init --sso
```

If you really need headless mode, for example on a server or in CI, set it explicitly:

```bash
MOODLE_DL_HEADLESS=1 ./moodle-dl --init --sso
```

### Start Downloading

```bash
./moodle-dl
```

### Show Help

```bash
./moodle-dl --help
```

## Common Commands

The quick start already covers first-time initialization, SSO initialization, downloading, and help. These commands are useful for later maintenance:

| Scenario | Command |
| --- | --- |
| Open the configuration wizard | `./moodle-dl --config` |
| Get a new token after the saved token expires | `./moodle-dl --new-token` |
| Get a new token through SSO | `./moodle-dl --new-token --sso` |
| Refresh browser cookies | `./moodle-dl --refresh-cookies` |
| Retry failed downloads | `./moodle-dl --retry-failed` |
| Set the download directory | `./moodle-dl --path /your/download/path` |
| Reset downloaded-file state | `./moodle-dl --reset-downloaded-files` |
| Reset downloaded-file state, Chinese alias | `./moodle-dl --重置下载文件` |

### Notification Configuration

First-time initialization no longer shows the notification-service selection menu. Configure notification services separately when needed:

```bash
./moodle-dl --change-notification-mail
./moodle-dl --change-notification-telegram
./moodle-dl --change-notification-discord
./moodle-dl --change-notification-ntfy
./moodle-dl --change-notification-xmpp
```

## Usage Notes

`moodle-dl` mainly relies on the Moodle Mobile API. If your Moodle site disables the API used by the official Moodle app, this tool cannot connect normally.

If you do not want to use the current working directory as the download directory, pass `--path` explicitly.

### `--init`

- Creates the initial configuration.
- The CLI wizard first asks you to choose Chinese or English, then walks you through the setup.
- Add `--sso` if your institution uses SSO.
- First-time initialization does not configure notifications. Use the `--change-notification-*` commands for notification setup.
- If Moodle rejects the saved token later, use `--new-token` to obtain a new one.
- For automated login, you can also provide `--username`, `--password`, or `--token`.

### `moodle-dl`

- After configuration is complete, running `moodle-dl` is usually enough to download all course content and print the result.

### `--config`

- Opens the CLI configuration wizard.
- Lets you change most common settings, including:
  - which courses to download
  - course directory names
  - whether to create per-course subdirectory structures
  - whether to download submissions, descriptions, links inside descriptions, database activities, quizzes, lessons, workshops, forums, and similar content
  - whether to download external files
  - whether to download content that depends on browser cookies

Not every advanced setting is exposed in the wizard. For finer control, inspect the configuration file directly.

## Main Changes In This Fork

Compared with the upstream project, this fork includes:

- Chinese / English selection for initialization. Most maintenance prompts still prioritize the Chinese user experience.
- A smoother interactive configuration experience, including forward/back navigation in the wizard.
- Headful SSO login by default, which is better for account selection, MFA, and browser-cookie migration.
- Notification setup skipped during first-time initialization, so users who do not need notifications are not interrupted by extra questions.
- Download-behavior changes:
  - `yt-dlp` is only enabled for embedded-video cases that actually need browser cookies, such as `kalvidres`, `helixmedia`, and some `LTI` content.
  - Normal web links prefer shortcut generation instead of blindly saving full page source.
  - Real external files are downloaded as files when possible.
- Stronger SSO, cookie, and database-state tracking support.

## FAQ

### 1. `moodle-dl: command not found`

Prefer one of these forms:

```bash
./moodle-dl --help
```

or:

```bash
python3 -m moodle_dl.main --help
```

Only debug your global `PATH` setup if you specifically want to run `moodle-dl` as a global command.

### 2. SSO Login Fails

Headful mode is already the default:

```bash
./moodle-dl --init --sso
```

If you previously set `MOODLE_DL_HEADLESS=1` or `MOODLE_DL_HEADFUL=0`, remove that environment variable first.

If the error includes `Executable doesn't exist` or `ms-playwright`, the Playwright browser runtime is usually missing:

```bash
python3 -m playwright install firefox
```

If the error says `browser-cookie3` or `playwright` is missing, install the dependencies:

```bash
python3 -m pip install playwright browser-cookie3
```

If your browser is logged into multiple Microsoft, Google, or institution accounts:

- Manually select the correct account in the browser first.
- Or use a separate browser profile.

### 3. Cookies Expired

Refresh them:

```bash
./moodle-dl --refresh-cookies
```

Or initialize again:

```bash
./moodle-dl --init --sso
```

### 4. Do I Need To Reinstall After `git pull`?

If you installed with:

```bash
python3 -m pip install -e .
```

then you usually do not need to reinstall after `git pull`.

## Configuration And Log Files

Common file locations:

- Configuration: `~/.moodle-dl/config.json`
- Log file: `~/.moodle-dl/MoodleDL.log`
- State database: `~/.moodle-dl/moodle_state.db`

Depending on your custom run directory, corresponding `config.json`, `MoodleDL.log`, and `moodle_state.db` files may also be created in the current directory.

## Development And Tests

Run tests:

```bash
python3 -m pytest
```

Run coverage, if `pytest-cov` is installed:

```bash
python3 -m pytest --cov=moodle_dl --cov-report=term-missing
```

## Security Notes

- Moodle account passwords are not stored long-term in plaintext during the standard configuration flow, but tokens, cookies, and notification credentials are still sensitive.
- The token stored in `config.json` is a high-sensitivity credential and must not be shared.
- If cookie-related features are enabled, cookie files or session data in the database are also high-sensitivity credentials.
- Email / XMPP and similar notification credentials may be stored in plaintext. Use dedicated notification accounts instead of your primary accounts where possible.

## Alternative Downloaders

These projects have similar goals to `moodle-dl`, but target different institutions, technologies, or workflows:

- [webeep-sync](https://github.com/toto04/webeep-sync#english-version)
  - Written in Node.js
  - Provides a GUI
  - Targets Politecnico di Milano Moodle

- [syncMyMoodle](https://github.com/Romern/syncMyMoodle)
  - Similar goal to `moodle-dl`
  - Targets RWTH Aachen University Moodle

- [edu-sync](https://github.com/mkroening/edu-sync)
  - Written in Rust
  - Focuses on performance

- [tum-moodle-downloader](https://github.com/omareldeeb/tum-moodle-downloader)
  - Uses web scraping rather than the Moodle Mobile API
  - Provides more fine-grained download commands
  - Targets Technical University of Munich Moodle

- [moodle-buddy](https://github.com/marcelreppi/moodle-buddy)
  - Firefox / Chrome extension
  - Supports batch downloads and notifications

- [moodle-downloader](https://github.com/harsilspatel/moodle-downloader)
  - Chrome extension
  - Batch-downloads Moodle resources

- [Orga Bot](https://github.com/YoshiiPlayzz/orga_bot)
  - Based on `moodle-dl`
  - Sends Moodle files through Discord

- [discord-moodle-bot](https://github.com/tjarbo/discord-moodle-bot)
  - Provides Discord notification support for Moodle courses

## Contributing

Issues and pull requests are welcome. This repository does not currently include a separate `CONTRIBUTING.md`; keep changes focused and run the relevant tests before submitting.

## License

This project is licensed under GPL-3.0. See `LICENSE` for details.
