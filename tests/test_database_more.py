# -*- coding: utf-8 -*-
import os
import sqlite3

import pytest
from unittest.mock import MagicMock, patch

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import Course, File, MoodleDlOpts


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
    saved_to='/tmp/file.pdf',
    file_id=None,
    hash_value=None,
    old_file_id=None,
    position_in_section=None,
):
    return File(
        file_id=file_id,
        module_id=module_id,
        section_name='Week 1',
        section_id=1,
        module_name='Module',
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


def test_file_comparison_helpers_cover_type_path_difference_and_movement(tmp_path):
    base = make_file(saved_to=str(tmp_path / 'base.pdf'), hash_value='same')
    same = make_file(saved_to=str(tmp_path / 'same.pdf'), hash_value='same')

    assert StateRecorder.files_have_same_type(base, same) is True
    assert StateRecorder.files_have_same_path(base, same) is True
    assert StateRecorder.files_are_diffrent(base, same) is False

    size_changed = make_file(filesize=101, hash_value='same')
    assert StateRecorder.files_are_diffrent(base, size_changed) is True

    description = make_file(content_type='description', filename='intro', hash_value='old')
    changed_description = make_file(content_type='description', filename='intro', hash_value='new')
    assert StateRecorder.files_are_diffrent(description, changed_description) is True
    assert StateRecorder.files_are_moveable(description, changed_description) is False

    html_without_hash = make_file(content_type='html', filename='page.html', hash_value=None)
    moved_html_without_hash = make_file(
        content_type='html',
        filename='page.html',
        filepath='/new/',
        hash_value=None,
    )
    assert StateRecorder.files_are_moveable(html_without_hash, moved_html_without_hash) is False

    moved = make_file(filepath='/new/', hash_value='same')
    assert StateRecorder.file_was_moved(moved, base) is True

    cookie_one = make_file(content_type='cookie_mod', module_modname='cookie_mod-kalvidres')
    cookie_two = make_file(
        content_type='cookie_mod',
        module_modname='cookie_mod-kalvidres',
        module_id=11,
        url='https://example.com/other',
    )
    assert StateRecorder.files_are_diffrent(cookie_one, cookie_two) is True

    cookie_size_changed = make_file(
        content_type='cookie_mod',
        module_modname='cookie_mod-kalvidres',
        filesize=2,
    )
    assert StateRecorder.files_are_diffrent(cookie_one, cookie_size_changed) is True
    assert StateRecorder.files_are_diffrent(cookie_one, make_file(
        content_type='cookie_mod',
        module_modname='cookie_mod-kalvidres',
    )) is False

    legacy_url = make_file(content_type='description-url', module_modname='url')
    prefixed_modname = make_file(
        content_type='description-url',
        module_modname='url-resource',
        url='https://example.com/changed-url',
    )
    assert StateRecorder.files_have_same_type(legacy_url, prefixed_modname) is True
    assert StateRecorder.files_are_diffrent(legacy_url, prefixed_modname) is True


def test_deleted_forum_calendar_files_are_ignored_and_disk_presence_is_checked(tmp_path):
    forum = make_file(module_modname='forum')
    calendar = make_file(module_modname='calendar')
    page = make_file(module_modname='page')

    assert StateRecorder.ignore_deleted(forum) is True
    assert StateRecorder.ignore_deleted(calendar) is True
    assert StateRecorder.ignore_deleted(page) is False

    missing = make_file(saved_to='')
    assert StateRecorder._file_exists_on_disk(missing) is False

    existing_path = tmp_path / 'downloaded.pdf'
    existing_path.write_text('data', encoding='utf-8')
    existing = make_file(saved_to=str(existing_path))
    assert StateRecorder._file_exists_on_disk(existing) is True


def test_insert_defaults_fill_download_tracking_and_extended_metadata():
    data = StateRecorder._set_insert_defaults({'download_status': 'failed', 'position_in_section': None})

    assert data['download_status'] == 'failed'
    assert data['position_in_section'] is None
    assert data['download_attempts'] == 0
    assert data['last_download_at'] == 0
    assert data['last_failed_reason'] is None
    assert data['consecutive_failures'] == 0
    assert data['modified'] == 0
    assert data['old_file_id'] == 0


def test_new_file_is_idempotent_and_cache_is_cleared_after_saving(recorder, tmp_path):
    first = make_file(
        module_id=1,
        filename='one.pdf',
        url='https://example.com/one.pdf',
        saved_to=str(tmp_path / 'one.pdf'),
    )
    first_id = recorder.save_file(first, course_id=101, course_fullname='Course One')
    duplicate_id = recorder.save_file(first, course_id=101, course_fullname='Course One')

    assert duplicate_id == first_id
    assert len(read_file_rows(recorder)) == 1

    stored = recorder.get_stored_files()
    assert [file.content_filename for file in stored[0].files] == ['one.pdf']

    second = make_file(
        module_id=2,
        filename='two.pdf',
        url='https://example.com/two.pdf',
        saved_to=str(tmp_path / 'two.pdf'),
    )
    recorder.save_file(second, course_id=101, course_fullname='Course One')

    refreshed = recorder.get_stored_files()
    assert [file.content_filename for file in refreshed[0].files] == ['one.pdf', 'two.pdf']


def test_modified_and_moved_files_preserve_old_references_for_notifications(recorder, tmp_path):
    old_modified_path = tmp_path / 'old-modified.pdf'
    old_modified_path.write_text('old data', encoding='utf-8')
    old_modified = make_file(
        module_id=1,
        filename='lecture.pdf',
        url='https://example.com/lecture-v1.pdf',
        saved_to=str(old_modified_path),
        hash_value='old',
    )
    old_modified.file_id = recorder.new_file(old_modified, 101, 'Course One')

    new_modified = make_file(
        module_id=1,
        filename='lecture.pdf',
        url='https://example.com/lecture-v2.pdf',
        saved_to=str(tmp_path / 'lecture-v2.pdf'),
        filesize=200,
        hash_value='new',
    )
    new_modified.modified = True
    new_modified.old_file = old_modified
    recorder.save_file(new_modified, 101, 'Course One')

    old_moved_path = tmp_path / 'old-moved.pdf'
    old_moved_path.write_text('move data', encoding='utf-8')
    old_moved = make_file(
        module_id=2,
        filename='slides.pdf',
        url='https://example.com/slides.pdf',
        filepath='/old/',
        saved_to=str(old_moved_path),
        hash_value='same',
    )
    old_moved.file_id = recorder.new_file(old_moved, 101, 'Course One')

    new_moved = make_file(
        module_id=2,
        filename='slides.pdf',
        url='https://example.com/slides.pdf',
        filepath='/new/',
        saved_to=str(tmp_path / 'slides.pdf'),
        hash_value='same',
    )
    new_moved.moved = True
    new_moved.old_file = old_moved
    recorder.save_file(new_moved, 101, 'Course One')

    rows = read_file_rows(recorder)
    rows_by_id = {row['file_id']: row for row in rows}
    assert rows_by_id[old_modified.file_id]['modified'] == 1
    assert rows_by_id[old_modified.file_id]['notified'] == 0
    assert rows_by_id[old_moved.file_id]['moved'] == 1
    assert rows_by_id[old_moved.file_id]['notified'] == 0

    replacement_rows = [row for row in rows if row['old_file_id']]
    assert {row['old_file_id'] for row in replacement_rows} == {old_modified.file_id, old_moved.file_id}
    assert all(row['notified'] == 1 for row in replacement_rows)

    changes = recorder.changes_to_notify()
    changed_files = {file.content_filename: file for file in changes[0].files}
    assert changed_files['lecture.pdf'].new_file.content_fileurl == 'https://example.com/lecture-v2.pdf'
    assert changed_files['slides.pdf'].new_file.content_filepath == '/new/'


def test_get_old_files_and_batch_delete_clear_replacement_references(recorder, tmp_path):
    old_file = make_file(
        module_id=7,
        filename='old.pdf',
        url='https://example.com/old-v1.pdf',
        saved_to=str(tmp_path / 'old.pdf'),
    )
    old_file.file_id = recorder.new_file(old_file, 101, 'Course One')

    replacement = make_file(
        module_id=7,
        filename='old.pdf',
        url='https://example.com/old-v2.pdf',
        saved_to=str(tmp_path / 'replacement.pdf'),
        filesize=120,
    )
    replacement.modified = True
    replacement.old_file = old_file
    recorder.save_file(replacement, 101, 'Course One')

    old_courses = recorder.get_old_files()
    assert len(old_courses) == 1
    assert old_courses[0].id == 101
    assert [file.file_id for file in old_courses[0].files] == [old_file.file_id]

    recorder.batch_delete_files_from_db(old_courses[0].files)

    rows = read_file_rows(recorder)
    assert len(rows) == 1
    assert rows[0]['content_fileurl'] == 'https://example.com/old-v2.pdf'
    assert rows[0]['old_file_id'] is None


def test_get_modified_and_new_files_detect_deleted_modified_moved_and_missing_disk_files(recorder, tmp_path):
    stored_existing_path = tmp_path / 'stored.pdf'
    stored_existing_path.write_text('data', encoding='utf-8')
    stored = Course(101, 'Course One')
    deleted = make_file(
        module_id=1,
        filename='deleted.pdf',
        url='https://example.com/deleted.pdf',
        filesize=1,
        saved_to=str(tmp_path / 'deleted.pdf'),
    )
    modified_old = make_file(
        module_id=2,
        filename='modified.pdf',
        url='https://example.com/old.pdf',
        filesize=200,
        saved_to=str(stored_existing_path),
    )
    moved_old = make_file(
        module_id=3,
        filename='moved.pdf',
        url='https://example.com/moved.pdf',
        filesize=300,
        filepath='/old/',
        saved_to=str(stored_existing_path),
    )
    manually_removed = make_file(
        module_id=4,
        filename='removed.pdf',
        url='https://example.com/removed.pdf',
        filesize=400,
        saved_to=str(tmp_path / 'missing-on-disk.pdf'),
    )
    stored.files = [deleted, modified_old, moved_old, manually_removed]

    current = Course(101, 'Course One')
    modified_new = make_file(
        module_id=2,
        filename='modified.pdf',
        url='https://example.com/new.pdf',
        filesize=201,
    )
    moved_new = make_file(
        module_id=3,
        filename='moved.pdf',
        url='https://example.com/moved.pdf',
        filesize=300,
        filepath='/new/',
    )
    same_but_missing_on_disk = make_file(
        module_id=4,
        filename='removed.pdf',
        url='https://example.com/removed.pdf',
        filesize=400,
    )
    brand_new = make_file(
        module_id=5,
        filename='brand-new.pdf',
        url='https://example.com/new-file.pdf',
        filesize=500,
    )
    current.files = [modified_new, moved_new, same_but_missing_on_disk, brand_new]

    changed = recorder.get_modified_files([stored], [current])
    assert [file.content_filename for file in changed[0].files] == [
        'deleted.pdf',
        'modified.pdf',
        'moved.pdf',
    ]
    assert changed[0].files[0].deleted is True
    assert changed[0].files[1].modified is True
    assert changed[0].files[1].old_file is modified_old
    assert changed[0].files[2].moved is True
    assert changed[0].files[2].old_file is moved_old

    changed = recorder.get_new_files(changed, [stored], [current])
    changed_names = [file.content_filename for file in changed[0].files]
    assert changed_names == [
        'deleted.pdf',
        'modified.pdf',
        'moved.pdf',
        'removed.pdf',
        'brand-new.pdf',
    ]


def test_get_new_files_handles_new_courses_kalvidres_and_existing_changed_course(recorder, tmp_path):
    stored_video_path = tmp_path / 'stored-video.mp4'
    stored_video_path.write_text('video', encoding='utf-8')
    stored_course = Course(101, 'Course One')
    stored_video = make_file(
        module_id=11,
        filename='Lecture - Video.mp4',
        url='https://example.com/kalvidres/one',
        module_modname='cookie_mod-kalvidres',
        content_type='cookie_mod',
        filesize=0,
        timemodified=0,
        saved_to=str(stored_video_path),
    )
    stored_course.files = [stored_video]

    current_course = Course(101, 'Course One')
    same_video = make_file(
        module_id=11,
        filename='Lecture - Video.mp4',
        url='https://example.com/kalvidres/one',
        module_modname='cookie_mod-kalvidres',
        content_type='cookie_mod',
        filesize=0,
        timemodified=0,
    )
    new_video = make_file(
        module_id=12,
        filename='New Video.mp4',
        url='https://example.com/kalvidres/two',
        module_modname='cookie_mod-kalvidres',
        content_type='cookie_mod',
        filesize=0,
        timemodified=0,
    )
    current_course.files = [same_video, new_video]

    existing_changed = Course(101, 'Course One')
    existing_changed.files = [make_file(module_id=99, filename='already-changed.pdf')]

    new_course = Course(202, 'Course Two')
    new_course.files = [make_file(module_id=1, filename='new-course-file.pdf')]

    changed = recorder.get_new_files(
        [existing_changed],
        [stored_course],
        [current_course, new_course],
    )

    by_course = {course.id: course for course in changed}
    assert [file.content_filename for file in by_course[101].files] == [
        'already-changed.pdf',
        'New Video.mp4',
    ]
    assert by_course[202] is new_course


def test_failed_file_lifecycle_and_grouped_queries(recorder):
    first = make_file(module_id=1, filename='failed-one.pdf', url='https://example.com/failed-one.pdf')
    second = make_file(module_id=2, filename='failed-two.pdf', url='https://example.com/failed-two.pdf')
    other_course = make_file(module_id=3, filename='other.pdf', url='https://example.com/other.pdf')

    recorder.save_failed_file(first, 101, 'Course One', 'x' * 600)
    recorder.save_failed_file(first, 101, 'Course One', 'second failure')
    recorder.save_failed_file(second, 101, 'Course One', 'first failure')
    recorder.save_failed_file(other_course, 202, 'Course Two', 'other failure')

    failed = recorder.get_failed_files(course_id=101, min_failures=2)
    assert [file.content_filename for file in failed] == ['failed-one.pdf']

    grouped = recorder.get_failed_files_with_course_info(min_failures=1)
    assert sorted(grouped) == [101, 202]
    assert [file.content_filename for file in grouped[101]['files']] == [
        'failed-one.pdf',
        'failed-two.pdf',
    ]

    summary = recorder.get_failed_files_summary()
    assert summary[101]['failed_count'] == 2
    assert summary[101]['total_failures'] == 3
    assert summary[101]['max_consecutive'] == 2

    recorder.reset_failed_file_for_retry(first, 101)
    retrying = recorder.get_failed_files(course_id=101, min_failures=99)
    assert [file.content_filename for file in retrying] == ['failed-one.pdf']

    recorder.mark_download_success(first, 101)
    assert [file.content_filename for file in recorder.get_failed_files(course_id=101)] == [
        'failed-two.pdf'
    ]


def test_delete_file_and_batch_delete_files_mark_rows_deleted_and_clear_cache(recorder):
    first = make_file(module_id=1, filename='delete-me.pdf', url='https://example.com/delete-me.pdf')
    first.file_id = recorder.new_file(first, 101, 'Course One')
    second = make_file(module_id=2, filename='batch-delete.pdf', url='https://example.com/batch-delete.pdf')
    second.file_id = recorder.new_file(second, 101, 'Course One')

    recorder._query_cache[recorder._get_cache_key('get_stored_files')] = ('stale-stored', 1)
    recorder._query_cache[recorder._get_cache_key('get_old_files')] = ('stale-old', 1)

    first.deleted = True
    first.time_stamp = 123
    recorder.save_file(first, 101, 'Course One')

    second.deleted = True
    second.time_stamp = 456
    course = Course(101, 'Course One')
    course.files = [second]
    recorder.batch_delete_files([course])

    rows = {row['content_filename']: row for row in read_file_rows(recorder)}
    assert rows['delete-me.pdf']['deleted'] == 1
    assert rows['delete-me.pdf']['notified'] == 0
    assert rows['delete-me.pdf']['time_stamp'] == 123
    assert rows['batch-delete.pdf']['deleted'] == 1
    assert rows['batch-delete.pdf']['time_stamp'] == 456
    assert recorder._query_cache == {}


def test_move_and_modify_without_old_file_insert_unnotified_active_rows(recorder):
    moved = make_file(
        module_id=10,
        filename='moved-without-old.pdf',
        url='https://example.com/moved-without-old.pdf',
    )
    moved.moved = True
    recorder.move_file(moved, 101, 'Course One')

    modified = make_file(
        module_id=11,
        filename='modified-without-old.pdf',
        url='https://example.com/modified-without-old.pdf',
    )
    modified.modified = True
    recorder.modify_file(modified, 101, 'Course One')

    rows = {row['content_filename']: row for row in read_file_rows(recorder)}
    assert rows['moved-without-old.pdf']['notified'] == 0
    assert rows['moved-without-old.pdf']['moved'] == 0
    assert rows['modified-without-old.pdf']['notified'] == 0
    assert rows['modified-without-old.pdf']['modified'] == 0


def test_incomplete_download_lifecycle(recorder):
    recorder.save_incomplete_download(
        file_id=7,
        file_url='https://example.com/file.pdf',
        file_path='/tmp/file.pdf',
        total_bytes=100,
        downloaded_bytes=20,
        server_supports_range=True,
        etag='abc',
        last_modified='Wed, 21 Oct 2015 07:28:00 GMT',
    )

    incomplete = recorder.get_incomplete_download(7, '/tmp/file.pdf')
    assert incomplete['file_url'] == 'https://example.com/file.pdf'
    assert incomplete['downloaded_bytes'] == 20
    assert incomplete['server_supports_range'] is True
    assert incomplete['attempts'] == 0

    recorder.save_incomplete_download(
        file_id=7,
        file_url='https://example.com/file.pdf?retry=1',
        file_path='/tmp/file.pdf',
        total_bytes=100,
        downloaded_bytes=60,
        server_supports_range=False,
    )
    updated = recorder.get_incomplete_download(7, '/tmp/file.pdf')
    assert updated['file_url'] == 'https://example.com/file.pdf?retry=1'
    assert updated['downloaded_bytes'] == 60
    assert updated['server_supports_range'] is False

    recorder.increment_incomplete_download_attempt(updated['download_id'], 'temporary error')
    retry_rows = recorder.get_incomplete_downloads_for_retry(max_attempts=2)
    assert retry_rows[0]['attempts'] == 1
    assert retry_rows[0]['file_id'] == 7

    recorder.mark_download_complete(7, '/tmp/file.pdf')
    assert recorder.get_incomplete_download(7, '/tmp/file.pdf') is None


def test_cleanup_old_incomplete_downloads_uses_cutoff(recorder):
    recorder.save_incomplete_download(1, 'https://example.com/old', '/tmp/old', 100, 10)
    recorder.save_incomplete_download(2, 'https://example.com/new', '/tmp/new', 100, 10)

    conn = sqlite3.connect(recorder.db_file)
    try:
        conn.execute(
            'UPDATE incomplete_downloads SET last_update_time = ? WHERE file_id = ?',
            (1, 1),
        )
        conn.commit()
    finally:
        conn.close()

    assert recorder.cleanup_old_incomplete_downloads(days_old=7) == 1
    assert recorder.get_incomplete_download(1, '/tmp/old') is None
    assert recorder.get_incomplete_download(2, '/tmp/new') is not None


def test_last_timestamps_changes_to_notify_and_notified(recorder):
    forum = make_file(
        module_id=10,
        filename='forum.html',
        module_modname='forum',
        content_type='description',
        timemodified=10,
        url='https://example.com/forum',
    )
    calendar = make_file(
        module_id=20,
        filename='calendar.html',
        module_modname='calendar',
        content_type='html',
        timemodified=30,
        url='https://example.com/calendar',
    )
    recorder.save_file(forum, 101, 'Course One')
    recorder.save_file(calendar, 101, 'Course One')

    assert recorder.get_last_timestamp_per_mod_module() == {
        'forum': {10: 10},
        'calendar': {20: 30},
    }

    changes = recorder.changes_to_notify()
    assert len(changes) == 1
    assert changes[0].id == 101
    assert [file.content_filename for file in changes[0].files] == ['forum.html', 'calendar.html']

    recorder.notified(changes)
    assert recorder.changes_to_notify() == []


def test_query_cache_keys_are_pattern_clearable(recorder):
    first_key = recorder._get_cache_key('get_stored_files')
    second_key = recorder._get_cache_key('get_old_files')
    recorder._query_cache[first_key] = ('stored', 1)
    recorder._query_cache[second_key] = ('old', 1)

    recorder._clear_cache('get_stored_files')

    assert first_key not in recorder._query_cache
    assert second_key in recorder._query_cache

    recorder._clear_cache()
    assert recorder._query_cache == {}


def test_query_cache_hit_and_optimized_query_empty_and_course_grouping(recorder):
    calls = []

    def query_func():
        calls.append('called')
        return 'fresh'

    with patch('moodle_dl.database.time.time', return_value=100):
        assert recorder._get_cached('cache-key', query_func) == 'fresh'

    with patch('moodle_dl.database.time.time', return_value=101):
        assert recorder._get_cached('cache-key', query_func) == 'fresh'

    assert calls == ['called']
    assert recorder._query_files_optimized('course_id = ?', (999,)) == []

    recorder.save_file(make_file(module_id=1, filename='one.pdf', url='https://example.com/one.pdf'), 101, 'Course One')
    recorder.save_file(make_file(module_id=2, filename='two.pdf', url='https://example.com/two.pdf'), 202, 'Course Two')

    grouped = recorder._query_files_optimized('deleted = ?', (0,))

    assert [course.id for course in grouped] == [101, 202]
    assert [course.files[0].content_filename for course in grouped] == ['one.pdf', 'two.pdf']


def test_database_rebuild_skips_unknown_tables_and_invalid_index_names(tmp_path):
    db_path = tmp_path / 'moodle_state.db'
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('CREATE TABLE files (file_id INTEGER);')
        conn.execute('CREATE TABLE unexpected_table (value INTEGER);')
        conn.execute('CREATE INDEX "bad-index-name" ON unexpected_table(value);')
        conn.execute('PRAGMA user_version = 1;')
        conn.commit()
    finally:
        conn.close()

    config = MagicMock(spec=ConfigHelper)
    config.get_misc_files_path.return_value = str(tmp_path)

    StateRecorder(config, MoodleDlOpts())

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute('PRAGMA user_version;').fetchone()[0] == 9
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='unexpected_table';"
        ).fetchone()[0] == 'unexpected_table'
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incomplete_downloads';"
        ).fetchone()[0] == 'incomplete_downloads'
    finally:
        conn.close()
