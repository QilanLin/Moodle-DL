# -*- coding: utf-8 -*-
import asyncio
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib import parse as urlparse
from unittest.mock import AsyncMock, MagicMock, call, patch

import aiohttp
import pytest
import requests
from yarl import URL

from moodle_dl.downloader.task import ContentRangeError, Task
from moodle_dl.types import Course, DlEvent, DownloadOptions, HeadInfo, MoodleDlOpts, TaskState


class FakeAsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_jwt(payload):
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).decode('ascii').rstrip('=')
    return f'header.{encoded_payload}.signature'


@pytest.fixture
def task_factory(tmp_path):
    pools = []

    def make_task(
        *,
        content_type='application/pdf',
        module_modname='resource',
        content_fileurl='https://example.com/file.pdf',
        content_filename='file.pdf',
        cookies_text=None,
        download_path=None,
        write_links=None,
        download_metadata_files=True,
        download_linked_files=False,
        whitelist=None,
        blacklist=None,
    ):
        opts = MoodleDlOpts()
        options = DownloadOptions(
            token='token-abc',
            moodle_url='https://moodle.example.com',
            download_linked_files=download_linked_files,
            download_domains_whitelist=whitelist or [],
            download_domains_blacklist=blacklist or [],
            cookies_text=cookies_text,
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links=write_links or {'url': True, 'desktop': False},
            download_path=str(download_path or tmp_path),
            download_metadata_files=download_metadata_files,
            global_opts=opts,
        )

        file = MagicMock()
        file.content_filepath = '/'
        file.content_filename = content_filename
        file.content_fileurl = content_fileurl
        file.content_filesize = 0
        file.content_timemodified = 123
        file.content_type = content_type
        file.module_modname = module_modname
        file.module_name = 'Module'
        file.section_name = 'Week 1'
        file.position_in_section = None
        file.old_file = None
        file.file_id = None
        file.module_id = 10
        file.modified = False
        file.moved = False
        file.text_content = None
        file.html_content = None
        file.content = None
        file.saved_to = ''

        course = Course(7, 'Course Name')
        pool = ThreadPoolExecutor(max_workers=1)
        pools.append(pool)
        events = []

        def callback(event, task, **kwargs):
            events.append((event, kwargs))

        task = Task(1, file, course, options, pool, callback)
        task.events = events
        return task

    yield make_task

    for pool in pools:
        pool.shutdown(wait=False)


def test_create_session_with_retry_loads_cookies_when_available(task_factory):
    task = task_factory(cookies_text='cookie-data')
    with patch('moodle_dl.downloader.task.MoodleDLCookieJar') as cookie_jar_cls:
        cookie_jar = MagicMock()
        cookie_jar_cls.return_value = cookie_jar

        session = task._create_session_with_retry()

    cookie_jar_cls.assert_called_once()
    cookie_jar.load.assert_called_once_with(ignore_discard=True, ignore_expires=True)
    assert session.cookies is cookie_jar


def test_create_session_with_retry_continues_when_cookie_loading_fails(task_factory):
    task = task_factory(cookies_text='broken-cookie-data')
    with patch('moodle_dl.downloader.task.MoodleDLCookieJar') as cookie_jar_cls:
        cookie_jar = MagicMock()
        cookie_jar.load.side_effect = RuntimeError('bad cookies')
        cookie_jar_cls.return_value = cookie_jar

        session = task._create_session_with_retry()

    assert session is not None
    cookie_jar.load.assert_called_once()


def test_yt_logger_censors_tokens_and_updates_task_flags(task_factory):
    task = task_factory()
    logger = Task.YtLogger(task)

    assert logger.clean_msg('token=secret123') == 'censored_sensitive_data'

    logger.warning('Falling back on generic information extractor')
    assert task.status.yt_dlp_used_generic_extractor is True

    task.status.yt_dlp_failed_with_error = False
    logger.error('Unsupported URL: https://example.com')
    assert task.status.yt_dlp_failed_with_error is False

    logger.error('fatal download failure')
    assert task.status.yt_dlp_failed_with_error is True


def test_add_token_to_url_handles_plain_urls_existing_tokens_and_pluginfiles(task_factory):
    task = task_factory()

    assert task.add_token_to_url('https://files.example.com/file.pdf') == (
        'https://files.example.com/file.pdf?token=token-abc'
    )
    assert task.add_token_to_url('https://files.example.com/file.pdf?token=old') == (
        'https://files.example.com/file.pdf?token=old'
    )

    pluginfile = 'https://moodle.example.com/pluginfile.php/1/mod_resource/content/file.pdf?forcedownload=1'
    fixed = task.add_token_to_url(pluginfile)
    assert '/webservice/pluginfile.php/' in fixed
    assert 'token=token-abc' in fixed
    assert 'offline=1' in fixed


def test_yt_logger_uses_quieter_paths_for_expected_messages(task_factory):
    task = task_factory()
    logger = Task.YtLogger(task)

    with patch('moodle_dl.downloader.task.logging') as mock_logging:
        logger.debug('ETA 00:01')
        logger.warning('Requested formats are incompatible for merge')
        logger.error('no suitable InfoExtractor for this URL')

    assert task.status.yt_dlp_failed_with_error is False
    mock_logging.warning.assert_not_called()
    mock_logging.error.assert_not_called()
    assert mock_logging.debug.call_count == 2


def test_yt_logger_logs_normal_debug_and_warning_messages(task_factory):
    task = task_factory()
    logger = Task.YtLogger(task)

    with patch('moodle_dl.downloader.task.logging') as mock_logging:
        logger.debug('line\nwith\rtoken=secret123\033[K')
        logger.warning('plain warning')

    mock_logging.debug.assert_called_once_with(
        '[%d] yt-dlp Debug: %s',
        task.task_id,
        'linewithcensored_sensitive_data',
    )
    mock_logging.warning.assert_called_once_with(
        '[%d] yt-dlp Warning: %s',
        task.task_id,
        'plain warning',
    )


def test_yt_hook_tracks_total_size_received_bytes_and_ignores_incomplete_events(task_factory):
    task = task_factory()

    task.yt_hook({'status': 'error', 'tmpfilename': 'ignored.part'})
    task.yt_hook({'status': 'downloading'})
    assert task.events == []

    task.yt_hook({
        'status': 'downloading',
        'tmpfilename': 'video.part',
        'total_bytes_estimate': 100,
        'downloaded_bytes': 10,
    })
    assert task.status.yt_dlp_current_file == 'video.part'
    assert task.status.external_total_size == 100
    assert task.status.bytes_downloaded == 10
    assert task.events == [
        (DlEvent.TOTAL_SIZE, {'content_length': 100}),
        (DlEvent.RECEIVED, {'bytes_received': 10}),
    ]

    task.yt_hook({
        'status': 'downloading',
        'tmpfilename': 'video.part',
        'total_bytes': 125,
        'downloaded_bytes': 30,
    })
    assert task.status.external_total_size == 125
    assert task.status.bytes_downloaded == 30
    assert task.events[-2:] == [
        (DlEvent.TOTAL_SIZE_UPDATE, {'content_length_diff': 25}),
        (DlEvent.RECEIVED, {'bytes_received': 20}),
    ]

    task.yt_hook({
        'status': 'downloading',
        'tmpfilename': 'video.part',
        'total_bytes': 125,
        'downloaded_bytes': 20,
    })
    assert task.status.bytes_downloaded == 30


def test_yt_hook_after_move_and_blocked_youtube_channel_detection(task_factory):
    task = task_factory()
    task.yt_hook_after_move(f'/prefix{task.destination}/video.mp4')

    assert task.file.saved_to == f'{task.destination}/video.mp4'
    assert task.is_blocked_for_yt_dlp('https://www.youtube.com/channel/abc') is True
    assert task.is_blocked_for_yt_dlp('https://www.youtube.com/watch?v=abc') is False
    assert task.is_blocked_for_yt_dlp('https://vimeo.com/channel/abc') is False


def test_set_utime_prefers_valid_server_timestamp_and_falls_back_to_file_timestamp(task_factory, tmp_path):
    task = task_factory()
    target = tmp_path / 'downloaded.pdf'
    target.write_text('data', encoding='utf-8')
    task.file.saved_to = str(target)

    with (
        patch('moodle_dl.downloader.task.timeconvert', return_value=111),
        patch('moodle_dl.downloader.task.time.time', return_value=999),
        patch('moodle_dl.downloader.task.os.utime') as utime,
    ):
        task.set_utime('Wed, 21 Oct 2015 07:28:00 GMT')

    utime.assert_called_once_with(str(target), (999, 111))

    with (
        patch('moodle_dl.downloader.task.timeconvert', return_value=0),
        patch('moodle_dl.downloader.task.time.time', return_value=1000),
        patch('moodle_dl.downloader.task.os.utime') as utime,
    ):
        task.set_utime('bad date')

    utime.assert_called_once_with(str(target), (1000, 123))


def test_set_utime_ignores_missing_files_and_os_errors(task_factory, tmp_path):
    task = task_factory()
    missing = tmp_path / 'missing.pdf'
    task.file.saved_to = str(missing)

    with patch('moodle_dl.downloader.task.os.utime') as utime:
        task.set_utime('Wed, 21 Oct 2015 07:28:00 GMT')

    utime.assert_not_called()

    target = tmp_path / 'downloaded.pdf'
    target.write_text('data', encoding='utf-8')
    task.file.saved_to = str(target)

    with (
        patch('moodle_dl.downloader.task.timeconvert', return_value=111),
        patch('moodle_dl.downloader.task.os.utime', side_effect=OSError('permission denied')) as utime,
        patch('moodle_dl.downloader.task.logging.debug') as debug,
    ):
        task.set_utime('Wed, 21 Oct 2015 07:28:00 GMT')

    utime.assert_called_once()
    debug.assert_called_once()


def test_is_filtered_external_domain_handles_invalid_blacklisted_and_whitelisted_domains(task_factory):
    task = task_factory(content_fileurl='mailto:test@example.com')
    assert task.is_filtered_external_domain() is True

    task = task_factory(content_fileurl='https://sub.bad.example/path', blacklist=['bad.example'])
    assert task.is_filtered_external_domain() is True

    task = task_factory(content_fileurl='https://sub.allowed.example/path', whitelist=['allowed.example'])
    assert task.is_filtered_external_domain() is False

    task = task_factory(content_fileurl='https://other.example/path', whitelist=['allowed.example'])
    assert task.is_filtered_external_domain() is True


@pytest.mark.asyncio
async def test_create_description_html_and_content_files(task_factory, tmp_path):
    description = task_factory(content_type='description')
    description.file.saved_to = str(tmp_path / 'description.md')
    description.file.text_content = '<p>Hello <strong>world</strong></p>'
    await description.create_description()
    assert 'Hello' in (tmp_path / 'description.md').read_text(encoding='utf-8')

    empty_description = task_factory(content_type='description')
    empty_description.file.saved_to = str(tmp_path / 'empty.md')
    (tmp_path / 'empty.md').write_text('old', encoding='utf-8')
    await empty_description.create_description()
    assert not (tmp_path / 'empty.md').exists()

    html_task = task_factory(content_type='html', module_modname='block_html')
    html_task.file.saved_to = str(tmp_path / 'block.html')
    html_task.file.html_content = '<h1>Title</h1><p><a href="https://example.com">Link</a></p>'
    await html_task.create_html_file()
    assert (tmp_path / 'block.html').read_text(encoding='utf-8') == html_task.file.html_content
    assert 'Title' in (tmp_path / 'block.md').read_text(encoding='utf-8')

    content_task = task_factory(content_type='content')
    content_task.file.saved_to = str(tmp_path / 'metadata.json')
    content_task.file.content = '{"ok": true}'
    await content_task.create_content_file()
    assert (tmp_path / 'metadata.json').read_text(encoding='utf-8') == '{"ok": true}'


@pytest.mark.asyncio
async def test_download_index_mod_page_saves_markdown_without_html_badges(task_factory):
    task = task_factory(
        module_modname='index_mod-page',
        content_filename=(
            '1.1 — Instructions <span class="label label-success">Start here</span> '
            '<span class="badge bg-success">Core!</span>'
        ),
        content_fileurl='https://moodle.example.com/webservice/pluginfile.php/1/mod_page/content/index.html',
    )

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return '<h1>Instructions Start here</h1><p><strong>Core!</strong> Use Git.</p>'

    class FakeClientSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers, ssl, timeout):
            assert method == 'GET'
            assert 'token=token-abc' in url
            return FakeResponse()

    with patch('moodle_dl.downloader.task.aiohttp.ClientSession', FakeClientSession):
        await task._download_index_mod_page()

    saved_path = Path(task.file.saved_to)
    saved_text = saved_path.read_text(encoding='utf-8')
    assert saved_path.suffix == '.md'
    assert '<span' not in saved_path.name
    assert 'Start here' in saved_path.name
    assert 'Core!' in saved_path.name
    assert 'Instructions Start here' in saved_text
    assert 'Use Git.' in saved_text


def test_may_perform_network_io_distinguishes_local_generated_tasks(task_factory):
    for content_type in ('description', 'html', 'content', 'directory_placeholder'):
        task = task_factory(content_type=content_type, content_fileurl='https://example.com/file')
        assert task.may_perform_network_io() is False

    data_url = task_factory(module_modname='url', content_fileurl='data:text/plain,hello')
    assert data_url.may_perform_network_io() is False

    skipped_metadata = task_factory(content_filename='metadata.json', download_metadata_files=False)
    assert skipped_metadata.may_perform_network_io() is False

    normal_file = task_factory(content_type='application/pdf', content_fileurl='https://example.com/file.pdf')
    assert normal_file.may_perform_network_io() is True

    linked_file = task_factory(module_modname='url', download_linked_files=True)
    assert linked_file.may_perform_network_io() is True

    shortcut_only = task_factory(module_modname='url', download_linked_files=False)
    assert shortcut_only.may_perform_network_io() is False


