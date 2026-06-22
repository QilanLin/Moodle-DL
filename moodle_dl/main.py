# -*- coding: utf-8 -*-
import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import traceback
from logging.handlers import RotatingFileHandler
from shutil import which
from typing import Dict, List, Optional, Union

import colorlog
import requests  # noqa: F401 pylint: disable=unused-import
import sentry_sdk
import urllib3

try:
    # In unix readline needs to be loaded so that arrow keys work in input
    import readline  # pylint: disable=unused-import # noqa: F401
except ImportError:
    pass

from colorama import just_fix_windows_console

from moodle_dl.cli import (
    ConfigWizard,
    DatabaseManager,
    MoodleWizard,
    NotificationsWizard,
    init_config,
)
from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.download_service import DownloadService
from moodle_dl.downloader.fake_download_service import FakeDownloadService
from moodle_dl.downloader.task import Task
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.network_throttle import NetworkThrottle
from moodle_dl.notifications import get_all_notify_services
from moodle_dl.types import Course, File, MoodleDlOpts
from moodle_dl.utils import PathTools as PT
from moodle_dl.utils import ProcessLock, check_debug
from moodle_dl.version import __version__


class ReRaiseOnError(logging.StreamHandler):
    "A logging-handler class which allows the exception-catcher of i.e. PyCharm to intervene"

    def emit(self, record):
        if hasattr(record, 'exception'):
            raise record.exception


def choose_task(config: ConfigHelper, opts: MoodleDlOpts) -> None:
    if opts.add_all_visible_courses:
        ConfigWizard(config, opts).interactively_add_all_visible_courses()
    elif opts.change_notification_mail:
        NotificationsWizard(config, opts).interactively_configure_mail()
    elif opts.change_notification_telegram:
        NotificationsWizard(config, opts).interactively_configure_telegram()
    elif opts.change_notification_discord:
        NotificationsWizard(config, opts).interactively_configure_discord()
    elif opts.change_notification_ntfy:
        NotificationsWizard(config, opts).interactively_configure_ntfy()
    elif opts.change_notification_xmpp:
        NotificationsWizard(config, opts).interactively_configure_xmpp()
    elif opts.config:
        ConfigWizard(config, opts).interactively_acquire_config()
    elif opts.delete_old_files:
        DatabaseManager(config, opts).delete_old_files()
    elif opts.manage_database:
        DatabaseManager(config, opts).interactively_manage_database()
    elif opts.reset_downloaded_files or opts.reset_downloaded_files_cn:
        DatabaseManager(config, opts).reset_all_downloaded_files()
    elif opts.new_token:
        MoodleWizard(config, opts).interactively_acquire_token(use_stored_url=True)
    elif opts.refresh_cookies:
        refresh_cookies_only(config, opts)
    elif opts.resume:
        resume_downloads(config, opts)
    elif opts.retry_failed:
        retry_failed_downloads(config, opts)
    else:
        run_main(config, opts)


def _initialize_retry_database(config: ConfigHelper, opts: MoodleDlOpts) -> StateRecorder:
    """Initialize database for retry operation.
    
    Args:
        config: ConfigHelper instance
        opts: MoodleDlOpts instance
    
    Returns:
        StateRecorder instance
    """
    return StateRecorder(config, opts)


def _get_failed_download_statistics(database: StateRecorder) -> Dict[int, Dict]:
    """Get summary of failed downloads from database.
    
    Args:
        database: StateRecorder instance
        
    Returns:
        Dictionary with failed files summary. Returns an empty dictionary if no failures
    """
    return database.get_failed_files_summary()


def _print_failed_statistics_header(summary: Dict[int, Dict]) -> None:
    """Print header and summary statistics of failed downloads.
    
    Args:
        summary: Dictionary with failed files information
    """
    total_failed_files = sum(info['failed_count'] for info in summary.values())
    total_failures = sum(info['total_failures'] for info in summary.values())

    logging.info('')
    logging.info('=' * 60)
    logging.info(f'找到 {total_failed_files} 个下载失败的文件（总失败次数：{total_failures}）')
    logging.info('=' * 60)


def _print_failed_statistics_details(summary: Dict[int, Dict]) -> None:
    """Print detailed statistics for each course.
    
    Args:
        summary: Dictionary with failed files information
    """
    for course_id, info in summary.items():
        logging.info(f"课程 ID {course_id} ({info['course_fullname']}):")
        logging.info(f"  - 失败文件数：{info['failed_count']}")
        logging.info(f"  - 总失败次数：{info['total_failures']}")
        logging.info(f"  - 最大连续失败：{info['max_consecutive']}")

    logging.info('=' * 60)
    logging.info('')


