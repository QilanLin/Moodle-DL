# -*- coding: utf-8 -*-
import json
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moodle_dl.moodle.mods.book import BookMod
from moodle_dl.types import Course
from moodle_dl.utils import PathTools as PT


@pytest.fixture(autouse=True)
def unrestricted_filenames(monkeypatch):
    monkeypatch.setattr(PT, 'restricted_filenames', False)


def make_book(download_books=True):
    client = MagicMock()
    client.token = 'token-abc'
    client.async_post = AsyncMock()
    client.async_wait_for_network_slot = AsyncMock()
    client.moodle_url = MagicMock()
    client.moodle_url.domain = 'keats.kcl.ac.uk'
    client.moodle_url.url_base = 'https://keats.kcl.ac.uk'

    config = MagicMock()
    config.get_download_books.return_value = download_books

    return BookMod(client, 2023100900, 42, {}, config)


def make_lti_src(entry_id, html_escaped=True):
    source = quote(
        f'https://kaf.example.com/browseandembed/index/media/entryid/{entry_id}/view',
        safe='',
    )
    separator = '&amp;' if html_escaped else '&'
    return (
        'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php'
        f'?foo=1{separator}source={source}'
    )


class FakeAsyncPlaywrightContext:
    def __init__(self, playwright):
        self.playwright = playwright

    async def __aenter__(self):
        return self.playwright

    async def __aexit__(self, exc_type, exc, tb):
        return False


def install_fake_playwright(monkeypatch, playwright):
    async_api = SimpleNamespace(async_playwright=lambda: FakeAsyncPlaywrightContext(playwright))
    monkeypatch.setitem(sys.modules, 'playwright', SimpleNamespace(async_api=async_api))
    monkeypatch.setitem(sys.modules, 'playwright.async_api', async_api)


def make_fake_print_book_playwright(html='<div class="book_chapter">Book</div>', current_url=None):
    page = MagicMock()
    page.url = current_url or 'https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20'
    page.goto = AsyncMock(side_effect=[MagicMock(), MagicMock()])
    page.content = AsyncMock(side_effect=['<html>course</html>', html])
    page.title = AsyncMock(return_value='Course page')
    page.wait_for_timeout = AsyncMock()
    page.on = MagicMock()

    context = MagicMock()
    context.add_cookies = AsyncMock()
    context.cookies = AsyncMock(return_value=[
        {'name': 'MoodleSession', 'value': 'abc', 'domain': 'keats.kcl.ac.uk'}
    ])
    context.new_page = AsyncMock(return_value=page)

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    firefox = MagicMock()
    firefox.launch = AsyncMock(return_value=browser)
    playwright = SimpleNamespace(firefox=firefox)
    return playwright, browser, context, page


def make_cookie_manager(cookies=None, refresh_result=False):
    cookie_manager = MagicMock()
    cookie_manager.get_cookies_from_db.return_value = cookies if cookies is not None else [
        {
            'name': 'MoodleSession',
            'value': 'abc',
            'domain': 'keats.kcl.ac.uk',
            'path': '/',
        }
    ]
    cookie_manager.refresh_cookies.return_value = refresh_result
    return cookie_manager


def make_print_book_personal_metadata():
    return (
        '<div class="w-50 float-start">'
        '<table class="float-end"><tbody>'
        '<tr><td>Printed by:</td><td class="ps-3">Egor Kuzmichev</td></tr>'
        '<tr><td>Date:</td><td class="ps-3">Monday, 18 May 2026, 4:21 PM</td></tr>'
        '</tbody></table>'
        '</div>'
    )


class FakeChapterResponse:
    def __init__(self, status, text=''):
        self.status = status
        self.text = AsyncMock(return_value=text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeChapterClientSession:
    response = None
    error = None
    requested = []

    def __init__(self, *args, **kwargs):
        # Accept (and ignore) all kwargs like `timeout` from
        # make_aiohttp_timeout(), so the test can patch the real
        # ClientSession without rewriting every caller.
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, timeout):
        # Accept either an aiohttp.ClientTimeout (with `.total`) or
        # a plain int/float (the new timeout=30 shorthand).
        recorded = getattr(timeout, 'total', timeout)
        FakeChapterClientSession.requested.append((url, recorded))
        if FakeChapterClientSession.error is not None:
            raise FakeChapterClientSession.error
        return FakeChapterClientSession.response


def test_download_condition_keeps_deleted_files_only_when_book_downloads_are_enabled():
    config = MagicMock()
    file = MagicMock(module_modname='mod_book', deleted=True)

    config.get_download_books.return_value = True
    assert BookMod.download_condition(config, file) is True

    config.get_download_books.return_value = False
    assert BookMod.download_condition(config, file) is False

    file.module_modname = 'page'
    assert BookMod.download_condition(config, file) is True


