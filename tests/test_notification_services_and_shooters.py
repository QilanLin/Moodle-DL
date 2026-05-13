# -*- coding: utf-8 -*-
import json
import unittest
from unittest.mock import MagicMock, mock_open, patch

from requests.exceptions import RequestException

from moodle_dl.notifications.discord.discord_shooter import DiscordShooter
from moodle_dl.notifications.discord.discord_service import DiscordService
from moodle_dl.notifications.mail.mail_shooter import MailShooter
from moodle_dl.notifications.mail.mail_service import MailService
from moodle_dl.notifications import get_all_notify_services, get_remote_notify_services
from moodle_dl.notifications import REMOTE_SERVICES
from moodle_dl.notifications.console.console_service import ConsoleService
from moodle_dl.notifications.notification_service import NotificationService
from moodle_dl.notifications.ntfy.ntfy_shooter import NtfyShooter
from moodle_dl.notifications.ntfy.ntfy_service import NtfyService
from moodle_dl.notifications.telegram.telegram_shooter import RequestRejectedError, TelegramShooter
from moodle_dl.notifications.telegram.telegram_service import TelegramService
from moodle_dl.notifications.xmpp.xmpp_formatter import XmppFormatter
from moodle_dl.notifications.xmpp.xmpp_shooter import XmppShooter
from moodle_dl.notifications.xmpp.xmpp_service import XmppService
from moodle_dl.types import Course, MoodleURL


def configured_config(key, value):
    config = MagicMock()

    def get_property(requested_key):
        if requested_key != key:
            raise ValueError(requested_key)
        return value

    config.get_property.side_effect = get_property
    config.get_moodle_URL.return_value = MoodleURL(False, 'moodle.example.com', '/')
    return config


def unconfigured_config():
    config = MagicMock()
    config.get_property.side_effect = ValueError('missing')
    return config


class TestDiscordService(unittest.TestCase):
    def test_notify_changes_skips_when_not_configured(self):
        service = DiscordService(unconfigured_config())

        with patch('moodle_dl.notifications.discord.discord_service.DF.create_full_moodle_diff_messages') as formatter:
            service.notify_about_changes_in_moodle([Course(1, 'Course')])

        formatter.assert_not_called()

    def test_send_embeds_skips_when_not_configured(self):
        service = DiscordService(unconfigured_config())

        with patch('moodle_dl.notifications.discord.discord_service.DiscordShooter') as shooter_cls:
            service._send_embeds([{'title': 'Change'}])

        shooter_cls.assert_not_called()

    def test_send_embeds_uses_configured_webhooks(self):
        config = configured_config('discord', {'webhook_urls': ['https://discord.example/hook']})
        service = DiscordService(config)

        with patch('moodle_dl.notifications.discord.discord_service.DiscordShooter') as shooter_cls:
            service._send_embeds([{'title': 'Change'}])

        shooter_cls.assert_called_once_with(['https://discord.example/hook'])
        shooter_cls.return_value.send.assert_called_once_with([{'title': 'Change'}])

    def test_send_embeds_rethrows_sender_errors(self):
        config = configured_config('discord', {'webhook_urls': ['https://discord.example/hook']})
        service = DiscordService(config)

        with patch('moodle_dl.notifications.discord.discord_service.DiscordShooter') as shooter_cls:
            shooter_cls.return_value.send.side_effect = RuntimeError('webhook failed')
            with self.assertRaisesRegex(RuntimeError, 'webhook failed'):
                service._send_embeds([{'title': 'Change'}])

    def test_notify_changes_formats_and_sends(self):
        service = DiscordService(configured_config('discord', {'webhook_urls': ['https://discord.example/hook']}))
        changes = [Course(1, 'Course')]

        with patch(
            'moodle_dl.notifications.discord.discord_service.DF.create_full_moodle_diff_messages',
            return_value=[{'title': 'Change'}],
        ) as formatter:
            with patch.object(service, '_send_embeds') as send_embeds:
                service.notify_about_changes_in_moodle(changes)

        formatter.assert_called_once_with(changes, 'https://moodle.example.com/')
        send_embeds.assert_called_once_with([{'title': 'Change'}])

    def test_error_and_failed_download_notifications_are_noops(self):
        service = DiscordService(configured_config('discord', {'webhook_urls': ['https://discord.example/hook']}))

        self.assertIsNone(service.notify_about_error('traceback'))
        self.assertIsNone(service.notify_about_failed_downloads([MagicMock()]))


