from unittest.mock import patch

from moodle_dl.moodle.result_builder import ResultBuilder
from moodle_dl.types import Course, MoodleURL


def make_builder(version=2024010100):
    return ResultBuilder(
        moodle_url=MoodleURL(use_http=False, domain='keats.kcl.ac.uk', path='/'),
        version=version,
        mod_plurals={'quiz': 'quizzes', 'resource': 'resources', 'page': 'pages'},
        token='token-abc',
    )


def make_location(**overrides):
    location = {
        'section_id': 1,
        'section_name': 'Week 1',
        'module_id': 10,
        'module_name': 'Module',
        'module_modname': 'resource',
    }
    location.update(overrides)
    return location


def test_filter_changing_attributes_normalizes_unstable_description_bits():
    description = (
        '<div id="abc">Hello%20World&nbsp;</div>'
        '<img src="/theme/image.php/theme/core/12345/icon">'
        '<input type="hidden" name="sesskey" value="ABC123" />'
        "<span id='other'>x</span>"
        "<input type='hidden' name='sesskey' value='DEF456' />"
    )

    result = ResultBuilder.filter_changing_attributes(description)

    assert 'id=' not in result
    assert 'sesskey' not in result
    assert 'Hello World' in result
    assert '/theme/image.php/theme/core/-1/icon' in result
    assert ResultBuilder.filter_changing_attributes(None) == ''
    assert ResultBuilder.filter_changing_attributes({'raw': 'value'}) == {'raw': 'value'}


def test_get_mod_plural_name_uses_moodle_plural_map_or_capitalizes_name():
    builder = make_builder()

    assert builder.get_mod_plural_name('quiz') == 'Quizzes'
    assert builder.get_mod_plural_name('forum') == 'Forum'


def test_get_files_in_sections_adds_summary_positions_and_kaltura_total():
    builder = make_builder()
    course_sections = [
        {
            'id': 1,
            'name': 'Week 1',
            'summary': '<p>Summary <a href="https://example.com/summary">link</a></p>',
            'modules': [
                {
                    'id': 20,
                    'name': 'Lecture Video',
                    'modname': 'kalvidres',
                    'url': 'https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=20',
                }
            ],
        }
    ]

    files = builder.get_files_in_sections(course_sections, fetched_mods={})

    summary = next(file for file in files if file.module_modname == 'section_summary')
    summary_url = next(file for file in files if file.content_fileurl == 'https://example.com/summary')
    video = next(file for file in files if file.module_modname == 'cookie_mod-kalvidres')

    assert summary.content_filename == 'Section summary'
    assert video.position_in_section == 0
    assert summary.position_in_section == 1
    assert summary_url.position_in_section == 2
    assert video.content_filename == 'Lecture Video'


def test_system_file_detection_and_position_assignment():
    builder = make_builder()
    system_names = [
        '.hidden',
        'chapter_metadata.json',
        'metadata.json',
        'Table of Contents.html',
        'video_info',
        'video_notes.md',
        'anything.json',
        'questions.json',
        'session_1.json',
    ]

    assert all(ResultBuilder._is_system_file(name) for name in system_names)
    assert ResultBuilder._is_system_file('lecture.pdf') is False

    files = [
        type('FileLike', (), {'content_filename': 'metadata.json'})(),
        type('FileLike', (), {'content_filename': 'lecture.pdf'})(),
        type('FileLike', (), {'content_filename': 'notes.docx'})(),
    ]
    builder._assign_positions_to_files(files)

    assert files[0].position_in_section is None
    assert files[1].position_in_section == 0
    assert files[2].position_in_section == 1
    assert ResultBuilder._get_extension_from_mimetype('') == ''


def test_handle_files_preserves_leganto_pdf_launch_payload():
    builder = make_builder()
    files = builder._handle_files(
        [
            {
                'filename': 'Reading List.pdf',
                'filepath': '/',
                'content_fileurl': 'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1',
                'content': '{"endpoint": "https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1"}',
                'type': 'leganto_pdf',
                'timemodified': 123,
                'isexternalfile': True,
            }
        ],
        **make_location(module_modname='lti', module_name='Reading List'),
    )

    assert len(files) == 1
    assert files[0].content_type == 'leganto_pdf'
    assert files[0].content_filename == 'Reading List.pdf'
    assert files[0].content == '{"endpoint": "https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1"}'


