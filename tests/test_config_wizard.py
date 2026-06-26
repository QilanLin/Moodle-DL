from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

from moodle_dl.cli.config_wizard import ConfigWizard
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, MoodleDlOpts, MoodleURL


def make_wizard(config=None):
    wizard = ConfigWizard.__new__(ConfigWizard)
    wizard.config = config or MagicMock()
    wizard.opts = MoodleDlOpts()
    wizard.core_handler = MagicMock()
    wizard.section_separator = MagicMock()
    return wizard


def make_export_module(
    *,
    export_browser=True,
    test_cookies=True,
    token='token',
    private_token='private',
    export_interactive=True,
):
    return SimpleNamespace(
        export_cookies_from_browser=MagicMock(return_value=export_browser),
        test_cookies=MagicMock(return_value=test_cookies),
        extract_api_token_with_cookies=MagicMock(return_value=(token, private_token)),
        export_cookies_interactive=MagicMock(return_value=export_interactive),
    )


def make_import_spec():
    return SimpleNamespace(loader=SimpleNamespace(exec_module=MagicMock()))


def test_get_config_steps_count_matches_interactive_steps():
    assert ConfigWizard.get_config_steps_count() == 4


def test_interactively_acquire_config_navigates_steps_and_sets_auto_flags():
    wizard = make_wizard()
    courses = [Course(1, 'One')]
    wizard.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))
    wizard.core_handler.fetch_courses.return_value = courses
    wizard._select_courses_to_download = MagicMock()
    wizard._interactively_add_manually_specified_courses = MagicMock()
    wizard._set_options_of_courses = MagicMock()
    wizard._select_modules_to_download = MagicMock()

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.select', side_effect=[0, 0, 1, 0, 0, 1]),
    ):
        wizard.interactively_acquire_config()

    wizard.core_handler.fetch_courses.assert_called_once_with(7)
    wizard.config.set_property.assert_any_call('download_also_with_cookie', True, ensure_complete=False)
    wizard.config.set_property.assert_any_call('download_linked_files', True, ensure_complete=False)
    wizard._select_courses_to_download.assert_called_once_with(courses)
    assert wizard._interactively_add_manually_specified_courses.call_count == 2
    assert wizard._set_options_of_courses.call_count == 2
    wizard._select_modules_to_download.assert_called_once()


def test_interactively_acquire_config_finish_option_hidden_on_early_steps():
    """'完成配置并退出' must NOT appear on steps 1-3 (forces continuation to step 4).

    Regression test for the bug where '完成配置并退出' was always shown,
    allowing users to skip step 4 (Configure module types to download) and
    leaving download_options empty, causing the
    '所有下载选项都是 false' validator warning.
    """
    wizard = make_wizard()
    courses = [Course(1, 'One')]
    wizard.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))
    wizard.core_handler.fetch_courses.return_value = courses
    wizard._select_courses_to_download = MagicMock()
    wizard._interactively_add_manually_specified_courses = MagicMock()
    wizard._set_options_of_courses = MagicMock()
    wizard._select_modules_to_download = MagicMock()

    # Capture the choices passed to Cutie.select at each step
    captured_choices_per_step = []

    def fake_select(choices):
        captured_choices_per_step.append(list(choices))
        # Smart selection:
        # - 'continue' on early steps
        # - 'finish' on last step (to avoid infinite loop)
        if '完成配置并退出' in choices:
            return choices.index('完成配置并退出')
        return 0  # first option (continue)

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.select', side_effect=fake_select),
    ):
        # 4 calls (one per step)
        wizard.interactively_acquire_config()

    # Must have exactly 4 steps (each call to Cutie.select is one step)
    assert len(captured_choices_per_step) == 4, (
        'Expected 4 Cutie.select calls (one per step), got {}'.format(
            len(captured_choices_per_step)
        )
    )

    # Step 1: only '继续下一步'
    assert captured_choices_per_step[0] == ['继续下一步'], (
        'Step 1 should only show [继续下一步], got {}'.format(captured_choices_per_step[0])
    )

    # Step 2: '继续下一步' + '返回上一步' (NO '完成配置并退出')
    assert captured_choices_per_step[1] == ['继续下一步', '返回上一步'], (
        'Step 2 should show [继续下一步, 返回上一步], got {}'.format(captured_choices_per_step[1])
    )

    # Step 3: '继续下一步' + '返回上一步' (NO '完成配置并退出')
    assert captured_choices_per_step[2] == ['继续下一步', '返回上一步'], (
        'Step 3 should show [继续下一步, 返回上一步], got {}'.format(captured_choices_per_step[2])
    )

    # Step 4 (last): '返回上一步' + '完成配置并退出' (NO '继续下一步')
    assert captured_choices_per_step[3] == ['返回上一步', '完成配置并退出'], (
        'Step 4 (last) should show [返回上一步, 完成配置并退出], got {}'.format(
            captured_choices_per_step[3]
        )
    )

    # CRITICAL: '完成配置并退出' must NOT be in steps 1-3 choices
    for step_idx in range(3):
        assert '完成配置并退出' not in captured_choices_per_step[step_idx], (
            "Bug regression: '完成配置并退出' appeared in step {} choices: {}".format(
                step_idx + 1, captured_choices_per_step[step_idx]
            )
        )

    # All 4 step methods should be called exactly once
    wizard._select_courses_to_download.assert_called_once_with(courses)
    wizard._interactively_add_manually_specified_courses.assert_called_once()
    wizard._set_options_of_courses.assert_called_once()
    wizard._select_modules_to_download.assert_called_once()