def test_create_ordered_index_escapes_titles_quotes_hrefs_and_marks_hidden_items():
    bm = BookMod.__new__(BookMod)
    index_html = bm.create_ordered_index([
        {
            'title': 'Intro & <overview>',
            'href': '10/index.html?x=1 2',
            'level': 0,
            'hidden': '1',
            'subitems': [
                {'title': 'Child', 'href': '20/index.html', 'level': 1},
            ],
        }
    ])

    assert index_html.count('<ol>') == 2
    assert 'Intro &amp; &lt;overview&gt; [Hidden]' in index_html
    assert 'class="level-0 hidden"' in index_html
    assert 'href="10/index.html%3Fx%3D1%202"' in index_html
    assert 'class="level-1"' in index_html


def test_toc_ordering_metadata_names_and_empty_chapter_placeholder():
    book = make_book()
    toc = [
        {
            'title': 'Chapter 20',
            'href': '/20/index.html',
            'subitems': [
                {'title': 'Chapter 10', 'href': '10/index.html'},
                {'title': 'Duplicate Chapter 20', 'href': '20/extra.html'},
            ],
        },
        {'title': 'No href', 'href': None},
    ]

    assert book._get_numbering_name(2) == 'Bullets'
    assert book._get_numbering_name(99) == 'Unknown'
    assert book._get_navstyle_name(0) == 'Image'
    assert book._get_navstyle_name(99) == 'Unknown'
    assert [entry['title'] for entry in book._get_flat_toc_list(toc)] == [
        'Chapter 20',
        'Chapter 10',
        'Duplicate Chapter 20',
        'No href',
    ]
    assert book._get_ordered_chapter_ids(toc, {'30': [], '10': [], '20': []}) == [
        '20',
        '10',
        '30',
    ]
    numbered_toc = [
        {'title': 'Introduction', 'href': '10/index.html'},
        {
            'title': 'Parent',
            'href': '20/index.html',
            'subitems': [
                {'title': 'Option 1', 'href': '21/index.html'},
                {'title': 'Hidden option', 'href': '22/index.html', 'hidden': '1'},
                {'title': 'Option 2', 'href': '23/index.html'},
            ],
        },
    ]
    assert book._get_toc_chapter_numbers(numbered_toc) == {
        '10': '1.',
        '20': '2.',
        '21': '2.1.',
        '22': '2.x.',
        '23': '2.2.',
    }
    assert book._format_chapter_folder_name('Option 1', '2.1.', 3) == '2.1. Option 1'
    assert book._format_chapter_folder_name('Missing', '', 3) == '03 - Missing'

    with patch('moodle_dl.moodle.mods.book.time.time', return_value=123.9):
        placeholder = book._create_empty_chapter_placeholder('42', '01 - Missing', 'Missing')

    assert placeholder == {
        'filename': '__empty_chapter_42__',
        'filepath': '/01 - Missing/',
        'type': 'directory_placeholder',
        'filesize': 0,
        'timemodified': 123,
        'description': 'Placeholder for empty chapter "Missing"',
        'no_search_for_urls': True,
        'contents': [],
    }


def test_convert_kaltura_url_to_kalvidres_keeps_original_url_and_extracts_entry_ids():
    book = make_book()
    plain_url = 'https://example.com/video'

    assert book._convert_kaltura_url_to_kalvidres(plain_url) == (plain_url, '')

    encoded_url = make_lti_src('1_abc123', html_escaped=False)
    assert book._convert_kaltura_url_to_kalvidres(encoded_url) == (encoded_url, '1_abc123')

    double_encoded_source = quote(
        quote('https://kaf.example.com/browseandembed/index/media/entryid/1_double/view', safe=''),
        safe='',
    )
    double_encoded_url = (
        'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php'
        f'?source={double_encoded_source}'
    )
    assert book._convert_kaltura_url_to_kalvidres(double_encoded_url) == (
        double_encoded_url,
        '1_double',
    )

    missing_entry = 'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?source=no-entry'
    assert book._convert_kaltura_url_to_kalvidres(missing_entry) == (missing_entry, '')


def test_extract_kaltura_videos_from_html_returns_download_entries():
    book = make_book()
    html = (
        f'<iframe src="{make_lti_src("1_a")}"></iframe>'
        f'<iframe src="{make_lti_src("1_b")}"></iframe>'
        '<iframe src="https://youtube.example/embed/abc"></iframe>'
    )

    with patch('moodle_dl.moodle.mods.book.time.time', return_value=1700):
        videos = book._extract_kaltura_videos_from_html(html, 'Chapter 1', 10, 20)

    assert [video['entry_id'] for video in videos] == ['1_a', '1_b']
    assert [video['filename'] for video in videos] == [
        'Chapter 1 - Video 1',
        'Chapter 1 - Video 2',
    ]
    assert all(video['type'] == 'kalvidres_embedded' for video in videos)
    assert all(video['mimetype'] == 'video/mp4' for video in videos)
    assert all(video['timemodified'] == 1700 for video in videos)
    assert '&source=' in videos[0]['fileurl']


