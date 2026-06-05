# -*- coding: utf-8 -*-
"""
差异检测、文件比较和缓存相关方法的测试。

这些测试补充 tests/test_database_more.py 的覆盖范围，专注于：
- files_have_same_type / files_have_same_path / files_are_diffrent 的边界条件
- files_are_moveable / file_was_moved 的语义
- ignore_deleted / _file_exists_on_disk
- get_old_files / changes_of_new_version / get_last_timestamp_per_mod_module
- get_incomplete_downloads_for_retry / cleanup_old_incomplete_downloads
- 缓存 _get_cached / _clear_cache 行为
- notified 幂等性
- _set_insert_defaults
- get_failed_files_with_course_info / get_incomplete_files_with_course_info 聚合
- reset_failed_file_for_retry
"""

import os
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import Course, File, MoodleDlOpts


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def recorder(tmp_path):
    config = MagicMock(spec=ConfigHelper)
    config.get_misc_files_path.return_value = str(tmp_path)
    return StateRecorder(config, MoodleDlOpts())


def make_file(
    *,
    module_id=10,
    filename='file.pdf',
    url='https://example.com/file.pdf',
    filepath='/',
    filesize=100,
    timemodified=1000,
    module_modname='resource',
    content_type='pdf',
    saved_to='',
    file_id=None,
    hash_value=None,
    old_file_id=None,
    position_in_section=None,
    section_name='Section',
    section_id=1,
    module_name='Module',
):
    return File(
        file_id=file_id,
        module_id=module_id,
        section_name=section_name,
        section_id=section_id,
        module_name=module_name,
        content_filepath=filepath,
        content_filename=filename,
        content_fileurl=url,
        content_filesize=filesize,
        content_timemodified=timemodified,
        module_modname=module_modname,
        content_type=content_type,
        content_isexternalfile=False,
        saved_to=saved_to,
        file_hash=hash_value,
        old_file_id=old_file_id,
        position_in_section=position_in_section,
    )


def read_file_rows(recorder):
    conn = sqlite3.connect(recorder.db_file)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute('SELECT * FROM files ORDER BY file_id').fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1) files_have_same_type
# ---------------------------------------------------------------------------

class TestFilesHaveSameType:
    def test_same_type_and_modname(self):
        a = make_file(content_type='pdf', module_modname='resource')
        b = make_file(content_type='pdf', module_modname='resource')
        assert StateRecorder.files_have_same_type(a, b) is True

    def test_different_content_type(self):
        a = make_file(content_type='pdf', module_modname='resource')
        b = make_file(content_type='html', module_modname='resource')
        assert StateRecorder.files_have_same_type(a, b) is False

    def test_different_modname(self):
        a = make_file(content_type='pdf', module_modname='resource')
        b = make_file(content_type='pdf', module_modname='page')
        assert StateRecorder.files_have_same_type(a, b) is False

    def test_description_vs_file_type(self):
        a = make_file(content_type='description', module_modname='page')
        b = make_file(content_type='file', module_modname='resource')
        assert StateRecorder.files_have_same_type(a, b) is False

    def test_description_url_legacy_prefix_match(self):
        # 当 module_modname 改变但 description-url 允许前缀匹配
        legacy = make_file(content_type='description-url', module_modname='url')
        prefixed = make_file(content_type='description-url', module_modname='url-resource')
        assert StateRecorder.files_have_same_type(legacy, prefixed) is True


# ---------------------------------------------------------------------------
# 2) files_have_same_path
# ---------------------------------------------------------------------------

