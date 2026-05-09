# -*- coding: utf-8 -*-
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moodle_dl.downloader.task import Task
from moodle_dl.types import Course, DlEvent, DownloadOptions, MoodleDlOpts, TaskState


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

    index_task = task_factory(module_modname='index_mod-page')
    index_task.external_download_url = AsyncMock()
    await index_task._execute_download()
    index_task.external_download_url.assert_awaited_once_with(
        add_token=True,
        delete_if_successful=True,
        needs_moodle_cookies=False,
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
async def test_metadata_prepare_directory_and_error_handlers(task_factory, tmp_path):
    metadata = task_factory(content_filename='metadata.json', download_metadata_files=False)
    assert await metadata._handle_metadata_file() is True
    assert metadata.status.state == TaskState.FINISHED
    assert metadata.events[-1][0] == DlEvent.FINISHED

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