def test_extract_print_book_videos_and_replace_matching_iframes():
    book = make_book()
    html = (
        f'<iframe class="kaltura-player-iframe" src="{make_lti_src("1_a")}"></iframe>'
        f'<iframe class="kaltura-player-iframe" src="{make_lti_src("1_b")}"></iframe>'
    )

    videos = book._extract_kaltura_videos_from_print_book(html, 'Book')

    assert [video['entry_id'] for video in videos] == ['1_a', '1_b']
    assert [video['video_filename'] for video in videos] == [
        'Book - Video 01 (1_a).mp4',
        'Book - Video 02 (1_b).mp4',
    ]
    assert videos[0]['lti_launch_url'] == make_lti_src('1_a', html_escaped=False)

    modified = book._replace_kaltura_iframes_with_video_tags(html, videos)

    assert '<iframe' not in modified
    assert 'source src="Book - Video 01 (1_a).mp4"' in modified
    assert 'source src="Book - Video 02 (1_b).mp4"' in modified
    assert 'Book - Video 01' in modified


def test_extract_print_book_videos_skips_iframes_without_source_or_entry_id():
    book = make_book()
    html = (
        '<iframe class="kaltura-player-iframe" '
        'src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?foo=1"></iframe>'
        '<iframe class="kaltura-player-iframe" '
        'src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?source=no-entry"></iframe>'
    )

    assert book._extract_kaltura_videos_from_print_book(html, 'Book') == []


def test_replace_kaltura_iframes_with_video_tags_handles_self_closing_iframes():
    book = make_book()
    modified = book._replace_kaltura_iframes_with_video_tags(
        '<iframe src="video-one" />',
        [{'iframe_src': 'video-one', 'relative_path': 'local/video-one.mp4', 'video_name': 'Video One'}],
    )

    assert '<iframe' not in modified
    assert 'source src="local/video-one.mp4"' in modified
    assert 'Video One' in modified


def test_replace_kaltura_iframes_with_video_tags_keeps_unmatched_iframe_references():
    book = make_book()
    html = '<p>No matching iframe here</p>'

    assert book._replace_kaltura_iframes_with_video_tags(
        html,
        [{'iframe_src': 'missing-src', 'relative_path': 'local/missing.mp4', 'video_name': 'Missing'}],
    ) == html


def test_extract_kaltura_videos_from_chapter_uses_simple_local_video_names():
    book = make_book()

    single = book._extract_kaltura_videos_from_chapter(
        f'<iframe class="kaltura-player-iframe" src="{make_lti_src("1_single")}"></iframe>',
        '01 - Intro',
        1,
    )
    assert single[0]['video_name'] == 'Video'
    assert single[0]['video_filename'] == 'Video (1_single).mp4'

    multiple = book._extract_kaltura_videos_from_chapter(
        (
            f'<iframe class="kaltura-player-iframe" src="{make_lti_src("1_a")}"></iframe>'
            f'<iframe class="kaltura-player-iframe" src="{make_lti_src("1_b")}"></iframe>'
        ),
        '01 - Intro',
        1,
    )

    assert [video['video_name'] for video in multiple] == ['Video 01', 'Video 02']
    assert [video['relative_path'] for video in multiple] == [
        'Video 01 (1_a).mp4',
        'Video 02 (1_b).mp4',
    ]


def test_extract_kaltura_videos_from_chapter_skips_invalid_iframes():
    book = make_book()
    invalid_html = (
        '<iframe class="kaltura-player-iframe" '
        'src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?foo=1"></iframe>'
        '<iframe class="kaltura-player-iframe" '
        'src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?source=no-entry"></iframe>'
    )

    assert book._extract_kaltura_videos_from_chapter(invalid_html, '01 - Intro', 1) == []