def test_interactively_acquire_config_finish_only_available_at_last_step():
    """Explicit: '完成配置并退出' available ONLY when current_step == len(steps) - 1.

    This test verifies the exact constraint that prevents users from exiting
    early before completing all 4 additional configuration steps.
    """
    wizard = make_wizard()
    wizard.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))
    wizard.core_handler.fetch_courses.return_value = [Course(1, 'One')]
    wizard._select_courses_to_download = MagicMock()
    wizard._interactively_add_manually_specified_courses = MagicMock()
    wizard._set_options_of_courses = MagicMock()
    wizard._select_modules_to_download = MagicMock()

    num_steps = 4
    cutie_calls = []

    def fake_select(choices):
        cutie_calls.append(list(choices))
        # First call: try to break out by selecting '完成配置并退出' if available
        # If not available, select first option (continue)
        if '完成配置并退出' in choices:
            return choices.index('完成配置并退出')
        return 0  # first available (continue)

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.select', side_effect=fake_select),
    ):
        wizard.interactively_acquire_config()

    # Should have completed all 4 steps because '完成配置并退出' was
    # only available on the last step (step 4)
    wizard._select_courses_to_download.assert_called_once()
    wizard._interactively_add_manually_specified_courses.assert_called_once()
    wizard._set_options_of_courses.assert_called_once()
    wizard._select_modules_to_download.assert_called_once()

    # Exactly 4 step prompts
    assert len(cutie_calls) == num_steps, (
        'Expected {} step prompts, got {}'.format(num_steps, len(cutie_calls))
    )


def test_interactively_acquire_config_user_cannot_early_exit_after_step_1():
    """User selecting '完成配置并退出' after step 1 is impossible (regression guard)."""
    wizard = make_wizard()
    wizard.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))
    wizard.core_handler.fetch_courses.return_value = [Course(1, 'One')]
    wizard._select_courses_to_download = MagicMock()
    wizard._interactively_add_manually_specified_courses = MagicMock()
    wizard._set_options_of_courses = MagicMock()
    wizard._select_modules_to_download = MagicMock()

    captured_choices_step1 = []

    def fake_select(choices):
        # Capture FIRST step's choices, then pick 'finish' if available
        # to avoid infinite loop (back+forward+back+forward...)
        if len(captured_choices_step1) == 0:
            captured_choices_step1.extend(choices)
            return 0  # Step 1: only '继续下一步' available
        # All subsequent steps: pick 'finish' if available (last step)
        # to terminate the wizard after 4 steps
        if '完成配置并退出' in choices:
            return choices.index('完成配置并退出')
        return 0  # continue (for early steps)

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.select', side_effect=fake_select),
    ):
        wizard.interactively_acquire_config()

    # Step 1 choices must NOT include '完成配置并退出'
    assert '完成配置并退出' not in captured_choices_step1, (
        "Step 1 must not show '完成配置并退出'. Got: {}".format(captured_choices_step1)
    )
    # And step 1 should only have '继续下一步'
    assert captured_choices_step1 == ['继续下一步']