def test_get_files_in_modules_handles_special_and_unhandled_modules():
    legacy_builder = make_builder(version=2016052300)
    modules = [
        {
            'id': 1,
            'name': 'Week 1 Software Page',
            'modname': 'page',
            'contents': [
                {
                    'type': 'html',
                    'filename': 'index.html',
                    'html': '<p>Legacy</p>',
                    'fileurl': 'https://example.com/legacy',
                }
            ],
        },
        {
            'id': 2,
            'name': 'Video Page',
            'modname': 'moodecvideo',
            'contents': [
                {
                    'type': 'html',
                    'filename': 'index.html',
                    'html': '<p>Video</p>',
                    'fileurl': 'https://example.com/video',
                }
            ],
        },
        {
            'id': 3,
            'name': 'Folder Index',
            'modname': 'akarifolder',
            'contents': [{'type': 'file', 'filename': 'folder.pdf', 'fileurl': 'https://example.com/folder.pdf'}],
        },
        {
            'id': 4,
            'name': 'Book Missing From Fetched Mods',
            'modname': 'book',
            'contents': [],
        },
        {
            'id': 5,
            'name': 'Unhandled',
            'modname': 'unknownmod',
            'url': 'https://example.com/unhandled',
        },
    ]

    files = legacy_builder._get_files_in_modules(
        modules,
        fetched_mods={},
        section_id=1,
        section_name='Week 1',
    )

    assert any(file.module_modname == 'index_mod-page' for file in files)
    assert any(file.module_modname == 'index_mod-moodecvideo' for file in files)
    assert any(file.content_filename == 'folder.pdf' for file in files)


def test_get_files_in_modules_marks_fetched_book_modules_on_main_page():
    builder = make_builder()
    fetched_book = {
        'book': {
            42: {
                'id': 42,
                'name': 'Fetched Book',
                'files': [{'type': 'file', 'filename': 'chapter.pdf', 'fileurl': 'https://example.com/chapter.pdf'}],
            }
        }
    }

    files = builder._get_files_in_modules(
        [{'id': 42, 'name': 'Fetched Book', 'modname': 'book'}],
        fetched_book,
        section_id=1,
        section_name='Week 1',
    )

    assert fetched_book['book'][42]['on_main_page'] is True
    assert files[0].content_filename == 'chapter.pdf'


def test_get_files_not_on_main_page_skips_marked_modules_and_handles_legacy_pages():
    builder = make_builder(version=2016052300)
    fetched_mods = {
        'quiz': {
            11: {
                'id': 11,
                'name': 'Hidden Quiz',
                'files': [{'type': 'file', 'filename': 'quiz.pdf', 'fileurl': 'https://example.com/quiz.pdf'}],
            },
            12: {
                'id': 12,
                'name': 'Main Quiz',
                'on_main_page': True,
                'files': [{'type': 'file', 'filename': 'main.pdf', 'fileurl': 'https://example.com/main.pdf'}],
            },
        },
        'page': {
            21: {
                'id': 21,
                'name': 'Legacy Page',
            'files': [
                {
                    'type': 'html',
                    'filename': 'index.html',
                    'html': '<p>Page</p>',
                    'fileurl': 'https://example.com/page',
                }
            ],
            }
        },
    }

    files = builder._get_files_not_on_main_page(fetched_mods)

    assert [file.content_filename for file in files] == ['quiz.pdf', 'Legacy Page']
    assert files[0].section_name == 'Quizzes not on main page'
    assert files[0].module_modname == 'quiz'
    assert files[1].section_name == 'Pages not on main page'
    assert files[1].module_modname == 'index_mod-page'


def test_find_all_urls_extracts_external_data_and_kaltura_urls():
    builder = make_builder()
    html = (
        '<a href="https://external.example.com/file.pdf">File</a>'
        '<img src="data:text/plain,hello">'
        '<a href="https://kaf.example.com/browseandembed/index/media/entryid/1_abc123">Video</a>'
    )

    files = builder._find_all_urls(
        html,
        no_search_for_moodle_urls=False,
        filter_urls_containing=[],
        **make_location(module_modname='page', content_filepath='/desc/'),
    )

    external = next(file for file in files if file.content_fileurl == 'https://external.example.com/file.pdf')
    data_file = next(file for file in files if file.content_fileurl.startswith('data:text/plain'))
    kaltura = next(file for file in files if file.module_modname == 'cookie_mod-kalvidres')

    assert external.module_modname == 'url-description-page'
    assert external.content_filename == 'https://external.example.com/file.pdf'
    assert data_file.content_filename.startswith('embedded_text (')
    assert data_file.content_filename.endswith('.txt')
    assert kaltura.content_filename == 'Kaltura Video 1_abc123'
    assert kaltura.content_fileurl == 'https://kaf.example.com/browseandembed/index/media/entryid/1_abc123'