def test_set_path_avoids_duplicate_generated_extensions_and_supports_forced_extensions(
    task_factory,
    tmp_path,
):
    description = task_factory(content_type='description', content_filename='intro.md')
    description.filename = 'intro.md'
    Path(description.destination).mkdir(parents=True, exist_ok=True)
    description.set_path()
    assert description.file.saved_to.endswith('intro.md')
    assert not description.file.saved_to.endswith('intro.md.md')

    html_task = task_factory(content_type='html', content_filename='page.html')
    html_task.filename = 'page.html'
    Path(html_task.destination).mkdir(parents=True, exist_ok=True)
    html_task.set_path()
    assert html_task.file.saved_to.endswith('page.html')
    assert not html_task.file.saved_to.endswith('page.html.html')

    regular = task_factory(content_filename='file.pdf')
    Path(regular.destination).mkdir(parents=True, exist_ok=True)
    regular.set_path(force_file_extension='url')
    assert regular.file.saved_to.endswith('file.pdf.url')


def test_rename_and_move_old_files(task_factory, tmp_path):
    task = task_factory()
    assert task.rename_old_file() is False
    assert task.move_old_file() is False

    old_path = tmp_path / 'old.pdf'
    old_path.write_text('old data', encoding='utf-8')
    task.file.old_file = MagicMock(saved_to=str(old_path))
    assert task.rename_old_file() is True
    assert not old_path.exists()
    assert task.file.old_file.saved_to.endswith('_old.pdf')

    move_task = task_factory()
    move_old_path = tmp_path / 'move-old.pdf'
    move_old_path.write_text('move data', encoding='utf-8')
    target_path = tmp_path / 'target.pdf'
    target_path.write_text('target data', encoding='utf-8')
    move_task.file.old_file = MagicMock(saved_to=str(move_old_path))
    move_task.file.saved_to = str(target_path)

    assert move_task.move_old_file() is True
    assert target_path.read_text(encoding='utf-8') == 'move data'


def test_rename_and_move_old_files_return_false_on_move_errors(task_factory, tmp_path):
    old_path = tmp_path / 'old.pdf'
    old_path.write_text('old data', encoding='utf-8')
    task = task_factory()
    task.file.old_file = MagicMock(saved_to=str(old_path))

    with patch('moodle_dl.downloader.task.shutil.move', side_effect=OSError('locked')):
        assert task.rename_old_file() is False

    move_old_path = tmp_path / 'move-old.pdf'
    move_old_path.write_text('move data', encoding='utf-8')
    move_task = task_factory()
    move_task.file.old_file = MagicMock(saved_to=str(move_old_path))
    move_task.file.saved_to = str(tmp_path / 'target.pdf')

    with patch('moodle_dl.downloader.task.shutil.move', side_effect=OSError('locked')):
        assert move_task.move_old_file() is False


@pytest.mark.asyncio
async def test_execute_download_dispatches_to_type_specific_handlers(task_factory):
    description = task_factory(content_type='description')
    description.create_description = AsyncMock()
    await description._execute_download()
    description.create_description.assert_awaited_once()

    html_task = task_factory(content_type='html')
    html_task.create_html_file = AsyncMock()
    await html_task._execute_download()
    html_task.create_html_file.assert_awaited_once()

    content_task = task_factory(content_type='content')
    content_task.create_content_file = AsyncMock()
    await content_task._execute_download()
    content_task.create_content_file.assert_awaited_once()

    leganto_task = task_factory(content_type='leganto_pdf')
    leganto_task._download_leganto_reading_list_pdf = AsyncMock()
    await leganto_task._execute_download()
    leganto_task._download_leganto_reading_list_pdf.assert_awaited_once()

    index_task = task_factory(module_modname='index_mod-page', content_type='html')
    index_task._download_index_mod_page = AsyncMock()
    await index_task._execute_download()
    index_task._download_index_mod_page.assert_awaited_once()

    index_asset = task_factory(
        module_modname='index_mod-page',
        content_type='file',
        content_filename='Screenshot 2024-10-21 at 22.07.07.png',
        content_fileurl='https://moodle.example.com/webservice/pluginfile.php/1/mod_page/content/3/image.png',
    )
    index_asset._download_index_mod_page = AsyncMock()
    index_asset.add_token_to_url = MagicMock(return_value='https://moodle.example.com/image.png?token=token-abc')
    index_asset.download_url = AsyncMock()
    await index_asset._execute_download()
    index_asset._download_index_mod_page.assert_not_awaited()
    index_asset.download_url.assert_awaited_once_with(
        'https://moodle.example.com/image.png?token=token-abc',
        index_asset.file.saved_to,
    )

    cookie_task = task_factory(module_modname='cookie_mod-helixmedia')
    cookie_task._download_cookie_mod_file = AsyncMock()
    await cookie_task._execute_download()
    cookie_task._download_cookie_mod_file.assert_awaited_once()

    url_task = task_factory(module_modname='url')
    url_task._download_external_url_with_fallback = AsyncMock()
    await url_task._execute_download()
    url_task._download_external_url_with_fallback.assert_awaited_once()

    data_task = task_factory(module_modname='url', content_fileurl='data:text/plain,hello')
    data_task.create_data_url_file = AsyncMock()
    await data_task._execute_download()
    data_task.create_data_url_file.assert_awaited_once()

    regular = task_factory(content_fileurl='https://example.com/file.pdf')
    regular.add_token_to_url = MagicMock(return_value='https://example.com/file.pdf?token=token-abc')
    regular.download_url = AsyncMock()
    await regular._execute_download()
    regular.download_url.assert_awaited_once_with(
        'https://example.com/file.pdf?token=token-abc',
        regular.file.saved_to,
    )

    no_url = task_factory(content_fileurl='')
    await no_url._execute_download()
    assert no_url.status.error == 'No URL available for download'


@pytest.mark.asyncio
async def test_cookie_mod_and_kalvidres_download_helpers_restore_original_url(task_factory, tmp_path):
    kalvidres = task_factory(module_modname='cookie_mod-kalvidres')
    kalvidres._handle_kalvidres_download = AsyncMock()
    await kalvidres._download_cookie_mod_file()
    kalvidres._handle_kalvidres_download.assert_awaited_once()

    other_cookie_mod = task_factory(module_modname='cookie_mod-helixmedia')
    other_cookie_mod.external_download_url = AsyncMock()
    await other_cookie_mod._download_cookie_mod_file()
    other_cookie_mod.external_download_url.assert_awaited_once_with(
        add_token=False,
        delete_if_successful=True,
        needs_moodle_cookies=True,
    )

    task = task_factory(
        module_modname='cookie_mod-kalvidres',
        content_fileurl='https://moodle.example.com/kalvidres',
    )
    task.file.saved_to = str(tmp_path / 'video.mp4')
    task.extract_kalvidres_text = AsyncMock()
    task.extract_kalvidres_video_url = AsyncMock(return_value='https://cdn.example.com/video')
    task.external_download_url = AsyncMock()

    await task._handle_kalvidres_download()

    task.extract_kalvidres_text.assert_awaited_once_with(
        'https://moodle.example.com/kalvidres',
        str(tmp_path / 'video_notes.md'),
    )
    task.external_download_url.assert_awaited_once_with(
        add_token=False,
        delete_if_successful=True,
        needs_moodle_cookies=True,
    )
    assert task.file.content_fileurl == 'https://moodle.example.com/kalvidres'


@pytest.mark.asyncio
async def test_kalvidres_direct_embed_download_skips_text_extraction(task_factory, tmp_path):
    task = task_factory(
        module_modname='cookie_mod-kalvidres',
        content_fileurl='https://media.kcl.ac.uk/embed/secure/iframe/entryId/1_5eu7vehb/uiConfId/50622292',
    )
    task.file.saved_to = str(tmp_path / 'video.mp4')
    task.extract_kalvidres_text = AsyncMock()
    task.extract_kalvidres_video_url = AsyncMock()

    downloaded_urls = []

    async def record_download(**_kwargs):
        downloaded_urls.append(task.file.content_fileurl)

    task.external_download_url = AsyncMock(side_effect=record_download)

    await task._handle_kalvidres_download()

    task.extract_kalvidres_text.assert_not_awaited()
    task.extract_kalvidres_video_url.assert_not_awaited()
    assert downloaded_urls == [
        'https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/'
        'uiconf_id/50622292/partner_id/2368101?iframeembed=true&entry_id=1_5eu7vehb'
    ]
    assert task.file.content_fileurl == (
        'https://media.kcl.ac.uk/embed/secure/iframe/entryId/1_5eu7vehb/uiConfId/50622292'
    )


@pytest.mark.asyncio
async def test_external_url_fallback_downloads_when_allowed_and_creates_shortcut_otherwise(task_factory):
    downloadable = task_factory(module_modname='url', download_linked_files=True)
    downloadable.is_filtered_external_domain = MagicMock(return_value=False)
    downloadable.external_download_url = AsyncMock()
    downloadable.create_shortcut = AsyncMock()
    await downloadable._download_external_url_with_fallback()
    downloadable.external_download_url.assert_awaited_once()
    downloadable.create_shortcut.assert_not_awaited()

    failing = task_factory(module_modname='url', download_linked_files=True)
    failing.is_filtered_external_domain = MagicMock(return_value=False)
    failing.external_download_url = AsyncMock(side_effect=RuntimeError('download failed'))
    failing.create_shortcut = AsyncMock()
    await failing._download_external_url_with_fallback()
    failing.create_shortcut.assert_awaited_once()

    disabled = task_factory(module_modname='url', download_linked_files=False)
    disabled.external_download_url = AsyncMock()
    disabled.create_shortcut = AsyncMock()
    await disabled._download_external_url_with_fallback()
    disabled.external_download_url.assert_not_awaited()
    disabled.create_shortcut.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_url_fallback_saves_leganto_reading_list_as_pdf(task_factory):
    leganto_url = 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'
    task = task_factory(
        module_modname='url',
        content_fileurl=leganto_url,
        content_filename='Reading List',
        download_linked_files=False,
    )
    await task._prepare_download()
    task.create_shortcut = AsyncMock()

    # 默认有头：让 Leganto 偶发跳 SSO 时用户能看到。锁定无头则被尊重。
    with (
        patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls,
        patch('moodle_dl.cli.authenticators._should_use_headless_sso', return_value=False),
    ):
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock()

        await task._download_external_url_with_fallback()

    printer_cls.assert_called_once_with(task.opts.cookies_text, skip_cert_verify=False, headless=False)
    # url-type Leganto link: no LTI launch_parameters, no module_id → 没有 stored_lti
    # 也没有 moodle_launch_url，只剩 course_url 这一级 fallback——直接走课程页点击。
    printer.print_to_pdf.assert_awaited_once_with(
        'https://moodle.example.com/course/view.php?id=7',
        task.file.saved_to,
        launch_parameters=None,
        moodle_launch_url=None,
        course_url='https://moodle.example.com/course/view.php?id=7',
    )
    task.create_shortcut.assert_not_awaited()
    assert task.file.saved_to.endswith('Reading List.pdf')


@pytest.mark.asyncio
async def test_external_url_fallback_raises_when_leganto_pdf_export_fails(task_factory):
    leganto_url = 'https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML'
    task = task_factory(
        module_modname='url',
        content_fileurl=leganto_url,
        content_filename='Reading List',
        download_linked_files=False,
    )
    await task._prepare_download()
    task.create_shortcut = AsyncMock()

    with patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls:
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock(side_effect=RuntimeError('print failed'))

        with pytest.raises(RuntimeError, match='print failed'):
            await task._download_external_url_with_fallback()

    task.create_shortcut.assert_not_awaited()
    assert task.filename == 'Reading List.pdf'


@pytest.mark.asyncio
async def test_leganto_pdf_download_uses_lti_launch_payload(task_factory):
    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        content_filename='Reading List.pdf',
        cookies_text='cookie-data',
    )
    task.file.content = (
        '{"endpoint": "https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1", '
        '"parameters": [{"name": "id_token", "value": "signed-token"}]}'
    )
    await task._prepare_download()

    with (
        patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls,
        patch('moodle_dl.cli.authenticators._should_use_headless_sso', return_value=False),
    ):
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock()

        await task._download_leganto_reading_list_pdf()

    printer_cls.assert_called_once_with('cookie-data', skip_cert_verify=False, headless=False)
    printer.print_to_pdf.assert_awaited_once_with(
        'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        task.file.saved_to,
        launch_parameters=[{'name': 'id_token', 'value': 'signed-token'}],
        moodle_launch_url=None,
        course_url=None,
    )


@pytest.mark.asyncio
async def test_leganto_pdf_download_honors_headless_env_override(task_factory):
    """MOODLE_DL_HEADLESS=1 → _should_use_headless_sso 返回 True → Leganto 也走无头。

    锁定 task.py 真的查了开关；少了这条用例，硬编码 headless=False 也能让所有现
    存测试通过，但 CI / 无人值守跑就会卡 SSO 弹窗。
    """
    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        content_filename='Reading List.pdf',
        cookies_text='cookie-data',
    )
    task.file.content = (
        '{"endpoint": "https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1", '
        '"parameters": [{"name": "id_token", "value": "signed-token"}]}'
    )
    await task._prepare_download()

    with (
        patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls,
        patch('moodle_dl.cli.authenticators._should_use_headless_sso', return_value=True),
    ):
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock()

        await task._download_leganto_reading_list_pdf()

    printer_cls.assert_called_once_with('cookie-data', skip_cert_verify=False, headless=True)


@pytest.mark.asyncio
async def test_leganto_pdf_target_removes_previous_shortcut_fallback(task_factory, tmp_path):
    old_shortcut = tmp_path / 'Week 1' / '*07* Reading List.webloc'
    old_shortcut.parent.mkdir(parents=True, exist_ok=True)
    old_shortcut.write_text('old shortcut', encoding='utf-8')
    old_appledouble = old_shortcut.with_name(f'._{old_shortcut.name}')
    old_appledouble.write_text('old metadata', encoding='utf-8')

    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        content_filename='Reading List.pdf',
    )
    task.file.saved_to = str(old_shortcut)

    await task._prepare_download()
    task._prepare_leganto_pdf_target()

    assert task.file.saved_to.endswith('Reading List.pdf')
    assert not old_shortcut.exists()
    assert not old_appledouble.exists()