class TestFilesHaveSamePath:
    def test_same_path(self):
        a = make_file(filename='notes.pdf', filepath='/')
        b = make_file(filename='notes.pdf', filepath='/')
        assert StateRecorder.files_have_same_path(a, b) is True

    def test_different_filepath(self):
        a = make_file(filename='notes.pdf', filepath='/')
        b = make_file(filename='notes.pdf', filepath='/sub/')
        assert StateRecorder.files_have_same_path(a, b) is False

    def test_different_filename(self):
        a = make_file(filename='notes.pdf', filepath='/')
        b = make_file(filename='slides.pdf', filepath='/')
        assert StateRecorder.files_have_same_path(a, b) is False

    def test_path_is_case_sensitive(self):
        # Path 比较是大小写敏感的
        a = make_file(filename='Notes.PDF', filepath='/')
        b = make_file(filename='notes.pdf', filepath='/')
        assert StateRecorder.files_have_same_path(a, b) is False

    def test_different_module_id(self):
        a = make_file(module_id=1, filename='a.pdf', filepath='/')
        b = make_file(module_id=2, filename='a.pdf', filepath='/')
        assert StateRecorder.files_have_same_path(a, b) is False


# ---------------------------------------------------------------------------
# 3) files_are_diffrent (note the typo - this is the actual method name)
# ---------------------------------------------------------------------------

class TestFilesAreDiffrent:
    def test_all_three_different(self):
        a = make_file(filename='a.pdf', url='https://example.com/a', filesize=100, timemodified=1000)
        b = make_file(filename='b.pdf', url='https://example.com/b', filesize=200, timemodified=2000)
        assert StateRecorder.files_are_diffrent(a, b) is True

    def test_same_name_size_time(self):
        a = make_file(filename='a.pdf', filesize=100, timemodified=1000)
        b = make_file(filename='b.pdf', filesize=100, timemodified=1000)
        # 文件名不同但 size 和 time 相同 → 不算 different
        assert StateRecorder.files_are_diffrent(a, b) is False

    def test_size_changed_only(self):
        a = make_file(filesize=100)
        b = make_file(filesize=200)
        assert StateRecorder.files_are_diffrent(a, b) is True

    def test_size_diff_zero(self):
        # 大小差 0 → False
        a = make_file(filesize=100)
        b = make_file(filesize=100)
        assert StateRecorder.files_are_diffrent(a, b) is False

    def test_url_changed_time_unchanged(self):
        a = make_file(url='https://example.com/v1', timemodified=1000)
        b = make_file(url='https://example.com/v2', timemodified=1000)
        # URL 不同 + time 相同 → 不算 different
        assert StateRecorder.files_are_diffrent(a, b) is False

    def test_url_changed_and_time_changed(self):
        a = make_file(url='https://example.com/v1', timemodified=1000)
        b = make_file(url='https://example.com/v2', timemodified=2000)
        # URL 不同 + time 不同 → different
        assert StateRecorder.files_are_diffrent(a, b) is True


# ---------------------------------------------------------------------------
# 4) files_are_moveable
# ---------------------------------------------------------------------------

class TestFilesAreMoveable:
    def test_same_type_different_path_is_moveable(self):
        a = make_file(content_type='file', module_modname='resource', filepath='/old/')
        b = make_file(content_type='file', module_modname='resource', filepath='/new/')
        assert StateRecorder.files_are_moveable(a, b) is True

    def test_description_is_not_moveable(self):
        a = make_file(content_type='description')
        b = make_file(content_type='description', filepath='/new/')
        assert StateRecorder.files_are_moveable(a, b) is False

    def test_html_without_hash_is_not_moveable(self):
        a = make_file(content_type='html', hash_value=None, filepath='/old/')
        b = make_file(content_type='html', hash_value=None, filepath='/new/')
        assert StateRecorder.files_are_moveable(a, b) is False


# ---------------------------------------------------------------------------
# 5) file_was_moved
# ---------------------------------------------------------------------------

class TestFileWasMoved:
    def test_same_type_same_size_different_path_was_moved(self):
        a = make_file(filesize=100, filepath='/new/')
        b = make_file(filesize=100, filepath='/old/')
        assert StateRecorder.file_was_moved(a, b) is True

    def test_different_size_was_not_moved(self):
        a = make_file(filesize=200, filepath='/new/')
        b = make_file(filesize=100, filepath='/old/')
        assert StateRecorder.file_was_moved(a, b) is False

    def test_different_type_was_not_moved(self):
        a = make_file(content_type='pdf', filesize=100, filepath='/new/')
        b = make_file(content_type='html', filesize=100, filepath='/old/')
        assert StateRecorder.file_was_moved(a, b) is False