def test_find_all_urls_skips_moodle_domain_urls_and_detects_helixmedia():
    builder = make_builder()
    html = (
        '<a href="https://keats.kcl.ac.uk/course/view.php?id=1">Moodle</a>'
        '<a href="https://media.example.com/mod/helixmedia/view.php?id=2">Helix</a>'
    )

    files = builder._find_all_urls(
        html,
        no_search_for_moodle_urls=False,
        filter_urls_containing=[],
        **make_location(module_modname='label', content_filepath='/'),
    )

    assert len(files) == 1
    assert files[0].module_modname == 'cookie_mod-helixmedia'
    assert files[0].content_fileurl == 'https://media.example.com/mod/helixmedia/view.php?id=2'


def test_find_all_urls_handles_large_data_unknown_mime_and_long_urls():
    builder = make_builder()
    large_data = 'a' * 100001
    long_url = 'https://external.example.com/' + ('x' * 300)
    html = (
        f'<img src="data:application/x-custom,{large_data}">'
        f'<a href="{long_url}">Long</a>'
    )

    files = builder._find_all_urls(
        html,
        no_search_for_moodle_urls=False,
        filter_urls_containing=[],
        **make_location(module_modname='page', content_filepath='/'),
    )

    data_file = next(file for file in files if file.content_fileurl.startswith('data:application/x-custom'))
    long_file = next(file for file in files if file.content_fileurl.startswith('https://external.example.com/'))

    assert data_file.content_filename.startswith('embedded_application (')
    assert data_file.content_filename.endswith('.application')
    assert len(long_file.content_filename) == 254


def test_find_all_urls_converts_kaltura_variants_and_moodle_webservice_links():
    builder = make_builder()
    html = (
        '<a href="https://kaf.keats.kcl.ac.uk/filter/kaltura/lti_launch.php?source='
        'https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_lti%2F">LTI</a>'
        '<iframe src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?courseid=0&amp;source='
        'https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_same%2F'
        'playerSkin%2F42864872%2F"></iframe>'
        '<a href="https://kaf.keats.kcl.ac.uk/browseandembed/index/media/entryid/1_direct/">Direct</a>'
        '<a href="https://kaf.keats.kcl.ac.uk/player/entryid/1_generic/">Generic</a>'
        '<a href="https://keats.kcl.ac.uk/webservice/pluginfile.php/1/file.pdf">Pluginfile</a>'
    )

    files = builder._find_all_urls(
        html,
        no_search_for_moodle_urls=False,
        filter_urls_containing=[],
        **make_location(module_modname='label', content_filepath='/'),
    )

    kaltura_files = [file for file in files if file.module_modname == 'cookie_mod-kalvidres']

    assert len(kaltura_files) == 4
    assert {file.content_filename for file in kaltura_files} == {
        'Kaltura Video 1_lti',
        'Kaltura Video 1_same',
        'Kaltura Video 1_direct',
        'Kaltura Video 1_generic',
    }
    same_domain_lti = next(file for file in kaltura_files if file.content_filename == 'Kaltura Video 1_same')
    assert same_domain_lti.content_fileurl.startswith(
        'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?'
    )
    assert '/playerSkin/42864872/' in same_domain_lti.content_fileurl
    assert any(
        file.content_fileurl.startswith('https://keats.kcl.ac.uk/browseandembed/index/media/entryid/')
        for file in kaltura_files
    )


def test_handle_description_extracts_same_domain_kaltura_iframe():
    builder = make_builder()
    html = (
        '<div class="kaltura-player-container">'
        '<iframe src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?courseid=0&amp;source='
        'https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_topic5%2F'
        'showTitle%2Ffalse%2FplayerSkin%2F42864872%2F"></iframe>'
        '</div>'
    )

    files = builder._handle_description(
        html,
        **make_location(module_name='Topic 5 introduction', module_modname='label'),
    )

    video = next(file for file in files if file.module_modname == 'cookie_mod-kalvidres')
    assert video.content_filename == 'Kaltura Video 1_topic5'
    assert video.content_type == 'description-url'
    assert '/playerSkin/42864872/' in video.content_fileurl


def test_find_all_urls_skips_empty_and_filter_matched_urls():
    builder = make_builder()
    files = builder._find_all_urls(
        '<a href="">Empty</a><a href="https://blocked.example.com/file.pdf">Blocked</a>',
        no_search_for_moodle_urls=False,
        filter_urls_containing=['blocked.example.com'],
        **make_location(module_modname='page', content_filepath='/'),
    )

    assert len(files) == 1
    assert files[0].content_fileurl == 'https://blocked.example.com/file.pdf'