@pytest.mark.asyncio
async def test_leganto_pdf_download_retries_from_moodle_module_when_stored_launch_fails(task_factory):
    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        content_filename='Reading List.pdf',
        cookies_text='cookie-data',
    )
    task.file.content = json.dumps({
        'endpoint': 'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        'parameters': [{'name': 'id_token', 'value': make_jwt({'exp': 9999999999})}],
    })
    await task._prepare_download()

    with patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls:
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock(side_effect=[RuntimeError('Invalid token'), None])

        await task._download_leganto_reading_list_pdf()

    assert printer.print_to_pdf.await_args_list[0].kwargs == {
        'launch_parameters': [{'name': 'id_token', 'value': make_jwt({'exp': 9999999999})}],
        'moodle_launch_url': None,
        'course_url': None,
    }
    assert printer.print_to_pdf.await_args_list[1].args == (
        'https://moodle.example.com/mod/lti/view.php?id=10',
        task.file.saved_to,
    )
    # 新 fallback 链显式地传完整的 print_kwargs（包括 launch_parameters=None），
    # 比之前的隐式覆盖更安全——少了它，重试用的还是上一次的 launch_parameters。
    assert printer.print_to_pdf.await_args_list[1].kwargs == {
        'launch_parameters': None,
        'moodle_launch_url': 'https://moodle.example.com/mod/lti/view.php?id=10',
        'course_url': None,
    }


@pytest.mark.asyncio
async def test_leganto_pdf_download_refreshes_expired_lti_launch_payload(task_factory):
    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        content_filename='Reading List.pdf',
        cookies_text='cookie-data',
    )
    task.file.content = json.dumps({
        'endpoint': 'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        'parameters': [{'name': 'id_token', 'value': make_jwt({'exp': 100})}],
    })
    await task._prepare_download()

    with patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls:
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock()

        await task._download_leganto_reading_list_pdf()

    printer.print_to_pdf.assert_awaited_once_with(
        'https://moodle.example.com/mod/lti/view.php?id=10',
        task.file.saved_to,
        launch_parameters=None,
        moodle_launch_url='https://moodle.example.com/mod/lti/view.php?id=10',
        course_url=None,
    )


@pytest.mark.asyncio
async def test_leganto_pdf_walks_three_stage_fallback_chain(task_factory):
    """stored_lti 失败 → moodle_lti 失败 → course_url 成功。

    锁定新加的第三级 fallback：当 LTI launch 也跳回主页时（用户报告的真实
    场景），从课程页点 Reading List 链接是已知有效的最后一招。
    """
    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        content_filename='Reading List.pdf',
        cookies_text='cookie-data',
    )
    task.file.content = json.dumps({
        'endpoint': 'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        'parameters': [{'name': 'id_token', 'value': make_jwt({'exp': 9999999999})}],
    })
    await task._prepare_download()

    with patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls:
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock(side_effect=[
            RuntimeError('Invalid token'),                                # stage 1 fail
            RuntimeError('Leganto reading list did not load; final URL was /my/'),  # stage 2 fail
            None,                                                          # stage 3 success
        ])

        await task._download_leganto_reading_list_pdf()

    assert printer.print_to_pdf.await_count == 3
    # stage 3 = course_url 路径
    final_call = printer.print_to_pdf.await_args_list[2]
    assert final_call.args[0] == 'https://moodle.example.com/course/view.php?id=7'
    assert final_call.kwargs == {
        'launch_parameters': None,
        'moodle_launch_url': None,
        'course_url': 'https://moodle.example.com/course/view.php?id=7',
    }


@pytest.mark.asyncio
async def test_leganto_permanent_failure_short_circuits_remaining_fallbacks(task_factory):
    """Reading List 已被删除（LegantoPermanentFailureError）→ 后面的 fallback 不再尝试。

    课程管理员删了 list 后，stored_lti / moodle_lti / course_url 三条路都会
    跳到同一个 nui/error/* 页。重试只是浪费 wall-clock budget；用户也无法救。
    """
    from moodle_dl.downloader.leganto_print import LegantoPermanentFailureError

    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        content_filename='Reading List.pdf',
        cookies_text='cookie-data',
    )
    task.file.content = json.dumps({
        'endpoint': 'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
        'parameters': [{'name': 'id_token', 'value': make_jwt({'exp': 9999999999})}],
    })
    await task._prepare_download()

    with patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls:
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock(
            side_effect=LegantoPermanentFailureError('Leganto reading list deleted'),
        )

        with pytest.raises(LegantoPermanentFailureError, match='deleted'):
            await task._download_leganto_reading_list_pdf()

    # 关键：只调了 1 次，后续 2 级 fallback 没尝试
    assert printer.print_to_pdf.await_count == 1


@pytest.mark.asyncio
async def test_leganto_pdf_retry_with_empty_url_uses_course_launch_fallback(task_factory):
    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='',
        content_filename='Reading List.pdf',
        cookies_text='cookie-data',
    )
    task.file.content = 'not-json'
    await task._prepare_download()

    with patch('moodle_dl.downloader.task.LegantoPdfPrinter') as printer_cls:
        printer = printer_cls.return_value
        printer.print_to_pdf = AsyncMock()

        await task._download_leganto_reading_list_pdf()

    printer.print_to_pdf.assert_awaited_once_with(
        'https://moodle.example.com/mod/lti/view.php?id=10',
        task.file.saved_to,
        launch_parameters=None,
        moodle_launch_url='https://moodle.example.com/mod/lti/view.php?id=10',
        course_url=None,
    )


@pytest.mark.asyncio
async def test_leganto_pdf_download_raises_when_no_launch_data_available(task_factory):
    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='',
        content_filename='Reading List.pdf',
    )
    task.file.module_id = None
    task.opts.moodle_url = ''
    task.course.id = None
    await task._prepare_download()

    with pytest.raises(RuntimeError, match='Leganto launch data is unavailable'):
        await task._download_leganto_reading_list_pdf()


@pytest.mark.asyncio
async def test_leganto_pdf_process_raises_when_export_fails(task_factory):
    task = task_factory(
        content_type='leganto_pdf',
        content_fileurl='',
        content_filename='Reading List.pdf',
        cookies_text='cookie-data',
    )
    await task._prepare_download()

    with patch.object(
        task,
        '_download_leganto_reading_list_pdf',
        AsyncMock(side_effect=RuntimeError('Failed LTI')),
    ):
        with pytest.raises(RuntimeError, match='Failed LTI'):
            await task._execute_download()

    assert task.filename == 'Reading List.pdf'
    assert task.file.content_fileurl == ''
    assert task.file.saved_to.endswith('Reading List.pdf')


def test_leganto_course_url_is_only_built_for_direct_reading_list(task_factory):
    direct = task_factory(
        content_fileurl='https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
    )
    assert direct._leganto_course_url() == 'https://moodle.example.com/course/view.php?id=7'

    lti_launch = task_factory(
        content_fileurl='https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
    )
    assert lti_launch._leganto_course_url() is None

    retry_leganto_pdf = task_factory(
        content_type='leganto_pdf',
        content_fileurl='',
    )
    assert retry_leganto_pdf._leganto_course_url() == 'https://moodle.example.com/course/view.php?id=7'
    assert retry_leganto_pdf._leganto_moodle_launch_url() == 'https://moodle.example.com/mod/lti/view.php?id=10'


def test_leganto_url_helpers_return_none_without_required_context(task_factory):
    no_moodle_url = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
    )
    no_moodle_url.opts.moodle_url = ''
    assert no_moodle_url._leganto_course_url() is None
    assert no_moodle_url._leganto_moodle_launch_url() is None

    no_course_id = task_factory(
        content_type='leganto_pdf',
        content_fileurl='https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML',
    )
    no_course_id.course.id = None
    assert no_course_id._leganto_course_url() is None

    no_module_id = task_factory(content_type='leganto_pdf')
    no_module_id.file.module_id = None
    assert no_module_id._leganto_moodle_launch_url() is None


def test_leganto_lti_launch_token_expiry_handles_malformed_parameters(task_factory):
    task = task_factory(content_type='leganto_pdf')

    assert task._leganto_lti_launch_token_expiry([{'name': 'other', 'value': make_jwt({'exp': 123})}]) is None
    assert task._leganto_lti_launch_token_expiry(['not-a-dict', {'name': 'other'}]) is None
    assert task._leganto_lti_launch_token_expiry([{'name': 'id_token', 'value': 123}]) is None
    assert task._leganto_lti_launch_token_expiry([{'name': 'id_token', 'value': 'not.jwt'}]) is None
    assert task._leganto_lti_launch_token_expiry([{'name': 'id_token', 'value': make_jwt({'exp': 123})}]) == 123


def test_remove_leganto_shortcut_fallbacks_ignores_empty_target(task_factory):
    task = task_factory(content_type='leganto_pdf')
    task.file.saved_to = ''

    with patch.object(task, '_remove_path_and_appledouble') as remove_path:
        task._remove_leganto_shortcut_fallbacks()

    remove_path.assert_not_called()


def test_remove_path_and_appledouble_ignores_invalid_appledouble_target():
    with patch('moodle_dl.downloader.task.PT.remove_file') as remove_file:
        Task._remove_path_and_appledouble('/')

    remove_file.assert_called_once_with('/')


@pytest.mark.asyncio
async def test_external_download_url_dispatches_by_head_info(task_factory):
    external_cmd = 'download-tool %U'
    html_with_downloader = task_factory(content_fileurl='https://video.example.com/watch')
    html_with_downloader.opts.external_file_downloaders = {'video.example.com': external_cmd}
    html_with_downloader.file.saved_to = str(Path(html_with_downloader.destination) / 'watch.url')
    html_with_downloader.get_head_infos = AsyncMock(return_value=HeadInfo(
        content_type='text/html',
        content_length=10,
        last_modified=None,
        final_url='https://video.example.com/watch',
        guessed_file_name='watch',
        host='video.example.com',
    ))
    html_with_downloader.download_using_external_downloader = AsyncMock()

    await html_with_downloader.external_download_url(
        add_token=False,
        delete_if_successful=False,
        needs_moodle_cookies=False,
    )

    html_with_downloader.download_using_external_downloader.assert_awaited_once_with(
        dl_url='https://video.example.com/watch',
        external_dl_cmd=external_cmd,
        delete_if_successful=False,
    )

    cookie_html = task_factory(module_modname='cookie_mod-helixmedia', cookies_text='cookies')
    cookie_html.get_head_infos = AsyncMock(return_value=HeadInfo(
        content_type='text/html',
        content_length=10,
        last_modified=None,
        final_url='https://moodle.example.com/video',
        guessed_file_name='video',
        host='moodle.example.com',
    ))
    cookie_html.is_blocked_for_yt_dlp = MagicMock(return_value=False)
    cookie_html.download_using_yt_dlp = AsyncMock(return_value=True)

    await cookie_html.external_download_url(
        add_token=False,
        delete_if_successful=False,
        needs_moodle_cookies=True,
    )

    cookie_html.download_using_yt_dlp.assert_awaited_once()

    html_shortcut = task_factory(content_fileurl='https://site.example.com/page')
    html_shortcut.file.saved_to = str(Path(html_shortcut.destination) / 'page.url')
    html_shortcut.get_head_infos = AsyncMock(return_value=HeadInfo(
        content_type='text/html',
        content_length=10,
        last_modified=None,
        final_url='https://site.example.com/page',
        guessed_file_name='page',
        host='site.example.com',
    ))
    html_shortcut.create_shortcut = AsyncMock()

    await html_shortcut.external_download_url(
        add_token=False,
        delete_if_successful=True,
        needs_moodle_cookies=False,
    )

    html_shortcut.create_shortcut.assert_awaited_once()

    direct = task_factory(content_type='description-url', content_filename='old-name.url')
    direct.get_head_infos = AsyncMock(return_value=HeadInfo(
        content_type='application/pdf',
        content_length=10,
        last_modified='Wed, 21 Oct 2015 07:28:00 GMT',
        final_url='https://files.example.com/paper.pdf',
        guessed_file_name='paper.pdf',
        host='files.example.com',
    ))
    direct.download_url = AsyncMock()
    Path(direct.destination).mkdir(parents=True, exist_ok=True)

    await direct.external_download_url(
        add_token=False,
        delete_if_successful=False,
        needs_moodle_cookies=False,
    )

    assert direct.filename == 'paper.pdf'
    direct.download_url.assert_awaited_once_with('https://example.com/file.pdf', direct.file.saved_to)


@pytest.mark.asyncio
async def test_external_download_url_rejects_missing_moodle_cookies_and_head_failures(task_factory):
    missing_cookies = task_factory(cookies_text=None)
    with pytest.raises(ValueError, match='Moodle cookies are missing'):
        await missing_cookies.external_download_url(
            add_token=False,
            delete_if_successful=False,
            needs_moodle_cookies=True,
        )

    failed_head = task_factory()
    failed_head.get_head_infos = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match='无法获取外部链接信息'):
        await failed_head.external_download_url(
            add_token=False,
            delete_if_successful=False,
            needs_moodle_cookies=False,
        )


@pytest.mark.asyncio
async def test_create_shortcut_writes_url_and_desktop_files(task_factory):
    task = task_factory(
        content_fileurl='https://example.com/resource',
        write_links={'url': True, 'desktop': True},
    )
    Path(task.destination).mkdir(parents=True, exist_ok=True)
    task.file.saved_to = str(Path(task.destination) / 'resource')

    await task.create_shortcut()

    url_file = Path(task.destination) / 'file.pdf.url'
    desktop_file = Path(task.destination) / 'file.pdf.desktop'
    assert 'https://example.com/resource' in url_file.read_text(encoding='utf-8')
    assert 'https://example.com/resource' in desktop_file.read_text(encoding='utf-8')
    assert 'Name=' in desktop_file.read_text(encoding='utf-8')


@pytest.mark.asyncio
async def test_empty_html_and_content_files_are_removed(task_factory, tmp_path):
    html_task = task_factory(content_type='html')
    html_path = tmp_path / 'empty.html'
    html_path.write_text('old html', encoding='utf-8')
    html_task.file.saved_to = str(html_path)
    await html_task.create_html_file()
    assert not html_path.exists()

    content_task = task_factory(content_type='content')
    content_path = tmp_path / 'empty.json'
    content_path.write_text('old content', encoding='utf-8')
    content_task.file.saved_to = str(content_path)
    await content_task.create_content_file()
    assert not content_path.exists()


