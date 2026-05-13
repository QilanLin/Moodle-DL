import asyncio
import http.cookiejar
import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from moodle_dl import utils as utils_module
from moodle_dl.utils import (
    Log,
    MoodleDLCookieJar,
    PathTools,
    ProcessLock,
    TerminalMenuRenderer,
    Timer,
    calc_speed,
    check_debug,
    check_verbose,
    determine_ext,
    float_or_none,
    format_bytes,
    format_decimal_suffix,
    format_seconds,
    format_speed,
    is_path_like,
    run_with_final_message,
    str_or_none,
    timeconvert,
)


@pytest.fixture(autouse=True)
def restore_path_tools_state():
    original_restricted = PathTools.restricted_filenames
    yield
    PathTools.restricted_filenames = original_restricted


def _make_cookie(name='session', value='abc', domain='.example.com', path='/', expires=None, discard=True):
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith('.'),
        path=path,
        path_specified=True,
        secure=False,
        expires=expires,
        discard=discard,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _cookie_file_line(name='sid', value='value', expires='1893456000'):
    return f'.example.com\tTRUE\t/\tFALSE\t{expires}\t{name}\t{value}\n'


def test_check_verbose_reads_short_and_long_flags(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['moodle-dl'])
    assert not check_verbose()

    monkeypatch.setattr(sys, 'argv', ['moodle-dl', '--verbose'])
    assert check_verbose()

    monkeypatch.setattr(sys, 'argv', ['moodle-dl', '-v'])
    assert check_verbose()


def test_check_debug_detects_pydevd_and_trace_function(monkeypatch):
    monkeypatch.setattr(sys, 'gettrace', lambda: None)
    assert not check_debug()

    monkeypatch.setitem(sys.modules, 'pydevd', object())
    assert check_debug()
    monkeypatch.delitem(sys.modules, 'pydevd')

    monkeypatch.setattr(sys, 'gettrace', lambda: object())
    assert check_debug()


@pytest.mark.parametrize(
    ('seconds', 'expected'),
    [
        (59, '00:59'),
        (60, '01:00'),
        (3600, '01:00:00'),
        (360000, '--:--:--'),
    ],
)
def test_format_seconds(seconds, expected):
    assert format_seconds(seconds) == expected


def test_speed_helpers_handle_empty_and_normal_values():
    assert calc_speed(10, 10, 1024) is None
    assert calc_speed(10, 11, 0) is None
    assert calc_speed(0, 2, 10) == 5.0

    assert format_speed(None) == '---b/s    '
    assert format_speed(1024) == '1.00KiB/s '


def test_numeric_format_helpers():
    assert float_or_none(None, default='fallback') == 'fallback'
    assert float_or_none('2.5', scale=2, invscale=4) == 5.0
    assert float_or_none('bad', default=-1) == -1

    assert format_decimal_suffix(-1) is None
    assert format_decimal_suffix(0) == '0'
    assert format_decimal_suffix(1500) == '1k'
    assert format_decimal_suffix(1536, '%.1f%s', factor=1024) == '1.5Ki'
    assert format_bytes(1536) == '1.50KiB'


def test_timeconvert_and_simple_type_helpers(tmp_path):
    assert timeconvert('Tue, 15 Nov 1994 08:12:31 GMT') == 784887151
    assert timeconvert('not a date') is None

    assert is_path_like(str(tmp_path))
    assert is_path_like(tmp_path)
    assert not is_path_like(123)

    assert str_or_none(None, default='fallback') == 'fallback'
    assert str_or_none(123) == '123'


def test_run_with_final_message_logs_after_loading(monkeypatch):
    calls = []

    async def load(entry):
        calls.append(entry)
        return 'loaded'

    info = MagicMock()
    monkeypatch.setattr(utils_module.logging, 'info', info)

    result = asyncio.run(run_with_final_message(load, {'id': 1}, 'done %s', 'now'))

    assert result == 'loaded'
    assert calls == [{'id': 1}]
    info.assert_called_once_with('done %s', 'now')


def test_timer_uses_time_module(monkeypatch):
    values = iter([100.0, 102.5])
    monkeypatch.setattr(utils_module.time, 'time', lambda: next(values))

    with Timer() as timer:
        pass

    assert timer.start == 100.0
    assert timer.duration == 2.5


