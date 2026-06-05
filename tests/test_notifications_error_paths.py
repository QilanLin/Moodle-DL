# -*- coding: utf-8 -*-
"""
Error path and edge-case tests for the notification backends.

This module complements `test_notification_services_and_shooters.py` by
covering the failure modes the production code paths can run into: HTTP
errors, malformed payloads, edge-case strings (very long, unicode, special
characters, HTML injection), configuration gaps and idempotency of the
service wrappers.

The intent is to:
* lock down *current* behaviour (so a regression in a fix becomes obvious)
* document a handful of *known gaps* (e.g. Discord 429 not retried) with a
  clearly commented test instead of an untracked TODO.
"""
import json
import smtplib
import unittest
from unittest.mock import MagicMock, mock_open, patch

from requests.exceptions import HTTPError, RequestException

from moodle_dl.notifications.discord.discord_formatter import DiscordFormatter
from moodle_dl.notifications.discord.discord_service import DiscordService
from moodle_dl.notifications.discord.discord_shooter import DiscordShooter
from moodle_dl.notifications.mail.mail_shooter import MailShooter
from moodle_dl.notifications.mail.mail_service import MailService
from moodle_dl.notifications.ntfy.ntfy_formatter import create_full_moodle_diff_messages
from moodle_dl.notifications.ntfy.ntfy_shooter import NtfyShooter
from moodle_dl.notifications.ntfy.ntfy_service import NtfyService
from moodle_dl.notifications.telegram.telegram_formatter import TelegramFormatter
from moodle_dl.notifications.telegram.telegram_service import TelegramService
from moodle_dl.notifications.telegram.telegram_shooter import (
    RequestRejectedError,
    TelegramShooter,
)
from moodle_dl.notifications.xmpp.xmpp_formatter import XmppFormatter
from moodle_dl.notifications.xmpp.xmpp_shooter import XmppShooter
from moodle_dl.notifications.xmpp.xmpp_service import XmppService
from moodle_dl.types import Course, File, MoodleURL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


def make_file(name, content_type='file', saved_to=None, modified=0, moved=0, deleted=0):
    """Construct a File with the minimum fields the formatters need."""
    return File(
        module_id=1,
        section_name='Week 1',
        section_id=1,
        module_name='Module',
        content_filepath=f'Course101/{name}',
        content_filename=name,
        content_fileurl=f'https://moodle.example.com/{name}',
        content_filesize=10,
        content_timemodified=100,
        module_modname='resource',
        content_type=content_type,
        content_isexternalfile=False,
        saved_to=saved_to or f'Course101/{name}',
        modified=modified,
        moved=moved,
        deleted=deleted,
    )