@pytest.mark.asyncio
async def test_create_data_url_file_rejects_bad_scheme_and_writes_http_data(task_factory, tmp_path):
    bad_scheme = task_factory(content_fileurl='file:///etc/passwd')
    assert await bad_scheme.create_data_url_file() is False

    http_task = task_factory(content_fileurl='https://example.com/data.bin', content_filename='data.bin')
    http_task.file.saved_to = str(tmp_path / 'data.bin')
    Path(http_task.destination).mkdir(parents=True, exist_ok=True)
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'data'
    response.__exit__.return_value = False

    with patch('moodle_dl.downloader.task.urllib.request.urlopen', return_value=response):
        await http_task.create_data_url_file()

    assert Path(http_task.file.saved_to).read_bytes() == b'data'


@pytest.mark.asyncio
async def test_metadata_prepare_directory_and_error_handlers(task_factory, tmp_path):
    metadata = task_factory(content_filename='metadata.json', download_metadata_files=False)
    assert await metadata._handle_metadata_file() is True
    assert metadata.status.state == TaskState.FINISHED
    assert metadata.events[-1][0] == DlEvent.FINISHED

    metadata_enabled = task_factory(content_filename='metadata.json', download_metadata_files=True)
    assert await metadata_enabled._handle_metadata_file() is False

    regular_file = task_factory(content_filename='lecture.pdf', download_metadata_files=False)
    assert await regular_file._handle_metadata_file() is False

    directory = task_factory(content_type='directory_placeholder')
    await directory._handle_directory_placeholder()
    assert directory.file.saved_to == directory.destination
    assert directory.status.state == TaskState.FINISHED

    prepared = task_factory()
    prepared.set_path = MagicMock()
    prepared.rename_old_file = MagicMock()
    prepared.move_old_file = MagicMock(return_value=False)
    prepared.file.modified = True
    prepared.file.moved = True
    assert await prepared._prepare_download() is True
    prepared.rename_old_file.assert_called_once()
    prepared.set_path.assert_called_once()
    prepared.move_old_file.assert_called_once()

    failing_file = tmp_path / 'failed.pdf'
    failing_file.write_text('partial', encoding='utf-8')
    failed = task_factory()
    failed.file.saved_to = str(failing_file)
    failed.status.bytes_downloaded = 7
    await failed._handle_error(RuntimeError('boom'))
    assert failed.status.state == TaskState.FAILED
    assert not failing_file.exists()
    assert (DlEvent.RECEIVED, {'bytes_received': -7}) in failed.events
    assert failed.events[-1][0] == DlEvent.FAILED


@pytest.mark.asyncio
async def test_cookie_jar_and_range_download_helpers(task_factory):
    assert task_factory(cookies_text=None).get_cookie_jar() is None

    task = task_factory(cookies_text='cookie-data')
    with (
        patch('moodle_dl.downloader.task.MoodleDLCookieJar') as cookie_jar_cls,
        patch('moodle_dl.downloader.task.convert_to_aiohttp_cookie_jar') as convert,
    ):
        cookie_jar = MagicMock()
        cookie_jar_cls.return_value = cookie_jar
        convert.return_value = 'aiohttp-cookie-jar'

        assert task.get_cookie_jar() == 'aiohttp-cookie-jar'

    cookie_jar.load.assert_called_once_with(ignore_discard=True, ignore_expires=True)
    convert.assert_called_once_with(cookie_jar)

    class FakeSession:
        async def request(self, method, url, headers):
            return MagicMock(status=206, headers={'Content-Range': 'bytes 0-4/10'})

    assert await task.check_range_download_opt('https://example.com/file', FakeSession()) is True

    class RaisingSession:
        async def request(self, method, url, headers):
            raise RuntimeError('network failed')

    assert await task.check_range_download_opt('https://example.com/file', RaisingSession()) is False


@pytest.mark.asyncio
async def test_cookie_jar_conversion_is_cached_per_download_options(task_factory):
    task = task_factory(cookies_text='cookie-data')
    with (
        patch('moodle_dl.downloader.task.MoodleDLCookieJar') as cookie_jar_cls,
        patch('moodle_dl.downloader.task.convert_to_aiohttp_cookie_jar') as convert,
    ):
        cookie_jar = MagicMock()
        cookie_jar_cls.return_value = cookie_jar
        convert.return_value = 'aiohttp-cookie-jar'

        assert task.get_cookie_jar() == 'aiohttp-cookie-jar'
        assert task.get_cookie_jar() == 'aiohttp-cookie-jar'

    cookie_jar_cls.assert_called_once()
    cookie_jar.load.assert_called_once_with(ignore_discard=True, ignore_expires=True)
    convert.assert_called_once_with(cookie_jar)


class FakeHeadClientSession:
    response = None
    error = None
    captured = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeHeadClientSession.captured.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def request(self, method, url, headers, ssl, timeout):
        if FakeHeadClientSession.error is not None:
            raise FakeHeadClientSession.error
        assert method == 'HEAD'
        assert headers == Task.RQ_HEADER
        assert timeout == 20
        return FakeAsyncContext(FakeHeadClientSession.response)


@pytest.fixture
def fake_head_client_session():
    FakeHeadClientSession.response = None
    FakeHeadClientSession.error = None
    FakeHeadClientSession.captured = []

    with patch('moodle_dl.downloader.task.aiohttp.ClientSession', FakeHeadClientSession):
        yield FakeHeadClientSession


@pytest.mark.asyncio
async def test_get_head_infos_extracts_response_metadata(task_factory, fake_head_client_session):
    task = task_factory(cookies_text='cookie-data')
    fake_cookie_jar = object()
    task.get_cookie_jar = MagicMock(return_value=fake_cookie_jar)
    ssl_context = object()
    fake_head_client_session.response = SimpleNamespace(
        url=URL('https://cdn.example.com/download/server-name.pdf'),
        history=[object()],
        headers={
            'Content-Disposition': 'attachment; filename=from-header.pdf',
            'Content-Type': 'application/pdf; charset=utf-8',
            'Content-Length': '321',
            'Last-Modified': 'Wed, 21 Oct 2015 07:28:00 GMT',
        },
    )

    with patch('moodle_dl.downloader.task.SslHelper.get_ssl_context', return_value=ssl_context) as get_ssl_context:
        infos = await task.get_head_infos('https://example.com/file.pdf')

    get_ssl_context.assert_called_once_with(False, False, False)
    assert fake_head_client_session.captured == [{'cookie_jar': fake_cookie_jar, 'raise_for_status': True}]
    assert infos.content_type == 'application/pdf'
    assert infos.content_length == 321
    assert infos.last_modified == 'Wed, 21 Oct 2015 07:28:00 GMT'
    assert infos.final_url == 'https://cdn.example.com/download/server-name.pdf'
    assert infos.guessed_file_name == 'from-header.pdf'
    assert infos.host == 'cdn.example.com'


@pytest.mark.asyncio
async def test_get_head_infos_returns_none_for_invalid_url_and_non_retryable_http_errors(
    task_factory,
    fake_head_client_session,
):
    task = task_factory()

    fake_head_client_session.error = aiohttp.InvalidURL('mailto:user@example.com')
    assert await task.get_head_infos('mailto:user@example.com') is None

    fake_head_client_session.error = aiohttp.ClientResponseError(
        request_info=None,
        history=(),
        status=404,
        message='not found',
    )
    assert await task.get_head_infos('https://example.com/missing') is None


@pytest.mark.asyncio
async def test_get_head_infos_reraises_retryable_and_unexpected_errors(task_factory, fake_head_client_session):
    task = task_factory()

    fake_head_client_session.error = aiohttp.ClientResponseError(
        request_info=None,
        history=(),
        status=429,
        message='too many requests',
    )
    with pytest.raises(aiohttp.ClientResponseError):
        await task.get_head_infos('https://example.com/rate-limited')

    fake_head_client_session.error = ValueError('bad content length')
    with pytest.raises(ValueError, match='bad content length'):
        await task.get_head_infos('https://example.com/bad')


@pytest.mark.asyncio
async def test_perform_download_request_writes_chunks_and_reports_progress(task_factory, tmp_path):
    """_perform_download_request now writes to .part suffix. Caller is
    expected to rename after success (download_url does this)."""
    task = task_factory()
    dest_path = tmp_path / 'download.bin'
    from moodle_dl.downloader.task import dest_path_to_part_path
    part_path = dest_path_to_part_path(str(dest_path))

    class FakeContent:
        async def iter_chunked(self, _chunk_size):
            yield b'ab'
            yield b'cde'

    class FakeResponse:
        status = 200
        headers = {'Content-Length': '5'}
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def request(self, method, url, headers, ssl, timeout):
            assert method == 'GET'
            return FakeResponse()

    file_obj, total, content_length, content_range = await task._perform_download_request(
        FakeSession(),
        'https://example.com/download.bin',
        str(dest_path),
        {},
        None,
        10,
        None,
        0,
    )
    await file_obj.close()

    assert total == 5
    assert content_length == 5
    assert content_range is None
    # .part file holds the data; rename to final mimics the production flow
    with open(part_path, 'rb') as f:
        assert f.read() == b'abcde'
    import os
    os.replace(part_path, str(dest_path))
    assert dest_path.read_bytes() == b'abcde'
    assert task.events == [
        (DlEvent.TOTAL_SIZE, {'content_length': 5}),
        (DlEvent.RECEIVED, {'bytes_received': 2}),
        (DlEvent.RECEIVED, {'bytes_received': 3}),
    ]


@pytest.mark.asyncio
async def test_perform_download_request_raises_retry_marker_for_gzip_payload_errors(task_factory, tmp_path):
    task = task_factory()

    class BrokenContent:
        async def iter_chunked(self, _chunk_size):
            raise aiohttp.ClientPayloadError('gzip content-encoding failed')
            yield b''

    class FakeResponse:
        status = 200
        headers = {'Content-Length': '5'}
        content = BrokenContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def request(self, method, url, headers, ssl, timeout):
            return FakeResponse()

    with pytest.raises(ValueError, match='需要禁用压缩重试'):
        await task._perform_download_request(
            FakeSession(),
            'https://example.com/download.bin',
            str(tmp_path / 'download.bin'),
            {},
            None,
            10,
            None,
            0,
        )


@pytest.mark.asyncio
async def test_download_url_retries_without_compression_after_payload_marker(task_factory, tmp_path):
    task = task_factory()
    dest_path = tmp_path / 'download.bin'
    fake_file = SimpleNamespace(closed=False, close=AsyncMock())
    task._perform_download_request = AsyncMock(side_effect=[
        ValueError('需要禁用压缩重试'),
        (fake_file, 5, 5, None),
    ])

    await task.download_url('https://example.com/download.bin', str(dest_path))

    assert task._perform_download_request.await_count == 2
    assert task._perform_download_request.await_args_list[1].kwargs['disable_compression'] is True
    fake_file.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_and_extract_kalvidres_text(task_factory, tmp_path):
    task = task_factory()
    save_path = tmp_path / 'notes.md'

    await task._save_kalvidres_text(
        {
            'page_title': 'Page Title',
            'module_name': 'Module Name',
            'activity_description': 'Line 1\nLine 2',
        },
        str(save_path),
    )

    assert save_path.read_text(encoding='utf-8') == '# Page Title\n\n## Module Name\n\nLine 1\nLine 2\n'

    response = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/mod/kalvidres/view.php?id=1',
        text=(
            '<html><head><title>Video page</title></head>'
            '<body><h1><span>Lecture</span></h1>'
            '<div class="activity-description"><p>Hello<br>world</p></div></div>'
            '</body></html>'
        ),
    )
    session = MagicMock()
    session.get.return_value = response
    task._save_kalvidres_text = AsyncMock()

    with patch('moodle_dl.downloader.task.requests.Session', return_value=session):
        assert await task.extract_kalvidres_text(
            'https://moodle.example.com/mod/kalvidres/view.php?id=1',
            str(tmp_path / 'extracted.md'),
        ) is True

    task._save_kalvidres_text.assert_awaited_once()
    saved_data = task._save_kalvidres_text.await_args.args[0]
    assert saved_data['page_title'] == 'Video page'
    assert saved_data['module_name'] == 'Lecture'
    assert 'Hello' in saved_data['activity_description']

    login_response = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/login/index.php',
        text='<html></html>',
    )
    session.get.return_value = login_response
    assert await task.extract_kalvidres_text(
        'https://moodle.example.com/mod/kalvidres/view.php?id=1',
        str(tmp_path / 'redirect.md'),
    ) is False


@pytest.mark.asyncio
async def test_extract_kalvidres_text_handles_cookie_fallback_and_empty_pages(task_factory, tmp_path):
    cookie_task = task_factory(cookies_text='cookie text')
    failed_session = MagicMock()
    failed_session.get.return_value = SimpleNamespace(
        status_code=500,
        url='https://moodle.example.com/mod/kalvidres/view.php?id=1',
        text='',
    )

    with patch('moodle_dl.downloader.task.requests.Session', return_value=failed_session), patch(
        'moodle_dl.downloader.task.MoodleDLCookieJar'
    ) as cookie_jar_cls:
        cookie_jar = MagicMock()
        cookie_jar_cls.return_value = cookie_jar

        assert await cookie_task.extract_kalvidres_text(
            'https://moodle.example.com/mod/kalvidres/view.php?id=1',
            str(tmp_path / 'failed.md'),
        ) is False

    cookie_jar_cls.assert_called_once()
    cookie_jar.load.assert_called_once_with(ignore_discard=True, ignore_expires=True)
    assert failed_session.cookies is cookie_jar

    login_task = task_factory()
    login_session = MagicMock()
    login_session.get.return_value = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/login/index.php',
        text='<html></html>',
    )
    with patch('moodle_dl.downloader.task.requests.Session', return_value=login_session):
        assert await login_task.extract_kalvidres_text(
            'https://moodle.example.com/mod/kalvidres/view.php?id=1',
            str(tmp_path / 'login.md'),
        ) is False

    empty_task = task_factory()
    empty_task._save_kalvidres_text = AsyncMock()
    empty_session = MagicMock()
    empty_session.get.return_value = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/mod/kalvidres/view.php?id=1',
        text='<html><body></body></html>',
    )
    with patch('moodle_dl.downloader.task.requests.Session', return_value=empty_session):
        assert await empty_task.extract_kalvidres_text(
            'https://moodle.example.com/mod/kalvidres/view.php?id=1',
            str(tmp_path / 'empty.md'),
        ) is False

    empty_task._save_kalvidres_text.assert_not_awaited()