def _load_failed_files_as_courses(database: StateRecorder) -> List[Course]:
    """Load failed files from database and convert to Course objects.
    
    Args:
        database: StateRecorder instance
        
    Returns:
        List of Course objects with failed files
    """
    from moodle_dl.types import Course
    
    courses_dict = database.get_failed_files_with_course_info()

    if not courses_dict:
        logging.warning('无法读取失败的文件，请检查数据库。')
        return []

    courses = []
    for course_id, course_info in courses_dict.items():
        course = Course(
            _id=course_id,
            fullname=course_info['course_fullname'],
            files=course_info['files']
        )
        courses.append(course)

    return courses


def _load_incomplete_download_files_as_courses(database: StateRecorder) -> List[Course]:
    """Load resumable incomplete downloads from the database as Course objects."""
    courses_dict = database.get_incomplete_files_with_course_info()

    if not courses_dict:
        return []

    courses = []
    for course_id, course_info in courses_dict.items():
        courses.append(
            Course(
                _id=course_id,
                fullname=course_info['course_fullname'],
                files=course_info['files'],
            )
        )

    return courses


def _file_resume_identity(file: File) -> tuple:
    if file.file_id is not None:
        return ('file_id', file.file_id)
    return (
        'path',
        file.module_id,
        file.content_type,
        file.content_filepath,
        file.content_filename,
        file.content_fileurl,
    )


def _merge_course_file_lists(primary_courses: List[Course], extra_courses: List[Course]) -> List[Course]:
    """Merge course file lists while keeping primary order and avoiding duplicate files."""
    merged_by_course_id = {}
    file_identities_by_course_id = {}
    merged_courses = []

    for course in primary_courses:
        merged_course = Course(course.id, course.fullname, list(course.files))
        merged_course.overwrite_name_with = course.overwrite_name_with
        merged_course.create_directory_structure = course.create_directory_structure
        merged_course.excluded_sections = list(course.excluded_sections)
        merged_by_course_id[course.id] = merged_course
        file_identities_by_course_id[course.id] = {
            _file_resume_identity(file) for file in merged_course.files
        }
        merged_courses.append(merged_course)

    for course in extra_courses:
        if course.id not in merged_by_course_id:
            merged_course = Course(course.id, course.fullname, [])
            merged_course.overwrite_name_with = course.overwrite_name_with
            merged_course.create_directory_structure = course.create_directory_structure
            merged_course.excluded_sections = list(course.excluded_sections)
            merged_by_course_id[course.id] = merged_course
            file_identities_by_course_id[course.id] = set()
            merged_courses.append(merged_course)

        merged_course = merged_by_course_id[course.id]
        known_file_identities = file_identities_by_course_id[course.id]
        for file in course.files:
            file_identity = _file_resume_identity(file)
            if file_identity in known_file_identities:
                continue
            merged_course.files.append(file)
            known_file_identities.add(file_identity)

    return merged_courses


def _reset_failed_files_for_retry(database: StateRecorder, courses: List[Course]) -> None:
    """Reset status of all failed files to pending for retry.
    
    Args:
        database: StateRecorder instance
        courses: List of Course objects with files to reset
    """
    logging.info('正在重置失败文件状态...')
    for course in courses:
        for file in course.files:
            database.reset_failed_file_for_retry(file, course.id)


def _create_downloader(
    courses: List[Course],
    config: ConfigHelper,
    opts: MoodleDlOpts,
    database: StateRecorder,
    network_throttle: Optional[NetworkThrottle] = None,
) -> Union[DownloadService, FakeDownloadService]:
    """Create appropriate downloader instance based on options.
    
    Args:
        courses: List of Course objects to download
        config: ConfigHelper instance
        opts: MoodleDlOpts instance
        database: StateRecorder instance
        
    Returns:
        DownloadService or FakeDownloadService instance
    """
    if opts.without_downloading_files:
        return FakeDownloadService(courses, config, opts, database)
    else:
        return DownloadService(courses, config, opts, database, network_throttle=network_throttle)


def _print_retry_results(new_failed_downloads: List[Task]) -> None:
    """Print results of retry operation.

    Args:
        new_failed_downloads: List of failed tasks after retry
    """
    logging.info('')
    logging.info('=' * 60)
    if len(new_failed_downloads) > 0:
        logging.warning(f'重试完成，仍有 {len(new_failed_downloads)} 个文件下载失败。')
        for task in new_failed_downloads:
            logging.warning(f'  - {task.file.content_filename}: {task.status.get_error_text()}')
    else:
        logging.info('✓ 所有失败的文件已成功重新下载！')
    logging.info('=' * 60)