class TestTelegramService(unittest.TestCase):
    def test_is_configured_returns_false_when_telegram_config_is_missing(self):
        service = TelegramService(unconfigured_config())

        self.assertFalse(service._is_configured())

    def test_send_messages_uses_configured_bot(self):
        config = configured_config('telegram', {'token': 'token', 'chat_id': 'chat'})
        service = TelegramService(config)

        with patch('moodle_dl.notifications.telegram.telegram_service.TelegramShooter') as shooter_cls:
            service._send_messages(['first', 'second'])

        shooter_cls.assert_called_once_with('token', 'chat')
        shooter_cls.return_value.send.assert_any_call('first')
        shooter_cls.return_value.send.assert_any_call('second')

    def test_send_messages_skips_empty_inputs(self):
        service = TelegramService(configured_config('telegram', {'token': 'token', 'chat_id': 'chat'}))

        with patch('moodle_dl.notifications.telegram.telegram_service.TelegramShooter') as shooter_cls:
            service._send_messages([])
            service._send_messages(None)

        shooter_cls.assert_not_called()

    def test_send_messages_rethrows_sender_errors(self):
        config = configured_config('telegram', {'token': 'token', 'chat_id': 'chat'})
        service = TelegramService(config)

        with patch('moodle_dl.notifications.telegram.telegram_service.TelegramShooter') as shooter_cls:
            shooter_cls.return_value.send.side_effect = RuntimeError('telegram failed')
            with self.assertRaisesRegex(RuntimeError, 'telegram failed'):
                service._send_messages(['message'])

    def test_notify_changes_skips_when_not_configured(self):
        service = TelegramService(unconfigured_config())

        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TF.create_full_moodle_diff_messages'
        ) as formatter:
            service.notify_about_changes_in_moodle([Course(1, 'Course')])

        formatter.assert_not_called()

    def test_notify_changes_formats_and_sends(self):
        service = TelegramService(configured_config('telegram', {'token': 'token', 'chat_id': 'chat'}))
        changes = [Course(1, 'Course')]

        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TF.create_full_moodle_diff_messages',
            return_value=['change'],
        ) as formatter:
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_changes_in_moodle(changes)

        formatter.assert_called_once_with(changes)
        send_messages.assert_called_once_with(['change'])

    def test_notify_error_skips_when_not_configured(self):
        service = TelegramService(unconfigured_config())

        with patch('moodle_dl.notifications.telegram.telegram_service.TF.create_full_error_messages') as formatter:
            service.notify_about_error('traceback')

        formatter.assert_not_called()

    def test_notify_error_respects_send_error_flag(self):
        service = TelegramService(
            configured_config('telegram', {'token': 'token', 'chat_id': 'chat', 'send_error_msg': False})
        )

        with patch.object(service, '_send_messages') as send_messages:
            service.notify_about_error('traceback')

        send_messages.assert_not_called()

    def test_notify_error_formats_and_sends_when_enabled(self):
        service = TelegramService(
            configured_config('telegram', {'token': 'token', 'chat_id': 'chat', 'send_error_msg': True})
        )

        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TF.create_full_error_messages',
            return_value=['error'],
        ) as formatter:
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_error('traceback')

        formatter.assert_called_once_with('traceback')
        send_messages.assert_called_once_with(['error'])

    def test_notify_failed_downloads_formats_and_sends_when_enabled(self):
        service = TelegramService(
            configured_config('telegram', {'token': 'token', 'chat_id': 'chat', 'send_error_msg': True})
        )
        failed_downloads = [MagicMock()]

        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TF.create_full_failed_downloads_messages',
            return_value=['failed'],
        ) as formatter:
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_failed_downloads(failed_downloads)

        formatter.assert_called_once_with(failed_downloads)
        send_messages.assert_called_once_with(['failed'])

    def test_notify_failed_downloads_skips_when_not_configured_or_disabled(self):
        failed_downloads = [MagicMock()]

        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TF.create_full_failed_downloads_messages'
        ) as formatter:
            TelegramService(unconfigured_config()).notify_about_failed_downloads(failed_downloads)

        formatter.assert_not_called()

        service = TelegramService(
            configured_config('telegram', {'token': 'token', 'chat_id': 'chat', 'send_error_msg': False})
        )

        with patch.object(service, '_send_messages') as send_messages:
            service.notify_about_failed_downloads(failed_downloads)

        send_messages.assert_not_called()