# ---- Session warm-up on enrol/login redirect -------------------------------
#
# 真实场景：用户已注册 PEP 课程，但 kalvidres 请求落到 enrol/index.php。
# Moodle 服务端的 session 偶尔会进入"降级"状态——cookie 有效，但服务端没认。
# 一次便宜的主页 GET 通常就能恢复。整个机制对每个 Task 实例透明，但状态在
# 类级别共享（节流 + Lock），所以测试要小心隔离。

def _reset_warmup_state():
    Task._last_session_warmup_at = 0.0
    Task._session_warmup_lock = None


@pytest.fixture(autouse=False)
def warmup_isolation():
    """每个 warm-up 测试前后清掉类级状态，避免相互污染。"""
    _reset_warmup_state()
    yield
    _reset_warmup_state()


@pytest.mark.asyncio
async def test_extract_kalvidres_text_warms_up_session_on_enrol_redirect(task_factory, tmp_path, warmup_isolation):
    """enrol/index.php 重定向 → warm-up 一次 → 重试拿到真页面 → 成功提取"""
    task = task_factory()

    enrol_redirect = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/enrol/index.php?id=119910',
        text='<html>enrol page</html>',
    )
    warmup_ok = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/my/',
        text='<html>my dashboard</html>',
    )
    real_page = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/mod/kalvidres/view.php?id=1',
        text='<html><head><title>Lecture</title></head><body><h1>Hello</h1></body></html>',
    )

    # 3 个 session 各自一次 .get()：原 fetch → warm-up → 重试 fetch
    responses_iter = iter([enrol_redirect, warmup_ok, real_page])
    sessions = []

    def make_session():
        sess = MagicMock()
        sess.get = MagicMock(side_effect=lambda *a, **kw: next(responses_iter))
        sessions.append(sess)
        return sess

    task._save_kalvidres_text = AsyncMock()
    with patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session):
        result = await task.extract_kalvidres_text(
            'https://moodle.example.com/mod/kalvidres/view.php?id=1',
            str(tmp_path / 'ok.md'),
        )

    assert result is True
    task._save_kalvidres_text.assert_awaited_once()
    # 3 个 session 被创建：原 fetch / warm-up GET / 重试 fetch
    assert len(sessions) == 3
    # 类状态被更新（节流计时器记上了）
    assert Task._last_session_warmup_at > 0


@pytest.mark.asyncio
async def test_extract_kalvidres_text_gives_up_after_warmup_still_redirects(task_factory, tmp_path, warmup_isolation):
    """warm-up 后还是 enrol/login → 标记 cookies 失效或未注册，返回 False，不再循环"""
    task = task_factory()

    redirect = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/login/index.php',
        text='<html>login</html>',
    )

    sessions = []

    def make_session():
        sess = MagicMock()
        # 所有 fetch 都返回 redirect
        sess.get = MagicMock(return_value=redirect)
        sessions.append(sess)
        return sess

    with patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session):
        result = await task.extract_kalvidres_text(
            'https://moodle.example.com/mod/kalvidres/view.php?id=1',
            str(tmp_path / 'fail.md'),
        )

    assert result is False
    # 只重试 1 次（warm-up + retry），不会无限循环
    # 原 fetch (1) + warm-up GET (1) + 重试 fetch (1) = 3
    assert len(sessions) == 3


@pytest.mark.asyncio
async def test_warmup_is_throttled_within_window(task_factory, warmup_isolation):
    """5 分钟窗口内第二次调用直接返回 False，不发起 GET。"""
    task = task_factory()

    sessions = []

    def make_session():
        sess = MagicMock()
        sess.get = MagicMock(return_value=SimpleNamespace(status_code=200, url='https://moodle.example.com/my/'))
        sessions.append(sess)
        return sess

    with patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session):
        first = await task._try_warmup_session('moodle.example.com')
        second = await task._try_warmup_session('moodle.example.com')

    assert first is True
    assert second is False, '5 分钟节流应阻止第二次 warm-up'
    assert len(sessions) == 1, '第二次调用不应创建新 session'


@pytest.mark.asyncio
async def test_warmup_swallows_network_errors(task_factory, warmup_isolation):
    """warm-up 自身失败不应抛——只是个保底机制"""
    task = task_factory()

    def make_session():
        sess = MagicMock()
        sess.get = MagicMock(side_effect=requests.ConnectionError('网络断了'))
        return sess

    with patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session):
        # 不应抛
        result = await task._try_warmup_session('moodle.example.com')

    # 仍返回 True（warm-up 这个"动作"已经执行了），并且节流时间戳已更新
    assert result is True
    assert Task._last_session_warmup_at > 0


@pytest.mark.asyncio
async def test_warmup_lock_lazy_initializes(warmup_isolation):
    """Lock 必须惰性创建，避免绑定到 import 时的 event loop。"""
    assert Task._session_warmup_lock is None
    lock = Task._get_session_warmup_lock()
    assert lock is not None
    # 第二次调用返回同一个实例
    assert Task._get_session_warmup_lock() is lock


@pytest.mark.asyncio
async def test_extract_kalvidres_text_does_not_warmup_on_cross_domain_redirect(task_factory, tmp_path, warmup_isolation):
    """重定向到外部 SSO (Microsoft) 不算 session 降级，不应触发 warm-up"""
    task = task_factory()

    sso_redirect = SimpleNamespace(
        status_code=200,
        url='https://login.microsoftonline.com/common/oauth2/authorize?...',
        text='<html>SSO</html>',
    )

    sessions = []

    def make_session():
        sess = MagicMock()
        sess.get = MagicMock(return_value=sso_redirect)
        sessions.append(sess)
        return sess

    with patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session):
        result = await task.extract_kalvidres_text(
            'https://moodle.example.com/mod/kalvidres/view.php?id=1',
            str(tmp_path / 'sso.md'),
        )

    assert result is False
    # 只有原 fetch，没有 warm-up 也没有重试
    assert len(sessions) == 1
    assert Task._last_session_warmup_at == 0.0


@pytest.mark.asyncio
async def test_concurrent_warmup_calls_serialize_and_only_one_fires(task_factory, warmup_isolation):
    """两个并发 task 同时撞上 enrol → Lock 串行化 → 只有 1 个真正发 GET。

    没这一条，删掉 _session_warmup_lock 整个文件别的测试还能过——下载并发场景
    才是 Lock 存在的全部理由。
    """
    task_a = task_factory()
    task_b = task_factory()

    sessions = []

    def make_session():
        sess = MagicMock()
        sess.get = MagicMock(return_value=SimpleNamespace(
            status_code=200,
            url='https://moodle.example.com/my/',
        ))
        sessions.append(sess)
        return sess

    with patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session):
        results = await asyncio.gather(
            task_a._try_warmup_session('moodle.example.com'),
            task_b._try_warmup_session('moodle.example.com'),
        )

    # 一个真跑了 warm-up（True），另一个被节流（False）—— 顺序不保证
    assert sorted(results) == [False, True]
    assert len(sessions) == 1, '并发调用只应触发 1 次 warm-up GET'


@pytest.mark.asyncio
async def test_warmup_runs_again_after_throttle_window_expires(task_factory, warmup_isolation):
    """超过 5 分钟窗口后，再次调用应真正发 warm-up；不能永久卡住。"""
    task = task_factory()

    sessions = []

    def make_session():
        sess = MagicMock()
        sess.get = MagicMock(return_value=SimpleNamespace(status_code=200, url='https://moodle.example.com/my/'))
        sessions.append(sess)
        return sess

    with patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session):
        first = await task._try_warmup_session('moodle.example.com')
        # 把时间戳推回到 6 分钟前，模拟窗口过期
        Task._last_session_warmup_at -= (Task.SESSION_WARMUP_MIN_INTERVAL_S + 60)
        second = await task._try_warmup_session('moodle.example.com')

    assert first is True
    assert second is True, '节流窗口过期后应允许再次 warm-up'
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_warmup_passes_existing_cookies_to_homepage_request(task_factory, warmup_isolation):
    """warm-up 的灵魂就是携带现有 cookie 让 Moodle 重新识别——少了这步整个机制无意义。"""
    task = task_factory(cookies_text='# Netscape cookie jar')

    cookie_jar_sentinel = MagicMock(name='cookie_jar')
    sessions = []

    def make_session():
        sess = MagicMock()
        sess.get = MagicMock(return_value=SimpleNamespace(status_code=200, url='https://moodle.example.com/my/'))
        sessions.append(sess)
        return sess

    with (
        patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session),
        patch.object(Task, '_get_requests_cookie_jar', return_value=cookie_jar_sentinel),
    ):
        result = await task._try_warmup_session('moodle.example.com')

    assert result is True
    assert len(sessions) == 1
    assert sessions[0].cookies is cookie_jar_sentinel, 'warm-up 必须用现有 cookie，否则等于没刷'


@pytest.mark.asyncio
async def test_extract_kalvidres_text_skips_retry_when_warmup_is_throttled(task_factory, tmp_path, warmup_isolation):
    """节流命中时不应再发起重试 fetch——这就是节流要省的开销，必须锁死。"""
    task = task_factory()

    # 预先把时间戳设到刚刚（在窗口内）让 warm-up 必被节流
    Task._last_session_warmup_at = asyncio.get_event_loop().time()

    redirect = SimpleNamespace(
        status_code=200,
        url='https://moodle.example.com/enrol/index.php?id=1',
        text='<html>enrol</html>',
    )
    sessions = []

    def make_session():
        sess = MagicMock()
        sess.get = MagicMock(return_value=redirect)
        sessions.append(sess)
        return sess

    with patch('moodle_dl.downloader.task.requests.Session', side_effect=make_session):
        result = await task.extract_kalvidres_text(
            'https://moodle.example.com/mod/kalvidres/view.php?id=1',
            str(tmp_path / 'throttled.md'),
        )

    assert result is False
    # 原 fetch 1 次；warm-up 被节流（无 GET）；不应有重试 fetch
    assert len(sessions) == 1, '节流命中时不应创建额外 session（既不 warm-up 也不 retry）'


@pytest.mark.asyncio
async def test_extract_kalvidres_video_url_success_and_error_paths(task_factory):
    task = task_factory()
    kalvidres_html = (
        '<iframe src="https://moodle.example.com/filter/kaltura/lti_launch.php?id=1&amp;foo=bar"></iframe>'
    )
    lti_html = (
        '<input name="target_link_uri" '
        'value="https://kaf.example.com/browseandembed/index/media/entryid/1_abcd/view/playerSkin/123456">'
    )
    browse_html = 'partnerId:987654 https://cdnapisec.kaltura.com/p/987654/embed'
    session = MagicMock()
    session.get.side_effect = [
        SimpleNamespace(status_code=200, text=kalvidres_html),
        SimpleNamespace(status_code=200, text=lti_html),
        SimpleNamespace(status_code=200, text=browse_html),
    ]
    task._create_session_with_retry = MagicMock(return_value=session)

    result = await task.extract_kalvidres_video_url('https://moodle.example.com/mod/kalvidres/view.php?id=1')

    assert result == (
        'https://cdnapisec.kaltura.com/p/987654/sp/98765400/embedIframeJs/'
        'uiconf_id/123456/partner_id/987654?iframeembed=true&entry_id=1_abcd'
    )

    forbidden = task_factory()
    forbidden_session = MagicMock()
    forbidden_session.get.return_value = SimpleNamespace(status_code=403, text='')
    forbidden._create_session_with_retry = MagicMock(return_value=forbidden_session)
    assert await forbidden.extract_kalvidres_video_url('https://moodle.example.com/video') is None

    missing_iframe = task_factory()
    missing_session = MagicMock()
    missing_session.get.return_value = SimpleNamespace(status_code=200, text='<html>No iframe</html>')
    missing_iframe._create_session_with_retry = MagicMock(return_value=missing_session)
    assert await missing_iframe.extract_kalvidres_video_url('https://moodle.example.com/video') is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('error', 'expected_calls'),
    [
        (requests.Timeout('slow'), 1),
        (requests.ConnectionError('offline'), 1),
    ],
)
async def test_extract_kalvidres_video_url_handles_initial_network_errors(task_factory, error, expected_calls):
    task = task_factory()
    session = MagicMock()
    session.get.side_effect = error
    task._create_session_with_retry = MagicMock(return_value=session)

    assert await task.extract_kalvidres_video_url('https://moodle.example.com/video') is None
    assert session.get.call_count == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize('status_code', [404, 500, 503])
async def test_extract_kalvidres_video_url_handles_initial_http_failures(task_factory, status_code):
    task = task_factory()
    session = MagicMock()
    session.get.return_value = SimpleNamespace(status_code=status_code, text='')
    task._create_session_with_retry = MagicMock(return_value=session)

    assert await task.extract_kalvidres_video_url('https://moodle.example.com/video') is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'lti_response',
    [
        requests.Timeout('slow lti'),
        SimpleNamespace(status_code=403, text=''),
        SimpleNamespace(status_code=500, text=''),
        SimpleNamespace(status_code=503, text=''),
        SimpleNamespace(status_code=200, text='<html>No target link</html>'),
    ],
)
async def test_extract_kalvidres_video_url_handles_lti_stage_failures(task_factory, lti_response):
    task = task_factory()
    session = MagicMock()
    kalvidres_html = (
        '<iframe src="https://moodle.example.com/filter/kaltura/lti_launch.php?id=1&amp;foo=bar"></iframe>'
    )
    first = SimpleNamespace(status_code=200, text=kalvidres_html)
    if isinstance(lti_response, BaseException):
        session.get.side_effect = [first, lti_response]
    else:
        session.get.side_effect = [first, lti_response]
    task._create_session_with_retry = MagicMock(return_value=session)

    assert await task.extract_kalvidres_video_url('https://moodle.example.com/video') is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'browse_response',
    [
        requests.Timeout('slow browse'),
        SimpleNamespace(status_code=403, text=''),
        SimpleNamespace(status_code=500, text=''),
        SimpleNamespace(status_code=503, text=''),
        SimpleNamespace(status_code=200, text='<html>No partner id</html>'),
    ],
)
async def test_extract_kalvidres_video_url_handles_browse_stage_failures(task_factory, browse_response):
    task = task_factory()
    session = MagicMock()
    kalvidres_html = (
        '<iframe src="https://moodle.example.com/filter/kaltura/lti_launch.php?id=1&amp;foo=bar"></iframe>'
    )
    lti_html = (
        '<input name="target_link_uri" '
        'value="https://kaf.example.com/browseandembed/index/media/entryid/1_abcd/view/playerSkin/123456">'
    )
    first = SimpleNamespace(status_code=200, text=kalvidres_html)
    second = SimpleNamespace(status_code=200, text=lti_html)
    session.get.side_effect = [first, second, browse_response]
    task._create_session_with_retry = MagicMock(return_value=session)

    assert await task.extract_kalvidres_video_url('https://moodle.example.com/video') is None