def retry_failed_downloads(config: ConfigHelper, opts: MoodleDlOpts):
    """
    Retry all failed downloads.

    Process pipeline:
    1. Initialize database
    2. Get failed download statistics
    3. Print statistics
    4. Load failed files as courses
    5. Reset file status
    6. Create and run downloader
    7. Print results

    Important: while the downloader is running, we temporarily clear
    `manually_specified_course_ids` from the in-memory config so that
    DownloadService.gen_all_tasks() does NOT re-enqueue all files from
    the user's manually specified courses (which would otherwise cause
    --retry-failed to re-download hundreds of already-successful files,
    producing spurious `*_01`, `*_02` duplicates on disk). The original
    list is restored even if the downloader raises.
    """
    logging.info('正在查询下载失败的文件...')

    # Step 1: Initialize database
    database = StateRecorder(config, opts)

    # Step 2: Get statistics
    summary = _get_failed_download_statistics(database)

    if not summary:
        logging.info('✓ 没有下载失败的文件！')
        return

    # Step 3: Print statistics
    _print_failed_statistics_header(summary)
    _print_failed_statistics_details(summary)

    # Step 4: Load failed files as courses
    courses = _load_failed_files_as_courses(database)

    if not courses:
        return

    # Step 5: Reset file status
    _reset_failed_files_for_retry(database, courses)

    logging.info('开始重载失败的文件...')

    # Step 6: Create downloader and run.
    # The downloader's gen_all_tasks() walks config.get_manually_specified_course_ids()
    # to also re-fetch those courses' files via the Web API. That is
    # desirable for the default `./moodle-dl` run but it is *not* what
    # users expect from `--retry-failed`. We therefore snapshot the
    # current list, clear it for the duration of the run, and restore
    # it afterwards (even on exception).
    original_manually_specified_ids = list(config.get_manually_specified_course_ids())
    needs_restore = bool(original_manually_specified_ids)
    if needs_restore:
        config.set_manually_specified_course_ids([])

    try:
        downloader = _create_downloader(courses, config, opts, database, NetworkThrottle())
        downloader.run()

        new_failed_downloads = downloader.get_failed_tasks()

        # Step 7: Print results
        _print_retry_results(new_failed_downloads)
    finally:
        if needs_restore:
            config.set_manually_specified_course_ids(original_manually_specified_ids)


def resume_downloads(config: ConfigHelper, opts: MoodleDlOpts):
    """Resume downloads from the saved database state."""
    logging.info('继续上次下载。 / Resuming the previous download.')
    run_main(config, opts)


