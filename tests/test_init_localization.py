# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from moodle_dl.cli.authenticators import (
    AuthenticationError,
    BrowserSelector,
    NormalAuthenticator,
    SSOReferenceHelper,
)
from moodle_dl.cli.config_wizard import ConfigWizard
from moodle_dl.cli.localization import get_init_language, set_init_language, tr
from moodle_dl.cli.moodle_wizard import MoodleWizard
from moodle_dl.cli.notifications_wizard import NotificationsWizard
from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts, MoodleURL


@pytest.fixture(autouse=True)
def reset_init_language():
    set_init_language("zh")
    yield
    set_init_language("zh")


def make_opts(**overrides):
    values = {
        "username": None,
        "password": None,
        "token": None,
        "path": ".",
        "verbose": 0,
        "log_responses": False,
        "skip_cert_verify": False,
        "allow_insecure_ssl": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_localization_defaults_to_chinese_and_switches_to_english():
    assert get_init_language() == "zh"
    assert tr("中文", "English") == "中文"

    set_init_language("English")

    assert get_init_language() == "en"
    assert tr("中文", "English") == "English"

    set_init_language("unsupported")

    assert get_init_language() == "zh"


def test_moodle_wizard_uses_english_url_prompt_and_warning():
    set_init_language("en")
    wizard = MoodleWizard(Mock(spec=ConfigHelper), Mock(spec=MoodleDlOpts))

    with (
        patch("builtins.input", return_value="http://moodle.example.com") as input_mock,
        patch("moodle_dl.moodle.moodle_service.MoodleService.split_moodle_url", return_value=("moodle.example.com", "/")),
        patch("moodle_dl.cli.moodle_wizard.Log.warning") as warning,
    ):
        result = wizard.interactively_get_moodle_url(use_stored_url=False)

    assert result.use_http is True
    input_mock.assert_called_once_with("Moodle URL (http(s):// may be omitted):   ")
    assert "insecure URL" in warning.call_args.args[0]


def test_notifications_wizard_uses_english_service_labels_and_descriptions():
    set_init_language("en")
    config = MagicMock()
    config.has_property.return_value = False
    wizard = NotificationsWizard(config, MoodleDlOpts())

    with (
        patch("moodle_dl.cli.notifications_wizard.Cutie.select_multiple", return_value=[]) as select_multiple,
        patch("moodle_dl.cli.notifications_wizard.Log") as log,
        patch("builtins.print"),
    ):
        wizard.interactively_configure_all_services()

    options = select_multiple.call_args.kwargs["options"]
    assert options[0].startswith("Mail notifications\tSend email notifications via SMTP")
    assert options[2].startswith("Discord notifications\tSend messages to a Discord channel")
    log.blue.assert_called_once_with("Select notification services to configure:")
    log.info.assert_any_call("No notification services configured")


def test_browser_selector_uses_english_prompt_and_errors():
    set_init_language("en")
    config = Mock(spec=ConfigHelper)
    config.get_property_or.return_value = None

    with (
        patch("moodle_dl.utils.Cutie.select", return_value=0),
        patch("moodle_dl.cli.authenticators.Log.blue") as blue,
        patch("moodle_dl.cli.authenticators.Log.info") as info,
        patch("builtins.print"),
    ):
        assert BrowserSelector.select_or_load(config) == "firefox"

    blue.assert_called_once_with("Select the browser you use:")
    info.assert_called_once_with("✓ Selected: Firefox")

    with patch("moodle_dl.utils.Cutie.select", return_value=99):
        with pytest.raises(AuthenticationError, match="Invalid browser selection"):
            BrowserSelector.select_or_load(config)


def test_sso_reference_helper_uses_english_manual_token_help(capsys):
    set_init_language("en")

    with (
        patch("moodle_dl.cli.authenticators.Log.warning") as warning,
        patch("moodle_dl.cli.authenticators.Log.info") as info,
    ):
        SSOReferenceHelper.show_manual_token_help(MoodleURL(False, "moodle.example.test", "/moodle/"))

    output = capsys.readouterr().out
    warning.assert_called_once_with("In your selected browser, do the following:")
    info.assert_called_once_with("moodledl://token=$apptoken")
    assert "1. Log in to your Moodle account" in output
    assert "Copy the failed request URL" in output
    assert "moodle.example.test/moodle/admin/tool/mobile/launch.php" in output


def test_config_wizard_uses_english_module_selection_copy():
    set_init_language("en")
    wizard = ConfigWizard.__new__(ConfigWizard)
    wizard.config = MagicMock()
    wizard.config.has_property.return_value = False
    wizard.opts = MoodleDlOpts()
    wizard.section_separator = MagicMock()

    with (
        patch("moodle_dl.cli.config_wizard.Cutie.select_multiple", return_value=[]) as select_multiple,
        patch("moodle_dl.cli.config_wizard.Log") as log,
        patch("builtins.print"),
    ):
        wizard._select_modules_to_download()

    options = select_multiple.call_args.kwargs["options"]
    assert options[0].startswith("Submissions\tFiles uploaded by you or teachers")
    assert options[-1].startswith("Metadata files\tMetadata files generated for resources")
    log.blue.assert_called_once_with("Select module types to download:")
    log.success.assert_called_once_with("Selected 0/30 module types for download")


def test_normal_authenticator_uses_english_interactive_validation_errors():
    set_init_language("en")
    auth = NormalAuthenticator(Mock(spec=ConfigHelper), make_opts(), MoodleURL(False, "example.test", "/"))

    with patch("moodle_dl.cli.authenticators.input", return_value="  "):
        with pytest.raises(AuthenticationError, match="Username cannot be empty"):
            auth._get_credentials()

    with (
        patch("moodle_dl.cli.authenticators.input", return_value="student"),
        patch("moodle_dl.cli.authenticators.getpass", return_value=""),
    ):
        with pytest.raises(AuthenticationError, match="Password cannot be empty"):
            auth._get_credentials()