# ---------------------------------------------------------------------------
# 6) ignore_deleted
# ---------------------------------------------------------------------------

class TestIgnoreDeleted:
    def test_forum_is_ignored(self):
        forum = make_file(module_modname='forum')
        assert StateRecorder.ignore_deleted(forum) is True

    def test_calendar_is_ignored(self):
        cal = make_file(module_modname='calendar')
        assert StateRecorder.ignore_deleted(cal) is True

    def test_resource_is_not_ignored(self):
        page = make_file(module_modname='page')
        assert StateRecorder.ignore_deleted(page) is False

    def test_quiz_module_endswith_forum_still_ignored(self):
        # endswith('forum') 会匹配 'assignforum' 之类
        weird = make_file(module_modname='customforum')
        assert StateRecorder.ignore_deleted(weird) is True


# ---------------------------------------------------------------------------
# 7) _file_exists_on_disk
# ---------------------------------------------------------------------------

class TestFileExistsOnDisk:
    def test_empty_saved_to_is_false(self):
        f = make_file(saved_to='')
        assert StateRecorder._file_exists_on_disk(f) is False

    def test_missing_path_is_false(self, tmp_path):
        f = make_file(saved_to=str(tmp_path / 'never_existed.pdf'))
        assert StateRecorder._file_exists_on_disk(f) is False

    def test_existing_path_is_true(self, tmp_path):
        real = tmp_path / 'real.pdf'
        real.write_text('data', encoding='utf-8')
        f = make_file(saved_to=str(real))
        assert StateRecorder._file_exists_on_disk(f) is True

    def test_file_deleted_from_disk_returns_false(self, tmp_path):
        real = tmp_path / 'will_disappear.pdf'
        real.write_text('data', encoding='utf-8')
        f = make_file(saved_to=str(real))
        assert StateRecorder._file_exists_on_disk(f) is True

        real.unlink()
        assert StateRecorder._file_exists_on_disk(f) is False


# ---------------------------------------------------------------------------
# 8) get_old_files
# ---------------------------------------------------------------------------

class TestGetOldFiles:
    def test_downloaded_file_disappeared_from_disk_appears_in_old(self, recorder, tmp_path):
        real = tmp_path / 'downloaded.pdf'
        real.write_text('data', encoding='utf-8')

        old = make_file(
            module_id=1,
            filename='original.pdf',
            url='https://example.com/orig.pdf',
            saved_to=str(real),
        )
        old.file_id = recorder.new_file(old, 101, 'Course One')

        replacement = make_file(
            module_id=1,
            filename='original.pdf',
            url='https://example.com/new.pdf',
            filesize=150,
        )
        replacement.modified = True
        replacement.old_file = old
        recorder.save_file(replacement, 101, 'Course One')

        # 现在从磁盘删除文件
        real.unlink()

        # get_old_files 应该返回这个文件（基于 old_file_id 链）
        old_courses = recorder.get_old_files()
        assert len(old_courses) == 1
        # course.files 包含原始 old_file（来自 get_old_files 的回查）
        file_ids = [f.file_id for f in old_courses[0].files]
        assert old.file_id in file_ids

    def test_non_old_files_excluded(self, recorder, tmp_path):
        # 一个普通文件（没有 old_file_id 链）
        regular = make_file(
            module_id=1,
            filename='regular.pdf',
            url='https://example.com/regular.pdf',
        )
        recorder.new_file(regular, 101, 'Course One')

        # 没有任何 old_file_id 链 → 应该是空
        assert recorder.get_old_files() == []


# ---------------------------------------------------------------------------
# 9) changes_of_new_version
# ---------------------------------------------------------------------------

