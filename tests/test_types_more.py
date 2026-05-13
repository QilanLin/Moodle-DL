# -*- coding: utf-8 -*-
from moodle_dl.types import File, HeadInfo, TaskStatus


def make_file(**overrides):
    values = {
        "module_id": 1,
        "section_name": "Week 1",
        "section_id": 2,
        "module_name": "Resource",
        "content_filepath": "/",
        "content_filename": "file.pdf",
        "content_fileurl": "https://example.test/file.pdf",
        "content_filesize": 123,
        "content_timemodified": 456,
        "module_modname": "resource",
        "content_type": "application/pdf",
        "content_isexternalfile": False,
        "saved_to": "/tmp/file.pdf",
        "time_stamp": 789,
        "modified": 0,
        "moved": 0,
        "deleted": 0,
        "notified": 0,
        "file_hash": "hash",
        "file_id": 99,
        "old_file_id": 88,
    }
    values.update(overrides)
    return File(**values)


def make_row(**overrides):
    row = {
        "file_id": 99,
        "module_id": 1,
        "section_name": "Week 1",
        "section_id": 2,
        "module_name": "Resource",
        "content_filepath": "/",
        "content_filename": "file.pdf",
        "content_fileurl": "https://example.test/file.pdf",
        "content_filesize": 123,
        "content_timemodified": 456,
        "module_modname": "resource",
        "content_type": "application/pdf",
        "content_isexternalfile": 1,
        "saved_to": "/tmp/file.pdf",
        "time_stamp": 789,
        "modified": 1,
        "moved": 1,
        "deleted": 1,
        "notified": 1,
        "hash": "hash",
        "old_file_id": 88,
    }
    row.update(overrides)
    return row


def test_file_external_flag_accepts_integer_values_and_maps_back_to_database_shape():
    external = make_file(content_isexternalfile=1, modified=1, moved=1, deleted=1, notified=1)
    regular = make_file(content_isexternalfile=0)

    assert external.content_isexternalfile is True
    assert regular.content_isexternalfile is False
    mapped = external.getMap()
    assert mapped["content_isexternalfile"] == 1
    assert mapped["modified"] == 1
    assert mapped["moved"] == 1
    assert mapped["deleted"] == 1
    assert mapped["notified"] == 1


def test_file_from_row_defaults_missing_compatibility_fields():
    file = File.fromRow(make_row())

    assert file.position_in_section is None
    assert file.visible == 1
    assert file.uservisible == 1
    assert file.availabilityinfo is None
    assert file.completion == 0
    assert file.timecreated == 0
    assert file.sortorder == 0
    assert file.content_isexternalfile is True
    assert file.modified is True
    assert file.moved is True
    assert file.deleted is True
    assert file.notified is True


def test_file_from_row_reads_extended_metadata_when_present():
    file = File.fromRow(
        make_row(
            position_in_section=3,
            visible=0,
            uservisible=0,
            availabilityinfo='{"op":"&"}',
            completion=2,
            timecreated=111,
            sortorder=222,
        )
    )

    assert file.position_in_section == 3
    assert file.visible == 0
    assert file.uservisible == 0
    assert file.availabilityinfo == '{"op":"&"}'
    assert file.completion == 2
    assert file.timecreated == 111
    assert file.sortorder == 222


def test_file_str_truncates_long_filename(monkeypatch):
    long_filename = "a" * 300 + ".pdf"

    def fake_to_valid_name(name, is_file, max_length=200):
        if name == long_filename:
            return long_filename
        return "" if name is None else str(name)

    monkeypatch.setattr("moodle_dl.types.PT.to_valid_name", fake_to_valid_name)
    file = make_file(content_filename=long_filename)

    rendered = str(file)

    assert "content_filename (longer than 256 chars)" in rendered
    assert "[...]" in rendered
    assert long_filename not in rendered


def test_task_status_uses_repr_for_blank_error_text():
    class BlankError:
        def __str__(self):
            return "  "

        def __repr__(self):
            return "<BlankError>"

    status = TaskStatus()
    status.set_error(BlankError())

    assert status.get_error_text() == "<BlankError>"


def test_head_info_treats_file_urls_as_non_html_despite_html_content_type():
    pdf = HeadInfo(
        content_type="text/html",
        content_length=10,
        last_modified="",
        final_url="https://example.test/slides.pdf?token=abc",
        guessed_file_name="slides.pdf",
        host="example.test",
    )
    no_url = HeadInfo(
        content_type="text/plain",
        content_length=10,
        last_modified="",
        final_url="",
        guessed_file_name="",
        host="",
    )
    html_page = HeadInfo(
        content_type="text/html",
        content_length=10,
        last_modified="",
        final_url="https://example.test/course/page",
        guessed_file_name="page",
        host="example.test",
    )

    assert pdf.is_html is False
    assert no_url.is_html is True
    assert no_url._url_has_non_html_extension() is False
    assert html_page.is_html is True