def test_timer_nanoseconds_converts_to_seconds(monkeypatch):
    values = iter([1_000_000_000, 1_250_000_000])
    monkeypatch.setattr(utils_module.time, 'perf_counter_ns', lambda: next(values))

    with Timer(nanoseconds=True) as timer:
        pass

    assert timer.start == 1_000_000_000
    assert timer.duration == 0.25


def test_moodle_cookie_jar_saves_and_loads_session_cookie(tmp_path):
    cookie_path = tmp_path / 'cookies.txt'
    jar = MoodleDLCookieJar()
    jar.set_cookie(_make_cookie(expires=None, discard=True))

    jar.save(cookie_path, ignore_discard=True, ignore_expires=True)

    assert '\t0\tsession\tabc\n' in cookie_path.read_text(encoding='utf-8')

    loaded = MoodleDLCookieJar()
    loaded.load(cookie_path, ignore_discard=True, ignore_expires=True)

    cookie = next(iter(loaded))
    assert cookie.name == 'session'
    assert cookie.value == 'abc'
    assert cookie.expires is None
    assert cookie.discard is True


def test_moodle_cookie_jar_loads_minus_one_expiry_as_session_cookie(tmp_path):
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(MoodleDLCookieJar._HEADER + _cookie_file_line(expires='-1'), encoding='utf-8')

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True)

    cookie = next(iter(jar))
    assert cookie.name == 'sid'
    assert cookie.expires is None
    assert cookie.discard is True


def test_moodle_cookie_jar_skips_invalid_expiry_entries(tmp_path):
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER
        + _cookie_file_line(name='bad', expires='not-a-number')
        + _cookie_file_line(name='good'),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True)

    assert [cookie.name for cookie in jar] == ['good']


def test_moodle_cookie_jar_rejects_json_cookie_files(tmp_path):
    cookie_path = tmp_path / 'cookies.json'
    cookie_path.write_text('{"cookies": []}\n', encoding='utf-8')

    with pytest.raises(http.cookiejar.LoadError, match='Netscape formatted'):
        MoodleDLCookieJar().load(cookie_path)


def test_moodle_cookie_jar_file_like_paths_and_missing_filename_errors():
    jar = MoodleDLCookieJar()

    with pytest.raises(ValueError, match='filename'):
        jar.save()
    with pytest.raises(ValueError, match='filename'):
        jar.load()

    jar.set_cookie(_make_cookie(name='nameless', value=None, expires=None, discard=True))
    output = io.StringIO('old data')
    jar.save(output, ignore_discard=True, ignore_expires=True)
    cookie_text = output.getvalue()

    assert cookie_text.startswith(MoodleDLCookieJar._HEADER)
    assert '\t0\t\tnameless\n' in cookie_text

    input_file = io.StringIO(
        MoodleDLCookieJar._HEADER
        + '#HttpOnly_.example.com\tTRUE\t/\tFALSE\t0\thttp_only\tsecret\n'
    )
    loaded = MoodleDLCookieJar(input_file)
    loaded.load(ignore_discard=True, ignore_expires=True)

    cookie = next(iter(loaded))
    assert cookie.name == 'http_only'
    assert cookie.expires is None
    assert cookie.discard is True


def test_moodle_cookie_jar_save_respects_discard_and_expiry_filters():
    jar = MoodleDLCookieJar()
    jar.set_cookie(_make_cookie(name='discarded', discard=True, expires=None))
    jar.set_cookie(_make_cookie(name='expired', discard=False, expires=1))
    jar.set_cookie(_make_cookie(name='kept', discard=False, expires=4_102_444_800))

    output = io.StringIO()
    jar.save(output)

    cookie_text = output.getvalue()
    assert 'kept' in cookie_text
    assert 'discarded' not in cookie_text
    assert 'expired' not in cookie_text


def test_path_tools_valid_name_and_sanitize_filename():
    PathTools.restricted_filenames = False

    assert PathTools.to_valid_name(' Lecture&nbsp;/ Notes?.pdf\n', is_file=True) == (
        'Lecture \u29f8 Notes\uff1f.pdf'
    )
    assert PathTools.sanitize_filename('a/b:c?d', restricted=False) == 'a\u29f8b\uff1ac\uff1fd'
    assert PathTools.sanitize_filename('\u00e9 test!', restricted=True) == 'e_test'
    assert PathTools.to_valid_name(None, is_file=True) is None