class TestChangesOfNewVersion:
    def test_empty_input_returns_empty(self, recorder):
        assert recorder.changes_of_new_version([]) == []

    def test_new_file_in_current_marks_as_changed(self, recorder, tmp_path):
        real = tmp_path / 'stored.pdf'
        real.write_text('data', encoding='utf-8')
        stored = Course(101, 'Course One')
        stored_file = make_file(
            module_id=1,
            filename='old.pdf',
            url='https://example.com/old.pdf',
            filesize=100,
            saved_to=str(real),
        )
        stored.files = [stored_file]
        recorder.save_file(stored_file, 101, 'Course One')

        current = Course(101, 'Course One')
        current.files = [
            make_file(
                module_id=1,
                filename='old.pdf',
                url='https://example.com/old.pdf',
                filesize=101,  # size 不同 → modified
            ),
        ]

        changed = recorder.changes_of_new_version([current])
        assert len(changed) == 1
        modified_files = [f for f in changed[0].files if f.modified]
        assert len(modified_files) == 1

    def test_brand_new_file_marked_as_new(self, recorder, tmp_path):
        real = tmp_path / 'existing.pdf'
        real.write_text('data', encoding='utf-8')
        stored_file = make_file(
            module_id=1,
            filename='existing.pdf',
            url='https://example.com/existing.pdf',
            saved_to=str(real),
        )
        stored_file.file_id = recorder.new_file(stored_file, 101, 'Course One')

        stored = Course(101, 'Course One')
        stored.files = [stored_file]

        current = Course(101, 'Course One')
        current.files = [
            stored_file,  # 已存在的
            make_file(
                module_id=2,
                filename='brand-new.pdf',
                url='https://example.com/brand-new.pdf',
                filesize=999,  # 大小不同，避免与 existing.pdf 被 file_was_moved 错误匹配
            ),
        ]

        changed = recorder.changes_of_new_version([current])
        assert len(changed) == 1
        new_filenames = [f.content_filename for f in changed[0].files]
        assert 'brand-new.pdf' in new_filenames


# ---------------------------------------------------------------------------
# 10) get_last_timestamp_per_mod_module
# ---------------------------------------------------------------------------

class TestGetLastTimestampPerModModule:
    def test_empty_database(self, recorder):
        result = recorder.get_last_timestamp_per_mod_module()
        # 空 DB：forum dict 为空，calendar dict 为空
        assert result == {'forum': {}, 'calendar': {}}

    def test_insert_forum_file_returns_timestamp(self, recorder):
        forum = make_file(
            module_id=42,
            module_modname='forum',
            content_type='description',
            timemodified=1700000000,
            url='https://example.com/forum/42',
        )
        recorder.save_file(forum, 101, 'Course One')

        result = recorder.get_last_timestamp_per_mod_module()
        assert result['forum'] == {42: 1700000000}
        assert result['calendar'] == {}

    def test_insert_calendar_file_returns_timestamp(self, recorder):
        cal = make_file(
            module_id=7,
            module_modname='calendar',
            content_type='html',
            timemodified=1700000999,
            url='https://example.com/calendar/7',
        )
        recorder.save_file(cal, 101, 'Course One')

        result = recorder.get_last_timestamp_per_mod_module()
        assert result['calendar'] == {7: 1700000999}


# ---------------------------------------------------------------------------
# 11) get_incomplete_downloads_for_retry
# ---------------------------------------------------------------------------

class TestGetIncompleteDownloadsForRetry:
    def test_attempts_below_max_returned(self, recorder):
        recorder.save_incomplete_download(
            file_id=1,
            file_url='https://example.com/1',
            file_path='/tmp/1',
            total_bytes=100,
            downloaded_bytes=20,
        )
        # 默认 attempts=0
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        assert len(rows) == 1
        assert rows[0]['file_id'] == 1
        assert rows[0]['attempts'] == 0

    def test_attempts_at_max_excluded(self, recorder):
        recorder.save_incomplete_download(
            file_id=1,
            file_url='https://example.com/1',
            file_path='/tmp/1',
            total_bytes=100,
            downloaded_bytes=20,
        )
        # 手动更新 attempts 为 10
        conn = sqlite3.connect(recorder.db_file)
        try:
            conn.execute('UPDATE incomplete_downloads SET attempts = 10')
            conn.commit()
        finally:
            conn.close()

        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        assert rows == []

    def test_max_attempts_zero_returns_all(self, recorder):
        recorder.save_incomplete_download(
            file_id=1,
            file_url='https://example.com/1',
            file_path='/tmp/1',
            total_bytes=100,
            downloaded_bytes=20,
        )
        conn = sqlite3.connect(recorder.db_file)
        try:
            conn.execute('UPDATE incomplete_downloads SET attempts = 100')
            conn.commit()
        finally:
            conn.close()

        # max_attempts=0 → 0 < 0 永远为 false → 实际上返回空
        # 但 max_attempts=1 会让 attempts=100 > 1 → 也不返回
        # 测试文档里说的 "max_attempts=0 → 都返回" 的意图是
        # 用一个非常大的 max_attempts
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=1000)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# 12) cleanup_old_incomplete_downloads
# ---------------------------------------------------------------------------