def test_handle_cookie_mod_uses_module_name_and_timemodified():
    builder = make_builder()

    files = builder._handle_cookie_mod(
        'https://video.example.com/view',
        **make_location(module_name='Video', module_modname='cookie_mod-kalvidres', content_timemodified=123),
    )

    assert len(files) == 1
    assert files[0].content_filename == 'Video'
    assert files[0].content_type == 'cookie_mod'
    assert files[0].content_timemodified == 123
    assert files[0].content_isexternalfile is True


def test_handle_files_resource_uses_display_name_and_pluginfile_fix():
    builder = make_builder()
    contents = [
        {
            'type': 'file',
            'filename': 'api-name.pdf',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/pluginfile.php/1/mod_resource/content/1/api-name.pdf',
            'filesize': 100,
            'timemodified': 5,
        }
    ]

    with patch('moodle_dl.moodle.result_builder.UrlHelper.fix_pluginfile_url', return_value='fixed-url') as fix:
        files = builder._handle_files(
            contents,
            **make_location(module_name='Displayed Name', module_modname='resource'),
        )

    fix.assert_called_once_with(
        'https://keats.kcl.ac.uk/pluginfile.php/1/mod_resource/content/1/api-name.pdf',
        token='token-abc',
        moodle_base_url='https://keats.kcl.ac.uk/',
    )
    assert files[0].content_filename == 'Displayed Name.pdf'
    assert files[0].content_fileurl == 'fixed-url'
    assert files[0].content_filesize == 100


def test_handle_files_resource_can_infer_extension_from_mimetype():
    builder = make_builder()
    contents = [{'type': 'file', 'filename': 'download', 'mimetype': 'application/pdf', 'fileurl': 'https://x'}]

    files = builder._handle_files(contents, **make_location(module_name='Lecture Handout', module_modname='resource'))

    assert files[0].content_filename == 'Lecture Handout.pdf'


def test_handle_files_processes_directory_placeholders_and_nested_contents():
    builder = make_builder()
    contents = [
        {
            'type': 'directory_placeholder',
            'filename': 'Chapter 1',
            'filepath': '/book/',
            'contents': [
                {
                    'type': 'content',
                    'filename': 'page.html',
                    'filepath': '/book/chapter-1/',
                    'content': '<p>Hello</p>',
                }
            ],
        }
    ]

    files = builder._handle_files(contents, **make_location(module_modname='book'))

    assert [file.content_filename for file in files] == ['Chapter 1', 'page.html']
    assert files[0].content_type == 'directory_placeholder'
    assert files[1].content == '<p>Hello</p>'


def test_handle_files_empty_directory_placeholder_and_pluginfile_fix_failure():
    builder = make_builder()
    contents = [
        {'type': 'directory_placeholder', 'filename': '', 'filepath': '/empty/'},
        {
            'type': 'file',
            'filename': 'broken.pdf',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/pluginfile.php/1/mod_resource/content/broken.pdf',
        },
    ]

    with patch('moodle_dl.moodle.result_builder.UrlHelper.fix_pluginfile_url', side_effect=RuntimeError('bad url')):
        files = builder._handle_files(contents, **make_location(module_modname='book'))

    assert files[0].content_filename == '__empty_chapter__'
    assert files[1].content_fileurl.endswith('broken.pdf')


def test_handle_files_skips_empty_url_modules_and_recurses_nested_regular_contents():
    builder = make_builder()
    contents = [
        {'type': 'file', 'filename': 'skip.url', 'fileurl': ''},
        {
            'type': 'content',
            'filename': 'parent.json',
            'content': '{}',
            'contents': [
                {
                    'type': 'kalvidres_embedded',
                    'filename': 'Nested Video',
                    'fileurl': 'https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=7',
                }
            ],
        },
    ]

    assert builder._handle_files(
        [{'type': 'file', 'filename': 'skip.url', 'fileurl': ''}],
        **make_location(module_modname='url'),
    ) == []

    files = builder._handle_files(contents[1:], **make_location(module_modname='book'))

    assert [file.content_filename for file in files] == ['parent.json', 'Nested Video']
    assert files[1].content_type == 'cookie_mod'