def test_path_tools_truncates_names_and_preserves_short_extensions():
    PathTools.restricted_filenames = False

    assert PathTools.truncate_filename('abcdef.pdf', is_file=True, max_length=8) == 'abc\u2026.pdf'
    assert PathTools.truncate_filename('abcdef.longextensionthatexceedstwenty', True, 8) == 'abcdef.\u2026'

    PathTools.restricted_filenames = True
    assert PathTools.truncate_name('abcdef', 5) == 'ab...'


def test_path_tools_sanitize_filename_edge_cases():
    PathTools.restricted_filenames = False

    assert PathTools.truncate_filename('abcdef', is_file=False, max_length=4) == 'abc\u2026'
    assert PathTools.sanitize_filename('line\nbreak', restricted=False) == 'line break'
    assert PathTools.sanitize_filename('a:b', restricted=True) == 'a_-b'
    assert PathTools.sanitize_filename('"quoted"', restricted=True) == 'quoted'
    assert PathTools.sanitize_filename('-leading', restricted=False, is_id=False) == '_leading'
    assert PathTools.sanitize_filename('___', restricted=False, is_id=False) == '_'
    assert PathTools.sanitize_filename('-_Title', restricted=True, is_id=False) == 'Title'


def test_path_tools_sanitize_and_build_paths(tmp_path):
    PathTools.restricted_filenames = False

    assert PathTools.remove_start('prefix-value', 'prefix-') == 'value'
    assert PathTools.remove_start('value', 'prefix-') == 'value'
    assert PathTools.remove_start(None, 'prefix-') is None

    sanitized = PathTools.sanitize_path('Course: 1/Week?/<bad>')
    assert sanitized == os.path.join('Course\uff1a 1', 'Week\uff1f', '\uff1cbad\uff1e')

    module_path = PathTools.path_of_file_in_module(
        str(tmp_path),
        'Course: 1',
        'Week/One',
        'Module*Name',
        'sub/Slides?.pdf',
    )
    assert Path(module_path).parts[-5:] == (
        'Course\uff1a 1',
        'Week\u29f8One',
        'Module\uff0aName',
        'sub',
        'Slides\uff1f.pdf',
    )

    course_path = PathTools.path_of_file(str(tmp_path), 'Course: 1', 'Week/One', 'sub/Slides?.pdf')
    assert Path(course_path).parts[-4:] == ('Course\uff1a 1', 'Week\u29f8One', 'sub', 'Slides\uff1f.pdf')

    flat_path = PathTools.flat_path_of_file(str(tmp_path), 'Course: 1', 'sub/Slides?.pdf')
    assert Path(flat_path).parts[-3:] == ('Course\uff1a 1', 'sub', 'Slides\uff1f.pdf')


def test_path_tools_filesystem_helpers(tmp_path):
    nested_file = tmp_path / 'a' / 'b.txt'
    PathTools.make_base_dir(str(nested_file))
    assert nested_file.parent.is_dir()

    PathTools.touch_file(str(nested_file))
    assert nested_file.exists()

    assert PathTools.make_path(str(tmp_path), 'a', 'b.txt') == str(nested_file)
    assert PathTools.get_abs_path(str(nested_file)) == str(nested_file.resolve())

    PathTools.remove_file(str(nested_file))
    assert not nested_file.exists()
    PathTools.remove_file(str(nested_file))

    nested_dir = tmp_path / 'c' / 'd'
    PathTools.make_dirs(str(nested_dir))
    assert nested_dir.is_dir()


def test_path_tools_file_name_helpers(tmp_path):
    existing = tmp_path / 'lecture.pdf'
    existing.write_text('', encoding='utf-8')

    assert PathTools.get_unused_filename(str(tmp_path), 'lecture', 'pdf') == str(tmp_path / 'lecture_01.pdf')
    assert PathTools.get_unused_filename(str(tmp_path), 'lecture', 'pdf', start_clear=False) == str(
        tmp_path / 'lecture_00.pdf'
    )

    parts = PathTools.get_path_parts(str(tmp_path / 'archive.tar.gz'))
    assert parts.dir_name == str(tmp_path)
    assert parts.file_name == 'archive.tar'
    assert parts.file_extension == 'gz'

    assert PathTools.get_file_exts('archive.tar.gz') == ('tar', 'gz')
    assert PathTools.get_file_exts('report.pdf') == (None, 'pdf')
    assert PathTools.get_file_exts('README') == (None, None)
    assert PathTools.get_file_ext('REPORT.PDF') == 'pdf'
    assert PathTools.get_file_ext('README') is None
    assert PathTools.get_file_stem_and_ext('lecture.PDF') == ('lecture', 'PDF')
    assert PathTools.get_file_stem_and_ext('README') == ('README', None)
    assert PathTools.get_cookies_path(str(tmp_path)) == str(tmp_path / 'Cookies.txt')