def test_replace_print_book_videos_links_to_downloaded_generated_and_fallback_paths():
    book = make_book()
    html = (
        '<iframe src="downloaded-src"></iframe>'
        '<iframe src="generated-one" />'
        '<iframe src="generated-two"></iframe>'
        '<iframe src="fallback-src"></iframe>'
    )
    videos = [
        {'entry_id': 'downloaded', 'iframe_src': 'downloaded-src', 'video_name': 'Downloaded Video'},
        {'entry_id': 'generated-one', 'iframe_src': 'generated-one', 'video_name': 'Ignored'},
        {'entry_id': 'generated-two', 'iframe_src': 'generated-two', 'video_name': 'Ignored'},
        {'entry_id': 'fallback', 'iframe_src': 'fallback-src', 'video_name': 'Ignored'},
    ]

    modified = book._replace_print_book_videos_with_chapter_links(
        html,
        videos,
        {'downloaded': '01 - Intro/Downloaded.mp4'},
        {'generated-one': '02 - Week', 'generated-two': '02 - Week'},
    )

    assert 'source src="01 - Intro/Downloaded.mp4"' in modified
    assert 'Downloaded Video' in modified
    assert 'source src="02 - Week/Video 01 (generated-one).mp4"' in modified
    assert 'source src="02 - Week/Video 02 (generated-two).mp4"' in modified
    assert 'source src="Video (fallback).mp4"' in modified


def test_replace_print_book_videos_generates_single_video_path_for_known_chapter():
    book = make_book()

    modified = book._replace_print_book_videos_with_chapter_links(
        '<iframe src="single-src"></iframe>',
        [{'entry_id': 'single', 'iframe_src': 'single-src', 'video_name': 'Ignored'}],
        {},
        {'single': '01 - Intro'},
    )

    assert 'source src="01 - Intro/Video (single).mp4"' in modified
    assert '>Video<' in modified


def test_chapter_video_mapping_and_reverse_mapping(tmp_path, monkeypatch):
    book = make_book()
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path))
    html = (
        '<div class="book_chapter pt-3" id="ch101">'
        '<iframe src="https://example.com/path%2Fentryid%2F1_a%2Fview"></iframe>'
        '<iframe src="https://example.com/entryid/1_b"></iframe>'
        '</div>'
        '<div class="book_chapter" id="ch102"><p>No videos here</p></div>'
    )

    mapping = book._extract_chapter_video_mapping_from_print_book(html)

    assert mapping == {'101': ['1_a', '1_b']}
    assert book._build_video_to_chapter_mapping(mapping) == {'1_a': '101', '1_b': '101'}


def test_create_linked_print_book_html_rewrites_mapped_kaltura_iframes_only():
    book = make_book()
    mapped_src = make_lti_src('1_a')
    missing_src = make_lti_src('1_missing')
    no_entry_src = 'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?source=no-entry'
    html = (
        f'<iframe class="kaltura-player-iframe" src="{mapped_src}"></iframe>'
        f'<iframe class="kaltura-player-iframe" src="{missing_src}"></iframe>'
        f'<iframe class="kaltura-player-iframe" src="{no_entry_src}"></iframe>'
    )

    modified = book._create_linked_print_book_html(
        html,
        {
            '101': {
                'folder_name': '01 - Intro',
                'videos': [
                    {'entry_id': '1_a', 'filename': 'Intro Video (1_a).mp4'},
                    {'entry_id': 'ignored-no-filename'},
                ],
            }
        },
    )

    assert 'source src="01 - Intro/Intro Video (1_a).mp4"' in modified
    assert mapped_src not in modified
    assert missing_src in modified
    assert no_entry_src in modified


def test_remove_print_book_personal_metadata_removes_only_print_header():
    book = make_book()
    course_metadata = (
        '<div class="w-50 float-start">'
        '<table class="float-start"><tbody><tr><td>Book:</td><td>Software installation</td></tr></tbody></table>'
        '</div>'
    )
    html = (
        '<header>'
        f'{course_metadata}'
        f'{make_print_book_personal_metadata()}'
        '</header>'
        '<main class="book p-4"><div class="book_chapter">Chapter</div></main>'
    )

    cleaned = book._remove_print_book_personal_metadata(html)

    assert 'Printed by:' not in cleaned
    assert 'Egor Kuzmichev' not in cleaned
    assert 'Monday, 18 May 2026' not in cleaned
    assert 'Book:' in cleaned
    assert 'Software installation' in cleaned
    assert '<div class="book_chapter">Chapter</div>' in cleaned
    assert book._remove_print_book_personal_metadata(cleaned) == cleaned


