# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from moodle_dl.notifications.mail.mail_formatter import (
    create_full_error_mail,
    create_full_failed_downloads_mail,
    create_full_moodle_diff_mail,
    create_full_welcome_mail,
)
from moodle_dl.notifications.discord.discord_formatter import DiscordFormatter
from moodle_dl.notifications.ntfy.ntfy_formatter import _get_change_type, create_full_moodle_diff_messages
from moodle_dl.notifications.telegram.telegram_formatter import TelegramFormatter
from moodle_dl.types import Course, File


def make_file(name, content_type='file', saved_to=None, modified=0, moved=0, deleted=0):
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


class TestDiscordFormatter(unittest.TestCase):
    def test_create_full_moodle_diff_messages_groups_changes_by_type(self):
        added = make_file('new.pdf')
        modified = make_file('changed.pdf', modified=1)
        moved = make_file('moved.pdf', moved=1)
        deleted = make_file('deleted.pdf', deleted=1)
        course = Course(42, 'Course101', [added, modified, moved, deleted])

        embeds = DiscordFormatter.create_full_moodle_diff_messages([course], 'https://moodle.example.com/')

        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0]['author']['url'], 'https://moodle.example.com/course/view.php?id=42')
        fields = {field['name']: field['value'] for field in embeds[0]['fields']}
        self.assertEqual(fields['Added'], '• new.pdf')
        self.assertEqual(fields['Modified'], '• changed.pdf')
        self.assertEqual(fields['Moved'], '• moved.pdf')
        self.assertEqual(fields['Deleted'], '• deleted.pdf')

    def test_create_full_moodle_diff_messages_uses_new_file_path_for_replacements(self):
        old_file = make_file('old.pdf', modified=1)
        old_file.new_file = make_file('new-name.pdf', saved_to='Course101/Renamed/new-name.pdf')
        course = Course(42, 'Course101', [old_file])

        embeds = DiscordFormatter.create_full_moodle_diff_messages([course], 'https://moodle.example.com/')

        self.assertEqual(embeds[0]['fields'][0]['value'], '• Renamed/new-name.pdf')


class TestNtfyFormatter(unittest.TestCase):
    def test_get_change_type_prioritizes_modified_deleted_moved(self):
        self.assertEqual(_get_change_type(make_file('modified.pdf', modified=1, deleted=1, moved=1)), 'modified')
        self.assertEqual(_get_change_type(make_file('deleted.pdf', deleted=1, moved=1)), 'deleted')
        self.assertEqual(_get_change_type(make_file('moved.pdf', moved=1)), 'moved')
        self.assertEqual(_get_change_type(make_file('new.pdf')), 'new')

    def test_create_full_moodle_diff_messages_splits_description_file_and_misc(self):
        description = make_file('intro.html', content_type='description', modified=1)
        description.content_filepath = 'Description intro.html'
        regular = make_file('slides.pdf', content_type='file', deleted=1)
        assignment = make_file('submission.pdf', content_type='assignfile', moved=1)
        misc = make_file('quiz.json', content_type='quiz')
        course = Course(42, 'Course101', [description, regular, assignment, misc])

        messages = create_full_moodle_diff_messages([course])

        self.assertEqual(messages[0]['title'], 'intro.html')
        self.assertIn('Message modified', messages[0]['message'])
        self.assertEqual(messages[1]['title'], '2 File Changes')
        self.assertIn('slides.pdf | File deleted', messages[1]['message'])
        self.assertIn('submission.pdf | Assignment File | File moved', messages[1]['message'])
        self.assertEqual(messages[2]['title'], '1 Misc Changes')
        self.assertIn('quiz.json', messages[2]['message'])