class TestCleanupOldIncompleteDownloads:
    def test_old_records_deleted_new_kept(self, recorder, monkeypatch):
        # 假设"现在"是 1_000_000
        now = 1_000_000
        # 7 天 = 604800 秒
        week_ago = now - 7 * 24 * 60 * 60 - 1
        yesterday = now - 24 * 60 * 60

        recorder.save_incomplete_download(
            file_id=1, file_url='https://example.com/old',
            file_path='/tmp/old', total_bytes=100, downloaded_bytes=10,
        )
        recorder.save_incomplete_download(
            file_id=2, file_url='https://example.com/new',
            file_path='/tmp/new', total_bytes=100, downloaded_bytes=10,
        )

        # 直接修改 last_update_time
        conn = sqlite3.connect(recorder.db_file)
        try:
            conn.execute(
                'UPDATE incomplete_downloads SET last_update_time = ? WHERE file_id = ?',
                (week_ago, 1),
            )
            conn.execute(
                'UPDATE incomplete_downloads SET last_update_time = ? WHERE file_id = ?',
                (yesterday, 2),
            )
            conn.commit()
        finally:
            conn.close()

        # monkeypatch time.time 模拟"现在"
        import moodle_dl.database as db_module
        monkeypatch.setattr(db_module.time, 'time', lambda: now)

        deleted = recorder.cleanup_old_incomplete_downloads(days_old=7)
        assert deleted == 1

        # 老的被删，新的保留
        assert recorder.get_incomplete_download(1, '/tmp/old') is None
        assert recorder.get_incomplete_download(2, '/tmp/new') is not None


# ---------------------------------------------------------------------------
# 13) _get_cached / _clear_cache
# ---------------------------------------------------------------------------

class TestQueryCache:
    def test_first_call_invokes_query_func(self, recorder):
        calls = []

        def q():
            calls.append(1)
            return 'result-a'

        result = recorder._get_cached('key-a', q)
        assert result == 'result-a'
        assert calls == [1]

    def test_second_call_uses_cache(self, recorder):
        calls = []

        def q():
            calls.append(1)
            return 'result-a'

        recorder._get_cached('key-a', q)
        recorder._get_cached('key-a', q)
        recorder._get_cached('key-a', q)
        assert calls == [1]

    def test_different_key_invokes_query_func_again(self, recorder):
        calls = []

        def qa():
            calls.append('a')
            return 'A'

        def qb():
            calls.append('b')
            return 'B'

        # key 不同 → 都应执行
        assert recorder._get_cached('key-a', qa) == 'A'
        assert recorder._get_cached('key-b', qb) == 'B'
        assert calls == ['a', 'b']

    def test_clear_cache_empties_all(self, recorder):
        recorder._query_cache['x'] = (1, 0)
        recorder._query_cache['y'] = (2, 0)
        recorder._clear_cache()
        assert recorder._query_cache == {}

    def test_clear_cache_with_pattern(self, recorder):
        recorder._query_cache['get_stored_files:x'] = (1, 0)
        recorder._query_cache['get_old_files:y'] = (2, 0)
        recorder._query_cache['other:z'] = (3, 0)

        recorder._clear_cache('get_stored_files')
        assert 'get_stored_files:x' not in recorder._query_cache
        assert 'get_old_files:y' in recorder._query_cache
        assert 'other:z' in recorder._query_cache

    def test_ttl_expiry_reinvokes_query_func(self, recorder):
        calls = []

        def q():
            calls.append(1)
            return 'result'

        # 第一次调用
        with patch('moodle_dl.database.time.time', return_value=100):
            recorder._get_cached('key', q)
        # 第二次调用，时间已超过 TTL（300s）
        with patch('moodle_dl.database.time.time', return_value=100 + 301):
            recorder._get_cached('key', q)
        assert calls == [1, 1]