@pytest.mark.asyncio
async def test_fetch_chapter_html_adds_token_and_handles_statuses_and_errors():
    book = make_book()
    FakeChapterClientSession.requested = []
    FakeChapterClientSession.error = None
    FakeChapterClientSession.response = FakeChapterResponse(200, '<h1>Chapter</h1>')

    with patch('aiohttp.ClientSession', FakeChapterClientSession):
        assert await book._fetch_chapter_html('https://keats.kcl.ac.uk/chapter/index.html') == '<h1>Chapter</h1>'

    assert FakeChapterClientSession.requested == [
        ('https://keats.kcl.ac.uk/chapter/index.html?token=token-abc', 30)
    ]
    FakeChapterClientSession.response.text.assert_awaited_once_with(encoding='utf-8')

    FakeChapterClientSession.requested = []
    FakeChapterClientSession.response = FakeChapterResponse(404)
    with patch('aiohttp.ClientSession', FakeChapterClientSession):
        assert await book._fetch_chapter_html('https://keats.kcl.ac.uk/chapter/index.html?forcedownload=1') == ''

    assert FakeChapterClientSession.requested == [
        ('https://keats.kcl.ac.uk/chapter/index.html?forcedownload=1&token=token-abc', 30)
    ]

    FakeChapterClientSession.error = RuntimeError('network failed')
    with patch('aiohttp.ClientSession', FakeChapterClientSession):
        assert await book._fetch_chapter_html('https://keats.kcl.ac.uk/chapter/broken.html') == ''


@pytest.mark.asyncio
async def test_fetch_chapter_html_uses_network_throttle_and_cache():
    book = make_book()
    FakeChapterClientSession.requested = []
    FakeChapterClientSession.error = None
    FakeChapterClientSession.response = FakeChapterResponse(200, '<h1>Cached</h1>')

    with patch('aiohttp.ClientSession', FakeChapterClientSession):
        first = await book._fetch_chapter_html('https://keats.kcl.ac.uk/chapter/cached.html')
        second = await book._fetch_chapter_html('https://keats.kcl.ac.uk/chapter/cached.html')

    assert first == '<h1>Cached</h1>'
    assert second == '<h1>Cached</h1>'
    assert FakeChapterClientSession.requested == [
        ('https://keats.kcl.ac.uk/chapter/cached.html?token=token-abc', 30)
    ]
    book.client.async_wait_for_network_slot.assert_awaited_once_with(
        'book chapter HTML https://keats.kcl.ac.uk/chapter/cached.html'
    )


@pytest.mark.asyncio
async def test_fetch_books_web_api_converts_core_course_modules_and_errors_when_empty():
    book = make_book()
    courses = [Course(1, 'Course One'), Course(2, 'Course Two')]
    core_contents = {
        1: [
            {
                'modules': [
                    {
                        'modname': 'book',
                        'instance': 7,
                        'id': 70,
                        'name': 'Book A',
                        'description': '<p>Intro</p>',
                        'timecreated': 11,
                        'timemodified': 22,
                    },
                    {'modname': 'page', 'id': 99},
                ]
            }
        ],
        2: [{'modules': [{'modname': 'book', 'id': 80}]}],
    }

    books = await book._fetch_books_web_api(courses, core_contents)

    assert books == [
        {
            'id': 7,
            'coursemodule': 70,
            'course': 1,
            'name': 'Book A',
            'intro': '<p>Intro</p>',
            'introformat': 1,
            'numbering': 0,
            'navstyle': 0,
            'customtitles': 0,
            'revision': 0,
            'timecreated': 11,
            'timemodified': 22,
        },
        {
            'id': 0,
            'coursemodule': 80,
            'course': 2,
            'name': 'Book',
            'intro': '',
            'introformat': 1,
            'numbering': 0,
            'navstyle': 0,
            'customtitles': 0,
            'revision': 0,
            'timecreated': 0,
            'timemodified': 0,
        },
    ]

    with pytest.raises(ValueError, match='Web API'):
        await book._fetch_books_web_api(courses, {})


