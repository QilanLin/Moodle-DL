# -*- coding: utf-8 -*-
import json
import unittest
from unittest.mock import MagicMock, mock_open, patch

from requests.exceptions import RequestException

from moodle_dl.notifications.discord.discord_shooter import DiscordShooter
from moodle_dl.notifications.discord.discord_service import DiscordService
from moodle_dl.notifications.mail.mail_shooter import MailShooter
from moodle_dl.notifications.mail.mail_service import MailService
from moodle_dl.notifications.ntfy.ntfy_shooter import NtfyShooter
from moodle_dl.notifications.ntfy.ntfy_service import NtfyService
from moodle_dl.notifications.telegram.telegram_shooter import RequestRejectedError, TelegramShooter
from moodle_dl.notifications.telegram.telegram_service import TelegramService
from moodle_dl.notifications.xmpp.xmpp_formatter import XmppFormatter
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


class TestTelegramService(unittest.TestCase):
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

    def test_notify_error_respects_send_error_flag(self):
        service = TelegramService(
            configured_config('telegram', {'token': 'token', 'chat_id': 'chat', 'send_error_msg': False})
        )

        with patch.object(service, '_send_messages') as send_messages:
            service.notify_about_error('traceback')

        send_messages.assert_not_called()

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


class TestMailService(unittest.TestCase):
    MAIL_CONFIG = {
        'sender': 'from@example.com',
        'target': 'to@example.com',
        'server_host': 'smtp.example.com',
        'server_port': '587',
        'username': 'user',
        'password': 'password',
    }

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


class TestXmppService(unittest.TestCase):
    XMPP_CONFIG = {'sender': 'bot@example.com/resource', 'password': 'secret', 'target': 'user@example.com'}

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

    def test_notify_error_and_failed_downloads_respect_send_error_flag(self):
        service = XmppService(configured_config('xmpp', dict(self.XMPP_CONFIG, send_error_msg=False)))

        with patch.object(service, '_send_messages') as send_messages:
            service.notify_about_error('traceback')
            service.notify_about_failed_downloads([MagicMock()])

        send_messages.assert_not_called()

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


class TestShooters(unittest.TestCase):
    def test_discord_response_code_validation(self):
        for status_code in (200, 204, 400):
            response = MagicMock(status_code=status_code)
            DiscordShooter._check_response_code(response)

        response = MagicMock(status_code=500, headers={'x': 'y'}, text='server error')
        with self.assertRaisesRegex(RuntimeError, 'Status code: 500'):
            DiscordShooter._check_response_code(response)

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


if __name__ == '__main__':
    unittest.main()