def refresh_cookies_only(config: ConfigHelper, opts: MoodleDlOpts):
    """
    只刷新浏览器 cookies，不重置任何文件下载状态。

    复用 `--init --sso` 的 `refresh_sso_cookies()`：先 Playwright SSO（默认有头，
    支持多账号 / MFA / 验证码交互），失败再回退到从浏览器读取 cookies 直接写入
    AuthSessionManager 数据库。

    Process:
    1. 检查 Moodle URL 配置
    2. 让用户选择浏览器
    3. 调用统一的 refresh_sso_cookies() 入口
    4. 成功时保存浏览器偏好
    """
    from moodle_dl.cli.authenticators import refresh_sso_cookies
    from moodle_dl.utils import Cutie, Log, PathTools as PT

    Log.info('')
    Log.info('=' * 80)
    Log.info('🔄 刷新浏览器 Cookies（不影响文件下载状态）')
    Log.info('=' * 80)
    Log.info('')

    # Step 1: 检查 Moodle URL
    moodle_url = config.get_moodle_URL()
    if moodle_url is None:
        Log.error('❌ 错误：未找到 Moodle URL 配置')
        Log.info('   请先运行: moodle-dl --init')
        return

    moodle_domain = moodle_url.domain
    cookies_path = PT.get_cookies_path(config.get_misc_files_path())

    Log.info(f'📍 Moodle 域名: {moodle_domain}')
    Log.info('')

    # Step 2: 让用户选择浏览器
    Log.info('请选择要导出 cookies 的浏览器：')
    browsers = ['Chrome', 'Firefox', 'Edge', 'Safari', 'Chromium', 'Brave', 'Opera', 'Vivaldi', '自动检测']
    browser_map = {
        'Chrome': 'chrome',
        'Firefox': 'firefox',
        'Edge': 'edge',
        'Safari': 'safari',
        'Chromium': 'chromium',
        'Brave': 'brave',
        'Opera': 'opera',
        'Vivaldi': 'vivaldi',
        '自动检测': None,
    }

    # 检查用户上次选择的浏览器
    try:
        preferred_browser_key = config.get_property('preferred_browser')
        for key, value in browser_map.items():
            if value == preferred_browser_key:
                Log.info(f'💡 上次使用的浏览器: {key}')
                break
    except (ValueError, KeyError):
        pass

    browser_choice = Cutie.select(browsers, deselected_prefix='  ', selected_prefix='→ ')
    selected_browser = browser_map[browsers[browser_choice]]
    # "自动检测" 时 SSO 阶段 fallback 到 firefox，与 cookie_manager 默认一致
    sso_browser = selected_browser or 'firefox'

    # Step 3: 统一入口 - 与 --init --sso 共享同一份 SSO + fallback 实现
    success = refresh_sso_cookies(
        moodle_domain=moodle_domain,
        cookies_path=cookies_path,
        preferred_browser=sso_browser,
        auth_manager=config.get_auth_manager(),
    )

    if success:
        Log.success('')
        Log.success('✅ Cookies 刷新成功！')
        Log.info('')
        Log.info('💡 下一步：')
        Log.info('   如果有下载失败的文件，可以运行：')
        Log.info('   moodle-dl --retry-failed')
        Log.info('')

        if selected_browser:
            config.set_property('preferred_browser', selected_browser)
            Log.info(f'✅ 已保存浏览器选择（{browsers[browser_choice]}），将用于下次自动刷新')
    else:
        Log.error('')
        Log.error('❌ Cookies 刷新失败（SSO 自动登录与浏览器导出都未成功）')
        Log.info('')
        Log.info('💡 故障排查：')
        Log.info('   1. 确保在浏览器中已登录 Moodle')
        Log.info('   2. 尝试选择其他浏览器')
        Log.info('   3. 检查 SSO cookies 是否已过期')


def connect_sentry(config: ConfigHelper) -> bool:
    "Return True if connected"
    try:
        sentry_dsn = config.get_property('sentry_dsn')
        if sentry_dsn:
            sentry_sdk.init(sentry_dsn)
            return True
    except (ValueError, sentry_sdk.utils.BadDsn, sentry_sdk.utils.ServerlessTimeoutWarning):
        pass
    return False


def _warn_if_buggy_files(database, config, opts):
    """Detect files affected by the workspace-isolation bug
    (commit d1ae09d) that were downloaded before the fix
    and are at a non-module-dir location. Print a one-line
    warning so the user knows they can run the
    repair_paths tool to fix the on-disk layout.

    This is purely informational. The downloader itself
    (post-d1ae09d) puts new downloads in the correct
    location automatically, so the user can keep using
    moodle-dl without any intervention. The warning is
    only useful for cleaning up legacy buggy files.
    """
    try:
        from moodle_dl.downloader.task_path_repair import find_buggy_files
    except ImportError:
        return  # task_path_repair is in moodle_dl/ but may not import

    try:
        workspace = config.get_workspace()
    except (AttributeError, Exception):
        return  # no workspace configured

    if not workspace or not os.path.isdir(workspace):
        return

    try:
        conn = sqlite3.connect(str(database.db_path))
    except (AttributeError, Exception):
        return

    try:
        buggy = find_buggy_files(conn, workspace=workspace)
    except Exception:
        return
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if buggy:
        # Categorize: how many exist on disk, how many are
        # truly unfixable (404 from Moodle).
        from collections import Counter
        buggy_by_course = Counter()
        for b in buggy:
            key = (b['course_fullname'], b['section_name'])
            buggy_by_course[key] += 1

        n_chapters = len(buggy_by_course)
        logging.warning(
            'Detected %d files affected by the workspace-isolation '
            'bug in %d (course, section) groups. These are files '
            'downloaded before commit d1ae09d and are at a '
            'non-module-dir location on disk; HTML referencing '
            'them will not render in the browser. New downloads '
            'are unaffected. To fix the on-disk layout, run:\n'
            '    python -m moodle_dl.downloader.repair_paths '
            '--db <path-to-moodle_state.db> --workspace <workspace>',
            len(buggy), n_chapters,
        )