@pytest.mark.asyncio
async def test_real_fetch_mod_entries_processes_mobile_book_contents():
    book = make_book()
    book.client.async_post.return_value = {
        'books': [
            {
                'id': 5,
                'course': 1,
                'coursemodule': 20,
                'name': 'Clinical Skills',
                'numbering': 1,
                'timemodified': 99,
            }
        ]
    }
    book._fetch_print_book_html = AsyncMock(return_value=(
        make_print_book_personal_metadata() +
        f'<div class="book_chapter" id="ch101">'
        f'<iframe class="kaltura-player-iframe" src="{make_lti_src("1_video")}"></iframe>'
        '</div>',
        'https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20',
    ))
    book._fetch_chapter_html = AsyncMock(return_value=(
        f'<p>Chapter html</p><iframe src="{make_lti_src("1_video")}"></iframe>'
    ))
    core_contents = {
        1: [
            {
                'modules': [
                    {
                        'id': 20,
                        'contents': [
                            {
                                'filename': 'toc.json',
                                'content': json.dumps([
                                    {'title': 'Intro', 'href': '101/index.html'},
                                    {'title': 'Empty chapter', 'href': '102/index.html'},
                                ]),
                            },
                            {
                                'filename': '101/index.html',
                                'fileurl': 'https://keats.kcl.ac.uk/pluginfile.php/chapter/101/index.html',
                                'content': '<p>Fallback</p>',
                            },
                            {
                                'filename': '101/slides.pdf',
                                'fileurl': 'https://keats.kcl.ac.uk/pluginfile.php/chapter/101/slides.pdf',
                            },
                            {
                                'filename': '102/handout.pdf',
                                'fileurl': 'https://keats.kcl.ac.uk/pluginfile.php/chapter/102/handout.pdf',
                            },
                        ],
                    }
                ]
            }
        ]
    }

    with patch('moodle_dl.moodle.mods.book.time.time', return_value=1800):
        result = await book.real_fetch_mod_entries([Course(1, 'Course One')], core_contents)

    module = result[1][20]
    files = module['files']
    toc_file, print_book_file, intro_chapter, empty_chapter = files

    assert module['id'] == 5
    assert module['name'] == 'Clinical Skills'
    assert toc_file['filename'] == 'Table of Contents'
    assert 'Intro' in toc_file['html']
    assert print_book_file['filename'] == 'Clinical Skills.html'
    assert '1. Intro/Intro - Video (1_video).mp4' in print_book_file['html']
    assert 'Printed by:' not in print_book_file['html']
    assert 'Egor Kuzmichev' not in print_book_file['html']

    assert intro_chapter['type'] == 'html'
    assert intro_chapter['filepath'].replace('_', ' ') == '/1. Intro/'
    assert intro_chapter['html'].startswith('<p>Chapter html</p>')
    assert [content['filename'] for content in intro_chapter['contents']] == [
        '101/slides.pdf',
        'Intro - Video (1_video).mp4',
    ]
    assert intro_chapter['contents'][0]['filepath'].replace('_', ' ') == '/1. Intro/'
    assert intro_chapter['contents'][1]['type'] == 'kalvidres_embedded'
    assert intro_chapter['contents'][1]['timemodified'] == 1800

    assert empty_chapter['type'] == 'directory_placeholder'
    assert empty_chapter['filepath'].replace('_', ' ') == '/2. Empty chapter/'
    assert empty_chapter['contents'][0]['filename'] == '102/handout.pdf'

    book.client.async_post.assert_awaited_once_with(
        'mod_book_get_books_by_courses',
        {'courseids': {'0': 1}},
    )
    book._fetch_print_book_html.assert_awaited_once_with(20, 1)
    book._fetch_chapter_html.assert_awaited_once_with(
        'https://keats.kcl.ac.uk/pluginfile.php/chapter/101/index.html'
    )


@pytest.mark.asyncio
async def test_real_fetch_mod_entries_uses_web_api_fallback_and_print_book_without_chapters():
    book = make_book()
    book.client.async_post.side_effect = RuntimeError('mobile api unavailable')
    book._fetch_books_web_api = AsyncMock(return_value=[
        {'id': 9, 'course': 1, 'coursemodule': 30, 'name': 'Fallback Book', 'timemodified': 66}
    ])
    print_book_html = make_print_book_personal_metadata() + '<div class="book_chapter">Only print</div>'
    expected_html = '<div class="book_chapter">Only print</div>'
    book._fetch_print_book_html = AsyncMock(return_value=(print_book_html, 'url'))

    result = await book.real_fetch_mod_entries([Course(1, 'Course One')], {1: []})

    assert result[1][30]['files'] == [
        {
            'filename': 'Fallback Book.html',
            'filepath': '/',
            'timemodified': 66,
            'html': expected_html,
            'type': 'html',
            'no_search_for_urls': True,
            'filesize': len(expected_html),
        }
    ]
    book._fetch_books_web_api.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_fetch_mod_entries_returns_empty_when_book_downloads_are_disabled():
    book = make_book(download_books=False)

    assert await book.real_fetch_mod_entries([Course(1, 'Course One')], {}) == {}
    book.client.async_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_print_book_html_returns_empty_when_cookie_database_is_empty(monkeypatch):
    book = make_book()
    playwright, _, _, _ = make_fake_print_book_playwright()
    install_fake_playwright(monkeypatch, playwright)
    cookie_manager = make_cookie_manager(cookies=[])

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=cookie_manager):
        assert await book._fetch_print_book_html(20, 1) == ('', '')

    playwright.firefox.launch.assert_not_awaited()