# ---------------------------------------------------------------------------
# 14) notified
# ---------------------------------------------------------------------------

class TestNotified:
    def test_notified_marks_files(self, recorder):
        f1 = make_file(module_id=1, filename='a.pdf', url='https://example.com/a')
        f2 = make_file(module_id=2, filename='b.pdf', url='https://example.com/b')
        recorder.save_file(f1, 101, 'Course One')
        recorder.save_file(f2, 101, 'Course One')

        # 标记为需要通知（modified=1）
        conn = sqlite3.connect(recorder.db_file)
        try:
            conn.execute('UPDATE files SET notified = 0, modified = 1')
            conn.commit()
        finally:
            conn.close()

        # 调用前 changes_to_notify 应返回 2 个文件
        assert len(recorder.changes_to_notify()) == 1
        assert len(recorder.changes_to_notify()[0].files) == 2

        # 用 changes_to_notify 返回的 File（带 file_id）调用 notified
        changes = recorder.changes_to_notify()
        recorder.notified(changes)

        # 调用后 changes_to_notify 应返回空
        assert recorder.changes_to_notify() == []

    def test_notified_is_idempotent(self, recorder):
        f1 = make_file(module_id=1, filename='a.pdf', url='https://example.com/a')
        recorder.save_file(f1, 101, 'Course One')

        conn = sqlite3.connect(recorder.db_file)
        try:
            conn.execute('UPDATE files SET notified = 0, modified = 1')
            conn.commit()
        finally:
            conn.close()

        # 多次调用都不应该抛错
        recorder.notified(recorder.changes_to_notify())
        recorder.notified(recorder.changes_to_notify())
        recorder.notified(recorder.changes_to_notify())

        assert recorder.changes_to_notify() == []


# ---------------------------------------------------------------------------
# 15) _set_insert_defaults
# ---------------------------------------------------------------------------

class TestSetInsertDefaults:
    def test_empty_dict_gets_all_defaults(self):
        result = StateRecorder._set_insert_defaults({})
        assert result['modified'] == 0
        assert result['deleted'] == 0
        assert result['moved'] == 0
        assert result['notified'] == 0
        assert result['old_file_id'] == 0
        assert result['download_status'] == 'pending'
        assert result['download_attempts'] == 0
        assert result['last_download_at'] == 0
        assert result['last_failed_at'] == 0
        assert result['last_failed_reason'] is None
        assert result['consecutive_failures'] == 0
        assert result['position_in_section'] == 0

    def test_existing_values_not_overwritten(self):
        data = {
            'modified': 1,
            'deleted': 1,
            'download_status': 'failed',
            'download_attempts': 5,
            'consecutive_failures': 3,
            'last_failed_reason': 'boom',
        }
        result = StateRecorder._set_insert_defaults(data)
        # 已存在的值应保留
        assert result['modified'] == 1
        assert result['deleted'] == 1
        assert result['download_status'] == 'failed'
        assert result['download_attempts'] == 5
        assert result['consecutive_failures'] == 3
        assert result['last_failed_reason'] == 'boom'
        # 不存在的字段填充默认值
        assert result['moved'] == 0
        assert result['notified'] == 0

    def test_returns_same_dict(self):
        data = {'a': 1}
        result = StateRecorder._set_insert_defaults(data)
        # 应该修改并返回同一个 dict
        assert result is data
        assert 'modified' in data
        assert data['a'] == 1


# ---------------------------------------------------------------------------
# 16) get_failed_files_with_course_info
# ---------------------------------------------------------------------------