def run_main(config: ConfigHelper, opts: MoodleDlOpts):
    sentry_connected = connect_sentry(config)
    notify_services = get_all_notify_services(config)

    try:
        network_throttle = NetworkThrottle()
        moodle = MoodleService(config, opts, network_throttle=network_throttle)

        logging.debug('Checking for changes for the configured Moodle-Account....')
        database = StateRecorder(config, opts)
        changed_courses = asyncio.run(moodle.fetch_state(database))

        # Check for buggy files from the workspace-isolation bug
        # (commit d1ae09d fixed the downloader, but files downloaded
        # before that commit may be at the wrong path on disk).
        # Warn the user if any are found.
        _warn_if_buggy_files(database, config, opts)

        if opts.resume:
            incomplete_courses = _load_incomplete_download_files_as_courses(database)
            if incomplete_courses:
                incomplete_count = sum(len(course.files) for course in incomplete_courses)
                logging.info(
                    '找到 %d 个未完成下载，将加入本次续传。 / Found %d incomplete download(s) to resume.',
                    incomplete_count,
                    incomplete_count,
                )
                changed_courses = _merge_course_file_lists(changed_courses, incomplete_courses)
            else:
                logging.info('未找到单独的未完成下载记录，将继续下载扫描到的剩余文件。')

        if opts.log_responses:
            logging.info("All JSON-responses from Moodle have been written to the responses.log file.")
            return

        logging.debug('Start downloading changed files...')

        if opts.without_downloading_files:
            downloader = FakeDownloadService(changed_courses, config, opts, database)
        else:
            downloader = DownloadService(changed_courses, config, opts, database, network_throttle=network_throttle)
        downloader.run()
        failed_downloads = downloader.get_failed_tasks()

        changed_courses_to_notify = database.changes_to_notify()

        if len(changed_courses_to_notify) > 0:
            for service in notify_services:
                service.notify_about_changes_in_moodle(changed_courses_to_notify)

            database.notified(changed_courses_to_notify)

        else:
            logging.info('为已配置的 Moodle 账户未找到变化。')

        if len(failed_downloads) > 0:
            for service in notify_services:
                service.notify_about_failed_downloads(failed_downloads)

    except BaseException as base_err:
        if sentry_connected:
            sentry_sdk.capture_exception(base_err)

        short_error = str(base_err)
        if not short_error or short_error.isspace():
            short_error = traceback.format_exc(limit=1)

        for service in notify_services:
            service.notify_about_error(short_error)

        raise base_err


def setup_logger(opts: MoodleDlOpts):
    file_log_handler = RotatingFileHandler(
        PT.make_path(opts.log_file_path, 'MoodleDL.log'),
        mode='a',
        maxBytes=1 * 1024 * 1024,
        backupCount=2,
        encoding='utf-8',
        delay=0,
    )
    file_log_handler.setFormatter(
        logging.Formatter('%(asctime)s  %(levelname)s  {%(module)s}  %(message)s', '%Y-%m-%d %H:%M:%S')
    )
    stdout_log_handler = colorlog.StreamHandler()
    if sys.stdout.isatty() and not opts.verbose:
        stdout_log_handler.setFormatter(colorlog.ColoredFormatter('%(log_color)s%(asctime)s %(message)s', '%H:%M:%S'))
    else:
        stdout_log_handler.setFormatter(
            colorlog.ColoredFormatter(
                '%(log_color)s%(asctime)s  %(levelname)s  {%(module)s}  %(message)s', '%Y-%m-%d %H:%M:%S'
            )
        )

    app_log = logging.getLogger()
    if opts.quiet:
        file_log_handler.setLevel(logging.ERROR)
        app_log.setLevel(logging.ERROR)
        stdout_log_handler.setLevel(logging.ERROR)
    elif opts.verbose:
        file_log_handler.setLevel(logging.DEBUG)
        app_log.setLevel(logging.DEBUG)
        stdout_log_handler.setLevel(logging.DEBUG)
    else:
        file_log_handler.setLevel(logging.INFO)
        app_log.setLevel(logging.INFO)
        stdout_log_handler.setLevel(logging.INFO)

    app_log.addHandler(stdout_log_handler)
    if opts.log_to_file:
        app_log.addHandler(file_log_handler)

    if opts.verbose:
        logging.debug('moodle-dl version: %s', __version__)
        logging.debug('python version: %s', ".".join(map(str, sys.version_info[:3])))
        ffmpeg_available = which('ffmpeg') is not None
        logging.debug('Is ffmpeg available: %s', ffmpeg_available)

    if check_debug():
        logging.info('Debug-Mode detected. Errors will be re-risen.')
        app_log.addHandler(ReRaiseOnError())

    if not opts.verbose:
        logging.getLogger("requests").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger('asyncio').setLevel(logging.WARNING)
        urllib3.disable_warnings()


