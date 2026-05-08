from unittest.mock import MagicMock, patch

import pytest

from moodle_dl.cli.notifications_wizard import NotificationsWizard
from moodle_dl.types import MoodleDlOpts


def make_wizard():
    config = MagicMock()
    opts = MoodleDlOpts()
    return NotificationsWizard(config, opts), config


def test_service_descriptions_are_known_and_unknown_is_empty():
    wizard, _ = make_wizard()

    assert 'SMTP' in wizard._get_service_description('mail')
    assert 'Telegram' in wizard._get_service_description('telegram')
    assert wizard._get_service_description('unknown') == ''


def test_configure_all_services_configures_selected_and_removes_unselected():
    wizard, config = make_wizard()
    config.has_property.side_effect = lambda key: key in {'mail', 'discord'}
    selected_methods = {
        'interactively_configure_telegram',
        'interactively_configure_ntfy',
    }

    for method_name in selected_methods:
        setattr(wizard, method_name, MagicMock())

    with patch('moodle_dl.cli.notifications_wizard.Log'):
        with patch('moodle_dl.cli.notifications_wizard.Cutie.select_multiple', return_value=[1, 3]) as select:
            wizard.interactively_configure_all_services()

    assert select.call_args.kwargs['ticked_indices'] == [0, 2]
    wizard.interactively_configure_telegram.assert_called_once_with(skip_prompt=True)
    wizard.interactively_configure_ntfy.assert_called_once_with(skip_prompt=True)
    config.remove_property.assert_any_call('mail')
    config.remove_property.assert_any_call('discord')
    config.remove_property.assert_any_call('xmpp')
    config.remove_property.assert_any_call('sentry_dsn')


@pytest.mark.parametrize(
    ('method_name', 'config_key'),
    [
        ('interactively_configure_mail', 'mail'),
        ('interactively_configure_telegram', 'telegram'),
        ('interactively_configure_discord', 'discord'),
        ('interactively_configure_ntfy', 'ntfy'),
        ('interactively_configure_xmpp', 'xmpp'),
        ('interactively_configure_sentry', 'sentry_dsn'),
    ],
)
def test_configure_service_prompt_no_removes_existing_config(method_name, config_key):
    wizard, config = make_wizard()

    with patch('moodle_dl.cli.notifications_wizard.Cutie.prompt_yes_or_no', return_value=False):
        getattr(wizard, method_name)()

    config.remove_property.assert_called_once_with(config_key)
    config.set_property.assert_not_called()


def test_configure_mail_uses_default_port_and_stores_error_flag():
    wizard, config = make_wizard()
    inputs = iter(
        [
            'from@example.com',
            'smtp.example.com',
            '',
            'smtp-user',
            'to@example.com',
            '',
            'y',
        ]
    )

    with patch('builtins.input', side_effect=lambda _: next(inputs)):
        with patch('moodle_dl.cli.notifications_wizard.getpass', return_value='secret'):
            with patch('moodle_dl.cli.notifications_wizard.create_full_welcome_mail', return_value=('html', {})):
                with patch('moodle_dl.cli.notifications_wizard.MailShooter') as shooter_cls:
                    wizard.interactively_configure_mail(skip_prompt=True)

    shooter_cls.assert_called_once_with('from@example.com', 'smtp.example.com', 587, 'smtp-user', 'secret')
    shooter_cls.return_value.send.assert_called_once_with('to@example.com', 'Hey!', 'html', {})
    config.set_property.assert_called_once_with(
        'mail',
        {
            'sender': 'from@example.com',
            'server_host': 'smtp.example.com',
            'server_port': '587',
            'username': 'smtp-user',
            'password': 'secret',
            'target': 'to@example.com',
            'send_error_msg': True,
        },
    )


def test_configure_telegram_retries_failed_test_message_then_stores_config():
    wizard, config = make_wizard()
    inputs = iter(
        [
            'bad-token',
            'bad-chat',
            'good-token',
            'good-chat',
            '',
            'n',
        ]
    )

    with patch('builtins.input', side_effect=lambda _: next(inputs)):
        with patch('moodle_dl.cli.notifications_wizard.TelegramShooter') as shooter_cls:
            shooter_cls.return_value.send.side_effect = [ConnectionError('offline'), None]
            wizard.interactively_configure_telegram(skip_prompt=True)

    assert shooter_cls.call_count == 2
    config.set_property.assert_called_once_with(
        'telegram',
        {'token': 'good-token', 'chat_id': 'good-chat', 'send_error_msg': False},
    )


def test_configure_discord_splits_and_strips_webhook_urls():
    wizard, config = make_wizard()
    inputs = iter([' https://one.example/hook , https://two.example/hook ', ''])

    with patch('builtins.input', side_effect=lambda _: next(inputs)):
        with patch('moodle_dl.cli.notifications_wizard.DiscordShooter') as shooter_cls:
            wizard.interactively_configure_discord(skip_prompt=True)

    webhook_urls = ['https://one.example/hook', 'https://two.example/hook']
    shooter_cls.assert_called_once_with(webhook_urls)
    shooter_cls.return_value.send_msg.assert_called_once()
    config.set_property.assert_called_once_with('discord', {'webhook_urls': webhook_urls})


def test_configure_ntfy_stores_custom_server_when_requested():
    wizard, config = make_wizard()
    inputs = iter(['topic-name', 'https://ntfy.example', ''])

    with patch('builtins.input', side_effect=lambda _: next(inputs)):
        with patch('moodle_dl.cli.notifications_wizard.Cutie.prompt_yes_or_no', return_value=True):
            with patch('moodle_dl.cli.notifications_wizard.NtfyShooter') as shooter_cls:
                wizard.interactively_configure_ntfy(skip_prompt=True)

    shooter_cls.assert_called_once_with(topic='topic-name', server='https://ntfy.example')
    shooter_cls.return_value.send.assert_called_once()
    config.set_property.assert_called_once_with(
        'ntfy',
        {'topic': 'topic-name', 'server': 'https://ntfy.example'},
    )


def test_configure_xmpp_stores_sender_target_and_error_flag():
    wizard, config = make_wizard()
    inputs = iter(['bot@example.com/resource', 'user@example.com', '', 'y'])

    with patch('builtins.input', side_effect=lambda _: next(inputs)):
        with patch('moodle_dl.cli.notifications_wizard.getpass', return_value='secret'):
            with patch('moodle_dl.cli.notifications_wizard.XmppShooter') as shooter_cls:
                wizard.interactively_configure_xmpp(skip_prompt=True)

    shooter_cls.assert_called_once_with('bot@example.com/resource', 'secret', 'user@example.com')
    shooter_cls.return_value.send.assert_called_once()
    config.set_property.assert_called_once_with(
        'xmpp',
        {
            'sender': 'bot@example.com/resource',
            'password': 'secret',
            'target': 'user@example.com',
            'send_error_msg': True,
        },
    )


def test_configure_sentry_sets_dsn_or_warns_when_empty():
    wizard, config = make_wizard()

    with patch('builtins.input', return_value='https://sentry.example/123'):
        with patch('moodle_dl.cli.notifications_wizard.Log') as log:
            wizard.interactively_configure_sentry(skip_prompt=True)

    config.set_property.assert_called_once_with('sentry_dsn', 'https://sentry.example/123')
    log.success.assert_called_once()

    wizard, config = make_wizard()
    with patch('builtins.input', return_value=''):
        with patch('moodle_dl.cli.notifications_wizard.Log') as log:
            wizard.interactively_configure_sentry(skip_prompt=True)

    config.set_property.assert_not_called()
    log.warning.assert_called_once()