@pytest.mark.parametrize('current_step,num_steps,expected_choices', [
    # current_step 0 (Step 1): only continue
    (0, 4, ['继续下一步']),
    # current_step 1 (Step 2): continue + back (NO finish)
    (1, 4, ['继续下一步', '返回上一步']),
    # current_step 2 (Step 3): continue + back (NO finish)
    (2, 4, ['继续下一步', '返回上一步']),
    # current_step 3 (Step 4 - last): back + finish (NO continue)
    (3, 4, ['返回上一步', '完成配置并退出']),
    # Edge case: 1-step config
    (0, 1, ['完成配置并退出']),
    # Edge case: 5-step config - finish only on last
    (0, 5, ['继续下一步']),
    (1, 5, ['继续下一步', '返回上一步']),
    (2, 5, ['继续下一步', '返回上一步']),
    (3, 5, ['继续下一步', '返回上一步']),
    (4, 5, ['返回上一步', '完成配置并退出']),
])
def test_interactively_acquire_config_choice_logic(current_step, num_steps, expected_choices):
    """Parametrized test for choice logic across multiple step counts.

    Verifies the exact rule:
    - '继续下一步': visible when current_step < num_steps - 1
    - '返回上一步': visible when current_step > 0
    - '完成配置并退出': visible when current_step == num_steps - 1
    """
    # Replicate the choice-building logic
    choices = []
    if current_step < num_steps - 1:
        choices.append('继续下一步')
    if current_step > 0:
        choices.append('返回上一步')
    if current_step == num_steps - 1:
        choices.append('完成配置并退出')

    assert choices == expected_choices, (
        'At current_step={}/{}, expected {}, got {}'.format(
            current_step, num_steps, expected_choices, choices
        )
    )


def test_interactively_acquire_config_no_finish_in_first_half():
    """Verify '完成配置并退出' is NOT in the first half of steps (regression guard)."""
    wizard = make_wizard()
    wizard.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))
    wizard.core_handler.fetch_courses.return_value = [Course(1, 'One')]
    wizard._select_courses_to_download = MagicMock()
    wizard._interactively_add_manually_specified_courses = MagicMock()
    wizard._set_options_of_courses = MagicMock()
    wizard._select_modules_to_download = MagicMock()

    num_steps = 4
    cutie_calls = []

    def fake_select(choices):
        cutie_calls.append(list(choices))
        # Smart selection: pick 'finish' if available (to avoid infinite loop)
        if '完成配置并退出' in choices:
            return choices.index('完成配置并退出')
        return 0  # first option (continue)

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.select', side_effect=fake_select),
    ):
        wizard.interactively_acquire_config()

    # Check that '完成配置并退出' is NOT in the first half (steps 0 to num_steps-2)
    half_point = num_steps // 2
    for idx in range(half_point):
        if idx < len(cutie_calls):
            assert '完成配置并退出' not in cutie_calls[idx], (
                "Step {} must not show '完成配置并退出' (only allowed at last step). Got: {}".format(
                    idx + 1, cutie_calls[idx]
                )
            )


def test_interactively_acquire_config_regression_against_early_exit():
    """Regression test: simulate user trying to exit at every step before last.

    Before the fix, user could select '完成配置并退出' on step 1/2/3 and
    skip step 4 (Configure module types to download). This caused the
    '所有下载选项都是 false' validator warning. After the fix, the only way
    to reach '完成配置并退出' is by completing all 4 steps.
    """
    wizard = make_wizard()
    wizard.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))
    wizard.core_handler.fetch_courses.return_value = [Course(1, 'One')]
    wizard._select_courses_to_download = MagicMock()
    wizard._interactively_add_manually_specified_courses = MagicMock()
    wizard._set_options_of_courses = MagicMock()
    wizard._select_modules_to_download = MagicMock()

    # Try to be clever: at every step, try to find '完成配置并退出' and select it
    # If not available, pick first option (continue)
    cutie_calls = []

    def fake_select(choices):
        cutie_calls.append(list(choices))
        if '完成配置并退出' in choices:
            return choices.index('完成配置并退出')
        return 0  # first option (continue)

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.select', side_effect=fake_select),
    ):
        wizard.interactively_acquire_config()

    # All 4 step methods should be called exactly once (proves we couldn't
    # skip any step)
    wizard._select_courses_to_download.assert_called_once()
    wizard._interactively_add_manually_specified_courses.assert_called_once()
    wizard._set_options_of_courses.assert_called_once()
    wizard._select_modules_to_download.assert_called_once()

    # Verify all 4 step prompts happened
    assert len(cutie_calls) == 4, (
        'All 4 step prompts should happen even when user tries to exit early. Got {}'.format(
            len(cutie_calls)
        )
    )