def get_parser():
    def _dir_path(path):
        # Handle relative paths safely, even if current working directory doesn't exist
        try:
            # If path is absolute, use it directly
            if os.path.isabs(path):
                abs_path = path
            else:
                # For relative paths, try to get current working directory
                # If that fails (e.g., directory was deleted), use home directory as fallback
                try:
                    cwd = os.getcwd()
                except (OSError, FileNotFoundError):
                    # Current working directory doesn't exist, use home directory
                    cwd = os.path.expanduser('~')
                abs_path = os.path.normpath(os.path.join(cwd, path))
            
            # Check if the resolved path exists and is a directory
            if os.path.isdir(abs_path):
                return abs_path
            raise argparse.ArgumentTypeError(f'"{str(path)}" is not a valid path. Make sure the directory exists.')
        except (OSError, ValueError) as e:
            raise argparse.ArgumentTypeError(f'"{str(path)}" is not a valid path: {e}')

    parser = argparse.ArgumentParser(
        description=('Moodle-DL helps you download all the course files from your Moodle account.')
    )
    group = parser.add_mutually_exclusive_group()

    group.add_argument(
        '-i',
        '--init',
        dest='init',
        default=False,
        action='store_true',
        help=(
            'Create an initial configuration. A CLI configuration wizard will lead you through'
            + ' the initial configuration.'
        ),
    )

    group.add_argument(
        '-c',
        '--config',
        dest='config',
        default=False,
        action='store_true',
        help=(
            'Start the configuration utility. It allows you to make almost all available moodle-dl settings'
            + ' conveniently via the CLI configuration wizard.'
        ),
    )

    group.add_argument(
        '-nt',
        '--new-token',
        dest='new_token',
        default=False,
        action='store_true',
        help=('Obtain a new login token. Use it if the saved token gets rejected by your Moodle.'),
    )

    group.add_argument(
        '-cm',
        '--change-notification-mail',
        dest='change_notification_mail',
        default=False,
        action='store_true',
        help=('Activate / deactivate / change the settings for receiving notifications via e-mail.'),
    )

    group.add_argument(
        '-ct',
        '--change-notification-telegram',
        dest='change_notification_telegram',
        default=False,
        action='store_true',
        help=('Activate / deactivate / change the settings for receiving notifications via Telegram.'),
    )

    group.add_argument(
        '-cd',
        '--change-notification-discord',
        dest='change_notification_discord',
        default=False,
        action='store_true',
        help=('Activate / deactivate / change the settings for receiving notifications via Discord.'),
    )

    group.add_argument(
        '-cn',
        '--change-notification-ntfy',
        dest='change_notification_ntfy',
        default=False,
        action='store_true',
        help=('Activate / deactivate / change the settings for receiving notifications via ntfy.'),
    )

    group.add_argument(
        '-cx',
        '--change-notification-xmpp',
        dest='change_notification_xmpp',
        default=False,
        action='store_true',
        help=('Activate / deactivate / change the settings for receiving notifications via XMPP.'),
    )

    group.add_argument(
        '-md',
        '--manage-database',
        dest='manage_database',
        default=False,
        action='store_true',
        help=(
            'Manage the offline database. It allows you to delete entries from the database'
            + ' that are no longer available locally so that they can be downloaded again.'
        ),
    )

    group.add_argument(
        '-dof',
        '--delete-old-files',
        dest='delete_old_files',
        default=False,
        action='store_true',
        help=(
            'Delete old copies of files. It allows you to delete entries from the database'
            + ' and from local file system.'
        ),
    )
    group.add_argument(
        '-rdf',
        '--reset-downloaded-files',
        dest='reset_downloaded_files',
        default=False,
        action='store_true',
        help=(
            'Reset all downloaded files to "not downloaded" status in database. '
            + 'This allows you to re-download all files on next run.'
        ),
    )
    group.add_argument(
        '-zdcf',
        '--重置下载文件',
        dest='reset_downloaded_files_cn',
        default=False,
        action='store_true',
        help=(
            '重置数据库中所有"已下载"的文件为"未下载"状态，'
            + '这样重新运行 "moodle-dl" 时就可以重新下载这些文件。'
        ),
    )

    group.add_argument(
        '--log-responses',
        dest='log_responses',
        default=False,
        action='store_true',
        help=(
            'Generate a responses.log file in which all JSON responses from your Moodle are logged'
            + ' along with the requested URLs.'
        ),
    )

    group.add_argument(
        '--add-all-visible-courses',
        dest='add_all_visible_courses',
        default=False,
        action='store_true',
        help='Add all courses visible to the user to the configuration file.',
    )

    group.add_argument(
        '-rf',
        '--retry-failed',
        dest='retry_failed',
        default=False,
        action='store_true',
        help=(
            'Retry downloading all previously failed files. '
            + 'This will attempt to re-download files that failed in previous runs.'
        ),
    )

    group.add_argument(
        '--resume',
        dest='resume',
        default=False,
        action='store_true',
        help=(
            'Resume a previously interrupted download. This re-scans Moodle for remaining files '
            + 'and also retries resumable incomplete downloads stored in the local database.'
        ),
    )

    group.add_argument(
        '--refresh-cookies',
        dest='refresh_cookies',
        default=False,
        action='store_true',
        help=(
            'Refresh browser cookies without resetting download status. '
            + 'This will re-export cookies from your browser and update them in the database. '
            + 'Useful when your cookies have expired and you need to retry failed downloads.'
        ),
    )

    group.add_argument(
        '--version',
        action='version',
        version='moodle-dl ' + __version__,
        help='Print program version and exit',
    )

    parser.add_argument(
        '-sso',
        '--sso',
        dest='sso',
        default=False,
        action='store_true',
        help=(
            'Use SSO login instead of normal login. This flag can be used together with --init and -nt.'
            + ' You will be guided through the Single Sign On (SSO) login process'
            + ' during initialization or new token retrieval.'
        ),
    )

    parser.add_argument(
        '-u',
        '--username',
        dest='username',
        default=None,
        type=str,
        help=('Specify username to skip the query when creating a new token.'),
    )

    parser.add_argument(
        '-pw',
        '--password',
        dest='password',
        default=None,
        type=str,
        help=('Specify password to skip the query when creating a new token.'),
    )

    parser.add_argument(
        '-tk',
        '--token',
        dest='token',
        default=None,
        type=str,
        help=('Specify token to skip the interactive login procedure.'),
    )
    parser.add_argument(
        '-p',
        '--path',
        dest='path',
        default='.',
        type=_dir_path,
        help=(
            'Sets the location of the configuration, logs and downloaded files. PATH must be an'
            + ' existing directory in which you have read and write access. (default: current working directory)'
        ),
    )

    parser.add_argument(
        '-mpac',
        '--max-parallel-api-calls',
        dest='max_parallel_api_calls',
        default=10,
        type=int,
        help=('Sets the number of max parallel Moodle Mobile API calls. (default: %(default)s)'),
    )

    parser.add_argument(
        '-mpd',
        '--max-parallel-downloads',
        dest='max_parallel_downloads',
        default=5,
        type=int,
        help=('Sets the number of max parallel downloads. (default: %(default)s)'),
    )

    parser.add_argument(
        '-mpyd',
        '--max-parallel-yt-dlp',
        dest='max_parallel_yt_dlp',
        default=5,
        type=int,
        help=('Sets the number of max parallel downloads using yt-dlp. (default: %(default)s)'),
    )

    parser.add_argument(
        '-dcs',
        '--download-chunk-size',
        dest='download_chunk_size',
        default=102400,
        type=int,
        help=('Sets the chunk size in bytes used when downloading files. (default: %(default)s)'),
    )

    parser.add_argument(
        '-iye',
        '--ignore-ytdl-errors',
        dest='ignore_ytdl_errors',
        default=False,
        action='store_true',
        help=(
            'Ignore errors that occur when downloading with the help of yt-dlp.'
            + ' Thus, no further attempt will be made to download the file using yt-dlp.'
            + ' By default, yt-dlp errors are critical, so the download of the corresponding file'
            + ' will be aborted and when you run moodle-dl again, the download will be repeated.'
        ),
    )

    parser.add_argument(
        '-wdf',
        '--without-downloading-files',
        dest='without_downloading_files',
        default=False,
        action='store_true',
        help=(
            'Do not download any file. This allows the local database to be updated'
            + ' without having to download all files.'
        ),
    )

    parser.add_argument(
        '-mplw',
        '--max-path-length-workaround',
        dest='max_path_length_workaround',
        default=False,
        action='store_true',
        help=(
            'Make all paths absolute in order to workaround the max_path limitation on Windows.'
            + ' To use relative paths on Windows you should disable the max_path limitation see:'
            + ' https://docs.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation'
        ),
    )

    parser.add_argument(
        '-ais',
        '--allow-insecure-ssl',
        dest='allow_insecure_ssl',
        default=False,
        action='store_true',
        help='Allow connections to unpatched servers. Use this option if your server uses a very old SSL version.',
    )
    parser.add_argument(
        '-rik',
        '--restart-incomplete-on-kill',
        dest='restart_incomplete_on_kill',
        default=True,
        action='store_true',
        help=(
            'On Ctrl-C / SIGTERM during a download, delete the partial '
            '.part file so the next run re-downloads the file from '
            'scratch (default). Set MOODLE_DL_KEEP_INCOMPLETE_ON_KILL=1 '
            'to keep the old resume-from-byte-N behavior.'
        ),
    )
    parser.add_argument(
        '-keep',
        '--keep-incomplete-on-kill',
        dest='restart_incomplete_on_kill',
        action='store_false',
        help=(
            'Opposite of --restart-incomplete-on-kill: keep the '
            'partial .part file on Ctrl-C / SIGTERM so the next run '
            'can resume from byte N. Set MOODLE_DL_KEEP_INCOMPLETE_ON_KILL=1.'
        ),
    )
    parser.add_argument(
        '-uac',
        '--use-all-ciphers',
        dest='use_all_ciphers',
        default=False,
        action='store_true',
        help=(
            'Allow connections to servers that use insecure ciphers.'
            + ' Use this option if your server uses an insecure cipher.'
        ),
    )
    parser.add_argument(
        '-scv',
        '--skip-cert-verify',
        dest='skip_cert_verify',
        default=False,
        action='store_true',
        help='Don\'t verify TLS certificates. This option should only be used in non production environments.',
    )

    parser.add_argument(
        '-v',
        '--verbose',
        dest='verbose',
        default=False,
        action='store_true',
        help='Print various debugging information',
    )

    parser.add_argument(
        '-q',
        '--quiet',
        dest='quiet',
        default=False,
        action='store_true',
        help='Sets the log level to error',
    )

    parser.add_argument(
        '-ltf',
        '--log-to-file',
        dest='log_to_file',
        default=False,
        action='store_true',
        help='Log all output additionally to a log file called MoodleDL.log',
    )

    parser.add_argument(
        '-lfp',
        '--log-file-path',
        dest='log_file_path',
        default=None,
        type=_dir_path,
        help=(
            'Sets the location of the log files created with --log-to-file. PATH must be an existing directory'
            + ' in which you have read and write access. (default: same as --path)'
        ),
    )

    return parser