class TestNtfyService(unittest.TestCase):
    def test_send_messages_posts_each_message(self):
        config = configured_config('ntfy', {'topic': 'moodle', 'server': 'https://ntfy.example'})
        service = NtfyService(config)
        messages = [
            {'title': 'One', 'message': 'First', 'source_url': None},
            {'title': 'Two', 'message': 'Second', 'source_url': 'https://moodle.example/course'},
        ]

        with patch('moodle_dl.notifications.ntfy.ntfy_service.NtfyShooter') as shooter_cls:
            service._send_messages(messages)

        shooter_cls.assert_called_once_with('moodle', 'https://ntfy.example')
        shooter_cls.return_value.send.assert_any_call(title='One', message='First', source_url=None)
        shooter_cls.return_value.send.assert_any_call(
            title='Two',
            message='Second',
            source_url='https://moodle.example/course',
        )

    def test_send_messages_skips_when_unconfigured_or_empty(self):
        service = NtfyService(unconfigured_config())

        with patch('moodle_dl.notifications.ntfy.ntfy_service.NtfyShooter') as shooter_cls:
            service._send_messages([{'title': 'unused', 'message': 'unused'}])
            service._send_messages([])

        shooter_cls.assert_not_called()

    def test_send_messages_rethrows_sender_errors(self):
        config = configured_config('ntfy', {'topic': 'moodle', 'server': 'https://ntfy.example'})
        service = NtfyService(config)

        with patch('moodle_dl.notifications.ntfy.ntfy_service.NtfyShooter') as shooter_cls:
            shooter_cls.return_value.send.side_effect = RuntimeError('ntfy failed')
            with self.assertRaisesRegex(RuntimeError, 'ntfy failed'):
                service._send_messages([{'title': 'Change', 'message': 'Body'}])

    def test_notify_changes_skips_when_not_configured(self):
        service = NtfyService(unconfigured_config())

        with patch('moodle_dl.notifications.ntfy.ntfy_service.NF.create_full_moodle_diff_messages') as formatter:
            service.notify_about_changes_in_moodle([Course(1, 'Course')])

        formatter.assert_not_called()

    def test_notify_changes_formats_and_sends(self):
        service = NtfyService(configured_config('ntfy', {'topic': 'moodle'}))
        changes = [Course(1, 'Course')]

        with patch(
            'moodle_dl.notifications.ntfy.ntfy_service.NF.create_full_moodle_diff_messages',
            return_value=[{'title': 'Change', 'message': 'Body'}],
        ) as formatter:
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_changes_in_moodle(changes)

        formatter.assert_called_once_with(changes)
        send_messages.assert_called_once_with([{'title': 'Change', 'message': 'Body'}])

    def test_error_and_failed_download_notifications_are_noops(self):
        service = NtfyService(configured_config('ntfy', {'topic': 'moodle'}))

        self.assertIsNone(service.notify_about_error('traceback'))
        self.assertIsNone(service.notify_about_failed_downloads([MagicMock()]))