@pytest.mark.asyncio
async def test_extract_kalvidres_video_url_uses_kcl_partner_fallback(task_factory):
    task = task_factory()
    session = MagicMock()
    kalvidres_html = (
        '<iframe src="https://moodle.example.com/filter/kaltura/lti_launch.php?id=1&amp;foo=bar"></iframe>'
    )
    lti_html = (
        '<input name="target_link_uri" '
        'value="https://kaf.kcl.ac.uk/browseandembed/index/media/entryid/1_abcd/view/playerSkin/123456">'
    )
    browse_html = '<html>No partner id but KCL KAF launch is valid</html>'
    session.get.side_effect = [
        SimpleNamespace(status_code=200, text=kalvidres_html),
        SimpleNamespace(status_code=200, text=lti_html),
        SimpleNamespace(status_code=200, text=browse_html),
    ]
    task._create_session_with_retry = MagicMock(return_value=session)

    result = await task.extract_kalvidres_video_url('https://moodle.example.com/mod/kalvidres/view.php?id=1')

    assert result == (
        'https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/'
        'uiconf_id/123456/partner_id/2368101?iframeembed=true&entry_id=1_abcd'
    )


@pytest.mark.asyncio
async def test_extract_kalvidres_video_url_uses_keats_kaf_partner_fallback(task_factory):
    task = task_factory()
    session = MagicMock()
    kalvidres_html = (
        '<iframe src="https://moodle.example.com/filter/kaltura/lti_launch.php?id=1&amp;foo=bar"></iframe>'
    )
    lti_html = (
        '<input name="target_link_uri" '
        'value="http://kaf.keats.kcl.ac.uk/browseandembed/index/media/entryid/1_abcd/view/playerSkin/123456'
        '?foo=1&amp;bar=2">'
    )
    browse_html = '<html>No partner id but KEATS KAF launch is valid</html>'
    session.get.side_effect = [
        SimpleNamespace(status_code=200, text=kalvidres_html),
        SimpleNamespace(status_code=200, text=lti_html),
        SimpleNamespace(status_code=200, text=browse_html),
    ]
    task._create_session_with_retry = MagicMock(return_value=session)

    result = await task.extract_kalvidres_video_url('https://moodle.example.com/mod/kalvidres/view.php?id=1')

    assert result == (
        'https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/'
        'uiconf_id/123456/partner_id/2368101?iframeembed=true&entry_id=1_abcd'
    )
    assert session.get.call_args_list[2][0][0].endswith('?foo=1&bar=2')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('source_url', 'expected_uiconf', 'expected_entry'),
    [
        (
            'https://media.kcl.ac.uk/embed/secure/iframe/entryId/1_5eu7vehb/uiConfId/50622292',
            '50622292',
            '1_5eu7vehb',
        ),
        (
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?courseid=0&source='
            'https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2F'
            'entryid%2F1_3ljlwqoz%2FplayerSkin%2F42864872%2F',
            '42864872',
            '1_3ljlwqoz',
        ),
        (
            'https://keats.kcl.ac.uk/browseandembed/index/media/entryid/1_l0ay588r',
            '50622292',
            '1_l0ay588r',
        ),
    ],
)
async def test_extract_kalvidres_video_url_builds_from_known_kcl_embed_urls(
    task_factory,
    source_url,
    expected_uiconf,
    expected_entry,
):
    task = task_factory()
    task._create_session_with_retry = MagicMock(side_effect=AssertionError('network should not be used'))

    result = await task.extract_kalvidres_video_url(source_url)

    assert result == (
        'https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/'
        f'uiconf_id/{expected_uiconf}/partner_id/2368101?iframeembed=true&entry_id={expected_entry}'
    )
    task._create_session_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_extract_kalvidres_video_url_builds_playlist_url_from_kaf_source(task_factory):
    source_url = (
        'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?courseid=0&source='
        'https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2F'
        'entryid%2F1_3ljlwqoz%2FshowDescription%2Ffalse%2FplayerSkin%2F42864872%2F'
        'isPlaylist%2Ftrue%2F'
    )
    task = task_factory()
    task._create_session_with_retry = MagicMock(side_effect=AssertionError('network should not be used'))

    result = await task.extract_kalvidres_video_url(source_url)

    parsed = urlparse.urlparse(result)
    query = urlparse.parse_qs(parsed.query)
    assert parsed.netloc == 'cdnapisec.kaltura.com'
    assert parsed.path == '/html5/html5lib/v2.101/mwEmbedFrame.php/p/2368101/uiconf_id/42864872'
    assert query['wid'] == ['_2368101']
    assert query['flashvars[playlistAPI.kpl0Id]'] == ['1_3ljlwqoz']
    assert query['flashvars[playlistAPI.playlistUrl]'] == [
        'https://kaf.keats.kcl.ac.uk/playlist/details/{playlistAPI.kpl0Id}'
    ]
    assert 'entry_id' not in query
    task._create_session_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_extract_kalvidres_video_url_handles_bad_browse_url_and_unknown_errors(task_factory):
    bad_browse = task_factory()
    bad_session = MagicMock()
    bad_session.get.side_effect = [
        SimpleNamespace(
            status_code=200,
            text='<iframe src="https://moodle.example.com/filter/kaltura/lti_launch.php?id=1"></iframe>',
        ),
        SimpleNamespace(
            status_code=200,
            text='<input name="target_link_uri" value="https://kaf.example.com/no-entry-or-skin">',
        ),
    ]
    bad_browse._create_session_with_retry = MagicMock(return_value=bad_session)
    assert await bad_browse.extract_kalvidres_video_url('https://moodle.example.com/video') is None

    unknown = task_factory()
    unknown._create_session_with_retry = MagicMock(side_effect=RuntimeError('ssl certificate failed'))
    assert await unknown.extract_kalvidres_video_url('https://moodle.example.com/video') is None

    auth_unknown = task_factory()
    auth_unknown._create_session_with_retry = MagicMock(side_effect=RuntimeError('auth cookie rejected'))
    assert await auth_unknown.extract_kalvidres_video_url('https://moodle.example.com/video') is None

    timeout_unknown = task_factory()
    timeout_unknown._create_session_with_retry = MagicMock(side_effect=RuntimeError('timeout while preparing session'))
    assert await timeout_unknown.extract_kalvidres_video_url('https://moodle.example.com/video') is None


@pytest.mark.asyncio
async def test_extract_kalvidres_video_url_handles_generic_request_exception(task_factory):
    task = task_factory()
    session = MagicMock()
    session.get.side_effect = requests.RequestException('broken request')
    task._create_session_with_retry = MagicMock(return_value=session)

    assert await task.extract_kalvidres_video_url('https://moodle.example.com/video') is None


def test_kalvidres_html_cleaners_handle_empty_input(task_factory):
    task = task_factory()

    assert task._clean_html_simple('') == ''
    assert task._clean_html_preserve_structure('') == ''


class FakeYoutubeDL:
    result = 0
    error = None
    set_generic_extractor_warning = False
    instances = []

    def __init__(self, opts):
        self.opts = opts
        self.params = {}
        self._download_retcode = 99
        FakeYoutubeDL.instances.append(self)

    def download(self, dl_url):
        if FakeYoutubeDL.error is not None:
            raise FakeYoutubeDL.error
        if FakeYoutubeDL.set_generic_extractor_warning:
            self.opts['logger'].warning('Falling back on generic information extractor')
        return FakeYoutubeDL.result


@pytest.fixture
def fake_yt_dlp():
    FakeYoutubeDL.result = 0
    FakeYoutubeDL.error = None
    FakeYoutubeDL.set_generic_extractor_warning = False
    FakeYoutubeDL.instances = []

    with (
        patch('moodle_dl.downloader.task.yt_dlp.YoutubeDL', FakeYoutubeDL),
        patch('moodle_dl.downloader.task.add_additional_extractors') as add_extractors,
    ):
        yield FakeYoutubeDL, add_extractors


@pytest.mark.asyncio
async def test_download_using_yt_dlp_success_sets_options_and_password(task_factory, fake_yt_dlp):
    fake_cls, add_extractors = fake_yt_dlp
    task = task_factory(
        content_type='description-url',
        content_filename='lecture.url',
        cookies_text='cookie-data',
    )
    task.opts.video_passwords = {'video.example.com': 'secret'}
    infos = HeadInfo(
        content_type='text/html',
        content_length=1,
        last_modified=None,
        final_url='https://video.example.com/watch',
        guessed_file_name='watch',
        host='video.example.com',
    )

    assert await task.download_using_yt_dlp(
        'https://video.example.com/watch',
        infos,
        delete_if_successful=True,
    ) is True

    ydl = fake_cls.instances[0]
    assert '%(title).180B' in ydl.opts['outtmpl']
    assert ydl.params['videopassword'] == 'secret'
    assert 'cookiefile' in ydl.opts
    add_extractors.assert_called_once_with(ydl)


@pytest.mark.asyncio
async def test_download_using_yt_dlp_uses_distinct_names_for_kaltura_playlist_items(
    task_factory,
    fake_yt_dlp,
):
    fake_cls, _add_extractors = fake_yt_dlp
    task = task_factory(
        content_type='cookie_mod',
        content_filename='CVs and VMock - Video (1_3ljlwqoz).mp4',
    )
    infos = HeadInfo(
        content_type='text/html',
        content_length=1,
        last_modified=None,
        final_url='https://cdnapisec.kaltura.com/html5/html5lib/v2.101/mwEmbedFrame.php',
        guessed_file_name='mwEmbedFrame.php',
        host='cdnapisec.kaltura.com',
    )

    assert await task.download_using_yt_dlp(
        'https://cdnapisec.kaltura.com/html5/html5lib/v2.101/mwEmbedFrame.php?'
        'flashvars%5BplaylistAPI.kpl0Id%5D=1_3ljlwqoz',
        infos,
        delete_if_successful=True,
    ) is True

    outtmpl = fake_cls.instances[0].opts['outtmpl']
    assert 'CVs and VMock - Video (1_3ljlwqoz)' in outtmpl
    assert '%(playlist_index)02d' in outtmpl
    assert '%(title).120B' in outtmpl
    assert '%(id).32B' in outtmpl


@pytest.mark.asyncio
async def test_download_using_yt_dlp_returns_false_for_legacy_pages_and_generic_extractor(
    task_factory,
    fake_yt_dlp,
):
    fake_cls, _add_extractors = fake_yt_dlp
    infos = HeadInfo(
        content_type='text/html',
        content_length=1,
        last_modified=None,
        final_url='https://video.example.com/watch',
        guessed_file_name='watch',
        host='video.example.com',
    )

    legacy_page = task_factory(content_filename='page.html')
    legacy_page.file.module_name = 'index_mod-page'
    assert await legacy_page.download_using_yt_dlp(
        'https://video.example.com/watch',
        infos,
        delete_if_successful=True,
    ) is False

    index_asset = task_factory(module_modname='index_mod-page', content_filename='image.png')
    assert await index_asset.download_using_yt_dlp(
        'https://video.example.com/watch',
        infos,
        delete_if_successful=True,
    ) is True

    fake_cls.set_generic_extractor_warning = True
    generic = task_factory(content_filename='video.mp4')
    assert await generic.download_using_yt_dlp(
        'https://video.example.com/watch',
        infos,
        delete_if_successful=True,
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error_message',
    [
        'DRM protected stream',
        '403 Forbidden',
        '404 Not Found',
        '503 Service Unavailable',
        'Timeout while connecting',
        'InvalidURL bad url',
        'unexpected extractor failure',
    ],
)
async def test_download_using_yt_dlp_raises_helpful_error_for_failures(
    task_factory,
    fake_yt_dlp,
    error_message,
):
    fake_cls, _add_extractors = fake_yt_dlp
    fake_cls.error = RuntimeError(error_message)
    task = task_factory(content_filename='video.mp4')
    task.file.saved_to = str(Path(task.destination) / 'video.mp4.url')
    infos = HeadInfo(
        content_type='text/html',
        content_length=1,
        last_modified=None,
        final_url='https://video.example.com/watch',
        guessed_file_name='watch',
        host='video.example.com',
    )

    with patch('moodle_dl.downloader.task.PT.remove_file') as remove_file:
        with pytest.raises(RuntimeError, match='yt-dlp 无法下载该 URL'):
            await task.download_using_yt_dlp(
                'https://video.example.com/watch',
                infos,
                delete_if_successful=False,
            )

    remove_file.assert_called_once_with(task.file.saved_to)
    assert task.status.yt_dlp_failed_with_error is True


@pytest.mark.asyncio
async def test_download_using_yt_dlp_ignores_errors_when_configured(task_factory, fake_yt_dlp):
    fake_cls, _add_extractors = fake_yt_dlp
    fake_cls.error = RuntimeError('403 Forbidden')
    task = task_factory(content_filename='video.mp4')
    task.opts.global_opts.ignore_ytdl_errors = True
    infos = HeadInfo(
        content_type='text/html',
        content_length=1,
        last_modified=None,
        final_url='https://video.example.com/watch',
        guessed_file_name='watch',
        host='video.example.com',
    )

    assert await task.download_using_yt_dlp(
        'https://video.example.com/watch',
        infos,
        delete_if_successful=False,
    ) is False