def test_print_book_playwright_cookies_are_filtered_and_cached():
    book = make_book()
    cookies = [
        {'name': 'MoodleSession', 'value': 'abc', 'domain': 'keats.kcl.ac.uk', 'path': '/'},
        {'name': 'KCLParent', 'value': 'parent', 'domain': '.kcl.ac.uk', 'path': '/'},
        {'name': 'UrlCookie', 'value': 'url', 'url': 'https://keats.kcl.ac.uk/course/view.php?id=1'},
        {'name': 'Other', 'value': 'other', 'domain': 'example.com', 'path': '/'},
    ]
    cookie_manager = make_cookie_manager(cookies=cookies)

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=cookie_manager):
        first = book._get_print_book_playwright_cookies()
        second = book._get_print_book_playwright_cookies()

    assert [cookie['name'] for cookie in first] == ['MoodleSession', 'KCLParent', 'UrlCookie']
    assert second is first
    cookie_manager.get_cookies_from_db.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_print_book_html_uses_cookies_and_returns_book_content(monkeypatch):
    book = make_book()
    html = (
        make_print_book_personal_metadata() +
        '<main class="book p-4"><div class="book_chapter">Chapter</div></main>'
    )
    expected_html = '<main class="book p-4"><div class="book_chapter">Chapter</div></main>'
    playwright, browser, context, page = make_fake_print_book_playwright(html=html)
    request_callbacks = []
    page.on = MagicMock(side_effect=lambda event, callback: request_callbacks.append((event, callback)))
    install_fake_playwright(monkeypatch, playwright)
    cookie_manager = make_cookie_manager()

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=cookie_manager):
        with patch('moodle_dl.cookie_manager.CookieManager.is_cookie_expired_response', return_value=False):
            result = await book._fetch_print_book_html(20, 1)

    assert result == (
        expected_html,
        'https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20',
    )
    playwright.firefox.launch.assert_awaited_once_with(headless=True)
    browser.new_context.assert_awaited_once()
    context.add_cookies.assert_awaited_once_with(cookie_manager.get_cookies_from_db.return_value)
    assert page.goto.await_args_list[0].args[0] == 'https://keats.kcl.ac.uk/course/view.php?id=1'
    assert page.goto.await_args_list[1].args[0] == (
        'https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20'
    )
    browser.close.assert_awaited_once()
    assert request_callbacks[0][0] == 'request'

    request = SimpleNamespace(
        url='https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20',
        all_headers=AsyncMock(return_value={'cookie': ''}),
    )
    await request_callbacks[0][1](request)
    request.all_headers.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_print_book_html_returns_empty_for_interactive_auth_redirect(monkeypatch):
    book = make_book()
    playwright, browser, _, _ = make_fake_print_book_playwright(
        current_url='https://login.microsoftonline.com/common/oauth2/authorize'
    )
    install_fake_playwright(monkeypatch, playwright)

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()):
        result = await book._fetch_print_book_html(20, 1)

    assert result == ('', '')
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_print_book_html_returns_empty_for_missing_response_and_auth_content(monkeypatch):
    no_response = make_book()
    playwright, browser, _, page = make_fake_print_book_playwright()
    page.goto = AsyncMock(side_effect=[MagicMock(), None])
    install_fake_playwright(monkeypatch, playwright)

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()):
        assert await no_response._fetch_print_book_html(20, 1) == ('', '')

    browser.close.assert_awaited_once()

    auth_content = make_book()
    playwright, browser, _, page = make_fake_print_book_playwright(
        current_url='https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20'
    )
    page.goto = AsyncMock(side_effect=[None, MagicMock()])
    page.content = AsyncMock(return_value='<html><input name="loginfmt" id="i0116"></html>')
    install_fake_playwright(monkeypatch, playwright)

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()):
        assert await auth_content._fetch_print_book_html(20, 1) == ('', '')

    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_print_book_html_refreshes_expired_cookies_once(monkeypatch):
    book = make_book()
    original_fetch_print_book_html = BookMod._fetch_print_book_html
    book._fetch_print_book_html = AsyncMock(return_value=('retried html', 'retried url'))
    playwright, browser, _, _ = make_fake_print_book_playwright(
        html='<div class="book_chapter">Session expired</div>',
        current_url='https://keats.kcl.ac.uk/course/view.php?id=1',
    )
    install_fake_playwright(monkeypatch, playwright)
    cookie_manager = make_cookie_manager(refresh_result=True)

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=cookie_manager):
        with patch('moodle_dl.cookie_manager.CookieManager.is_cookie_expired_response', return_value=True):
            result = await original_fetch_print_book_html(book, 20, 1)

    assert result == ('retried html', 'retried url')
    cookie_manager.refresh_cookies.assert_called_once_with(auto_get_token=False)
    book._fetch_print_book_html.assert_awaited_once_with(20, 1, retry_count=1)
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_print_book_html_handles_cookie_refresh_failures_and_retry_exhaustion(monkeypatch):
    refresh_failed = make_book()
    playwright, browser, _, _ = make_fake_print_book_playwright(
        html='<div class="book_chapter">Guest user</div>',
        current_url='https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20',
    )
    install_fake_playwright(monkeypatch, playwright)
    cookie_manager = make_cookie_manager(refresh_result=False)

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=cookie_manager):
        with patch('moodle_dl.cookie_manager.CookieManager.is_cookie_expired_response', return_value=True):
            assert await refresh_failed._fetch_print_book_html(20, 1) == ('', '')

    cookie_manager.refresh_cookies.assert_called_once_with(auto_get_token=False)
    browser.close.assert_awaited_once()

    retry_enrol = make_book()
    playwright, browser, _, _ = make_fake_print_book_playwright(
        html='<div class="book_chapter">Still expired</div>',
        current_url='https://keats.kcl.ac.uk/enrol/index.php?id=1',
    )
    install_fake_playwright(monkeypatch, playwright)
    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()):
        with patch('moodle_dl.cookie_manager.CookieManager.is_cookie_expired_response', return_value=True):
            assert await retry_enrol._fetch_print_book_html(20, 1, retry_count=1) == ('', '')
    browser.close.assert_awaited_once()

    retry_login_like = make_book()
    playwright, browser, _, _ = make_fake_print_book_playwright(
        html='<div class="book_chapter">Still expired</div>',
        current_url='https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20',
    )
    install_fake_playwright(monkeypatch, playwright)
    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()):
        with patch('moodle_dl.cookie_manager.CookieManager.is_cookie_expired_response', return_value=True):
            assert await retry_login_like._fetch_print_book_html(20, 1, retry_count=1) == ('', '')
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_print_book_html_returns_empty_when_timeout_refresh_fails(monkeypatch):
    book = make_book()
    playwright, browser, _, page = make_fake_print_book_playwright()
    page.goto = AsyncMock(side_effect=[MagicMock(), RuntimeError('Timeout 60000ms exceeded')])
    install_fake_playwright(monkeypatch, playwright)
    cookie_manager = make_cookie_manager(refresh_result=False)

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=cookie_manager):
        result = await book._fetch_print_book_html(20, 1)

    assert result == ('', '')
    cookie_manager.refresh_cookies.assert_called_once_with(auto_get_token=False)
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_print_book_html_handles_non_book_content_and_page_errors(monkeypatch):
    non_book = make_book()
    playwright, browser, _, page = make_fake_print_book_playwright(
        current_url='https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20'
    )
    page.goto = AsyncMock(side_effect=[None, MagicMock()])
    page.content = AsyncMock(return_value='<html><p>Not a book</p></html>')
    install_fake_playwright(monkeypatch, playwright)

    with (
        patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()),
        patch('moodle_dl.cookie_manager.CookieManager.is_cookie_expired_response', return_value=False),
        patch('builtins.open', side_effect=OSError('debug write failed')),
    ):
        assert await non_book._fetch_print_book_html(20, 1) == ('', '')
    browser.close.assert_awaited_once()

    timeout_after_retry = make_book()
    playwright, browser, _, page = make_fake_print_book_playwright()
    page.goto = AsyncMock(side_effect=[MagicMock(), RuntimeError('Timeout 60000ms exceeded')])
    install_fake_playwright(monkeypatch, playwright)
    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()):
        assert await timeout_after_retry._fetch_print_book_html(20, 1, retry_count=1) == ('', '')
    browser.close.assert_awaited_once()

    unexpected_error = make_book()
    playwright, browser, _, page = make_fake_print_book_playwright()
    page.goto = AsyncMock(side_effect=[MagicMock(), RuntimeError('browser crashed')])
    install_fake_playwright(monkeypatch, playwright)
    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()):
        assert await unexpected_error._fetch_print_book_html(20, 1) == ('', '')
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_print_book_html_reports_missing_playwright_browser(monkeypatch):
    book = make_book()
    firefox = MagicMock()
    firefox.launch = AsyncMock(
        side_effect=RuntimeError("Executable doesn't exist at /Users/test/ms-playwright/firefox")
    )
    playwright = SimpleNamespace(firefox=firefox)
    install_fake_playwright(monkeypatch, playwright)

    with patch('moodle_dl.cookie_manager.create_cookie_manager_from_client', return_value=make_cookie_manager()):
        with patch('moodle_dl.moodle.mods.book.logging') as mock_logging:
            result = await book._fetch_print_book_html(20, 1)

    assert result == ('', '')
    assert any('Playwright 浏览器未安装' in args[0] for args, _kwargs in mock_logging.error.call_args_list)


def test_interactive_auth_detection_identifies_account_picker_and_login_form():
    assert BookMod._detect_interactive_auth_requirement(
        'https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20',
        '<html>Pick an account <button>Use another account</button></html>',
    ) == 'microsoft_account_picker'
    assert BookMod._detect_interactive_auth_requirement(
        'https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=20',
        '<html><input name="loginfmt"><input id="i0116"></html>',
    ) == 'microsoft_sign_in_form'