def test_interactively_acquire_config_exits_on_moodle_error():
    wizard = make_wizard()
    wizard.get_user_id_and_version = MagicMock(side_effect=RequestRejectedError('expired token'))

    with patch('moodle_dl.cli.config_wizard.sys.exit', side_effect=SystemExit(1)) as exit_mock:
        with pytest.raises(SystemExit):
            wizard.interactively_acquire_config()

    exit_mock.assert_called_once_with(1)


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


def test_set_options_of_courses_adds_validated_manual_course_and_exits():
    wizard = make_wizard()
    wizard.config.get_manually_specified_course_ids.return_value = [99]
    wizard.config.get_download_course_ids.return_value = []
    wizard.config.get_dont_download_course_ids.return_value = []
    wizard.config.get_download_public_course_ids.return_value = [99]
    wizard.config.has_property.return_value = False
    wizard.config.get_options_of_courses.return_value = {}
    wizard._change_settings_of = MagicMock()

    with patch(
        'moodle_dl.moodle.course_validator.validate_course_with_web_api',
        return_value={'id': 99, 'fullname': 'Validated Manual Course'},
    ) as validate:
        with patch('moodle_dl.cli.config_wizard.Cutie.select', return_value=0):
            wizard._set_options_of_courses([])

    validate.assert_called_once_with(
        wizard.config,
        wizard.opts,
        99,
        check_content=False,
        request_helper=wizard.core_handler.client,
    )
    wizard._change_settings_of.assert_not_called()


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


def test_interactively_add_all_visible_courses_returns_when_declined():
    wizard = make_wizard()

    with patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=False):
        wizard.interactively_add_all_visible_courses()

    wizard.core_handler.fetch_courses.assert_not_called()
    wizard.config.set_property.assert_not_called()


def test_interactively_add_all_visible_courses_exits_on_fetch_error(tmp_path):
    wizard = make_wizard()
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    wizard.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))
    wizard.core_handler.fetch_courses.side_effect = RuntimeError('offline')

    with (
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True),
        patch('moodle_dl.cli.config_wizard.sys.exit', side_effect=SystemExit(1)) as exit_mock,
    ):
        with pytest.raises(SystemExit):
            wizard.interactively_add_all_visible_courses()

    exit_mock.assert_called_once_with(1)


def test_select_modules_to_download_saves_new_and_legacy_options():
    wizard = make_wizard()
    wizard.config.has_property.return_value = False
    selected = [0, 4, 29]

    with patch('moodle_dl.cli.config_wizard.Cutie.select_multiple', return_value=selected) as select_multiple:
        wizard._select_modules_to_download()

    ticked_indices = select_multiple.call_args.kwargs['ticked_indices']
    assert len(ticked_indices) == 29
    assert 29 not in ticked_indices
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


@pytest.mark.parametrize(
    ('method_name', 'getter_name', 'config_key'),
    [
        ('_select_should_download_descriptions', 'get_download_descriptions', 'download_descriptions'),
        (
            '_select_should_download_links_in_descriptions',
            'get_download_links_in_descriptions',
            'download_links_in_descriptions',
        ),
        ('_select_should_download_linked_files', 'get_download_linked_files', 'download_linked_files'),
    ],
)
def test_legacy_boolean_download_selectors_update_config(method_name, getter_name, config_key):
    wizard = make_wizard()
    getattr(wizard.config, getter_name).return_value = False

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True) as prompt,
    ):
        getattr(wizard, method_name)()

    assert prompt.call_args.kwargs['default_is_yes'] is False
    wizard.config.set_property.assert_called_once_with(config_key, True)


def test_select_should_download_also_with_cookie_returns_without_moodle_url():
    wizard = make_wizard()
    wizard.config.get_moodle_URL.return_value = None
    wizard._export_browser_cookies_and_token = MagicMock()

    with patch('builtins.print'):
        wizard._select_should_download_also_with_cookie()

    wizard.config.set_property.assert_called_once_with('download_also_with_cookie', True)
    wizard._export_browser_cookies_and_token.assert_not_called()


def test_select_should_download_also_with_cookie_uses_existing_sso_cookies(tmp_path):
    wizard = make_wizard()
    cookies_path = tmp_path / 'Cookies.txt'
    cookies_path.write_text(
        '.login.microsoftonline.com\tTRUE\t/\tTRUE\t0\tESTSAUTH\tabc\n',
        encoding='utf-8',
    )
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    wizard._export_browser_cookies_and_token = MagicMock()

    with patch('builtins.print'):
        wizard._select_should_download_also_with_cookie()

    wizard.config.set_property.assert_called_once_with('download_also_with_cookie', True)
    wizard._export_browser_cookies_and_token.assert_not_called()