@pytest.mark.asyncio
async def test_download_using_external_downloader_success_and_failures(task_factory):
    success = task_factory(content_filename='video.mp4')
    Path(success.destination).mkdir(parents=True, exist_ok=True)
    proc = SimpleNamespace(
        stdout=SimpleNamespace(readline=AsyncMock(return_value=b'')),
        communicate=AsyncMock(return_value=(b'', b'')),
        returncode=0,
    )

    with patch('moodle_dl.downloader.task.asyncio.create_subprocess_exec', AsyncMock(return_value=proc)) as create_proc:
        await success.download_using_external_downloader(
            'https://video.example.com/watch',
            'downloader %U',
            delete_if_successful=True,
        )

    create_proc.assert_awaited_once()
    assert success.file.saved_to == str(Path(success.destination) / success.filename)

    failing = task_factory(content_filename='video.mp4')
    failing.file.saved_to = str(Path(failing.destination) / 'video.mp4.url')
    failing_proc = SimpleNamespace(
        stdout=SimpleNamespace(readline=AsyncMock(return_value=b'')),
        communicate=AsyncMock(return_value=(b'', b'bad')),
        returncode=2,
    )

    with (
        patch('moodle_dl.downloader.task.asyncio.create_subprocess_exec', AsyncMock(return_value=failing_proc)),
        patch('moodle_dl.downloader.task.PT.remove_file') as remove_file,
    ):
        with pytest.raises(RuntimeError, match='external downloader'):
            await failing.download_using_external_downloader(
                'https://video.example.com/watch',
                'downloader %U',
                delete_if_successful=False,
            )

    remove_file.assert_called_once_with(failing.file.saved_to)

    broken = task_factory(content_filename='video.mp4')
    with patch(
        'moodle_dl.downloader.task.asyncio.create_subprocess_exec',
        AsyncMock(side_effect=ValueError('bad command')),
    ):
        with pytest.raises(RuntimeError, match='external downloader'):
            await broken.download_using_external_downloader(
                'https://video.example.com/watch',
                'downloader %U',
                delete_if_successful=True,
            )


def test_save_incomplete_download_persists_existing_file_id(task_factory):
    task = task_factory()
    task.file.file_id = 123

    with (
        patch('moodle_dl.config.ConfigHelper') as config_cls,
        patch('moodle_dl.database.StateRecorder') as recorder_cls,
    ):
        task._save_incomplete_download('/tmp/file.bin', 'https://example.com/file.bin', 50, 100)

    recorder_cls.assert_called_once_with(config_cls.return_value, task.opts)
    recorder_cls.return_value.save_incomplete_download.assert_called_once_with(
        file_id=123,
        file_url='https://example.com/file.bin',
        file_path='/tmp/file.bin',
        total_bytes=100,
        downloaded_bytes=50,
        server_supports_range=True,
        etag=None,
        last_modified=None,
    )


def test_save_incomplete_download_creates_file_id_when_missing(task_factory):
    task = task_factory()
    task.file.file_id = None
    database = MagicMock(db_file='/tmp/moodle.db')
    cursor = MagicMock()
    cursor.fetchone.return_value = (456,)
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with (
        patch('moodle_dl.config.ConfigHelper') as config_cls,
        patch('moodle_dl.database.StateRecorder', return_value=database),
        patch('sqlite3.connect', return_value=conn) as connect,
    ):
        task._save_incomplete_download('/tmp/file.bin', 'https://example.com/file.bin', 50, 100)

    database.new_file.assert_called_once_with(task.file, task.course.id, task.course.fullname)
    connect.assert_called_once_with('/tmp/moodle.db')
    cursor.execute.assert_called_once()
    conn.close.assert_called_once()
    database.save_incomplete_download.assert_called_once()
    assert database.save_incomplete_download.call_args.kwargs['file_id'] == 456


def test_save_incomplete_download_returns_when_new_file_id_cannot_be_loaded(task_factory):
    task = task_factory()
    task.file.file_id = None
    database = MagicMock(db_file='/tmp/moodle.db')
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', return_value=database),
        patch('sqlite3.connect', return_value=conn),
    ):
        task._save_incomplete_download('/tmp/file.bin', 'https://example.com/file.bin', 50, 100)

    database.new_file.assert_called_once()
    database.save_incomplete_download.assert_not_called()


@pytest.mark.asyncio
async def test_resume_incomplete_download_short_circuit_paths(task_factory, tmp_path):
    no_file_id = task_factory()
    no_file_id.file.file_id = None
    assert await no_file_id._resume_incomplete_download(
        str(tmp_path / 'file.bin'),
        'https://example.com/file.bin',
        MagicMock(),
        {},
        None,
    ) == (0, None)

    task = task_factory()
    task.file.file_id = 123
    database = MagicMock()
    database.get_incomplete_download.return_value = None
    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', return_value=database),
    ):
        assert await task._resume_incomplete_download(
            str(tmp_path / 'file.bin'),
            'https://example.com/file.bin',
            MagicMock(),
            {},
            None,
        ) == (0, None)

    mismatched = task_factory()
    mismatched.file.file_id = 456
    mismatch_path = tmp_path / 'mismatch.bin'
    mismatch_path.write_bytes(b'abc')
    database = MagicMock()
    database.get_incomplete_download.return_value = {
        'download_id': 9,
        'downloaded_bytes': 10,
        'server_supports_range': True,
    }
    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', return_value=database),
    ):
        assert await mismatched._resume_incomplete_download(
            str(mismatch_path),
            'https://example.com/file.bin',
            MagicMock(),
            {},
            None,
        ) == (0, None)

    database.increment_incomplete_download_attempt.assert_called_once_with(9, '文件大小不匹配')


@pytest.mark.asyncio
async def test_resume_incomplete_download_head_checks_and_success(task_factory, tmp_path):
    file_path = tmp_path / 'partial.bin'
    file_path.write_bytes(b'12345')

    no_range = task_factory()
    no_range.file.file_id = 123
    database = MagicMock()
    database.get_incomplete_download.return_value = {
        'download_id': 1,
        'downloaded_bytes': 5,
        'server_supports_range': False,
    }
    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', return_value=database),
    ):
        assert await no_range._resume_incomplete_download(
            str(file_path),
            'https://example.com/file.bin',
            MagicMock(),
            {},
            None,
        ) == (0, None)

    class FakeHeadSession:
        def __init__(self, response):
            self.response = response

        def head(self, *args, **kwargs):
            return FakeAsyncContext(self.response)

    bad_head = task_factory()
    bad_head.file.file_id = 124
    database = MagicMock()
    database.get_incomplete_download.return_value = {
        'download_id': 2,
        'downloaded_bytes': 5,
        'server_supports_range': True,
    }
    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', return_value=database),
    ):
        assert await bad_head._resume_incomplete_download(
            str(file_path),
            'https://example.com/file.bin',
            FakeHeadSession(SimpleNamespace(status=500, content_length=10)),
            {'User-Agent': 'test'},
            None,
        ) == (0, None)

    complete = task_factory()
    complete.file.file_id = 125
    database = MagicMock()
    database.get_incomplete_download.return_value = {
        'download_id': 3,
        'downloaded_bytes': 5,
        'server_supports_range': True,
    }
    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', return_value=database),
    ):
        assert await complete._resume_incomplete_download(
            str(file_path),
            'https://example.com/file.bin',
            FakeHeadSession(SimpleNamespace(status=200, content_length=5)),
            {'User-Agent': 'test'},
            None,
        ) == (0, None)
    database.mark_download_complete.assert_called_once_with(125, str(file_path))

    resumable = task_factory()
    resumable.file.file_id = 126
    database = MagicMock()
    database.get_incomplete_download.return_value = {
        'download_id': 4,
        'downloaded_bytes': 5,
        'server_supports_range': True,
    }
    file_obj = SimpleNamespace(close=AsyncMock())
    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', return_value=database),
        patch('moodle_dl.downloader.task.aiofiles.open', AsyncMock(return_value=file_obj)) as open_file,
    ):
        assert await resumable._resume_incomplete_download(
            str(file_path),
            'https://example.com/file.bin',
            FakeHeadSession(SimpleNamespace(status=206, content_length=10)),
            {'User-Agent': 'test'},
            None,
        ) == (5, file_obj)

    open_file.assert_awaited_once_with(str(file_path), 'a+b')


@pytest.mark.asyncio
async def test_run_is_idempotent_and_sets_success_timestamp(task_factory):
    task = task_factory()

    async def successful_real_run():
        task.report_success()
        return True

    task.real_run = AsyncMock(side_effect=successful_real_run)
    task.set_utime = MagicMock()

    with patch('moodle_dl.downloader.task.time.time', return_value=1234.9):
        await task.run()
        await task.run()

    task.real_run.assert_awaited_once()
    task.set_utime.assert_called_once()
    assert task.status.state == TaskState.FINISHED
    assert task.file.time_stamp == 1234


def test_is_metadata_file_recognizes_optional_sidecars(task_factory):
    assert task_factory(content_filename='resource.JSON')._is_metadata_file() is True
    assert task_factory(content_filename='lecture_info')._is_metadata_file() is True
    assert task_factory(content_filename='lecture_notes.md')._is_metadata_file() is True
    assert task_factory(content_filename='Launch Form.html')._is_metadata_file() is True
    assert task_factory(content_filename='lecture.pdf')._is_metadata_file() is False


@pytest.mark.asyncio
async def test_real_run_orchestrates_pluginfile_prepare_failure_and_errors(task_factory):
    pluginfile = task_factory(content_fileurl='https://moodle.example.com/pluginfile.php/1/file.pdf')
    pluginfile._handle_metadata_file = AsyncMock(return_value=False)
    pluginfile._prepare_download = AsyncMock(return_value=True)
    pluginfile._execute_download = AsyncMock()
    pluginfile.report_success = MagicMock()

    with patch(
        'moodle_dl.downloader.task.UrlHelper.fix_pluginfile_url',
        return_value='https://moodle.example.com/webservice/pluginfile.php/1/file.pdf?token=token-abc',
    ) as fix_pluginfile_url:
        assert await pluginfile.real_run() is True

    fix_pluginfile_url.assert_called_once_with(
        'https://moodle.example.com/pluginfile.php/1/file.pdf',
        token='token-abc',
        moodle_base_url='https://moodle.example.com',
    )
    assert pluginfile.file.content_fileurl.endswith('token=token-abc')
    pluginfile._execute_download.assert_awaited_once()
    pluginfile.report_success.assert_called_once()

    prepare_failed = task_factory()
    prepare_failed._handle_metadata_file = AsyncMock(return_value=False)
    prepare_failed._prepare_download = AsyncMock(return_value=False)
    prepare_failed._execute_download = AsyncMock()
    assert await prepare_failed.real_run() is False
    prepare_failed._execute_download.assert_not_awaited()

    broken = task_factory()
    error = RuntimeError('download failed')
    broken._handle_metadata_file = AsyncMock(return_value=False)
    broken._prepare_download = AsyncMock(return_value=True)
    broken._execute_download = AsyncMock(side_effect=error)
    broken._handle_error = AsyncMock()
    assert await broken.real_run() is False
    broken._handle_error.assert_awaited_once_with(error)


@pytest.mark.asyncio
async def test_execute_download_dispatches_by_content_and_module_type(task_factory):
    description = task_factory(content_type='description')
    description.create_description = AsyncMock()
    await description._execute_download()
    description.create_description.assert_awaited_once()

    html = task_factory(content_type='html')
    html.create_html_file = AsyncMock()
    await html._execute_download()
    html.create_html_file.assert_awaited_once()

    content = task_factory(content_type='content')
    content.create_content_file = AsyncMock()
    await content._execute_download()
    content.create_content_file.assert_awaited_once()

    index_mod = task_factory(module_modname='index_mod-page', content_type='html')
    index_mod._download_index_mod_page = AsyncMock()
    await index_mod._execute_download()
    index_mod._download_index_mod_page.assert_awaited_once()

    cookie_mod = task_factory(module_modname='cookie_mod-helixmedia')
    cookie_mod._download_cookie_mod_file = AsyncMock()
    await cookie_mod._execute_download()
    cookie_mod._download_cookie_mod_file.assert_awaited_once()

    url_mod = task_factory(module_modname='url')
    url_mod._download_external_url_with_fallback = AsyncMock()
    await url_mod._execute_download()
    url_mod._download_external_url_with_fallback.assert_awaited_once()

    data_url = task_factory(content_fileurl='data:text/plain;base64,SGVsbG8=')
    data_url.create_data_url_file = AsyncMock()
    await data_url._execute_download()
    data_url.create_data_url_file.assert_awaited_once()

    normal = task_factory(content_fileurl='https://example.com/file.pdf')
    normal.file.saved_to = '/tmp/file.pdf'
    normal.add_token_to_url = MagicMock(return_value='https://example.com/file.pdf?token=token-abc')
    normal.download_url = AsyncMock()
    await normal._execute_download()
    normal.download_url.assert_awaited_once_with(
        'https://example.com/file.pdf?token=token-abc',
        '/tmp/file.pdf',
    )

    no_url = task_factory(content_fileurl=None)
    no_url.status.set_error = MagicMock()
    await no_url._execute_download()
    no_url.status.set_error.assert_called_once_with('No URL available for download')


@pytest.mark.asyncio
async def test_cookie_mod_and_kalvidres_handlers_restore_original_url(task_factory, tmp_path):
    kalvidres = task_factory(
        module_modname='cookie_mod-kalvidres',
        content_fileurl='https://moodle.example.com/mod/kalvidres/view.php?id=1',
    )
    kalvidres.file.saved_to = str(tmp_path / 'lecture.mp4')
    kalvidres.extract_kalvidres_text = AsyncMock()
    kalvidres.extract_kalvidres_video_url = AsyncMock(return_value='https://kaltura.example.com/embed')
    kalvidres.external_download_url = AsyncMock()

    await kalvidres._download_cookie_mod_file()

    kalvidres.extract_kalvidres_text.assert_awaited_once_with(
        'https://moodle.example.com/mod/kalvidres/view.php?id=1',
        str(tmp_path / 'lecture_notes.md'),
    )
    kalvidres.external_download_url.assert_awaited_once_with(
        add_token=False,
        delete_if_successful=True,
        needs_moodle_cookies=True,
    )
    assert kalvidres.file.content_fileurl == 'https://moodle.example.com/mod/kalvidres/view.php?id=1'

    fallback = task_factory(
        module_modname='cookie_mod-kalvidres',
        content_fileurl='https://moodle.example.com/mod/kalvidres/view.php?id=2',
    )
    fallback.file.saved_to = str(tmp_path / 'fallback.mp4')
    fallback.extract_kalvidres_text = AsyncMock()
    fallback.extract_kalvidres_video_url = AsyncMock(return_value=None)
    fallback.external_download_url = AsyncMock()

    await fallback._handle_kalvidres_download()

    fallback.external_download_url.assert_awaited_once_with(
        add_token=False,
        delete_if_successful=True,
        needs_moodle_cookies=True,
    )
    assert fallback.file.content_fileurl == 'https://moodle.example.com/mod/kalvidres/view.php?id=2'

    other_cookie_mod = task_factory(module_modname='cookie_mod-other')
    other_cookie_mod.external_download_url = AsyncMock()
    await other_cookie_mod._download_cookie_mod_file()
    other_cookie_mod.external_download_url.assert_awaited_once_with(
        add_token=False,
        delete_if_successful=True,
        needs_moodle_cookies=True,
    )