class TestTelegramFormatter(unittest.TestCase):
    def test_append_with_limit_escapes_untrusted_angle_brackets(self):
        messages = []

        content = TelegramFormatter.append_with_limit('<b>ok</b> <script>', '', messages, limit=100)

        self.assertEqual(content, '<b>ok</b> &lt;script&gt;')
        self.assertEqual(messages, [])

    def test_append_with_limit_splits_and_truncates_long_lines(self):
        messages = []

        content = TelegramFormatter.append_with_limit('abcdef', '12345', messages, limit=10)

        self.assertEqual(messages, ['12345'])
        self.assertEqual(content, 'abcdef')

        messages = []
        content = TelegramFormatter.append_with_limit('abcdefghij', '', messages, limit=6)

        self.assertEqual(messages, [''])
        self.assertEqual(content, 'abc…')

    def test_create_full_error_messages_splits_long_details(self):
        messages = TelegramFormatter.create_full_error_messages('line1\n' + ('x' * 4100))

        self.assertGreaterEqual(len(messages), 2)
        self.assertIn('line1', messages[0])

    def test_create_full_moodle_diff_messages_includes_text_file_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            description_path = os.path.join(temp_dir, 'description.txt')
            page_path = os.path.join(temp_dir, 'page.html')
            with open(description_path, 'w', encoding='utf-8') as description_file:
                description_file.write('Plain description\nsecond line')
            with open(page_path, 'w', encoding='utf-8') as page_file:
                page_file.write('<h1>Page Title</h1><p>Page body</p>')

            description = make_file('description.txt', content_type='description', saved_to=description_path)
            page = make_file('page.html', content_type='html', saved_to=page_path)
            page.module_modname = 'page'
            modified = make_file('modified.pdf', modified=1)
            moved = make_file('moved.pdf', moved=1)
            deleted = make_file('deleted.pdf', deleted=1)
            replacement = make_file('old.pdf', modified=1)
            replacement.new_file = make_file('new.pdf', saved_to='Course101/new.pdf')

            messages = TelegramFormatter.create_full_moodle_diff_messages(
                [Course(42, 'Course101', [description, page, modified, moved, deleted, replacement])]
            )

        rendered = '\n'.join(messages)
        self.assertIn('6 new Changes', rendered)
        self.assertIn('Plain description', rendered)
        self.assertIn('Page Title', rendered)
        self.assertIn('Modified:', rendered)
        self.assertIn('Moved:', rendered)
        self.assertIn('Deleted:', rendered)
        self.assertIn('Course101/new.pdf', rendered)

    def test_create_full_failed_downloads_messages_handles_url_and_plain_file_cases(self):
        task_with_url = MagicMock()
        task_with_url.file.content_filename = 'lecture.pdf'
        task_with_url.file.content_fileurl = 'https://moodle.example.com/lecture.pdf'
        task_with_url.status.get_error_text.return_value = 'HTTP 500'

        task_without_url = MagicMock()
        task_without_url.file.content_filename = 'notes.pdf'
        task_without_url.file.content_fileurl = 'notes.pdf'
        task_without_url.status.get_error_text.return_value = 'Checksum failed'

        messages = TelegramFormatter.create_full_failed_downloads_messages([task_with_url, task_without_url])

        rendered = ''.join(messages)
        self.assertIn('lecture.pdf (https://moodle.example.com/lecture.pdf)', rendered)
        self.assertIn('HTTP 500', rendered)
        self.assertIn('notes.pdf:', rendered)
        self.assertIn('Checksum failed', rendered)

    def test_create_full_failed_downloads_messages_returns_empty_for_empty_list(self):
        self.assertEqual(TelegramFormatter.create_full_failed_downloads_messages([]), [])


class TestMailFormatter(unittest.TestCase):
    def test_create_full_moodle_diff_mail_renders_change_types_and_attachments(self):
        moved = make_file('old-location.pdf', moved=1)
        moved.new_file = make_file('new-location.pdf', saved_to='Course101/New/new-location.pdf')
        course = Course(
            42,
            'Course101',
            [
                make_file('added.pdf'),
                make_file('modified.pdf', modified=1),
                make_file('deleted.pdf', deleted=1),
                moved,
            ],
        )

        html, attachments = create_full_moodle_diff_mail([course])

        self.assertIn('Changes were noted in the following course', html)
        self.assertIn('Course101/added.pdf', html)
        self.assertIn('Course101/modified.pdf', html)
        self.assertIn('Course101/deleted.pdf', html)
        self.assertIn('Course101/old-location.pdf ==> Course101/New/new-location.pdf', html)
        self.assertEqual(set(attachments.values()), {'header.png', 'header_extender.png'})

    def test_create_full_moodle_diff_mail_uses_plural_introduction_for_multiple_courses(self):
        html, _attachments = create_full_moodle_diff_mail([Course(1, 'One'), Course(2, 'Two')])

        self.assertIn('Changes were found in 2 courses', html)

    def test_create_welcome_error_and_failed_download_mails(self):
        welcome_html, welcome_attachments = create_full_welcome_mail()
        self.assertIn('Wow, it works!', welcome_html)
        self.assertEqual(set(welcome_attachments.values()), {'header.png', 'header_extender.png'})

        error_html, _error_attachments = create_full_error_mail('Stacktrace here')
        self.assertIn('Stacktrace here', error_html)

        failed_task = MagicMock()
        failed_task.file.content_filename = 'lecture.pdf'
        failed_task.status.get_error_text.return_value = 'HTTP 500'

        failed_html, _failed_attachments = create_full_failed_downloads_mail([failed_task])
        self.assertIn('lecture.pdf', failed_html)
        self.assertIn('HTTP 500', failed_html)


if __name__ == '__main__':
    unittest.main()