def post_process_opts(opts: MoodleDlOpts):
    if opts.log_file_path is None:
        opts.log_file_path = opts.path

    if opts.max_path_length_workaround:
        opts.path = PT.win_max_path_length_workaround(opts.path)

    # Max 32 yt-dlp threads
    opts.max_parallel_yt_dlp = min(opts.max_parallel_downloads, min(32, opts.max_parallel_yt_dlp))

    # 🔧 Ctrl-C resilience: env var override. The default of True
    # (delete the .part on kill) is exposed to CLI flags
    # (--restart-incomplete-on-kill / --keep-incomplete-on-kill);
    # we also honor the legacy MOODLE_DL_KEEP_INCOMPLETE_ON_KILL
    # env var for users with pre-existing scripts.
    if os.environ.get('MOODLE_DL_KEEP_INCOMPLETE_ON_KILL') == '1':
        opts.restart_incomplete_on_kill = False
    elif os.environ.get('MOODLE_DL_KEEP_INCOMPLETE_ON_KILL') == '0':
        opts.restart_incomplete_on_kill = True

    return opts


# --- called at the program invocation: -------------------------------------
def main(args=None):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    just_fix_windows_console()
    opts = post_process_opts(MoodleDlOpts(**vars(get_parser().parse_args(args))))
    setup_logger(opts)

    config = ConfigHelper(opts)
    if opts.init:
        init_config(config, opts)
        sys.exit(0)
    else:
        try:
            config.load()
        except ConfigHelper.NoConfigError as err_config:
            logging.error('Error: %s', err_config)
            logging.warning('You can create a configuration with the --init option')
            sys.exit(-1)

    try:
        if not check_debug():
            ProcessLock.lock(config.get_misc_files_path())

        choose_task(config, opts)

        logging.info('全部完成。正在退出..')
        ProcessLock.unlock(config.get_misc_files_path())
    except BaseException as base_err:  # pylint: disable=broad-except
        if not isinstance(base_err, ProcessLock.LockError):
            ProcessLock.unlock(config.get_misc_files_path())

        if opts.verbose or check_debug():
            logging.error(traceback.format_exc(), extra={'exception': base_err})
        else:
            logging.error('Exception: %s', base_err)

        logging.debug('Exception-Handling completed. Exiting...')

        sys.exit(1)