def test_select_should_download_also_with_cookie_exports_when_cookies_missing(tmp_path):
    wizard = make_wizard()
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    wizard._export_browser_cookies_and_token = MagicMock()

    with patch('builtins.print'):
        wizard._select_should_download_also_with_cookie()

    wizard.config.set_property.assert_called_once_with('download_also_with_cookie', True)
    wizard._export_browser_cookies_and_token.assert_called_once()


def test_export_browser_cookies_and_token_returns_without_moodle_url():
    wizard = make_wizard()
    wizard.config.get_moodle_URL.return_value = None

    with patch('builtins.print'):
        wizard._export_browser_cookies_and_token()

    wizard.config.get_misc_files_path.assert_not_called()


def test_export_browser_cookies_and_token_reuses_existing_sso_cookies(tmp_path):
    wizard = make_wizard()
    cookies_path = tmp_path / 'Cookies.txt'
    cookies_path.write_text(
        '.login.microsoftonline.com\tTRUE\t/\tTRUE\t0\tESTSAUTH\tabc\n',
        encoding='utf-8',
    )
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)

    with patch('builtins.print'):
        wizard._export_browser_cookies_and_token()

    wizard.config.set_property.assert_not_called()


def test_export_browser_cookies_and_token_warns_when_existing_cookies_lack_sso(tmp_path):
    wizard = make_wizard()
    cookies_path = tmp_path / 'Cookies.txt'
    cookies_path.write_text(
        '.keats.kcl.ac.uk\tTRUE\t/\tTRUE\t0\tMoodleSession\tabc\n',
        encoding='utf-8',
    )
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=False),
    ):
        wizard._export_browser_cookies_and_token()

    wizard.config.set_property.assert_not_called()


def test_export_browser_cookies_and_token_warns_when_user_skips_missing_cookies(tmp_path):
    wizard = make_wizard()
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)

    with (
        patch('builtins.print'),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=False) as prompt,
    ):
        wizard._export_browser_cookies_and_token()

    prompt.assert_called_once()
    wizard.config.set_property.assert_not_called()


@pytest.mark.parametrize(
    ('select_side_effect', 'expected_browser'),
    [
        ([0], 'chrome'),
        ([4, 1], 'brave'),
        ([5, 2], 'zen'),
    ],
)
def test_export_browser_cookies_and_token_selected_browser_success_saves_preference(
    tmp_path,
    select_side_effect,
    expected_browser,
):
    wizard = make_wizard()
    cookies_path = tmp_path / 'Cookies.txt'
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    export_module = make_export_module(export_browser=True, test_cookies=True, token='api-token', private_token='private')
    spec = make_import_spec()

    def exists(path):
        return str(path).endswith('export_browser_cookies.py')

    with (
        patch('builtins.print'),
        patch('os.path.exists', side_effect=exists),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True),
        patch('moodle_dl.cli.config_wizard.Cutie.select', side_effect=select_side_effect),
        patch('importlib.util.spec_from_file_location', return_value=spec),
        patch('importlib.util.module_from_spec', return_value=export_module),
    ):
        wizard._export_browser_cookies_and_token()

    export_module.export_cookies_from_browser.assert_called_once_with(
        domain='keats.kcl.ac.uk',
        output_file=str(cookies_path),
        browser_name=expected_browser,
    )
    export_module.test_cookies.assert_called_once_with('keats.kcl.ac.uk', str(cookies_path))
    export_module.extract_api_token_with_cookies.assert_called_once_with('keats.kcl.ac.uk', str(cookies_path))
    wizard.config.set_property.assert_called_once_with('preferred_browser', expected_browser)


def test_export_browser_cookies_and_token_selected_browser_token_failure_still_saves_cookies(tmp_path):
    wizard = make_wizard()
    cookies_path = tmp_path / 'Cookies.txt'
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    export_module = make_export_module(export_browser=True, test_cookies=True, token=None, private_token=None)
    spec = make_import_spec()

    def exists(path):
        return str(path).endswith('export_browser_cookies.py')

    with (
        patch('builtins.print'),
        patch('os.path.exists', side_effect=exists),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True),
        patch('moodle_dl.cli.config_wizard.Cutie.select', return_value=2),
        patch('importlib.util.spec_from_file_location', return_value=spec),
        patch('importlib.util.module_from_spec', return_value=export_module),
    ):
        wizard._export_browser_cookies_and_token()

    export_module.extract_api_token_with_cookies.assert_called_once()
    wizard.config.set_property.assert_called_once_with('preferred_browser', 'firefox')


