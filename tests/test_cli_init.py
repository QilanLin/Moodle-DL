# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import moodle_dl.cli as cli
from moodle_dl.cli.localization import set_init_language


@pytest.fixture(autouse=True)
def reset_init_language():
    set_init_language("zh")
    yield
    set_init_language("zh")


def make_config(is_present=False):
    config = MagicMock()
    config.is_present.return_value = is_present
    return config


def make_opts(path="/tmp/moodle-dl"):
    return SimpleNamespace(path=path)


def test_init_config_exits_when_existing_config_is_not_overridden():
    config = make_config(is_present=True)
    opts = make_opts()

    with (
        patch("moodle_dl.cli.Cutie.select", return_value=0),
        patch("moodle_dl.cli.Cutie.prompt_yes_or_no", return_value=False) as prompt,
        patch("moodle_dl.cli.Log.error_str", return_value="overwrite?") as error_str,
        patch("moodle_dl.cli.NotificationsWizard") as notifications_wizard,
        patch("moodle_dl.cli.MoodleWizard") as moodle_wizard,
        patch("moodle_dl.cli.ConfigWizard") as config_wizard,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.init_config(config, opts)

    assert exc_info.value.code == 0
    error_str.assert_called_once_with("你想要覆盖现有的配置吗？")
    prompt.assert_called_once_with("overwrite?")
    notifications_wizard.assert_not_called()
    moodle_wizard.assert_not_called()
    config_wizard.assert_not_called()


def test_init_config_runs_all_wizards_and_prints_unix_periodic_help():
    config = make_config(is_present=False)
    opts = make_opts(path="/workspace/course")

    with (
        patch("moodle_dl.cli.Cutie.select", return_value=0),
        patch("moodle_dl.cli.NotificationsWizard") as notifications_wizard,
        patch("moodle_dl.cli.MoodleWizard") as moodle_wizard,
        patch("moodle_dl.cli.ConfigWizard") as config_wizard,
        patch("moodle_dl.cli.Log") as log,
        patch("moodle_dl.cli.os.path.abspath", side_effect=lambda value: f"/abs/{value}"),
        patch("moodle_dl.cli.sys.argv", ["moodle-dl"]),
        patch("builtins.print") as print_mock,
    ):
        config_wizard.get_config_steps_count.return_value = 3

        cli.init_config(config, opts)

    notifications_wizard.assert_called_once_with(config, opts)
    notifications_wizard.return_value.interactively_configure_all_services.assert_called_once_with()
    moodle_wizard.assert_called_once_with(config, opts)
    moodle_wizard.return_value.interactively_acquire_token.assert_called_once_with()
    config_wizard.assert_called_once_with(config, opts)
    config_wizard.get_config_steps_count.assert_called_once_with()
    config_wizard.return_value.interactively_acquire_config.assert_called_once_with()

    log.success.assert_has_calls(
        [
            call("配置已完成并保存！"),
            call("一切就绪，可以开始了！"),
        ]
    )
    log.info.assert_any_call("开始额外配置向导（3个配置步骤）...")
    periodic_messages = [args[0] for args, _ in log.info.call_args_list]
    assert any("crontab -e" in message for message in periodic_messages)
    assert any('cd "/abs//workspace/course"' in message for message in periodic_messages)
    assert any('"/abs/moodle-dl"' in message for message in periodic_messages)
    assert print_mock.call_count == 5


def test_init_config_logs_windows_periodic_help():
    config = make_config(is_present=False)
    opts = make_opts()

    with (
        patch("moodle_dl.cli.Cutie.select", return_value=0),
        patch("moodle_dl.cli.NotificationsWizard"),
        patch("moodle_dl.cli.MoodleWizard"),
        patch("moodle_dl.cli.ConfigWizard") as config_wizard,
        patch("moodle_dl.cli.Log") as log,
        patch("moodle_dl.cli.os.name", "nt"),
        patch("builtins.print"),
    ):
        config_wizard.get_config_steps_count.return_value = 1

        cli.init_config(config, opts)

    info_messages = [args[0] for args, _ in log.info.call_args_list]
    assert any("如果你想定期运行 moodle-dl" in message for message in info_messages)
    assert not any("crontab -e" in message for message in info_messages)


def test_init_config_uses_english_language_selection_for_init_prompts():
    config = make_config(is_present=True)
    opts = make_opts()

    with (
        patch("moodle_dl.cli.Cutie.select", return_value=1),
        patch("moodle_dl.cli.Cutie.prompt_yes_or_no", return_value=False) as prompt,
        patch("moodle_dl.cli.Log.error_str", return_value="overwrite?") as error_str,
        patch("moodle_dl.cli.NotificationsWizard") as notifications_wizard,
        patch("moodle_dl.cli.MoodleWizard") as moodle_wizard,
        patch("moodle_dl.cli.ConfigWizard") as config_wizard,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.init_config(config, opts)

    assert exc_info.value.code == 0
    error_str.assert_called_once_with("Do you want to overwrite the existing configuration?")
    prompt.assert_called_once_with("overwrite?")
    notifications_wizard.assert_not_called()
    moodle_wizard.assert_not_called()
    config_wizard.assert_not_called()


def test_cli_public_exports_include_wizard_classes():
    assert cli.__all__ == ["ConfigWizard", "DatabaseManager", "NotificationsWizard"]