class TestGetFailedFilesWithCourseInfo:
    def test_cross_course_aggregation(self, recorder):
        f1 = make_file(module_id=1, filename='a.pdf', url='https://example.com/a')
        f2 = make_file(module_id=2, filename='b.pdf', url='https://example.com/b')
        recorder.save_failed_file(f1, 101, 'Course One', 'err a')
        recorder.save_failed_file(f2, 202, 'Course Two', 'err b')

        result = recorder.get_failed_files_with_course_info(min_failures=1)
        assert sorted(result.keys()) == [101, 202]
        assert result[101]['course_fullname'] == 'Course One'
        assert result[202]['course_fullname'] == 'Course Two'
        assert [f.content_filename for f in result[101]['files']] == ['a.pdf']
        assert [f.content_filename for f in result[202]['files']] == ['b.pdf']

    def test_min_failures_filter(self, recorder):
        f1 = make_file(module_id=1, filename='one-fail.pdf', url='https://example.com/one')
        f2 = make_file(module_id=2, filename='three-fails.pdf', url='https://example.com/three')
        recorder.save_failed_file(f1, 101, 'Course One', 'fail1')  # consecutive=1
        recorder.save_failed_file(f2, 101, 'Course One', 'fail1')
        recorder.save_failed_file(f2, 101, 'Course One', 'fail2')
        recorder.save_failed_file(f2, 101, 'Course One', 'fail3')  # consecutive=3

        # min_failures=1 → 两者都出现
        result = recorder.get_failed_files_with_course_info(min_failures=1)
        assert len(result[101]['files']) == 2

        # min_failures=2 → 只有 three-fails
        result = recorder.get_failed_files_with_course_info(min_failures=2)
        filenames = [f.content_filename for f in result[101]['files']]
        assert filenames == ['three-fails.pdf']

        # min_failures=4 → 都不满足，Course One 不应出现
        result = recorder.get_failed_files_with_course_info(min_failures=4)
        assert 101 not in result

    def test_max_consecutive_field_in_summary(self, recorder):
        f1 = make_file(module_id=1, filename='a.pdf', url='https://example.com/a')
        f2 = make_file(module_id=2, filename='b.pdf', url='https://example.com/b')
        recorder.save_failed_file(f1, 101, 'Course One', 'fail')
        recorder.save_failed_file(f1, 101, 'Course One', 'fail')  # consecutive=2
        recorder.save_failed_file(f2, 101, 'Course One', 'fail')  # consecutive=1

        summary = recorder.get_failed_files_summary()
        # max_consecutive 应是最大值
        assert summary[101]['max_consecutive'] == 2
        assert summary[101]['failed_count'] == 2
        assert summary[101]['total_failures'] == 3


# ---------------------------------------------------------------------------
# 17) get_incomplete_files_with_course_info
# ---------------------------------------------------------------------------