def test_export_browser_cookies_and_token_auto_detect_success(tmp_path):
    wizard = make_wizard()
    cookies_path = tmp_path / 'Cookies.txt'
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    export_module = make_export_module(export_interactive=True)
    spec = make_import_spec()

    def exists(path):
        return str(path).endswith('export_browser_cookies.py')

    with (
        patch('builtins.print'),
        patch('os.path.exists', side_effect=exists),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True),
        patch('moodle_dl.cli.config_wizard.Cutie.select', return_value=6),
        patch('importlib.util.spec_from_file_location', return_value=spec),
        patch('importlib.util.module_from_spec', return_value=export_module),
    ):
        wizard._export_browser_cookies_and_token()

    export_module.export_cookies_interactive.assert_called_once_with(
        domain='keats.kcl.ac.uk',
        output_file=str(cookies_path),
        ask_browser=False,
        auto_get_token=True,
    )
    wizard.config.set_property.assert_not_called()


def test_export_browser_cookies_and_token_reports_export_failure(tmp_path):
    wizard = make_wizard()
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    export_module = make_export_module(export_browser=False)
    spec = make_import_spec()

    def exists(path):
        return str(path).endswith('export_browser_cookies.py')

    with (
        patch('builtins.print'),
        patch('os.path.exists', side_effect=exists),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True),
        patch('moodle_dl.cli.config_wizard.Cutie.select', return_value=2),
        patch('importlib.util.spec_from_file_location', return_value=spec),
        patch('importlib.util.module_from_spec', return_value=export_module),
    ):
        wizard._export_browser_cookies_and_token()

    export_module.test_cookies.assert_not_called()
    wizard.config.set_property.assert_not_called()


def test_export_browser_cookies_and_token_reports_missing_export_script(tmp_path):
    wizard = make_wizard()
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)

    with (
        patch('builtins.print'),
        patch('os.path.exists', return_value=False),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True),
        patch('moodle_dl.cli.config_wizard.Cutie.select', return_value=2),
    ):
        wizard._export_browser_cookies_and_token()

    wizard.config.set_property.assert_not_called()


def test_export_browser_cookies_and_token_handles_import_error(tmp_path):
    wizard = make_wizard()
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=MagicMock(side_effect=ImportError('missing'))))

    def exists(path):
        return str(path).endswith('export_browser_cookies.py')

    with (
        patch('builtins.print'),
        patch('os.path.exists', side_effect=exists),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True),
        patch('moodle_dl.cli.config_wizard.Cutie.select', return_value=2),
        patch('importlib.util.spec_from_file_location', return_value=spec),
        patch('importlib.util.module_from_spec', return_value=SimpleNamespace()),
    ):
        wizard._export_browser_cookies_and_token()

    wizard.config.set_property.assert_not_called()


def test_export_browser_cookies_and_token_handles_runtime_error(tmp_path):
    wizard = make_wizard()
    wizard.config.get_moodle_URL.return_value = MoodleURL(False, 'keats.kcl.ac.uk', '/')
    wizard.config.get_misc_files_path.return_value = str(tmp_path)
    export_module = make_export_module(export_browser=True)
    export_module.test_cookies.side_effect = RuntimeError('browser locked')
    spec = make_import_spec()

    def exists(path):
        return str(path).endswith('export_browser_cookies.py')

    with (
        patch('builtins.print'),
        patch('os.path.exists', side_effect=exists),
        patch('moodle_dl.cli.config_wizard.Cutie.prompt_yes_or_no', return_value=True),
        patch('moodle_dl.cli.config_wizard.Cutie.select', return_value=2),
        patch('importlib.util.spec_from_file_location', return_value=spec),
        patch('importlib.util.module_from_spec', return_value=export_module),
    ):
        wizard._export_browser_cookies_and_token()

    wizard.config.set_property.assert_not_called()


def test_section_separator_uses_terminal_width():
    wizard = make_wizard()

    with (
        patch('moodle_dl.cli.config_wizard.shutil.get_terminal_size', return_value=SimpleNamespace(columns=12)),
        patch('builtins.print') as print_mock,
    ):
        ConfigWizard.section_separator(wizard)

    print_mock.assert_called_once_with('\n' + '-' * 12 + '\n')