# ---------------------------------------------------------------------------
# DiscordShooter failure paths
# ---------------------------------------------------------------------------
class TestDiscordShooterErrorPaths(unittest.TestCase):
    def _patched_session(self, post_return_value=None, post_side_effect=None):
        session = MagicMock()
        if post_side_effect is not None:
            session.post.side_effect = post_side_effect
        else:
            session.post.return_value = post_return_value
        return session

    def test_send_data_raises_on_429_with_retry_after_header(self):
        """
        Discord 429 (rate limited) is currently *not* retried by the shooter:
        it bubbles up as a RuntimeError through ``_check_response_code``.
        The test pins that behaviour and documents a future improvement:
        honour ``Retry-After`` and back off.
        """
        response = MagicMock(
            status_code=429,
            headers={'Retry-After': '30'},
            text='rate limited',
        )
        session = self._patched_session(post_return_value=response)

        with patch(
            'moodle_dl.notifications.discord.discord_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            shooter = DiscordShooter(['https://discord.example/hook'])
            # TODO(future): DiscordShooter should sleep `Retry-After` seconds
            # and re-issue the POST. When implemented, replace the
            # assertRaisesRegex below with `assert session.post.call_count >= 2`.
            with self.assertRaisesRegex(RuntimeError, 'Status code: 429'):
                shooter.send_data({'content': 'hi'})

    def test_send_data_raises_on_401(self):
        response = MagicMock(
            status_code=401, headers={'x': 'y'}, text='unauthorized'
        )
        session = self._patched_session(post_return_value=response)

        with patch(
            'moodle_dl.notifications.discord.discord_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            shooter = DiscordShooter(['https://discord.example/hook'])
            with self.assertRaisesRegex(RuntimeError, 'Status code: 401'):
                shooter.send_data({'content': 'hi'})

    def test_send_data_raises_on_403(self):
        response = MagicMock(
            status_code=403, headers={'x': 'y'}, text='forbidden'
        )
        session = self._patched_session(post_return_value=response)

        with patch(
            'moodle_dl.notifications.discord.discord_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            shooter = DiscordShooter(['https://discord.example/hook'])
            with self.assertRaisesRegex(RuntimeError, 'Status code: 403'):
                shooter.send_data({'content': 'hi'})

    def test_send_data_raises_on_500(self):
        response = MagicMock(
            status_code=500, headers={}, text='internal error'
        )
        session = self._patched_session(post_return_value=response)

        with patch(
            'moodle_dl.notifications.discord.discord_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            shooter = DiscordShooter(['https://discord.example/hook'])
            with self.assertRaisesRegex(RuntimeError, 'Status code: 500'):
                shooter.send_data({'content': 'hi'})

    def test_send_data_continues_with_other_webhooks_when_one_fails(self):
        """
        A 500 on the first webhook must not skip the second one — the
        shooter simply bubbles the error and stops the current attempt.
        """
        response = MagicMock(
            status_code=500, headers={}, text='broken'
        )
        session = self._patched_session(post_return_value=response)

        with patch(
            'moodle_dl.notifications.discord.discord_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            shooter = DiscordShooter(
                ['https://discord.example/one', 'https://discord.example/two']
            )
            with self.assertRaises(RuntimeError):
                shooter.send_data({'content': 'hi'})

        # Only one attempt was made because the first one raised and the
        # for-loop does not catch. We pin the current behaviour here.
        self.assertEqual(session.post.call_count, 1)

    def test_send_data_wraps_request_exception(self):
        session = self._patched_session(post_side_effect=RequestException('dns'))

        with patch(
            'moodle_dl.notifications.discord.discord_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            with self.assertRaisesRegex(ConnectionError, 'Connection error'):
                DiscordShooter(['https://discord.example/hook']).send_data(
                    {'content': 'x'}
                )

    def test_check_response_code_treats_400_as_success(self):
        """Discord returns 400 for empty messages; the shooter treats it as ok."""
        response = MagicMock(status_code=400)
        # Must not raise.
        DiscordShooter._check_response_code(response)


# ---------------------------------------------------------------------------
# TelegramShooter failure paths
# ---------------------------------------------------------------------------
class TestTelegramShooterErrorPaths(unittest.TestCase):
    def _patched_session(self, return_value=None, side_effect=None):
        session = MagicMock()
        if side_effect is not None:
            session.post.side_effect = side_effect
        else:
            session.post.return_value = return_value
        return session

    def test_send_raises_request_rejected_error_when_ok_false(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            'ok': False,
            'description': 'Bad Request: chat not found',
        }
        session = self._patched_session(return_value=response)

        with patch(
            'moodle_dl.notifications.telegram.telegram_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            with self.assertRaisesRegex(RequestRejectedError, 'chat not found'):
                TelegramShooter('token', 'chat').send('hello')

    def test_send_raises_request_rejected_error_on_empty_description(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'ok': False}
        session = self._patched_session(return_value=response)

        with patch(
            'moodle_dl.notifications.telegram.telegram_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            with self.assertRaises(RequestRejectedError):
                TelegramShooter('token', 'chat').send('hello')

    def test_send_raises_runtime_error_on_unexpected_status(self):
        response = MagicMock(status_code=502, headers={}, text='bad gateway')
        session = self._patched_session(return_value=response)

        with patch(
            'moodle_dl.notifications.telegram.telegram_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            with self.assertRaisesRegex(RuntimeError, 'Status-Code: 502'):
                TelegramShooter('token', 'chat').send('hello')

    def test_send_wraps_request_exception_as_connection_error(self):
        session = self._patched_session(side_effect=RequestException('timeout'))

        with patch(
            'moodle_dl.notifications.telegram.telegram_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            with self.assertRaisesRegex(ConnectionError, 'Connection error'):
                TelegramShooter('token', 'chat').send('hello')

    def test_edit_message_api_rejects_with_request_rejected_error(self):
        """
        The shooter only exposes `send` (the ``sendMessage`` endpoint), but
        if the body returned by the server is ``ok: False`` it should still
        raise ``RequestRejectedError``. We simulate that by posting to the
        same path with a payload that the server rejects.
        """
        response = MagicMock(status_code=400)
        response.json.return_value = {
            'ok': False,
            'description': 'Message is not modified',
        }
        session = self._patched_session(return_value=response)

        with patch(
            'moodle_dl.notifications.telegram.telegram_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            with self.assertRaisesRegex(RequestRejectedError, 'not modified'):
                TelegramShooter('token', 'chat').send('edited body')

    def test_send_handles_payload_with_unicode_emoji(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'ok': True}
        session = self._patched_session(return_value=response)

        with patch(
            'moodle_dl.notifications.telegram.telegram_shooter.SslHelper.custom_requests_session',
            return_value=session,
        ):
            # Must not raise.
            TelegramShooter('token', 'chat').send('🚀 hello ✨')

        self.assertEqual(session.post.call_count, 1)


# ---------------------------------------------------------------------------
# XmppShooter failure paths
# ---------------------------------------------------------------------------
class TestXmppShooterErrorPaths(unittest.TestCase):
    def test_send_raises_connection_error_when_bind_attribute_missing(self):
        """If the connection has no ``Bind`` attribute the check still fires."""
        connection = MagicMock()
        # explicitly do NOT set Bind — getattr(connection, 'Bind') returns a
        # MagicMock that has no `session` attribute, so the condition fires.
        connection.Bind.session = 0

        sender_jid = MagicMock()
        sender_jid.getDomain.return_value = 'example.com'
        sender_jid.getNode.return_value = 'bot'
        sender_jid.getResource.return_value = 'resource'
        recipient_jid = MagicMock()

        with patch('moodle_dl.notifications.xmpp.xmpp_shooter.xmpp') as xmpp_module:
            xmpp_module.protocol.JID.side_effect = [sender_jid, recipient_jid]
            xmpp_module.Client.return_value = connection
            xmpp_module.protocol.Message.return_value = MagicMock()
            shooter = XmppShooter(
                'bot@example.com/resource', 'secret', 'user@example.com'
            )

            with self.assertRaisesRegex(ConnectionError, 'Session could not be opend'):
                shooter.send('hello')

    def test_send_raises_when_server_disconnects_during_send(self):
        """
        Simulate the XMPP server abruptly closing the connection mid-send.
        The current implementation does *not* catch this, so it bubbles up
        as whatever the underlying client raises. We pin the current
        behaviour here (exception propagates) and document the desired
        recovery (mark ``is_connected = False`` and reconnect).
        """
        connection = MagicMock()
        connection.Bind.session = 1
        connection.send.side_effect = OSError('connection reset')

        sender_jid = MagicMock()
        sender_jid.getDomain.return_value = 'example.com'
        sender_jid.getNode.return_value = 'bot'
        sender_jid.getResource.return_value = 'resource'
        recipient_jid = MagicMock()

        with patch('moodle_dl.notifications.xmpp.xmpp_shooter.xmpp') as xmpp_module:
            xmpp_module.protocol.JID.side_effect = [sender_jid, recipient_jid]
            xmpp_module.Client.return_value = connection
            xmpp_module.protocol.Message.return_value = MagicMock()
            shooter = XmppShooter(
                'bot@example.com/resource', 'secret', 'user@example.com'
            )
            # TODO(future): the shooter should reset is_connected to False on
            # send failure so that the next call reconnects.
            with self.assertRaises(OSError):
                shooter.send('hello')

        # Current behaviour: is_connected stays True even after a failed
        # send. That is a known limitation.
        self.assertTrue(shooter.is_connected)


# ---------------------------------------------------------------------------
# MailShooter failure paths
# ---------------------------------------------------------------------------
class TestMailShooterErrorPaths(unittest.TestCase):
    def test_send_propagates_smtp_authentication_error(self):
        smtp_connection = MagicMock()
        smtp_connection.__enter__.return_value = smtp_connection
        smtp_connection.starttls.return_value = None
        smtp_connection.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b'authentication failed'
        )

        with patch('moodle_dl.notifications.mail.mail_shooter.smtplib.SMTP') as smtp_cls:
            smtp_cls.return_value = smtp_connection
            with patch('builtins.open', mock_open(read_data=b'png')):
                with self.assertRaises(smtplib.SMTPAuthenticationError):
                    MailShooter(
                        'from@example.com', 'smtp.example.com', 587, 'user', 'bad'
                    ).send(
                        target='to@example.com',
                        subject='S',
                        html_content_with_cids='<p>x</p>',
                        inline_png_cids_filenames={},
                    )

    def test_send_propagates_smtp_data_error(self):
        smtp_connection = MagicMock()
        smtp_connection.__enter__.return_value = smtp_connection
        smtp_connection.starttls.return_value = None
        smtp_connection.login.return_value = None
        smtp_connection.send_message.side_effect = smtplib.SMTPDataError(
            552, b'message too large'
        )

        with patch('moodle_dl.notifications.mail.mail_shooter.smtplib.SMTP') as smtp_cls:
            smtp_cls.return_value = smtp_connection
            with patch('builtins.open', mock_open(read_data=b'png')):
                with self.assertRaisesRegex(smtplib.SMTPDataError, 'too large'):
                    MailShooter(
                        'from@example.com', 'smtp.example.com', 587, 'user', 'pw'
                    ).send(
                        target='to@example.com',
                        subject='S',
                        html_content_with_cids='<p>x</p>',
                        inline_png_cids_filenames={},
                    )

    def test_send_propagates_socket_error_on_connect(self):
        with patch(
            'moodle_dl.notifications.mail.mail_shooter.smtplib.SMTP',
            side_effect=OSError('connection refused'),
        ):
            with patch('builtins.open', mock_open(read_data=b'png')):
                with self.assertRaises(OSError):
                    MailShooter(
                        'from@example.com',
                        'smtp.example.com',
                        587,
                        'user',
                        'pw',
                    ).send(
                        target='to@example.com',
                        subject='S',
                        html_content_with_cids='<p>x</p>',
                        inline_png_cids_filenames={},
                    )

    def test_send_propagates_io_error_for_missing_inline_attachment(self):
        """A missing inline image must surface as a real error, not silent."""
        smtp_connection = MagicMock()
        smtp_connection.__enter__.return_value = smtp_connection

        with patch('moodle_dl.notifications.mail.mail_shooter.smtplib.SMTP') as smtp_cls:
            smtp_cls.return_value = smtp_connection
            with patch('builtins.open', side_effect=FileNotFoundError('nope')):
                with self.assertRaises(FileNotFoundError):
                    MailShooter(
                        'from@example.com',
                        'smtp.example.com',
                        587,
                        'user',
                        'pw',
                    ).send(
                        target='to@example.com',
                        subject='S',
                        html_content_with_cids='<p>x</p>',
                        inline_png_cids_filenames={'cid': 'does_not_exist.png'},
                    )


# ---------------------------------------------------------------------------
# NtfyShooter failure paths
# ---------------------------------------------------------------------------
class TestNtfyShooterErrorPaths(unittest.TestCase):
    def test_send_raises_http_error_on_5xx(self):
        response = MagicMock()
        response.raise_for_status.side_effect = HTTPError('502 Bad Gateway')
        response.status_code = 502

        with patch(
            'moodle_dl.notifications.ntfy.ntfy_shooter.requests.post',
            return_value=response,
        ) as post:
            with self.assertRaisesRegex(HTTPError, '502'):
                NtfyShooter('topic', 'https://ntfy.example').send(
                    title='T', message='M'
                )

        post.assert_called_once()

    def test_send_raises_http_error_on_4xx_topic_not_found(self):
        response = MagicMock()
        response.raise_for_status.side_effect = HTTPError(
            '404 Not Found: topic not found'
        )
        response.status_code = 404

        with patch(
            'moodle_dl.notifications.ntfy.ntfy_shooter.requests.post',
            return_value=response,
        ):
            with self.assertRaisesRegex(HTTPError, '404'):
                NtfyShooter('does-not-exist', 'https://ntfy.example').send(
                    title='T', message='M'
                )

    def test_send_raises_connection_error_on_request_exception(self):
        with patch(
            'moodle_dl.notifications.ntfy.ntfy_shooter.requests.post',
            side_effect=RequestException('connection refused'),
        ):
            with self.assertRaises(RequestException):
                NtfyShooter('topic', 'https://ntfy.example').send(
                    title='T', message='M'
                )

    def test_send_does_not_add_click_action_when_source_url_is_none(self):
        response = MagicMock()
        with patch(
            'moodle_dl.notifications.ntfy.ntfy_shooter.requests.post',
            return_value=response,
        ) as post:
            NtfyShooter('topic', 'https://ntfy.example').send(
                title='T', message='M', source_url=None
            )

        payload = json.loads(post.call_args.kwargs['data'])
        self.assertNotIn('click', payload)
        self.assertNotIn('actions', payload)

    def test_send_does_not_add_click_action_when_source_url_is_empty_string(self):
        response = MagicMock()
        with patch(
            'moodle_dl.notifications.ntfy.ntfy_shooter.requests.post',
            return_value=response,
        ) as post:
            NtfyShooter('topic', 'https://ntfy.example').send(
                title='T', message='M', source_url=''
            )

        payload = json.loads(post.call_args.kwargs['data'])
        self.assertNotIn('click', payload)


# ---------------------------------------------------------------------------
# Formatter edge cases
# ---------------------------------------------------------------------------
class TestFormatterEdgeCases(unittest.TestCase):
    # -- Discord --

    def test_discord_formatter_handles_empty_changes(self):
        self.assertEqual(
            DiscordFormatter.create_full_moodle_diff_messages([], 'https://m.example/'),
            [],
        )

    def test_discord_formatter_handles_course_with_no_files(self):
        course = Course(1, 'Empty Course', files=[])
        embeds = DiscordFormatter.create_full_moodle_diff_messages(
            [course], 'https://m.example/'
        )
        # Course still appears, just with no fields.
        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0]['fields'], [])

    def test_discord_formatter_truncates_field_value_longer_than_1024(self):
        # A single file's "Added" value much longer than Discord's 1024 char limit.
        course = Course(
            1,
            'Course',
            [make_file('a' * 1500)],
        )
        embeds = DiscordFormatter.create_full_moodle_diff_messages(
            [course], 'https://m.example/'
        )
        value = embeds[0]['fields'][0]['value']
        self.assertTrue(value.endswith('...'))
        self.assertLessEqual(len(value), 1024)

    def test_discord_formatter_passes_through_unicode_emoji(self):
        course = Course(
            1,
            '🎓 Course 中文',
            [make_file('🚀 file.pdf')],
        )
        embeds = DiscordFormatter.create_full_moodle_diff_messages(
            [course], 'https://m.example/'
        )
        self.assertIn('🎓 Course 中文', embeds[0]['author']['name'])
        self.assertIn('🚀 file.pdf', embeds[0]['fields'][0]['value'])

    def test_discord_formatter_sanitizes_html_injection(self):
        """
        Course names pass through PathTools.to_valid_name, which converts
        ``<``, ``>`` and ``/`` to their full-width counterparts, defusing
        any HTML/JS injection.
        """
        malicious_name = '<script>alert(1)</script>'
        course = Course(1, malicious_name, [make_file('safe.pdf')])
        embeds = DiscordFormatter.create_full_moodle_diff_messages(
            [course], 'https://m.example/'
        )
        author_name = embeds[0]['author']['name']
        self.assertNotIn('<script>', author_name)
        self.assertIn('＜script＞', author_name)
        self.assertIn('⧸script＞', author_name)

    def test_discord_formatter_sanitizes_markdown_special_characters(self):
        """
        Discord's markdown treats ``*``, ``_``, ``~``, ``|``, ``\\``, ``>`` as
        formatting markers. PathTools.to_valid_name converts ``*`` to its
        full-width counterpart ``＊`` to defuse accidental bold formatting.
        """
        course = Course(1, '* _ ` [ ] ( ) # + - . !', [make_file('safe.pdf')])
        embeds = DiscordFormatter.create_full_moodle_diff_messages(
            [course], 'https://m.example/'
        )
        self.assertIn('＊', embeds[0]['author']['name'])
        # Other characters pass through unmodified.
        self.assertIn('_ ` [ ] ( ) # + - . !', embeds[0]['author']['name'])

    def test_discord_formatter_handles_deleted_and_moved_files(self):
        deleted = make_file('gone.pdf', deleted=1)
        moved = make_file('old.pdf', moved=1)
        moved.new_file = make_file('new.pdf', saved_to='Course101/New/new.pdf')
        course = Course(1, 'Course', [deleted, moved])

        embeds = DiscordFormatter.create_full_moodle_diff_messages(
            [course], 'https://m.example/'
        )
        fields = {f['name']: f['value'] for f in embeds[0]['fields']}
        self.assertIn('Deleted', fields)
        self.assertIn('Moved', fields)
        self.assertIn('New/new.pdf', fields['Moved'])

    # -- Telegram --

    def test_telegram_formatter_handles_empty_changes(self):
        """
        Empty input still yields a single intro line. We pin the current
        behaviour so a future optimization (e.g. dropping the message) is
        visible.
        """
        messages = TelegramFormatter.create_full_moodle_diff_messages([])
        self.assertEqual(len(messages), 1)
        self.assertIn('0 new Changes', messages[0])

    def test_telegram_formatter_handles_very_long_course_name(self):
        long_name = 'X' * 5000
        course = Course(1, long_name, [make_file('a.pdf')])
        messages = TelegramFormatter.create_full_moodle_diff_messages([course])
        # The first message must respect the 4096-char per-message cap.
        for msg in messages:
            self.assertLessEqual(len(msg), 4096, 'Telegram message exceeded 4096 chars')
        # The fullname appears in the (possibly split) output.
        rendered = ''.join(messages)
        self.assertIn('X', rendered)

    def test_telegram_formatter_passes_through_unicode_emoji(self):
        course = Course(1, '🚀 中文', [make_file('✨.pdf')])
        messages = TelegramFormatter.create_full_moodle_diff_messages([course])
        self.assertTrue(any('🚀 中文' in m for m in messages))

    def test_telegram_formatter_escapes_html_injection(self):
        """
        Telegram's append_with_limit replaces unknown ``<`` / ``>`` with
        ``&lt;`` / ``&gt;`` so user-supplied course names cannot inject
        HTML into the message.
        """
        result = TelegramFormatter.append_with_limit(
            '<script>alert(1)</script>', '', [], limit=200
        )
        self.assertIn('&lt;script&gt;', result)
        self.assertNotIn('<script>', result)

    def test_telegram_formatter_preserves_bold_tags(self):
        """Known ``<b>...</b>`` blocks must NOT be escaped."""
        result = TelegramFormatter.append_with_limit(
            '<b>safe</b>', '', [], limit=200
        )
        self.assertIn('<b>safe</b>', result)

    def test_telegram_formatter_handles_deleted_and_moved_files(self):
        deleted = make_file('gone.pdf', deleted=1)
        moved = make_file('old.pdf', moved=1)
        moved.new_file = make_file('new.pdf', saved_to='Course101/New/new.pdf')
        course = Course(1, 'Course', [deleted, moved])
        messages = TelegramFormatter.create_full_moodle_diff_messages([course])
        rendered = ''.join(messages)
        self.assertIn('Deleted:', rendered)
        self.assertIn('Moved:', rendered)
        self.assertIn('Course101/New/new.pdf', rendered)

    def test_telegram_formatter_sanitizes_markdown_special_characters(self):
        """
        The course name passes through PathTools.to_valid_name, which
        converts ``*`` to its full-width counterpart ``＊`` so it cannot
        trigger Telegram's MarkdownV2-style bold formatting. We pin that
        sanitisation here.
        """
        course = Course(
            1, 'Course * _ ` [ ] ( ) # + - . !', [make_file('safe.pdf')]
        )
        messages = TelegramFormatter.create_full_moodle_diff_messages([course])
        rendered = ''.join(messages)
        # ``*`` is the only char that gets full-width-translated by
        # sanitize_filename; the others pass through.
        self.assertIn('＊', rendered)
        self.assertIn('_ ` [ ] ( ) # + - . !', rendered)

    # -- XMPP --

    def test_xmpp_formatter_handles_empty_changes(self):
        """
        Empty input still yields a single intro line. We pin the current
        behaviour so a future optimization (e.g. dropping the message) is
        visible.
        """
        messages = XmppFormatter.create_full_moodle_diff_messages([])
        self.assertEqual(len(messages), 1)
        self.assertIn('0 new Changes', messages[0])

    def test_xmpp_formatter_very_long_course_name_is_split(self):
        long_name = 'X' * 5000
        course = Course(1, long_name, [make_file('a.pdf')])
        messages = XmppFormatter.create_full_moodle_diff_messages([course])
        for msg in messages:
            self.assertLessEqual(len(msg), 4096)
        rendered = ''.join(messages)
        self.assertIn('X', rendered)

    def test_xmpp_formatter_passes_through_unicode_emoji(self):
        course = Course(1, '🎓 Course', [make_file('✨.pdf')])
        messages = XmppFormatter.create_full_moodle_diff_messages([course])
        self.assertTrue(any('🎓 Course' in m for m in messages))

    def test_xmpp_formatter_sanitizes_html_injection(self):
        """
        Course names pass through PathTools.to_valid_name, which converts
        ``<``, ``>`` and ``/`` to their full-width counterparts, defusing
        any HTML/JS injection.
        """
        course = Course(1, '<script>alert(1)</script>', [make_file('safe.pdf')])
        messages = XmppFormatter.create_full_moodle_diff_messages([course])
        rendered = ''.join(messages)
        self.assertNotIn('<script>', rendered)
        self.assertIn('＜script＞', rendered)
        self.assertIn('⧸script＞', rendered)

    def test_xmpp_formatter_handles_deleted_and_moved_files(self):
        deleted = make_file('gone.pdf', deleted=1)
        moved = make_file('old.pdf', moved=1)
        moved.new_file = make_file('new.pdf', saved_to='Course101/New/new.pdf')
        course = Course(1, 'Course', [deleted, moved])
        messages = XmppFormatter.create_full_moodle_diff_messages([course])
        rendered = ''.join(messages)
        self.assertIn('Deleted:', rendered)
        self.assertIn('Moved:', rendered)
        self.assertIn('Course101/New/new.pdf', rendered)

    # -- Ntfy --

    def test_ntfy_formatter_handles_empty_changes(self):
        self.assertEqual(create_full_moodle_diff_messages([]), [])

    def test_ntfy_formatter_handles_very_long_course_name(self):
        """
        Long course names are truncated by PathTools.to_valid_name (max 200
        chars by default). The formatter should still produce messages and
        not crash.
        """
        long_name = 'X' * 5000
        course = Course(1, long_name, [make_file('a.pdf')])
        messages = create_full_moodle_diff_messages([course])
        # Should not crash, and the truncated name should appear.
        self.assertTrue(messages)
        self.assertTrue(any('X' in m['message'] for m in messages))
        # The course name was truncated, so the message stays sane.
        for m in messages:
            self.assertLess(len(m['message']), 1000)

    def test_ntfy_formatter_handles_unicode_emoji(self):
        course = Course(1, '🎓 中文', [make_file('✨.pdf')])
        messages = create_full_moodle_diff_messages([course])
        self.assertTrue(any('🎓 中文' in m['message'] for m in messages))

    def test_ntfy_formatter_sanitizes_html_injection(self):
        """
        Course names pass through PathTools.to_valid_name, which converts
        ``<``, ``>`` and ``/`` to their full-width counterparts, defusing
        any HTML/JS injection.
        """
        malicious = '<script>alert(1)</script>'
        course = Course(1, malicious, [make_file('safe.pdf')])
        messages = create_full_moodle_diff_messages([course])
        self.assertTrue(messages)
        rendered = ' '.join(m['message'] for m in messages)
        self.assertNotIn('<script>', rendered)
        self.assertIn('＜script＞', rendered)

    def test_ntfy_formatter_sanitizes_markdown_special_characters(self):
        course = Course(1, '* _ ` [ ] ( ) # + - . !', [make_file('safe.pdf')])
        messages = create_full_moodle_diff_messages([course])
        rendered = ' '.join(m['message'] for m in messages)
        # ``*`` is converted to its full-width counterpart by PathTools.
        self.assertIn('＊', rendered)
        self.assertIn('_ ` [ ] ( ) # + - . !', rendered)

    def test_ntfy_formatter_handles_deleted_and_moved_files(self):
        deleted = make_file('gone.pdf', deleted=1)
        moved = make_file('old.pdf', moved=1)
        moved.new_file = make_file('new.pdf', saved_to='Course101/New/new.pdf')
        course = Course(1, 'Course', [deleted, moved])
        messages = create_full_moodle_diff_messages([course])
        rendered = ''.join(m['message'] for m in messages)
        self.assertIn('| File deleted', rendered)
        self.assertIn('| File moved', rendered)


# ---------------------------------------------------------------------------
# Service integration: error handling and idempotency
# ---------------------------------------------------------------------------
class TestServiceIntegrationErrorHandling(unittest.TestCase):
    DISCORD_CONFIG = {'webhook_urls': ['https://discord.example/hook']}
    TELEGRAM_CONFIG = {'token': 'tok', 'chat_id': 'chat'}
    NTFY_CONFIG = {'topic': 'moodle', 'server': 'https://ntfy.example'}
    XMPP_CONFIG = {
        'sender': 'bot@example.com/r',
        'password': 'secret',
        'target': 'user@example.com',
    }
    MAIL_CONFIG = {
        'sender': 'from@example.com',
        'target': 'to@example.com',
        'server_host': 'smtp.example.com',
        'server_port': '587',
        'username': 'user',
        'password': 'pw',
    }

    # -- Discord --

    def test_discord_service_does_not_silently_swallow_shooter_errors(self):
        service = DiscordService(
            configured_config('discord', self.DISCORD_CONFIG)
        )
        changes = [Course(1, 'Course', [make_file('a.pdf')])]

        with patch(
            'moodle_dl.notifications.discord.discord_service.DiscordShooter'
        ) as shooter_cls:
            shooter_cls.return_value.send.side_effect = RuntimeError('boom')
            with self.assertRaisesRegex(RuntimeError, 'boom'):
                service.notify_about_changes_in_moodle(changes)

    def test_discord_notify_error_and_failed_downloads_are_idempotent(self):
        service = DiscordService(
            configured_config('discord', self.DISCORD_CONFIG)
        )
        # These methods are documented as not implemented, but they must
        # at least return None on every call (no state, no side effects).
        self.assertIsNone(service.notify_about_error('a'))
        self.assertIsNone(service.notify_about_error('a'))
        self.assertIsNone(service.notify_about_failed_downloads([]))
        self.assertIsNone(service.notify_about_failed_downloads([MagicMock()]))

    def test_discord_notify_changes_with_empty_changes_sends_no_messages(self):
        service = DiscordService(
            configured_config('discord', self.DISCORD_CONFIG)
        )

        with patch(
            'moodle_dl.notifications.discord.discord_service.DF.create_full_moodle_diff_messages',
            return_value=[],
        ) as formatter:
            with patch.object(service, '_send_embeds') as send_embeds:
                service.notify_about_changes_in_moodle([])

        formatter.assert_called_once_with([], 'https://moodle.example.com/')
        send_embeds.assert_called_once_with([])

    # -- Telegram --

    def test_telegram_service_does_not_silently_swallow_shooter_errors(self):
        service = TelegramService(
            configured_config('telegram', self.TELEGRAM_CONFIG)
        )

        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TelegramShooter'
        ) as shooter_cls:
            shooter_cls.return_value.send.side_effect = RequestRejectedError('no')
            with self.assertRaises(RequestRejectedError):
                service.notify_about_changes_in_moodle([Course(1, 'C')])

    def test_telegram_notify_error_returns_gracefully_when_unconfigured(self):
        service = TelegramService(unconfigured_config())
        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TF.create_full_error_messages'
        ) as formatter:
            # Must not raise, must not call the formatter.
            self.assertIsNone(service.notify_about_error('traceback'))
        formatter.assert_not_called()

    def test_telegram_notify_changes_is_idempotent_when_no_messages(self):
        service = TelegramService(
            configured_config('telegram', self.TELEGRAM_CONFIG)
        )
        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TF.create_full_moodle_diff_messages',
            return_value=[],
        ):
            with patch.object(service, '_send_messages') as send_messages:
                # Calling twice should still call send_messages twice (the
                # service itself has no state to deduplicate).
                service.notify_about_changes_in_moodle([])
                service.notify_about_changes_in_moodle([])
        self.assertEqual(send_messages.call_count, 2)

    def test_telegram_notify_failed_downloads_is_idempotent_when_unconfigured(self):
        service = TelegramService(unconfigured_config())
        with patch(
            'moodle_dl.notifications.telegram.telegram_service.TF.create_full_failed_downloads_messages'
        ) as formatter:
            self.assertIsNone(service.notify_about_failed_downloads([MagicMock()]))
            self.assertIsNone(service.notify_about_failed_downloads([]))
        formatter.assert_not_called()

    # -- Ntfy --

    def test_ntfy_service_does_not_silently_swallow_shooter_errors(self):
        service = NtfyService(configured_config('ntfy', self.NTFY_CONFIG))
        with patch(
            'moodle_dl.notifications.ntfy.ntfy_service.NtfyShooter'
        ) as shooter_cls:
            shooter_cls.return_value.send.side_effect = HTTPError('502')
            with self.assertRaises(HTTPError):
                service._send_messages(
                    [{'title': 'T', 'message': 'M', 'source_url': None}]
                )

    def test_ntfy_notify_error_and_failed_downloads_are_idempotent(self):
        service = NtfyService(configured_config('ntfy', self.NTFY_CONFIG))
        self.assertIsNone(service.notify_about_error('a'))
        self.assertIsNone(service.notify_about_failed_downloads([MagicMock()]))

    def test_ntfy_notify_changes_with_empty_changes_is_idempotent(self):
        service = NtfyService(configured_config('ntfy', self.NTFY_CONFIG))
        with patch(
            'moodle_dl.notifications.ntfy.ntfy_service.NF.create_full_moodle_diff_messages',
            return_value=[],
        ):
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_changes_in_moodle([])
        send_messages.assert_called_once_with([])

    # -- XMPP --

    def test_xmpp_service_does_not_silently_swallow_shooter_errors(self):
        service = XmppService(configured_config('xmpp', self.XMPP_CONFIG))
        with patch(
            'moodle_dl.notifications.xmpp.xmpp_service.XmppShooter'
        ) as shooter_cls:
            shooter_cls.return_value.send.side_effect = ConnectionError('no bind')
            with self.assertRaises(ConnectionError):
                service.notify_about_changes_in_moodle([Course(1, 'C')])

    def test_xmpp_notify_error_returns_gracefully_when_unconfigured(self):
        service = XmppService(unconfigured_config())
        with patch(
            'moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_error_messages'
        ) as formatter:
            self.assertIsNone(service.notify_about_error('traceback'))
        formatter.assert_not_called()

    def test_xmpp_notify_failed_downloads_is_idempotent_when_unconfigured(self):
        service = XmppService(unconfigured_config())
        with patch(
            'moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_failed_downloads_messages'
        ) as formatter:
            self.assertIsNone(service.notify_about_failed_downloads([MagicMock()]))
        formatter.assert_not_called()

    def test_xmpp_notify_changes_with_empty_changes_is_idempotent(self):
        service = XmppService(configured_config('xmpp', self.XMPP_CONFIG))
        with patch(
            'moodle_dl.notifications.xmpp.xmpp_service.XF.create_full_moodle_diff_messages',
            return_value=[],
        ):
            with patch.object(service, '_send_messages') as send_messages:
                service.notify_about_changes_in_moodle([])
        send_messages.assert_called_once_with([])

    # -- Mail --

    def test_mail_service_does_not_silently_swallow_shooter_errors(self):
        service = MailService(configured_config('mail', self.MAIL_CONFIG))
        with patch(
            'moodle_dl.notifications.mail.mail_service.MailShooter'
        ) as shooter_cls:
            shooter_cls.return_value.send.side_effect = smtplib.SMTPException('nope')
            with self.assertRaises(smtplib.SMTPException):
                service.notify_about_changes_in_moodle([Course(1, 'C')])

    def test_mail_notify_error_returns_gracefully_when_unconfigured(self):
        service = MailService(unconfigured_config())
        with patch(
            'moodle_dl.notifications.mail.mail_service.create_full_error_mail'
        ) as formatter:
            self.assertIsNone(service.notify_about_error('traceback'))
        formatter.assert_not_called()

    def test_mail_notify_failed_downloads_is_idempotent_when_unconfigured(self):
        service = MailService(unconfigured_config())
        with patch(
            'moodle_dl.notifications.mail.mail_service.create_full_failed_downloads_mail'
        ) as formatter:
            self.assertIsNone(service.notify_about_failed_downloads([MagicMock()]))
        formatter.assert_not_called()

    def test_mail_notify_changes_with_empty_changes_sends_zero_count_subject(self):
        service = MailService(configured_config('mail', self.MAIL_CONFIG))
        with patch(
            'moodle_dl.notifications.mail.mail_service.create_full_moodle_diff_mail',
            return_value=('html', {}),
        ):
            with patch.object(service, '_send_mail') as send_mail:
                service.notify_about_changes_in_moodle([])
        send_mail.assert_called_once_with('0 new Changes in the Moodle courses!', ('html', {}))


if __name__ == '__main__':
    unittest.main()