def test_handle_files_creates_description_hash_html_content_and_url_files():
    builder = make_builder()
    contents = [
        {
            'type': 'description',
            'filename': 'description.md',
            'description': '<p id="unstable">See <a href="https://example.com">Example</a></p>',
        },
        {
            'type': 'html',
            'filename': 'page.html',
            'html': '<p>Page</p>',
            'no_search_for_urls': True,
            'visible': 0,
            'completion': 2,
            'timecreated': 11,
            'sortorder': 4,
        },
    ]

    files = builder._handle_files(contents, **make_location(module_modname='page'))

    description = next(file for file in files if file.content_filename == 'description.md')
    extracted_url = next(file for file in files if file.content_type == 'description-url')
    html_file = next(file for file in files if file.content_filename == 'page.html')

    assert description.text_content.startswith('<p id=')
    assert len(description.hash) == 64
    assert extracted_url.content_fileurl == 'https://example.com'
    assert html_file.html_content == '<p>Page</p>'
    assert html_file.visible == 0
    assert html_file.completion == 2
    assert html_file.timecreated == 11
    assert html_file.sortorder == 4


def test_handle_files_embedded_kaltura_becomes_cookie_mod_file():
    builder = make_builder()
    contents = [
        {
            'type': 'kalvidres_embedded',
            'filename': 'Lecture video',
            'filepath': '/chapter/',
            'fileurl': 'https://keats.kcl.ac.uk/browseandembed/index/media/entryid/1_video',
            'timemodified': 99,
        }
    ]

    files = builder._handle_files(contents, **make_location(module_modname='book'))

    assert len(files) == 1
    assert files[0].module_modname == 'cookie_mod-kalvidres'
    assert files[0].content_type == 'cookie_mod'
    assert files[0].content_timemodified == 99


def test_handle_description_creates_description_file_and_url_entries():
    builder = make_builder()

    files = builder._handle_description(
        '<p>Read <a href="https://example.com/info">Info</a></p>',
        **make_location(module_name='Intro', module_modname='url'),
    )

    assert files[0].content_filename == 'Intro'
    assert files[0].module_modname == 'url_description'
    assert files[0].text_content.startswith('<p>Read')
    assert len(files[0].hash) == 64
    assert files[1].content_fileurl == 'https://example.com/info'


def test_add_files_to_courses_uses_course_specific_core_and_mod_data():
    builder = make_builder()
    course = Course(1, 'Course')
    core_contents = {
        1: [
            {
                'id': 10,
                'name': 'Week 1',
                'modules': [{'id': 100, 'name': 'Slides', 'modname': 'resource', 'contents': []}],
            }
        ]
    }
    fetched_mods = {
        'resource': {
            1: {
                100: {
                    'id': 100,
                    'name': 'Slides',
                    'files': [{'type': 'file', 'filename': 'slides.pdf', 'fileurl': 'https://example.com/slides.pdf'}],
                }
            },
            2: {
                200: {
                    'id': 200,
                    'name': 'Other',
                    'files': [{'type': 'file', 'filename': 'other.pdf', 'fileurl': 'https://example.com/other.pdf'}],
                }
            },
        }
    }

    builder.add_files_to_courses([course], core_contents, fetched_mods)

    assert [file.content_filename for file in course.files] == ['Slides.pdf']
    assert course.files[0].section_name == 'Week 1'


def test_get_files_from_blocks_filters_noise_and_creates_html_files():
    builder = make_builder()
    blocks = [
        {
            'name': 'html',
            'instanceid': 1,
            'visible': True,
            'contents': {'title': 'Key Contacts', 'content': '<p>A</p>'},
        },
        {'name': 'calendar_month', 'instanceid': 2, 'visible': True, 'contents': {'title': 'Calendar', 'content': 'x'}},
        {'name': 'html', 'instanceid': 3, 'visible': False, 'contents': {'title': 'Hidden', 'content': 'x'}},
        {'name': 'html', 'instanceid': 4, 'visible': True, 'contents': {'title': '', 'content': 'x'}},
    ]

    files = builder.get_files_from_blocks(blocks, course_id=1)

    assert len(files) == 1
    assert files[0].content_filename.replace('_', ' ') == 'Key Contacts'
    assert files[0].section_name == '_course_info'
    assert files[0].module_modname == 'block_html'
    assert files[0].content_type == 'html'
    assert files[0].html_content == '<p>A</p>'
    assert len(files[0].hash) == 32


def test_add_blocks_to_course_extends_existing_files():
    builder = make_builder()
    course = Course(1, 'Course')
    course.files = []

    builder.add_blocks_to_course(
        course,
        [{'name': 'html', 'instanceid': 1, 'visible': True, 'contents': {'title': 'Info', 'content': '<p>I</p>'}}],
    )

    assert len(course.files) == 1
    assert course.files[0].content_filename == 'Info'