def test_path_tools_project_directories_use_xdg_environment(tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('XDG directory behavior is only used on non-Windows platforms')

    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config-root'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data-root'))

    assert PathTools.get_user_config_directory() == str(tmp_path / 'config-root')
    assert PathTools.get_user_data_directory() == str(tmp_path / 'data-root')

    project_config = Path(PathTools.get_project_config_directory())
    project_data = Path(PathTools.get_project_data_directory())

    assert project_config == tmp_path / 'config-root' / 'moodle-dl'
    assert project_config.is_dir()
    assert project_data == tmp_path / 'data-root' / 'moodle-dl'
    assert project_data.is_dir()


def test_path_tools_windows_user_directories_and_long_path_workaround(monkeypatch):
    monkeypatch.setattr(utils_module.os, 'name', 'nt', raising=False)
    monkeypatch.setattr(utils_module.sys, 'platform', 'win32')

    monkeypatch.setenv('LOCALAPPDATA', r'C:\Users\me\AppData\Local')
    monkeypatch.setenv('APPDATA', r'C:\Users\me\AppData\Roaming')
    assert PathTools.get_user_config_directory() == r'C:\Users\me\AppData\Local'
    assert PathTools.get_user_data_directory() == r'C:\Users\me\AppData\Local'

    monkeypatch.delenv('LOCALAPPDATA')
    assert PathTools.get_user_config_directory() == r'C:\Users\me\AppData\Roaming'
    assert PathTools.get_user_data_directory() == r'C:\Users\me\AppData\Roaming'

    monkeypatch.delenv('APPDATA')
    assert PathTools.get_user_config_directory() is None
    assert PathTools.get_user_data_directory() is None

    monkeypatch.setattr(PathTools, 'get_abs_path', MagicMock(return_value=r'C:\abs\relative\path.txt'))
    long_path = PathTools.win_max_path_length_workaround('relative/path.txt')
    assert long_path.startswith('\\\\?\\')


def test_process_lock_creates_rejects_and_removes_lock(tmp_path):
    ProcessLock.lock(str(tmp_path))
    assert (tmp_path / 'running.lock').exists()

    with pytest.raises(ProcessLock.LockError, match='already running'):
        ProcessLock.lock(str(tmp_path))

    ProcessLock.unlock(str(tmp_path))
    assert not (tmp_path / 'running.lock').exists()
    ProcessLock.unlock(str(tmp_path))


def test_log_color_helpers_wrap_messages():
    assert Log.info_str('hello') == '\033[1;37mhello\033[0m'
    assert Log.success_str('ok') == '\033[1;32mok\033[0m'
    assert Log.warning_str('warn') == '\033[1;33mwarn\033[0m'
    assert Log.error_str('bad') == '\033[1;31mbad\033[0m'
    assert Log.blue_str('link') == '\033[1;34mlink\033[0m'
    assert Log.magenta_str('mag') == '\033[1;35mmag\033[0m'
    assert Log.cyan_str('debug') == '\033[1;36mdebug\033[0m'


def test_terminal_menu_renderer_calculates_visible_window(monkeypatch):
    monkeypatch.setattr(utils_module.shutil, 'get_terminal_size', lambda: os.terminal_size((20, 7)))

    renderer = TerminalMenuRenderer(options_count=10, reserved_lines=3, extra_lines=1)

    assert renderer.calculate_view_height() == 4
    assert renderer.calculate_data_bottom(4) == (2, False)

    renderer.shift = 100
    assert renderer.calculate_data_bottom(4) == (9, True)
    assert renderer.shift == 8


def test_terminal_menu_renderer_truncates_by_display_width():
    renderer = TerminalMenuRenderer(options_count=1)

    assert renderer._char_display_width('a') == 1
    assert renderer._char_display_width('\u754c') == 2
    assert renderer._char_display_width('\u0301') == 0
    assert renderer.truncate_option_text('abcdef', max_width=4) == 'ab..'
    assert renderer.truncate_option_text('abc\ndef', max_width=20) == 'abc def'
    assert renderer.truncate_option_text('abcdef', max_width=1) == 'a'
    assert renderer.truncate_option_text('abcdef', max_width=0) == ''