@pytest.mark.asyncio
async def test_external_url_fallback_creates_shortcut_only_when_download_does_not_succeed(task_factory):
    disabled = task_factory(module_modname='url', download_linked_files=False)
    disabled.create_shortcut = AsyncMock()
    disabled.external_download_url = AsyncMock()
    await disabled._download_external_url_with_fallback()
    disabled.external_download_url.assert_not_awaited()
    disabled.create_shortcut.assert_awaited_once()

    downloaded = task_factory(module_modname='url', download_linked_files=True)
    downloaded.is_filtered_external_domain = MagicMock(return_value=False)
    downloaded.external_download_url = AsyncMock()
    downloaded.create_shortcut = AsyncMock()
    await downloaded._download_external_url_with_fallback()
    downloaded.external_download_url.assert_awaited_once_with(
        add_token=False,
        delete_if_successful=True,
        needs_moodle_cookies=False,
    )
    downloaded.create_shortcut.assert_not_awaited()

    failed = task_factory(module_modname='url', download_linked_files=True)
    failed.is_filtered_external_domain = MagicMock(return_value=False)
    failed.external_download_url = AsyncMock(side_effect=RuntimeError('network failed'))
    failed.create_shortcut = AsyncMock()
    await failed._download_external_url_with_fallback()
    failed.create_shortcut.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_error_tolerates_getsize_failure(task_factory):
    task = task_factory()
    task.file.saved_to = '/tmp/failed-download.bin'
    task.status.bytes_downloaded = 11

    with (
        patch('moodle_dl.downloader.task.os.path.isfile', return_value=True),
        patch('moodle_dl.downloader.task.os.path.getsize', side_effect=OSError('stat failed')),
        patch('moodle_dl.downloader.task.PT.remove_file') as remove_file,
    ):
        await task._handle_error(RuntimeError('boom'))

    # 🔧 Part-file resume: cleanup tries both final and .part paths.
    # The .part is removed FIRST so the final-renaming fallback
    # doesn't accidentally race. The order of these two calls is
    # a contract — the test pins it.
    expected_calls = [
        call('/tmp/failed-download.bin.part'),
        call('/tmp/failed-download.bin'),
    ]
    assert remove_file.call_args_list == expected_calls
    assert task.status.state == TaskState.FAILED
    assert (DlEvent.RECEIVED, {'bytes_received': -11}) in task.events


@pytest.mark.asyncio
async def test_perform_download_request_handles_status_headers_and_payload_errors(task_factory):
    task = task_factory()
    file_obj = SimpleNamespace(write=AsyncMock(), closed=False)

    class EmptyContent:
        async def iter_chunked(self, _chunk_size):
            if False:
                yield b''

    class BrokenContent:
        async def iter_chunked(self, _chunk_size):
            raise aiohttp.ClientPayloadError('broken stream')
            yield b''

    class FakeResponse:
        def __init__(self, *, status, headers, content):
            self.status = status
            self.headers = headers
            self.content = content

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, response):
            self.response = response
            self.headers = None

        def request(self, _method, _url, headers, ssl, timeout):
            self.headers = headers
            return self.response

    empty_session = FakeSession(FakeResponse(status=500, headers={'Content-Length': '0'}, content=EmptyContent()))
    returned_file, total, content_length, content_range = await task._perform_download_request(
        empty_session,
        'https://example.com/file.bin',
        '/tmp/file.bin',
        {},
        None,
        10,
        file_obj,
        0,
        disable_compression=True,
    )

    assert returned_file is file_obj
    assert total == 0
    assert content_length == 0
    assert content_range is None
    assert empty_session.headers['Accept-Encoding'] == 'identity'

    broken_session = FakeSession(FakeResponse(status=200, headers={'Content-Length': '1'}, content=BrokenContent()))
    with pytest.raises(aiohttp.ClientPayloadError, match='broken stream'):
        await task._perform_download_request(
            broken_session,
            'https://example.com/file.bin',
            '/tmp/file.bin',
            {},
            None,
            10,
            file_obj,
            0,
        )


@pytest.mark.asyncio
async def test_download_url_marks_complete_and_ignores_cleanup_errors(task_factory, tmp_path):
    class FakeClientSession:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            FakeClientSession.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    task = task_factory()
    task.file.file_id = 123
    dest_path = tmp_path / 'download.bin'
    fake_file = SimpleNamespace(closed=False, close=AsyncMock())
    task._perform_download_request = AsyncMock(return_value=(fake_file, 5, 5, None))
    task.get_cookie_jar = MagicMock(return_value='cookie-jar')

    with (
        patch('moodle_dl.downloader.task.aiohttp.ClientSession', FakeClientSession),
        patch('moodle_dl.downloader.task.SslHelper.get_ssl_context', return_value='ssl-context'),
        patch('moodle_dl.config.ConfigHelper') as config_cls,
        patch('moodle_dl.database.StateRecorder') as recorder_cls,
    ):
        await task.download_url('https://example.com/download.bin', str(dest_path))

    assert FakeClientSession.instances[0].kwargs == {'cookie_jar': 'cookie-jar', 'raise_for_status': True}
    fake_file.close.assert_awaited_once()
    recorder_cls.assert_called_once_with(config_cls.return_value, task.opts)
    recorder_cls.return_value.mark_download_complete.assert_called_once_with(123, str(dest_path))

    cleanup_error = task_factory()
    cleanup_error.file.file_id = 456
    cleanup_error._perform_download_request = AsyncMock(
        return_value=(SimpleNamespace(closed=True, close=AsyncMock()), 1, 1, None)
    )

    with (
        patch('moodle_dl.downloader.task.aiohttp.ClientSession', FakeClientSession),
        patch('moodle_dl.downloader.task.SslHelper.get_ssl_context', return_value='ssl-context'),
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', side_effect=RuntimeError('database locked')),
    ):
        await cleanup_error.download_url('https://example.com/download.bin', str(tmp_path / 'other.bin'))


@pytest.mark.asyncio
async def test_download_url_ignores_resume_errors_and_downloads_from_start(task_factory, tmp_path):
    class FakeClientSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    task = task_factory()
    task.file.file_id = 123
    dest_path = tmp_path / 'partial.bin'
    # 🔧 Part-file resume: the resume trigger looks for .part suffix
    from moodle_dl.downloader.task import dest_path_to_part_path
    part_path = dest_path_to_part_path(str(dest_path))
    # Write the partial file under the .part path that download_url
    # actually scans for during resume.
    import os
    os.makedirs(os.path.dirname(part_path) or '.', exist_ok=True)
    with open(part_path, 'wb') as f:
        f.write(b'partial')
    fake_file = SimpleNamespace(closed=False, close=AsyncMock())
    captured_headers = []

    async def perform_download(_session, _url, _dest_path, headers, *_args, **_kwargs):
        captured_headers.append(dict(headers))
        return fake_file, 4, 4, None

    task._resume_incomplete_download = AsyncMock(side_effect=RuntimeError('resume failed'))
    task._perform_download_request = AsyncMock(side_effect=perform_download)

    with (
        patch('moodle_dl.downloader.task.aiohttp.ClientSession', FakeClientSession),
        patch('moodle_dl.downloader.task.SslHelper.get_ssl_context', return_value=None),
    ):
        await task.download_url('https://example.com/download.bin', str(dest_path))

    task._resume_incomplete_download.assert_awaited_once()
    task._perform_download_request.assert_awaited_once()
    assert 'Range' not in captured_headers[0]
    fake_file.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_url_missing_content_range_after_retry_cleans_file_and_fails(task_factory, tmp_path):
    class FakeClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeFile:
        def __init__(self):
            self.closed = False
            self.close_count = 0

        async def close(self):
            self.close_count += 1
            self.closed = True

    task = task_factory()
    task.MAX_DL_RETRIES = 2
    dest_path = tmp_path / 'download.bin'
    fake_file = FakeFile()
    task.check_range_download_opt = AsyncMock(return_value=True)
    task._perform_download_request = AsyncMock(
        side_effect=[
            aiohttp.ClientConnectionError('connection dropped'),
            (fake_file, 5, 5, None),
        ]
    )

    with (
        patch('moodle_dl.downloader.task.aiohttp.ClientSession', return_value=FakeClientSession()),
        patch('moodle_dl.downloader.task.SslHelper.get_ssl_context', return_value=None),
        patch('moodle_dl.downloader.task.PT.remove_file') as remove_file,
        patch('moodle_dl.downloader.task.asyncio.sleep', new_callable=AsyncMock) as sleep_mock,
    ):
        with pytest.raises(ContentRangeError, match='requested range data'):
            await task.download_url('https://example.com/download.bin', str(dest_path))

    task.check_range_download_opt.assert_awaited_once()
    assert task._perform_download_request.await_count == 2
    assert task._perform_download_request.await_args_list[1].args[3]['Range'] == 'bytes=0-'
    assert fake_file.close_count == 1
    # 🔧 Part-file resume: the .part file is what gets removed
    # (the dest path is also passed to PT.remove_file as a no-op
    # safety net, so we accept either 1 or 2 calls).
    from moodle_dl.downloader.task import dest_path_to_part_path
    expected_part = dest_path_to_part_path(str(dest_path))
    assert any(
        c == call(expected_part) for c in remove_file.call_args_list
    ), f'Expected {expected_part} in {remove_file.call_args_list}'
    assert (DlEvent.RECEIVED, {'bytes_received': -5}) in task.events
    sleep_mock.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_download_url_non_retryable_http_status_raises_without_retry(task_factory, tmp_path):
    class FakeClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    task = task_factory()
    request_info = SimpleNamespace(real_url='https://example.com/download.bin')
    error = aiohttp.ClientResponseError(
        request_info=request_info,
        history=(),
        status=500,
        message='server failed',
    )
    task.check_range_download_opt = AsyncMock(return_value=False)
    task._perform_download_request = AsyncMock(side_effect=error)

    with (
        patch('moodle_dl.downloader.task.aiohttp.ClientSession', return_value=FakeClientSession()),
        patch('moodle_dl.downloader.task.SslHelper.get_ssl_context', return_value=None),
        patch('moodle_dl.downloader.task.asyncio.sleep', new_callable=AsyncMock) as sleep_mock,
    ):
        with pytest.raises(aiohttp.ClientResponseError):
            await task.download_url('https://example.com/download.bin', str(tmp_path / 'download.bin'))

    task.check_range_download_opt.assert_awaited_once()
    task._perform_download_request.assert_awaited_once()
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_url_saves_incomplete_download_when_final_retry_fails_after_partial(
    task_factory,
    tmp_path,
):
    class FakeClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeFile:
        closed = False

        async def close(self):
            self.closed = True

    task = task_factory()
    task.MAX_DL_RETRIES = 2
    dest_path = tmp_path / 'partial.bin'
    fake_file = FakeFile()
    final_error = aiohttp.ClientConnectionError('connection dropped again')
    task.check_range_download_opt = AsyncMock(return_value=True)
    task._perform_download_request = AsyncMock(
        side_effect=[
            (fake_file, 5, 10, 'bytes 0-4/10'),
            final_error,
        ]
    )
    task._save_incomplete_download = MagicMock()

    with (
        patch('moodle_dl.downloader.task.aiohttp.ClientSession', return_value=FakeClientSession()),
        patch('moodle_dl.downloader.task.SslHelper.get_ssl_context', return_value=None),
        patch('moodle_dl.downloader.task.PT.remove_file') as remove_file,
        patch('moodle_dl.downloader.task.asyncio.sleep', new_callable=AsyncMock) as sleep_mock,
    ):
        with pytest.raises(aiohttp.ClientConnectionError):
            await task.download_url('https://example.com/download.bin', str(dest_path))

    task.check_range_download_opt.assert_awaited_once()
    assert task._perform_download_request.await_count == 2
    assert task._perform_download_request.await_args_list[1].args[3]['Range'] == 'bytes=5-'
    # 🔧 Part-file resume: _save_incomplete_download now records the
    # .part path (the actual file the downloader is writing to).
    from moodle_dl.downloader.task import dest_path_to_part_path
    task._save_incomplete_download.assert_called_once_with(
        dest_path_to_part_path(str(dest_path)),
        'https://example.com/download.bin',
        5,
        10,
    )
    remove_file.assert_not_called()
    sleep_mock.assert_awaited_once_with(1)


def test_save_incomplete_download_reraises_database_errors(task_factory):
    task = task_factory()
    task.file.file_id = 123
    database = MagicMock()
    database.save_incomplete_download.side_effect = RuntimeError('database failed')

    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', return_value=database),
    ):
        with pytest.raises(RuntimeError, match='database failed'):
            task._save_incomplete_download('/tmp/file.bin', 'https://example.com/file.bin', 1, 10)


@pytest.mark.asyncio
async def test_resume_incomplete_download_returns_empty_on_recorder_errors(task_factory, tmp_path):
    task = task_factory()
    task.file.file_id = 123

    with (
        patch('moodle_dl.config.ConfigHelper'),
        patch('moodle_dl.database.StateRecorder', side_effect=RuntimeError('database failed')),
    ):
        assert await task._resume_incomplete_download(
            str(tmp_path / 'file.bin'),
            'https://example.com/file.bin',
            MagicMock(),
            {},
            None,
        ) == (0, None)