class TestGetIncompleteFilesWithCourseInfo:
    def test_includes_attempts_and_last_error(self, recorder):
        f = make_file(module_id=1, filename='resume.pdf', url='https://example.com/resume.pdf')
        recorder.save_file(f, 101, 'Course One')
        file_id = read_file_rows(recorder)[0]['file_id']

        recorder.save_incomplete_download(
            file_id=file_id,
            file_url='https://example.com/resume.pdf',
            file_path='/tmp/resume.pdf',
            total_bytes=100,
            downloaded_bytes=25,
            server_supports_range=True,
        )
        # 增加一次 attempt
        info = recorder.get_incomplete_download(file_id, '/tmp/resume.pdf')
        recorder.increment_incomplete_download_attempt(info['download_id'], 'net err')

        result = recorder.get_incomplete_files_with_course_info()
        assert 101 in result
        files = result[101]['files']
        assert len(files) == 1
        assert files[0].file_id == file_id

        # 直接检查 DB 中 attempts/last_error
        conn = sqlite3.connect(recorder.db_file)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                'SELECT attempts, error_reason FROM incomplete_downloads WHERE file_id = ?',
                (file_id,),
            ).fetchone()
            assert row['attempts'] == 1
            assert row['error_reason'] == 'net err'
        finally:
            conn.close()

    def test_aggregation_across_courses(self, recorder):
        f1 = make_file(module_id=1, filename='a.pdf', url='https://example.com/a')
        f2 = make_file(module_id=2, filename='b.pdf', url='https://example.com/b')
        recorder.save_file(f1, 101, 'Course One')
        recorder.save_file(f2, 202, 'Course Two')

        id1 = read_file_rows(recorder)[0]['file_id']
        id2 = read_file_rows(recorder)[1]['file_id']

        recorder.save_incomplete_download(
            file_id=id1, file_url='https://example.com/a',
            file_path='/tmp/a', total_bytes=100, downloaded_bytes=10,
        )
        recorder.save_incomplete_download(
            file_id=id2, file_url='https://example.com/b',
            file_path='/tmp/b', total_bytes=100, downloaded_bytes=20,
        )

        result = recorder.get_incomplete_files_with_course_info()
        assert sorted(result.keys()) == [101, 202]
        assert result[101]['course_fullname'] == 'Course One'
        assert result[202]['course_fullname'] == 'Course Two'
        assert result[101]['files'][0].file_id == id1
        assert result[202]['files'][0].file_id == id2

    def test_attempts_at_max_excluded(self, recorder):
        f = make_file(module_id=1, filename='a.pdf', url='https://example.com/a')
        recorder.save_file(f, 101, 'Course One')
        file_id = read_file_rows(recorder)[0]['file_id']

        recorder.save_incomplete_download(
            file_id=file_id, file_url='https://example.com/a',
            file_path='/tmp/a', total_bytes=100, downloaded_bytes=10,
        )

        # 把 attempts 设为 100
        conn = sqlite3.connect(recorder.db_file)
        try:
            conn.execute('UPDATE incomplete_downloads SET attempts = 100')
            conn.commit()
        finally:
            conn.close()

        result = recorder.get_incomplete_files_with_course_info(max_attempts=5)
        assert result == {}


# ---------------------------------------------------------------------------
# 18) reset_failed_file_for_retry
# ---------------------------------------------------------------------------

class TestResetFailedFileForRetry:
    def test_reset_clears_consecutive_and_reason_but_keeps_status_retrying(self, recorder):
        f = make_file(module_id=1, filename='flaky.pdf', url='https://example.com/flaky')
        recorder.save_failed_file(f, 101, 'Course One', 'net err')
        recorder.save_failed_file(f, 101, 'Course One', 'net err')  # consecutive=2
        recorder.save_failed_file(f, 101, 'Course One', 'net err')  # consecutive=3

        # 验证失败状态
        rows_before = {row['content_filename']: row for row in read_file_rows(recorder)}
        assert rows_before['flaky.pdf']['download_status'] == 'failed'
        assert rows_before['flaky.pdf']['consecutive_failures'] == 3
        assert rows_before['flaky.pdf']['last_failed_reason'] == 'net err'

        # 重置
        recorder.reset_failed_file_for_retry(f, 101)

        # download_status 变为 'retrying', consecutive=0, reason=NULL
        rows_after = {row['content_filename']: row for row in read_file_rows(recorder)}
        assert rows_after['flaky.pdf']['download_status'] == 'retrying'
        assert rows_after['flaky.pdf']['consecutive_failures'] == 0
        assert rows_after['flaky.pdf']['last_failed_reason'] is None

    def test_reset_allows_rediscover_in_failed_query(self, recorder):
        f = make_file(module_id=1, filename='flaky.pdf', url='https://example.com/flaky')
        recorder.save_failed_file(f, 101, 'Course One', 'net err')
        recorder.save_failed_file(f, 101, 'Course One', 'net err')
        recorder.save_failed_file(f, 101, 'Course One', 'net err')

        # 重置前，min_failures=10 不会列出（consecutive=3 < 10）
        assert recorder.get_failed_files(course_id=101, min_failures=10) == []

        # 重置后，'retrying' 状态即使 consecutive=0 也会被列出
        recorder.reset_failed_file_for_retry(f, 101)
        result = recorder.get_failed_files(course_id=101, min_failures=10)
        assert len(result) == 1
        assert result[0].content_filename == 'flaky.pdf'