def test_terminal_menu_renderer_print_helpers(capsys):
    renderer = TerminalMenuRenderer(options_count=5)
    renderer.lines_printed = 3
    renderer.shift = 2

    renderer.move_cursor_to_start()
    renderer.print_above_indicator()
    renderer.print_option_line('[x] ', 'abcdef')
    renderer.print_bottom_indicator(data_bottom=3, bottom_text=None, error_message='err')
    renderer.print_bottom_indicator(data_bottom=5, bottom_text='confirm', error_message='ok')
    renderer.print_bottom_indicator(data_bottom=5, bottom_text=None, error_message='only-error')

    output = capsys.readouterr().out
    assert '\033[3A' in output
    assert '2 more lines above' in output
    assert '[x] abcdef' in output
    assert '2 more lines below... err' in output
    assert 'confirm ok' in output
    assert 'only-error' in output


def test_determine_ext_handles_none_query_fallback_and_extension_like_values():
    assert determine_ext(None, default_ext='bin') == 'bin'
    assert determine_ext('https://example.com/download?file=archive.tar.gz', default_ext='bin') == 'gz'
    assert determine_ext('https://example.com/download?filename=bad%2Fname.pdf', default_ext='bin') == 'pdf'


def test_ssl_helper_custom_requests_session_mounts_context(monkeypatch):
    ssl_context = object()
    get_ssl_context = MagicMock(return_value=ssl_context)
    session = MagicMock()

    monkeypatch.setattr(utils_module.SslHelper, 'get_ssl_context', get_ssl_context)
    monkeypatch.setattr(utils_module.requests, 'Session', MagicMock(return_value=session))

    result = utils_module.SslHelper.custom_requests_session(
        skip_cert_verify=True,
        allow_insecure_ssl=False,
        use_all_ciphers=True,
    )

    assert result is session
    assert session.verify is False
    get_ssl_context.assert_called_once_with(True, False, True)
    mount_args = session.mount.call_args.args
    assert mount_args[0] == 'https://'
    assert mount_args[1].ssl_context is ssl_context


def test_ssl_helper_load_default_certs_fallback_and_locations(monkeypatch, tmp_path):
    utils_module.SslHelper.warned_about_certifi = False
    missing_context = MagicMock()

    monkeypatch.setattr(utils_module, 'extract_zipped_paths', lambda _path: str(tmp_path / 'missing.pem'))
    utils_module.SslHelper.load_default_certs(missing_context)
    utils_module.SslHelper.load_default_certs(missing_context)

    assert missing_context.load_default_certs.call_count == 2
    assert utils_module.SslHelper.warned_about_certifi is True

    cert_file = tmp_path / 'cacert.pem'
    cert_file.write_text('cert', encoding='utf-8')
    file_context = MagicMock()
    monkeypatch.setattr(utils_module, 'extract_zipped_paths', lambda _path: str(cert_file))
    utils_module.SslHelper.load_default_certs(file_context)
    file_context.load_verify_locations.assert_called_once_with(cafile=str(cert_file))

    cert_dir = tmp_path / 'certs'
    cert_dir.mkdir()
    dir_context = MagicMock()
    monkeypatch.setattr(utils_module, 'extract_zipped_paths', lambda _path: str(cert_dir))
    utils_module.SslHelper.load_default_certs(dir_context)
    dir_context.load_verify_locations.assert_called_once_with(capath=str(cert_dir))


def test_ssl_helper_get_ssl_context_applies_insecure_flags(monkeypatch):
    class FakeSslContext:
        def __init__(self):
            self.options = 0
            self.ciphers = None
            self.alpn = None

        def set_ciphers(self, ciphers):
            self.ciphers = ciphers

        def set_alpn_protocols(self, protocols):
            self.alpn = protocols

    context = FakeSslContext()
    utils_module.SslHelper.get_ssl_context.cache_clear()
    monkeypatch.setattr(utils_module.ssl, '_create_unverified_context', MagicMock(return_value=context))

    result = utils_module.SslHelper.get_ssl_context(
        skip_cert_verify=True,
        allow_insecure_ssl=True,
        use_all_ciphers=True,
    )

    assert result is context
    assert context.options & 0x4
    assert context.ciphers == 'ALL'
    assert context.alpn == ['http/1.1']
