import argparse
import asyncio
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from shutil import which

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
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.notifications import get_all_notify_services
from moodle_dl.types import MoodleDlOpts
from moodle_dl.utils import PathTools as PT
from moodle_dl.utils import ProcessLock, check_debug
from moodle_dl.version import __version__


class ReRaiseOnError(logging.StreamHandler):
    "A logging-handler class which allows the exception-catcher of i.e. PyCharm to intervene"

    def emit(self, record):
        if hasattr(record, 'exception'):
            raise record.exception


def choose_task(config: ConfigHelper, opts: MoodleDlOpts):
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
    elif opts.new_token:
        MoodleWizard(config, opts).interactively_acquire_token(use_stored_url=True)
    elif opts.retry_failed:
        retry_failed_downloads(config, opts)
    else:
        run_main(config, opts)


def retry_failed_downloads(config: ConfigHelper, opts: MoodleDlOpts):
    """重试所有下载失败的文件"""
    from moodle_dl.types import Course

    logging.info('正在查询下载失败的文件...')

    # 初始化数据库
    database = StateRecorder(config, opts)

    # 获取失败文件统计
    summary = database.get_failed_files_summary()

    if not summary:
        logging.info('✓ 没有下载失败的文件！')
        return

    # 显示统计信息
    total_failed_files = sum(info['failed_count'] for info in summary.values())
    total_failures = sum(info['total_failures'] for info in summary.values())

    logging.info('')
    logging.info('=' * 60)
    logging.info(f'找到 {total_failed_files} 个下载失败的文件（总失败次数：{total_failures}）')
    logging.info('=' * 60)

    for course_id, info in summary.items():
        logging.info(f"课程 ID {course_id} ({info['course_name']}):")
        logging.info(f"  - 失败文件数：{info['failed_count']}")
        logging.info(f"  - 总失败次数：{info['total_failures']}")
        logging.info(f"  - 最大连续失败：{info['max_consecutive']}")

    logging.info('=' * 60)
    logging.info('')

    # 获取所有失败的文件（按课程分组）
    courses_dict = database.get_failed_files_with_course_info()

    if not courses_dict:
        logging.warning('无法读取失败的文件，请检查数据库。')
        return

    # 构造 Course 对象
    courses = []
    for course_id, course_info in courses_dict.items():
        course = Course(
            _id=course_id,
            fullname=course_info['course_fullname'],
            files=course_info['files']
        )
        courses.append(course)

    # 重置失败文件的状态为 pending
    logging.info('正在重置失败文件状态...')
    for course in courses:
        for file in course.files:
            database.reset_failed_file_for_retry(file, course.id)

    logging.info('开始重试下载失败的文件...')

    # TODO: Change this
    PT.restricted_filenames = config.get_restricted_filenames()

    # 使用下载服务重新下载
    if opts.without_downloading_files:
        downloader = FakeDownloadService(courses, config, opts, database)
    else:
        downloader = DownloadService(courses, config, opts, database)

    downloader.run()

    new_failed_downloads = downloader.get_failed_tasks()

    # 显示结果
    logging.info('')
    logging.info('=' * 60)
    if len(new_failed_downloads) > 0:
        logging.warning(f'重试完成，仍有 {len(new_failed_downloads)} 个文件下载失败。')
        for task in new_failed_downloads:
            logging.warning(f'  - {task.file.content_filename}: {task.status.get_error_text()}')
    else:
        logging.info('✓ 所有失败的文件已成功重新下载！')
    logging.info('=' * 60)


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


def run_main(config: ConfigHelper, opts: MoodleDlOpts):
    sentry_connected = connect_sentry(config)
    notify_services = get_all_notify_services(config)

    # TODO: Change this
    PT.restricted_filenames = config.get_restricted_filenames()

    try:
        moodle = MoodleService(config, opts)

        logging.debug('Checking for changes for the configured Moodle-Account....')
        database = StateRecorder(config, opts)
        changed_courses = asyncio.run(moodle.fetch_state(database))

        if opts.log_responses:
            logging.info("All JSON-responses from Moodle have been written to the responses.log file.")
            return

        logging.debug('Start downloading changed files...')

        if opts.without_downloading_files:
            downloader = FakeDownloadService(changed_courses, config, opts, database)
        else:
            downloader = DownloadService(changed_courses, config, opts, database)
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
        if os.path.isdir(path):
            return path
        raise argparse.ArgumentTypeError(f'"{str(path)}" is not a valid path. Make sure the directory exists.')

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
