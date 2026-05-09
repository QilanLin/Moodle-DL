# -*- coding: utf-8 -*-
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from moodle_dl.downloader.download_service import DownloadService
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.types import Course, DlEvent, DownloadStatus, File, MoodleURL, TaskState


def make_file(
    filename='file.pdf',
    file_id=None,
    section_id=1,
    size=100,
    modname='resource',
    content_type='file',
    deleted=0,
):
    return File(
        module_id=10,
        section_name='Week 1',
        section_id=section_id,
        module_name='Module',
        content_filepath='/',
        content_filename=filename,
        content_fileurl='https://example.test/file',
        content_filesize=size,
        content_timemodified=1700000000,
        module_modname=modname,
        content_type=content_type,
        content_isexternalfile=False,
        file_id=file_id,
        deleted=deleted,
    )


def make_download_service():
    service = DownloadService.__new__(DownloadService)
    service.courses = []
    service.config = MagicMock()
    service.opts = MagicMock()
    service.database = MagicMock()
    service.status = DownloadStatus()
    service.progress_tracker = MagicMock()
    return service


class TestDownloadServiceOrchestration(unittest.TestCase):
    def test_gen_all_tasks_orders_incomplete_web_and_normal_tasks(self):
        service = make_download_service()
        incomplete_file = make_file('resume.bin', file_id=10, size=5)
        normal_file = make_file('fresh.pdf', file_id=20, size=7)
        deleted_file = make_file('deleted.pdf', file_id=30, deleted=1)
        course = Course(1, 'Course', [incomplete_file, normal_file, deleted_file])
        service.courses = [course]
        service.config.get_manually_specified_course_ids.return_value = [99]
        service.database.cleanup_old_incomplete_downloads.return_value = 0
        service._configure_task_settings = MagicMock(return_value=('options', 'pool'))
        service._load_incomplete_downloads_map = MagicMock(
            return_value={10: {'downloaded_bytes': 2, 'total_bytes': 5}}
        )

        def create_task(course_file, course, dl_options, thread_pool, api_source='mobile'):
            return SimpleNamespace(
                file=course_file,
                course=course,
                api_source=api_source,
                status=SimpleNamespace(state=TaskState.INIT),
            )

        service._create_task = MagicMock(side_effect=create_task)
        service._log_incomplete_download = MagicMock()
        web_priority = SimpleNamespace(file=make_file('web-resume.pdf'), api_source='web')
        web_normal = SimpleNamespace(file=make_file('web-fresh.pdf'), api_source='web')
        service._create_tasks_for_manually_specified_courses = MagicMock(
            return_value=([web_priority], [web_normal])
        )

        with patch('moodle_dl.downloader.download_service.logging') as mock_logging:
            tasks = service.gen_all_tasks()

        self.assertEqual([task.file.content_filename for task in tasks], [
            'resume.bin',
            'web-resume.pdf',
            'fresh.pdf',
            'web-fresh.pdf',
        ])
        self.assertEqual(service.status.files_to_download, 2)
        self.assertEqual(service.status.bytes_to_download, 12)
        service._log_incomplete_download.assert_called_once_with(
            incomplete_file, {10: {'downloaded_bytes': 2, 'total_bytes': 5}}
        )
        mock_logging.info.assert_called_once()
        self.assertIn('来自手动指定课程', mock_logging.info.call_args.args[0])

    def test_status_callback_updates_counters_and_persists_results(self):
        service = make_download_service()
        course = Course(7, 'Status Course')
        task = SimpleNamespace(
            file=make_file('status.pdf'),
            course=course,
            status=SimpleNamespace(get_error_text=MagicMock(return_value='network error')),
        )

        service.status_callback(DlEvent.RECEIVED, task, bytes_received=25)
        service.status_callback(DlEvent.TOTAL_SIZE, task, content_length=100)
        service.status_callback(DlEvent.TOTAL_SIZE_UPDATE, task, content_length_diff=50)
        service.status_callback(DlEvent.FAILED, task)
        service.status_callback(DlEvent.FINISHED, task)

        self.assertEqual(service.status.bytes_downloaded, 25)
        self.assertEqual(service.status.bytes_to_download, 150)
        self.assertEqual(service.status.files_failed, 1)
        self.assertEqual(service.status.files_downloaded, 1)
        service.database.save_failed_file.assert_called_once_with(
            task.file, 7, course.fullname, 'network error'
        )
        service.database.save_file.assert_called_once_with(task.file, 7, course.fullname)
        service.database.mark_download_success.assert_called_once_with(task.file, 7)

    def test_status_callback_counts_persistence_errors(self):
        service = make_download_service()
        course = Course(8, 'Broken Persistence')
        task = SimpleNamespace(
            file=make_file('broken.pdf'),
            course=course,
            status=SimpleNamespace(get_error_text=MagicMock(return_value='failed')),
        )

        service.database.save_failed_file.side_effect = RuntimeError('write failed')
        service.status_callback(DlEvent.FAILED, task)
        self.assertEqual(service.status.files_failed, 1)

        service.database.save_file.side_effect = RuntimeError('write failed')
        service.status_callback(DlEvent.FINISHED, task)
        self.assertEqual(service.status.files_failed, 2)
        self.assertEqual(service.status.files_downloaded, 0)

    def test_real_run_deletes_old_files_runs_tasks_and_shows_summary(self):
        service = make_download_service()
        first = SimpleNamespace(run=AsyncMock())
        second = SimpleNamespace(run=AsyncMock())
        service.all_tasks = [first, second]
        service.status.files_to_download = 2
        service.log_download_status = AsyncMock()
        service._display_download_summary = MagicMock()

        with patch('moodle_dl.downloader.download_service.random.uniform', return_value=0.2):
            with patch('moodle_dl.downloader.download_service.asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
                asyncio.run(service.real_run())

        service.database.batch_delete_files.assert_called_once_with(service.courses)
        first.run.assert_awaited_once()
        second.run.assert_awaited_once()
        mock_sleep.assert_awaited_once_with(0.5)
        service._display_download_summary.assert_called_once()

    def test_real_run_returns_when_queue_is_empty(self):
        service = make_download_service()
        service.all_tasks = []
        service.log_download_status = AsyncMock()

        asyncio.run(service.real_run())

        service.database.batch_delete_files.assert_called_once_with(service.courses)
        service.log_download_status.assert_not_called()

    def test_log_download_status_updates_progress_tracker_once(self):
        service = make_download_service()
        service.status.bytes_downloaded = 20
        service.status.bytes_to_download = 100
        service.status.files_downloaded = 1
        service.status.files_failed = 2
        service.status.files_to_download = 4
        service.progress_tracker.get_progress_line.return_value = 'progress'
        service.progress_tracker.get_statistics_line.return_value = 'stats'
        sleep_calls = {'count': 0}

        async def fake_sleep(_seconds):
            sleep_calls['count'] += 1
            if sleep_calls['count'] > 1:
                raise asyncio.CancelledError()

        async def run_once():
            with patch('moodle_dl.downloader.download_service.asyncio.sleep', side_effect=fake_sleep):
                with patch('moodle_dl.downloader.download_service.logging') as mock_logging:
                    with self.assertRaises(asyncio.CancelledError):
                        await service.log_download_status()
                    return mock_logging

        mock_logging = asyncio.run(run_once())
        service.progress_tracker.update.assert_called_once_with(
            downloaded_bytes=20,
            total_bytes=100,
            completed=1,
            failed=2,
            total=4,
            skipped=0,
        )
        mock_logging.info.assert_has_calls([call('progress'), call('   stats')])

    def test_display_download_summary_logs_each_line(self):
        service = make_download_service()
        service.progress_tracker.get_summary.return_value = 'line one\nline two'

        with patch('moodle_dl.downloader.download_service.logging') as mock_logging:
            service._display_download_summary()

        mock_logging.info.assert_has_calls([call('line one'), call('line two')])

    @patch('moodle_dl.moodle.request_helper.RequestHelper')
    def test_fetch_course_data_from_web_api_wraps_section_list(self, mock_request_helper):
        service = make_download_service()
        service.config.get_moodle_URL.return_value = MoodleURL(False, 'moodle.test', '/')
        service.config.get_token.return_value = 'token'
        mock_request_helper.return_value.post.return_value = [{'id': 1, 'modules': []}]

        result = service._fetch_course_data_from_web_api(123)

        self.assertEqual(result, {'id': 123, 'sections': [{'id': 1, 'modules': []}]})
        mock_request_helper.return_value.post.assert_called_once_with(
            'core_course_get_contents', {'courseid': 123}
        )

    @patch('moodle_dl.moodle.request_helper.RequestHelper')
    def test_fetch_course_data_from_web_api_returns_empty_for_bad_response_or_error(self, mock_request_helper):
        service = make_download_service()
        service.config.get_moodle_URL.return_value = MoodleURL(False, 'moodle.test', '/')
        service.config.get_token.return_value = 'token'
        mock_request_helper.return_value.post.return_value = {'exception': 'bad'}
        self.assertEqual(service._fetch_course_data_from_web_api(123), {})

        mock_request_helper.return_value.post.side_effect = RuntimeError('api failed')
        self.assertEqual(service._fetch_course_data_from_web_api(456), {})

    def test_build_course_from_web_api_data_extracts_files_and_positions_non_system_files(self):
        service = make_download_service()
        course_data = {
            'sections': [
                {
                    'id': 101,
                    'name': 'Week A',
                    'modules': [
                        {
                            'id': 201,
                            'name': 'Slides',
                            'modname': 'resource',
                            'contents': [
                                {'content': '<p>inline html</p>'},
                                {
                                    'fileurl': 'https://example.test/lecture.pdf',
                                    'filename': 'lecture.pdf',
                                    'filesize': 42,
                                    'timemodified': 1700000001,
                                    'fileid': '500',
                                },
                                {
                                    'fileurl': 'https://example.test/metadata.json',
                                    'filename': 'metadata.json',
                                    'fileid': 'not-an-int',
                                },
                                {
                                    'fileurl': 'https://example.test/worksheet.docx',
                                    'filename': 'worksheet.docx',
                                },
                            ],
                        }
                    ],
                }
            ]
        }

        course = service._build_course_from_web_api_data(77, {}, course_data)

        self.assertEqual(course.id, 77)
        self.assertEqual(course.fullname.replace('_', ' '), 'Course 77')
        self.assertEqual([file.content_filename for file in course.files], [
            'lecture.pdf',
            'metadata.json',
            'worksheet.docx',
        ])
        self.assertEqual(course.files[0].file_id, 500)
        self.assertEqual(course.files[0].position_in_section, 0)
        self.assertIsNone(course.files[1].file_id)
        self.assertIsNone(course.files[1].position_in_section)
        self.assertEqual(course.files[2].position_in_section, 1)

    def test_system_file_detection_and_section_filtering(self):
        self.assertTrue(DownloadService._is_system_file_from_web_api('.hidden'))
        self.assertTrue(DownloadService._is_system_file_from_web_api('metadata.json'))
        self.assertTrue(DownloadService._is_system_file_from_web_api('lesson_info'))
        self.assertTrue(DownloadService._is_system_file_from_web_api('notes.json'))
        self.assertTrue(DownloadService._is_system_file_from_web_api('grade'))
        self.assertFalse(DownloadService._is_system_file_from_web_api('lecture.pdf'))

        keep = make_file('keep.pdf', section_id=1)
        drop = make_file('drop.pdf', section_id=2)
        self.assertEqual(
            DownloadService._filter_files_by_excluded_sections([keep, drop], []),
            [keep, drop],
        )
        self.assertEqual(
            DownloadService._filter_files_by_excluded_sections([keep, drop], [2]),
            [keep],
        )

    def test_get_failed_tasks_returns_only_failed_statuses(self):
        service = make_download_service()
        failed = SimpleNamespace(status=SimpleNamespace(state=TaskState.FAILED))
        finished = SimpleNamespace(status=SimpleNamespace(state=TaskState.FINISHED))
        service.all_tasks = [failed, finished]

        self.assertEqual(service.get_failed_tasks(), [failed])


class TestMoodleServiceOrchestration(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.opts = MagicMock()
        self.service = MoodleService(self.config, self.opts)

    def test_get_courses_list_filters_enrolled_courses_and_adds_public_courses(self):
        self.config.get_download_course_ids.return_value = [1]
        self.config.get_download_public_course_ids.return_value = [99]
        self.config.get_dont_download_course_ids.return_value = []
        self.config.has_property.side_effect = lambda prop: prop == 'download_course_ids'
        enrolled = [Course(1, 'Keep'), Course(2, 'Skip')]
        public = [Course(99, 'Public')]
        core_handler = MagicMock()
        core_handler.fetch_courses.return_value = enrolled
        core_handler.fetch_courses_info.return_value = public

        result = self.service.get_courses_list(core_handler, user_id=42)

        self.assertEqual([course.id for course in result], [1, 99])
        core_handler.fetch_courses.assert_called_once_with(42)
        core_handler.fetch_courses_info.assert_called_once_with([99])

    def test_get_courses_list_uses_blacklist_when_configured(self):
        self.config.get_download_course_ids.return_value = []
        self.config.get_download_public_course_ids.return_value = []
        self.config.get_dont_download_course_ids.return_value = [2]
        self.config.has_property.side_effect = lambda prop: prop == 'dont_download_course_ids'
        core_handler = MagicMock()
        core_handler.fetch_courses.return_value = [Course(1, 'Keep'), Course(2, 'Drop')]
        core_handler.fetch_courses_info.return_value = []

        result = self.service.get_courses_list(core_handler, user_id=42)

        self.assertEqual([course.id for course in result], [1])

    def test_get_user_id_and_version_fetches_missing_and_converts_configured_values(self):
        core_handler = MagicMock()
        core_handler.fetch_userid_and_version.return_value = (123, 2024010100)
        self.config.get_userid_and_version.return_value = (None, None)
        self.assertEqual(self.service.get_user_id_and_version(core_handler), (123, 2024010100))

        core_handler.fetch_userid_and_version.reset_mock()
        self.config.get_userid_and_version.return_value = ('456', 2024020200)
        self.assertEqual(self.service.get_user_id_and_version(core_handler), (456, 2024020200))
        self.assertEqual(core_handler.version, 2024020200)
        core_handler.fetch_userid_and_version.assert_not_called()

        core_handler.fetch_userid_and_version.return_value = (789, 2024030300)
        self.config.get_userid_and_version.return_value = ('not-int', 2024020200)
        self.assertEqual(self.service.get_user_id_and_version(core_handler), (789, 2024030300))

    def test_fetch_state_orchestrates_all_phases(self):
        database = MagicMock()
        request_helper = MagicMock()
        core_handler = MagicMock()
        courses = [Course(1, 'Course')]
        changes = [Course(1, 'Changed')]
        self.service._initialize_handlers = AsyncMock(return_value=(request_helper, core_handler, 7, 2024010100))
        self.service._setup_cookie_handler = MagicMock(return_value='cookie-handler')
        self.service.get_courses_list = MagicMock(return_value=courses)
        self.service._load_course_contents_and_modules = AsyncMock()
        self.service._merge_results_and_add_blocks = AsyncMock()
        self.service._detect_and_filter_changes = MagicMock(return_value=changes)

        result = asyncio.run(self.service.fetch_state(database))

        self.assertEqual(result, changes)
        self.service.get_courses_list.assert_called_once_with(core_handler, 7)
        self.service._load_course_contents_and_modules.assert_awaited_once_with(
            core_handler, courses, 7, database, request_helper
        )
        self.service._merge_results_and_add_blocks.assert_awaited_once_with(
            core_handler, courses, request_helper
        )
        self.service._detect_and_filter_changes.assert_called_once_with(
            database, courses, 'cookie-handler'
        )

    def test_initialize_handlers_creates_request_and_core_handlers(self):
        self.config.get_token.return_value = 'token'
        self.config.get_moodle_URL.return_value = MoodleURL(False, 'moodle.test', '/')
        self.service.get_user_id_and_version = MagicMock(return_value=(7, 2024010100))

        with patch('moodle_dl.moodle.moodle_service.RequestHelper') as mock_request:
            with patch('moodle_dl.moodle.moodle_service.CoreHandler') as mock_core:
                result = asyncio.run(self.service._initialize_handlers())

        request_helper = mock_request.return_value
        core_handler = mock_core.return_value
        self.assertEqual(result, (request_helper, core_handler, 7, 2024010100))
        mock_request.assert_called_once_with(
            self.config, self.opts, self.config.get_moodle_URL.return_value, 'token'
        )
        mock_core.assert_called_once_with(request_helper)
        self.service.get_user_id_and_version.assert_called_once_with(core_handler)

    def test_setup_cookie_handler_is_optional(self):
        request_helper = MagicMock()
        self.config.get_download_also_with_cookie.return_value = False
        self.assertIsNone(self.service._setup_cookie_handler(request_helper, 2024010100, 7))

        self.config.get_download_also_with_cookie.return_value = True
        self.config.get_privatetoken.return_value = 'private'
        with patch('moodle_dl.moodle.moodle_service.CookieHandler') as mock_cookie:
            cookie_handler = self.service._setup_cookie_handler(request_helper, 2024010100, 7)

        self.assertEqual(cookie_handler, mock_cookie.return_value)
        mock_cookie.assert_called_once_with(request_helper, 2024010100, self.config, self.opts)
        cookie_handler.check_and_fetch_cookies.assert_called_once_with('private', 7)

    def test_load_course_contents_and_modules_merges_core_and_mod_results(self):
        self.config.get_moodle_URL.return_value = MoodleURL(False, 'moodle.test', '/')
        self.config.get_token.return_value = 'token'
        core_handler = MagicMock()
        core_handler.async_load_core_contents = AsyncMock(return_value={'core': 'contents'})
        database = MagicMock()
        database.get_last_timestamp_per_mod_module.return_value = {'resource': 123}
        request_helper = MagicMock()
        courses = [Course(1, 'Course')]
        self.service._log_kalvidres_count_after_merge = MagicMock()

        with patch('moodle_dl.moodle.moodle_service.get_all_mods', return_value=['mod']) as mock_get_all_mods:
            with patch('moodle_dl.moodle.moodle_service.fetch_mods_files', new_callable=AsyncMock) as mock_fetch_mods:
                with patch('moodle_dl.moodle.moodle_service.get_mod_plurals', return_value={'resource': 'resources'}):
                    with patch('moodle_dl.moodle.moodle_service.ResultBuilder') as mock_builder:
                        mock_fetch_mods.return_value = {'resource': ['file']}
                        asyncio.run(
                            self.service._load_course_contents_and_modules(
                                core_handler, courses, 7, database, request_helper
                            )
                        )

        mock_get_all_mods.assert_called_once_with(
            request_helper, 1, 7, {'resource': 123}, self.config
        )
        mock_fetch_mods.assert_awaited_once_with(['mod'], courses, {'core': 'contents'})
        mock_builder.assert_called_once_with(
            self.config.get_moodle_URL.return_value, 1, {'resource': 'resources'}, token='token'
        )
        mock_builder.return_value.add_files_to_courses.assert_called_once_with(
            courses, {'core': 'contents'}, {'resource': ['file']}
        )
        self.service._log_kalvidres_count_after_merge.assert_called_once_with(
            courses, 'AFTER add_files_to_courses()'
        )

    def test_merge_results_and_add_blocks_adds_blocks_and_continues_after_error(self):
        self.config.get_moodle_URL.return_value = MoodleURL(False, 'moodle.test', '/')
        self.config.get_token.return_value = 'token'
        courses = [Course(1, 'No Blocks'), Course(2, 'With Blocks'), Course(3, 'Broken')]
        core_handler = MagicMock()
        core_handler.fetch_course_blocks.side_effect = [
            [],
            [{'type': 'calendar'}],
            RuntimeError('block api failed'),
        ]

        with patch('moodle_dl.moodle.moodle_service.get_mod_plurals', return_value={}):
            with patch('moodle_dl.moodle.moodle_service.ResultBuilder') as mock_builder:
                asyncio.run(
                    self.service._merge_results_and_add_blocks(core_handler, courses, MagicMock())
                )

        mock_builder.return_value.add_blocks_to_course.assert_called_once_with(
            courses[1], [{'type': 'calendar'}]
        )

    def test_log_kalvidres_count_after_merge_logs_when_kaltura_files_exist(self):
        course = Course(1, 'Media Course', [make_file('video.mp4', modname='cookie_mod-kalvidres')])

        with patch('moodle_dl.moodle.moodle_service.logging') as mock_logging:
            self.service._log_kalvidres_count_after_merge([course], 'after merge')

        mock_logging.info.assert_called_once()
        self.assertIn('Kaltura videos', mock_logging.info.call_args.args[0])

    def test_detect_and_filter_changes_applies_options_and_filtering(self):
        database = MagicMock()
        change = Course(1, 'Changed', [make_file('video.mp4', modname='cookie_mod-kalvidres')])
        database.changes_of_new_version.return_value = [change]
        optioned = [change]
        filtered = [Course(1, 'Filtered')]
        self.service.add_options_to_courses = MagicMock(return_value=optioned)
        self.service.filter_courses = MagicMock(return_value=filtered)

        courses = [Course(1, 'Course')]
        with patch('moodle_dl.moodle.moodle_service.logging'):
            result = self.service._detect_and_filter_changes(database, courses, 'cookie')

        self.assertEqual(result, filtered)
        self.service.add_options_to_courses.assert_called_once_with([change])
        self.service.filter_courses.assert_called_once_with(
            optioned, self.config, 'cookie', courses
        )


if __name__ == '__main__':
    unittest.main()
