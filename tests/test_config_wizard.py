from unittest.mock import MagicMock, patch

import pytest

from moodle_dl.cli.config_wizard import ConfigWizard
from moodle_dl.types import Course, MoodleDlOpts


def make_wizard(config=None):
    wizard = ConfigWizard.__new__(ConfigWizard)
    wizard.config = config or MagicMock()
    wizard.opts = MoodleDlOpts()
    wizard.core_handler = MagicMock()
    wizard.section_separator = MagicMock()
    return wizard


def test_get_config_steps_count_matches_interactive_steps():
    assert ConfigWizard.get_config_steps_count() == 4


def test_get_user_id_and_version_uses_configured_values():
    wizard = make_wizard()
    wizard.config.get_userid_and_version.return_value = ('123', 2024010100)

    assert wizard.get_user_id_and_version() == (123, 2024010100)
    assert wizard.core_handler.version == 2024010100
    wizard.core_handler.fetch_userid_and_version.assert_not_called()


def test_get_user_id_and_version_fetches_when_config_missing_or_invalid():
    wizard = make_wizard()
    wizard.config.get_userid_and_version.return_value = (None, None)
    wizard.core_handler.fetch_userid_and_version.return_value = (456, 2024020200)

    assert wizard.get_user_id_and_version() == (456, 2024020200)

    wizard = make_wizard()
    wizard.config.get_userid_and_version.return_value = ('not-an-int', 2024010100)
    wizard.core_handler.fetch_userid_and_version.return_value = (789, 2024030300)

    assert wizard.get_user_id_and_version() == (789, 2024030300)


@pytest.mark.parametrize(
    ('raw_input', 'expected'),
    [
        ('137304', [137304]),
        ('137304 137305 137306', [137304, 137305, 137306]),
        ('137304, 137305,137306', [137304, 137305, 137306]),
        ('https://keats.example/course/view.php?id=137304', [137304]),
        ('keats.example/course/view.php?foo=bar&id=137305', [137305]),
        ('0 -1 2', [2]),
        ('137304 abc', []),
        ('', []),
    ],
)
def test_parse_course_ids(raw_input, expected):
    assert ConfigWizard._parse_course_ids(raw_input) == expected


def test_select_courses_to_download_saves_whitelist_selection():
    wizard = make_wizard()
    courses = [Course(1, 'One'), Course(2, 'Two'), Course(3, 'Three')]
    wizard.config.get_download_course_ids.return_value = [2]
    wizard.config.get_dont_download_course_ids.return_value = []

    with patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', side_effect=[True, True]):
        with patch('moodle_dl.cli.config_wizard.Cutie.select_multiple', return_value=[0, 2]) as select_multiple:
            wizard._select_courses_to_download(courses)

    assert select_multiple.call_args.kwargs['ticked_indices'] == [1]
    wizard.config.set_property.assert_called_once_with('download_course_ids', [1, 3])
    wizard.config.remove_property.assert_called_once_with('dont_download_course_ids')


def test_select_courses_to_download_saves_blacklist_selection():
    wizard = make_wizard()
    courses = [Course(1, 'One'), Course(2, 'Two'), Course(3, 'Three')]
    wizard.config.get_download_course_ids.return_value = []
    wizard.config.get_dont_download_course_ids.return_value = [3]

    with patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', side_effect=[False, True]):
        with patch('moodle_dl.cli.config_wizard.Cutie.select_multiple', return_value=[1]) as select_multiple:
            wizard._select_courses_to_download(courses)

    assert select_multiple.call_args.kwargs['ticked_indices'] == [2]
    wizard.config.set_property.assert_called_once_with('dont_download_course_ids', [2])
    wizard.config.remove_property.assert_called_once_with('download_course_ids')


def test_select_courses_to_download_empty_blacklist_removes_filters():
    wizard = make_wizard()
    courses = [Course(1, 'One')]
    wizard.config.get_download_course_ids.return_value = []
    wizard.config.get_dont_download_course_ids.return_value = []

    with patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', side_effect=[False, True]):
        with patch('moodle_dl.cli.config_wizard.Cutie.select_multiple', return_value=[]):
            wizard._select_courses_to_download(courses)

    wizard.config.remove_property.assert_any_call('dont_download_course_ids')
    wizard.config.remove_property.assert_any_call('download_course_ids')
    wizard.config.set_property.assert_not_called()


def test_select_sections_to_download_returns_unselected_section_ids():
    wizard = make_wizard()
    sections = [
        {'id': '1', 'name': 'Intro'},
        {'id': 2, 'name': 'Week 2'},
        {'id': None},
        {'id': 'bad', 'name': 'Broken'},
    ]

    with patch('moodle_dl.cli.config_wizard.Cutie.select_multiple', return_value=[0, 2]):
        result = wizard._select_sections_to_download(sections, excluded=[2])

    assert result == [2, 'bad']


def test_change_settings_updates_name_structure_and_sections():
    wizard = make_wizard()
    course = Course(10, 'Original')
    options = {}
    wizard.core_handler.fetch_sections.return_value = [{'id': 1, 'name': 'Intro'}]
    wizard._select_sections_to_download = MagicMock(return_value=[1])

    with patch('builtins.input', return_value='Renamed'):
        with patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', side_effect=[False, True]):
            wizard._change_settings_of(course, options)

    assert options['10'] == {
        'original_name': 'Original',
        'overwrite_name_with': 'Renamed',
        'create_directory_structure': False,
        'excluded_sections': [1],
    }
    wizard.config.set_property.assert_called_once_with('options_of_courses', options)
    wizard.core_handler.fetch_sections.assert_called_once_with(10)