class TestMailService(unittest.TestCase):
    MAIL_CONFIG = {
        'sender': 'from@example.com',
        'target': 'to@example.com',
        'server_host': 'smtp.example.com',
        'server_port': '587',
        'username': 'user',
        'password': 'password',
    }

    def test_is_configured_returns_false_when_mail_config_is_missing(self):
        service = MailService(unconfigured_config())

        self.assertFalse(service._is_configured())

    def test_send_mail_skips_when_not_configured(self):
        service = MailService(unconfigured_config())

        with patch('moodle_dl.notifications.mail.mail_service.MailShooter') as shooter_cls:
            service._send_mail('Subject', ('<p>HTML</p>', {}))

        shooter_cls.assert_not_called()

    def test_send_mail_uses_configured_smtp(self):
        service = MailService(configured_config('mail', self.MAIL_CONFIG))

        with patch('moodle_dl.notifications.mail.mail_service.MailShooter') as shooter_cls:
            service._send_mail('Subject', ('<p>HTML</p>', {'plain': 'text'}))

        shooter_cls.assert_called_once_with('from@example.com', 'smtp.example.com', 587, 'user', 'password')
        shooter_cls.return_value.send.assert_called_once_with(
            'to@example.com',
            'Subject',
            '<p>HTML</p>',
            {'plain': 'text'},
        )

    def test_send_mail_rethrows_sender_errors(self):
        service = MailService(configured_config('mail', self.MAIL_CONFIG))

        with patch('moodle_dl.notifications.mail.mail_service.MailShooter') as shooter_cls:
            shooter_cls.return_value.send.side_effect = RuntimeError('smtp failed')
            with self.assertRaisesRegex(RuntimeError, 'smtp failed'):
                service._send_mail('Subject', ('<p>HTML</p>', {}))

    def test_notify_changes_skips_when_not_configured(self):
        service = MailService(unconfigured_config())

        with patch('moodle_dl.notifications.mail.mail_service.create_full_moodle_diff_mail') as formatter:
            service.notify_about_changes_in_moodle([Course(1, 'Course')])

        formatter.assert_not_called()

    def test_notify_changes_counts_changed_files_in_subject(self):
        service = MailService(configured_config('mail', self.MAIL_CONFIG))
        course = Course(1, 'Course', files=[MagicMock(), MagicMock()])

        with patch('moodle_dl.notifications.mail.mail_service.create_full_moodle_diff_mail', return_value=('html', {})):
            with patch.object(service, '_send_mail') as send_mail:
                service.notify_about_changes_in_moodle([course])

        send_mail.assert_called_once_with('2 new Changes in the Moodle courses!', ('html', {}))

    def test_notify_error_respects_send_error_flag(self):
        mail_cfg = dict(self.MAIL_CONFIG, send_error_msg=False)
        service = MailService(configured_config('mail', mail_cfg))

        with patch.object(service, '_send_mail') as send_mail:
            service.notify_about_error('traceback')

        send_mail.assert_not_called()

    def test_notify_error_skips_when_not_configured(self):
        service = MailService(unconfigured_config())

        with patch('moodle_dl.notifications.mail.mail_service.create_full_error_mail') as formatter:
            service.notify_about_error('traceback')

        formatter.assert_not_called()

    def test_notify_error_formats_and_sends_when_enabled(self):
        service = MailService(configured_config('mail', dict(self.MAIL_CONFIG, send_error_msg=True)))

        with patch(
            'moodle_dl.notifications.mail.mail_service.create_full_error_mail',
            return_value=('html', {}),
        ) as formatter:
            with patch.object(service, '_send_mail') as send_mail:
                service.notify_about_error('traceback')

        formatter.assert_called_once_with('traceback')
        send_mail.assert_called_once_with('Error!', ('html', {}))

    def test_notify_failed_downloads_formats_and_sends_when_enabled(self):
        service = MailService(configured_config('mail', dict(self.MAIL_CONFIG, send_error_msg=True)))
        failed_downloads = [MagicMock()]

        with patch(
            'moodle_dl.notifications.mail.mail_service.create_full_failed_downloads_mail',
            return_value=('html', {}),
        ) as formatter:
            with patch.object(service, '_send_mail') as send_mail:
                service.notify_about_failed_downloads(failed_downloads)

        formatter.assert_called_once_with(failed_downloads)
        send_mail.assert_called_once_with('Faild to download files!', ('html', {}))

    def test_notify_failed_downloads_respects_send_error_flag(self):
        service = MailService(configured_config('mail', dict(self.MAIL_CONFIG, send_error_msg=False)))

        with patch.object(service, '_send_mail') as send_mail:
            service.notify_about_failed_downloads([MagicMock()])

        send_mail.assert_not_called()

    def test_notify_failed_downloads_skips_when_not_configured(self):
        service = MailService(unconfigured_config())

        with patch('moodle_dl.notifications.mail.mail_service.create_full_failed_downloads_mail') as formatter:
            service.notify_about_failed_downloads([MagicMock()])

        formatter.assert_not_called()