def test_set_options_of_courses_adds_manual_course_fallback_and_edits_visible_course():
    wizard = make_wizard()
    wizard.config.get_manually_specified_course_ids.return_value = [99]
    wizard.config.get_download_course_ids.return_value = [1]
    wizard.config.get_dont_download_course_ids.return_value = []
    wizard.config.get_download_public_course_ids.return_value = [99]
    wizard.config.has_property.side_effect = lambda key: key == 'download_course_ids'
    wizard.config.get_options_of_courses.return_value = {}
    wizard._change_settings_of = MagicMock()

    with patch('moodle_dl.moodle.course_validator.validate_course_with_web_api', side_effect=RuntimeError('offline')):
        with patch('moodle_dl.cli.config_wizard.Cutie.select', side_effect=[2, 0]):
            wizard._set_options_of_courses([Course(1, 'Enrolled')])

    selected_course = wizard._change_settings_of.call_args.args[0]
    assert selected_course.id == 99
    assert selected_course.fullname.replace('_', ' ') == 'Course 99'


def test_add_manually_specified_courses_validates_new_ids_and_updates_config():
    wizard = make_wizard()
    wizard.config.get_manually_specified_course_ids.return_value = [100]
    wizard.config.get_download_public_course_ids.return_value = [100]

    with patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', side_effect=[True, False]):
        with patch('builtins.input', return_value='101, 102'):
            with patch(
                'moodle_dl.moodle.course_validator.validate_course_with_web_api',
                side_effect=lambda *args, **kwargs: {'id': args[2], 'fullname': f'Course {args[2]}'},
            ):
                wizard._interactively_add_manually_specified_courses()

    wizard.config.set_manually_specified_course_ids.assert_called_once_with([100, 101, 102])
    wizard.config.set_property.assert_called_once_with('download_public_course_ids', [100, 101, 102])


def test_add_manually_specified_courses_returns_when_user_declines():
    wizard = make_wizard()
    wizard.config.get_manually_specified_course_ids.return_value = [100]

    with patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=False):
        wizard._interactively_add_manually_specified_courses()

    wizard.config.set_manually_specified_course_ids.assert_not_called()
    wizard.config.set_property.assert_not_called()


def test_interactively_add_all_visible_courses_filters_enrolled_courses_and_updates_config(tmp_path):
    wizard = make_wizard()
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    wizard.config.get_download_public_course_ids.return_value = []
    wizard.config.get_options_of_courses.return_value = {}
    wizard.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))
    wizard.core_handler.fetch_courses.return_value = [Course(1, 'Enrolled')]
    wizard.core_handler.fetch_all_visible_courses.return_value = [
        Course(1, 'Enrolled'),
        Course(2, 'Visible'),
    ]

    with patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True):
        wizard.interactively_add_all_visible_courses()

    wizard.config.set_property.assert_any_call(
        'options_of_courses',
        {
            '2': {
                'original_name': 'Visible',
                'overwrite_name_with': None,
                'create_directory_structure': True,
            }
        },
    )
    wizard.config.set_property.assert_any_call('download_public_course_ids', [2])


def test_select_modules_to_download_saves_new_and_legacy_options():
    wizard = make_wizard()
    wizard.config.has_property.return_value = False
    selected = [0, 4, 29]

    with patch('moodle_dl.cli.config_wizard.Cutie.select_multiple', return_value=selected) as select_multiple:
        wizard._select_modules_to_download()

    assert len(select_multiple.call_args.kwargs['ticked_indices']) == 30
    download_options_call = wizard.config.set_property.call_args_list[0]
    assert download_options_call.args[0] == 'download_options'
    download_options = download_options_call.args[1]
    assert download_options['submissions'] is True
    assert download_options['resources'] is True
    assert download_options['metadata_files'] is True
    assert download_options['books'] is False
    assert download_options_call.kwargs == {'ensure_complete': False}
    wizard.config.set_property.assert_any_call('download_submissions', True, ensure_complete=False)
    wizard.config.set_property.assert_any_call('download_books', False, ensure_complete=False)


def test_select_modules_to_download_uses_existing_config_for_defaults():
    config = MagicMock(spec=['has_property', 'set_property', 'get_download_submissions', 'get_download_quizzes'])
    wizard = make_wizard(config)
    wizard.config.has_property.return_value = True
    wizard.config.get_download_submissions.return_value = True
    wizard.config.get_download_quizzes.return_value = True

    with patch('moodle_dl.cli.config_wizard.Cutie.select_multiple', return_value=[]) as select_multiple:
        wizard._select_modules_to_download()

    ticked = select_multiple.call_args.kwargs['ticked_indices']
    assert 0 in ticked
    assert 1 in ticked
    assert len(ticked) == 2


def test_check_sso_cookies_exist_detects_non_moodle_domain(tmp_path):
    wizard = make_wizard()
    cookies_path = tmp_path / 'Cookies.txt'
    cookies_path.write_text(
        '# Netscape HTTP Cookie File\n'
        '.keats.kcl.ac.uk\tTRUE\t/\tTRUE\t0\tMoodleSession\tabc\n'
        '.login.microsoftonline.com\tTRUE\t/\tTRUE\t0\tESTSAUTH\tabc\n',
        encoding='utf-8',
    )

    assert wizard._check_sso_cookies_exist(str(cookies_path), 'keats.kcl.ac.uk') is True

    cookies_path.write_text(
        '.keats.kcl.ac.uk\tTRUE\t/\tTRUE\t0\tMoodleSession\tabc\n',
        encoding='utf-8',
    )
    assert wizard._check_sso_cookies_exist(str(cookies_path), 'keats.kcl.ac.uk') is False
    assert wizard._check_sso_cookies_exist(str(tmp_path / 'missing.txt'), 'keats.kcl.ac.uk') is False