class TestXmppService(unittest.TestCase):
    XMPP_CONFIG = {'sender': 'bot@example.com/resource', 'password': 'secret', 'target': 'user@example.com'}

    def test_is_configured_returns_false_when_xmpp_config_is_missing(self):
        service = XmppService(unconfigured_config())

        self.assertFalse(service._is_configured())

    def test_send_messages_uses_configured_account(self):
        service = XmppService(configured_config('xmpp', self.XMPP_CONFIG))

        with patch('moodle_dl.notifications.xmpp.xmpp_service.XmppShooter') as shooter_cls:
            service._send_messages(['first', 'second'])

        shooter_cls.assert_called_once_with('bot@example.com/resource', 'secret', 'user@example.com')
        shooter_cls.return_value.send.assert_any_call('first')
        shooter_cls.return_value.send.assert_any_call('second')

    def test_send_messages_skips_empty_and_unconfigured_inputs(self):
        service = XmppService(unconfigured_config())

        with patch('moodle_dl.notifications.xmpp.xmpp_service.XmppShooter') as shooter_cls:
            service._send_messages(['unused'])
            service._send_messages([])
            service._send_messages(None)

        shooter_cls.assert_not_called()

    def test_send_messages_rethrows_sender_errors(self):
        service = XmppService(configured_config('xmpp', self.XMPP_CONFIG))

        with patch('moodle_dl.notifications.xmpp.xmpp_service.XmppShooter') as shooter_cls:
            shooter_cls.return_value.send.side_effect = RuntimeError('xmpp failed')
            with self.assertRaisesRegex(RuntimeError, 'xmpp failed'):
                service._send_messages(['message'])

    def test_notify_changes_skips_when_not_configured(self):
        service = XmppService(unconfigured_config())

        with patch('moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_moodle_diff_messages') as formatter:
            service.notify_about_changes_in_moodle([Course(1, 'Course')])

        formatter.assert_not_called()

    def test_notify_changes_formats_and_sends(self):
        service = XmppService(configured_config('xmpp', self.XMPP_CONFIG))
        changes = [Course(1, 'Course')]

        with patch(
            'moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_moodle_diff_messages',
            return_value=['change'],
        ) as formatter:
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_changes_in_moodle(changes)

        formatter.assert_called_once_with(changes)
        send_messages.assert_called_once_with(['change'])

    def test_notify_error_skips_when_not_configured(self):
        service = XmppService(unconfigured_config())

        with patch('moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_error_messages') as formatter:
            service.notify_about_error('traceback')

        formatter.assert_not_called()

    def test_notify_error_and_failed_downloads_respect_send_error_flag(self):
        service = XmppService(configured_config('xmpp', dict(self.XMPP_CONFIG, send_error_msg=False)))

        with patch.object(service, '_send_messages') as send_messages:
            service.notify_about_error('traceback')
            service.notify_about_failed_downloads([MagicMock()])

        send_messages.assert_not_called()

    def test_notify_error_formats_when_enabled(self):
        service = XmppService(configured_config('xmpp', dict(self.XMPP_CONFIG, send_error_msg=True)))

        with patch(
            'moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_error_messages',
            return_value=['error'],
        ) as formatter:
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_error('traceback')

        formatter.assert_called_once_with('traceback')
        send_messages.assert_called_once_with(['error'])

    def test_notify_failed_downloads_skips_when_not_configured(self):
        service = XmppService(unconfigured_config())

        with patch('moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_failed_downloads_messages') as formatter:
            service.notify_about_failed_downloads([MagicMock()])

        formatter.assert_not_called()

    def test_notify_failed_downloads_formats_when_enabled(self):
        service = XmppService(configured_config('xmpp', dict(self.XMPP_CONFIG, send_error_msg=True)))
        failed_downloads = [MagicMock()]

        with patch(
            'moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_failed_downloads_messages',
            return_value=['failed'],
        ) as formatter:
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_failed_downloads(failed_downloads)

        formatter.assert_called_once_with(failed_downloads)
        send_messages.assert_called_once_with(['failed'])


class ConcreteNotificationService(NotificationService):
    def notify_about_changes_in_moodle(self, changes):
        return super().notify_about_changes_in_moodle(changes)

    def notify_about_error(self, error_description):
        return super().notify_about_error(error_description)

    def notify_about_failed_downloads(self, failed_downloads):
        return super().notify_about_failed_downloads(failed_downloads)


class TestNotificationServiceBase(unittest.TestCase):
    def test_base_methods_are_noops_and_store_config(self):
        config = MagicMock()
        service = ConcreteNotificationService(config)

        self.assertIs(service.config, config)
        self.assertIsNone(service.notify_about_changes_in_moodle([]))
        self.assertIsNone(service.notify_about_error('traceback'))
        self.assertIsNone(service.notify_about_failed_downloads([]))


class TestShooters(unittest.TestCase):
    def test_discord_response_code_validation(self):
        for status_code in (200, 204, 400):
            response = MagicMock(status_code=status_code)
            DiscordShooter._check_response_code(response)

        response = MagicMock(status_code=500, headers={'x': 'y'}, text='server error')
        with self.assertRaisesRegex(RuntimeError, 'Status code: 500'):
            DiscordShooter._check_response_code(response)

    def test_discord_send_msg_and_embeds_wrap_payloads(self):
        shooter = DiscordShooter(['https://discord.example/hook'])

        with patch.object(shooter, 'send_data') as send_data:
            shooter.send_msg('hello')
            shooter.send([{'title': 'Change'}])

        self.assertEqual(send_data.call_args_list[0].args[0]['content'], 'hello')
        self.assertEqual(send_data.call_args_list[0].args[0]['username'], 'Moodle Notifications')
        self.assertEqual(send_data.call_args_list[1].args[0]['embeds'], [{'title': 'Change'}])
        self.assertEqual(send_data.call_args_list[1].args[0]['username'], 'Moodle Notifications')

    def test_discord_send_data_posts_to_each_webhook(self):
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=204)

        with patch(
            'moodle_dl.notifications.discord.discord_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            DiscordShooter(['https://discord.example/one', 'https://discord.example/two']).send_data({'content': 'hi'})

        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(session.post.call_args_list[0].args[0], 'https://discord.example/one')
        self.assertEqual(session.post.call_args_list[1].args[0], 'https://discord.example/two')

    def test_discord_send_data_wraps_request_errors(self):
        session = MagicMock()
        session.post.side_effect = RequestException('dns failed')

        with patch(
            'moodle_dl.notifications.discord.discord_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            with self.assertRaisesRegex(ConnectionError, 'dns failed'):
                DiscordShooter(['https://discord.example/hook']).send_data({'content': 'hello'})

    def test_telegram_response_and_json_errors(self):
        shooter = TelegramShooter('token', 'chat')

        response = MagicMock(status_code=200)
        response.json.return_value = {'ok': True}
        self.assertEqual(shooter._check_errors(response), {'ok': True})

        response.json.return_value = {'ok': False, 'description': 'bad chat'}
        with self.assertRaisesRegex(RequestRejectedError, 'bad chat'):
            shooter._check_errors(response)

        invalid_status = MagicMock(status_code=500, headers={}, text='broken')
        with self.assertRaisesRegex(RuntimeError, 'Status-Code: 500'):
            TelegramShooter._check_response_code(invalid_status)

        invalid_json = MagicMock(status_code=200)
        invalid_json.json.side_effect = ValueError('not json')
        invalid_json.read.return_value = b'not-json'
        with self.assertRaisesRegex(RuntimeError, 'parse the json response'):
            TelegramShooter('token', 'chat')._check_errors(invalid_json)

    def test_telegram_send_posts_encoded_payload_and_checks_response(self):
        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {'ok': True}
        session.post.return_value = response

        with patch(
            'moodle_dl.notifications.telegram.telegram_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            TelegramShooter('token', 'chat id').send('hello world')

        session.post.assert_called_once()
        self.assertEqual(session.post.call_args.args[0], 'https://api.telegram.org/bottoken/sendMessage')
        data = session.post.call_args.kwargs['data']
        self.assertIn('chat_id=chat+id', data)
        self.assertIn('text=hello+world', data)
        self.assertIn('parse_mode=HTML', data)

    def test_telegram_send_wraps_request_errors(self):
        session = MagicMock()
        session.post.side_effect = RequestException('timeout')

        with patch(
            'moodle_dl.notifications.telegram.telegram_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            with self.assertRaisesRegex(ConnectionError, 'timeout'):
                TelegramShooter('token', 'chat').send('hello')

    def test_ntfy_send_posts_json_payload_with_optional_action(self):
        response = MagicMock()
        with patch('moodle_dl.notifications.ntfy.ntfy_shooter.requests.post', return_value=response) as post:
            NtfyShooter('topic', 'https://ntfy.example').send(
                title='Change',
                message='Body',
                source_url='https://moodle.example/course',
            )

        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], 'https://ntfy.example')
        payload = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(payload['topic'], 'topic')
        self.assertEqual(payload['click'], 'https://moodle.example/course')
        self.assertEqual(payload['actions'][0]['url'], 'https://moodle.example/course')
        response.raise_for_status.assert_called_once()

    def test_xmpp_shooter_connects_authenticates_and_sends_message(self):
        sender_jid = MagicMock()
        sender_jid.getDomain.return_value = 'example.com'
        sender_jid.getNode.return_value = 'bot'
        sender_jid.getResource.return_value = 'resource'
        recipient_jid = MagicMock()
        connection = MagicMock()
        connection.Bind.session = 1
        message = MagicMock()

        with patch('moodle_dl.notifications.xmpp.xmpp_shooter.check_verbose', return_value=True):
            with patch('moodle_dl.notifications.xmpp.xmpp_shooter.xmpp') as xmpp_module:
                xmpp_module.protocol.JID.side_effect = [sender_jid, recipient_jid]
                xmpp_module.Client.return_value = connection
                xmpp_module.protocol.Message.return_value = message

                shooter = XmppShooter('bot@example.com/resource', 'secret', 'user@example.com')
                shooter.send('hello')

        xmpp_module.Client.assert_called_once_with(server='example.com', debug=True)
        connection.connect.assert_called_once_with()
        connection.auth.assert_called_once_with(user='bot', password='secret', resource='resource')
        xmpp_module.protocol.Message.assert_called_once_with(to=recipient_jid, body='hello')
        connection.send.assert_called_once_with(message)
        self.assertTrue(shooter.is_connected)

    def test_xmpp_shooter_reuses_existing_connection(self):
        connection = MagicMock()
        connection.Bind.session = 1

        with patch('moodle_dl.notifications.xmpp.xmpp_shooter.xmpp') as xmpp_module:
            xmpp_module.protocol.JID.return_value = MagicMock()
            xmpp_module.Client.return_value = connection
            shooter = XmppShooter('bot@example.com/resource', 'secret', 'user@example.com')
            shooter.send('first')
            shooter.send('second')

        connection.connect.assert_called_once_with()
        self.assertEqual(connection.send.call_count, 2)

    def test_xmpp_shooter_raises_when_session_is_not_bound(self):
        connection = MagicMock()
        connection.Bind.session = 0

        with patch('moodle_dl.notifications.xmpp.xmpp_shooter.xmpp') as xmpp_module:
            xmpp_module.protocol.JID.return_value = MagicMock()
            xmpp_module.Client.return_value = connection
            shooter = XmppShooter('bot@example.com/resource', 'secret', 'user@example.com')

            with self.assertRaisesRegex(ConnectionError, 'Session could not be opend'):
                shooter.send('hello')

        connection.send.assert_not_called()

    def test_xmpp_formatter_uses_plain_text_bold_and_message_limit(self):
        messages = []

        content = XmppFormatter.append_with_limit('abcdef', '12345', messages, limit=10)

        self.assertEqual(messages, ['12345'])
        self.assertEqual(content, 'abcdef')
        self.assertEqual(XmppFormatter.make_bold('Course'), '*Course*')

    def test_mail_shooter_builds_message_and_sends_via_smtp(self):
        shooter = MailShooter('from@example.com', 'smtp.example.com', 587, 'user', 'password')

        with patch('builtins.open', mock_open(read_data=b'png')):
            with patch('moodle_dl.notifications.mail.mail_shooter.smtplib.SMTP') as smtp_cls:
                shooter.send(
                    target='to@example.com',
                    subject='Subject',
                    html_content_with_cids='<p>Hello</p>',
                    inline_png_cids_filenames={'<cid@example>': 'header.png'},
                )

        smtp_cls.assert_called_once_with('smtp.example.com', 587)
        smtp_connection = smtp_cls.return_value.__enter__.return_value
        smtp_connection.starttls.assert_called_once()
        smtp_connection.login.assert_called_once_with('user', 'password')
        sent_message = smtp_connection.send_message.call_args.args[0]
        self.assertEqual(sent_message['Subject'], 'Subject')
        self.assertEqual(sent_message['From'], 'from@example.com')
        self.assertEqual(sent_message['To'], 'to@example.com')


class TestConsoleService(unittest.TestCase):
    def test_notify_changes_prints_each_file_state(self):
        modified = MagicMock(saved_to='modified.pdf', new_file=None, modified=True, moved=False, deleted=False)
        moved = MagicMock(
            saved_to='old.pdf',
            new_file=MagicMock(saved_to='new.pdf'),
            modified=False,
            moved=True,
            deleted=False,
        )
        deleted = MagicMock(saved_to='deleted.pdf', new_file=None, modified=False, moved=False, deleted=True)
        added = MagicMock(saved_to='added.pdf', new_file=None, modified=False, moved=False, deleted=False)
        course = MagicMock(fullname='Course', files=[modified, moved, deleted, added])
        empty_course = MagicMock(fullname='Empty', files=[])

        with patch('moodle_dl.notifications.console.console_service.Log') as log:
            log.cyan_str.return_value = 'cyan'
            log.green_str.return_value = 'green'
            log.magenta_str.return_value = 'magenta'
            with patch('builtins.print') as print_mock:
                ConsoleService(MagicMock()).notify_about_changes_in_moodle([empty_course, course])

        log.success.assert_called_once()
        self.assertIn('4', log.success.call_args.args[0])
        log.blue.assert_called_once_with('Course')
        log.yellow.assert_called_once_with('\u2260\tmodified.pdf')
        log.cyan_str.assert_called_once_with('<->\told.pdf')
        log.green_str.assert_any_call(' ==> new.pdf')
        log.green_str.assert_any_call('+\tadded.pdf')
        log.magenta_str.assert_called_once_with('-\tdeleted.pdf')
        print_mock.assert_any_call('cyangreen')
        print_mock.assert_any_call('magenta')
        print_mock.assert_any_call(log.green_str.return_value)

    def test_notify_error_writes_error_message(self):
        with patch('moodle_dl.notifications.console.console_service.Log') as log:
            ConsoleService(MagicMock()).notify_about_error('traceback')

        log.error.assert_called_once()
        self.assertIn('traceback', log.error.call_args.args[0])

    def test_notify_failed_downloads_prints_task_details_and_truncates_long_url(self):
        task = MagicMock()
        task.filename = 'lecture.pdf'
        task.status.get_error_text.return_value = 'timeout'
        task.file.saved_to = '/downloads/lecture.pdf'
        task.file.content_fileurl = 'https://example.com/' + ('a' * 130) + '/lecture.pdf'

        with patch('moodle_dl.notifications.console.console_service.Log') as log:
            with patch('builtins.print'):
                ConsoleService(MagicMock()).notify_about_failed_downloads([task])

        log.warning.assert_called_once()
        log.cyan.assert_called_once_with('lecture.pdf')
        log.error.assert_called_once_with('  \u9519\u8bef: timeout')
        log.info.assert_any_call('  \u76ee\u6807: /downloads/lecture.pdf')
        source_lines = [
            call.args[0] for call in log.info.call_args_list if call.args[0].startswith('  \u6765\u6e90:')
        ]
        source_line = source_lines[0]
        self.assertIn('...', source_line)
        self.assertLess(len(source_line), 140)

    def test_notify_failed_downloads_uses_fallbacks_for_partial_task(self):
        task = MagicMock(spec=['file', 'status'])
        task.file.content_filename = 'raw/name?.pdf'
        task.file.saved_to = None
        task.file.content_fileurl = None
        task.status.get_error_text.return_value = 'not found'

        with patch('moodle_dl.notifications.console.console_service.PT.restricted_filenames', False):
            with patch('moodle_dl.notifications.console.console_service.Log') as log:
                with patch('builtins.print'):
                    ConsoleService(MagicMock()).notify_about_failed_downloads([task])

        log.cyan.assert_called_once_with('raw\u29f8name\uff1f.pdf')
        log.info.assert_any_call('  \u76ee\u6807: (\u672a\u77e5\u8def\u5f84)')
        log.info.assert_any_call('  \u6765\u6e90: (\u672a\u77e5 URL)')


class TestNotificationFactories(unittest.TestCase):
    def test_notification_service_base_stores_config_when_implemented(self):
        class ConcreteNotificationService(NotificationService):
            def notify_about_changes_in_moodle(self, changes):
                return None

            def notify_about_error(self, error_description):
                return None

            def notify_about_failed_downloads(self, failed_downloads):
                return None

        config = MagicMock()
        service = ConcreteNotificationService(config)

        self.assertIs(service.config, config)

    def test_get_notify_services_instantiates_expected_services(self):
        config = MagicMock()

        remote_services = get_remote_notify_services(config)
        all_services = get_all_notify_services(config)

        self.assertEqual([type(service) for service in remote_services], REMOTE_SERVICES)
        self.assertIsInstance(all_services[0], ConsoleService)
        self.assertEqual([type(service) for service in all_services[1:]], REMOTE_SERVICES)
        self.assertTrue(all(service.config is config for service in all_services))


if __name__ == '__main__':
    unittest.main()
