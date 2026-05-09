import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from moodle_dl.moodle.mods.assign import AssignMod
from moodle_dl.moodle.mods.url import UrlMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course


def make_config(**values):
    config = Mock()
    config.get_download_submissions.return_value = values.get("download_submissions", True)
    config.get_download_urls.return_value = values.get("download_urls", True)
    return config


def make_assign_mod(config=None, version=2016052300, user_id=7):
    client = Mock()
    client.async_post = AsyncMock()
    return AssignMod(client, version, user_id, {}, config or make_config())


def make_url_mod(config=None, version=2015111600, user_id=7):
    client = Mock()
    client.async_post = AsyncMock()
    return UrlMod(client, version, user_id, {}, config or make_config())


def test_assign_download_condition_respects_submission_and_deleted_assignment_flags():
    file = SimpleNamespace(module_modname="assign", deleted=True)

    assert AssignMod.download_condition(make_config(download_submissions=True), file) is True
    assert AssignMod.download_condition(make_config(download_submissions=False), file) is False

    regular_file = SimpleNamespace(module_modname="resource", deleted=True)
    assert AssignMod.download_condition(make_config(download_submissions=False), regular_file) is True


@pytest.mark.asyncio
async def test_assign_mobile_api_success_and_empty_response_failure():
    mod = make_assign_mod()
    courses = [Course(10, "Course")]
    mod.client.async_post.return_value = {"courses": [{"id": 10, "assignments": []}]}

    assert await mod._fetch_assignments_mobile_api(courses) == [{"id": 10, "assignments": []}]
    mod.client.async_post.assert_awaited_once_with(
        "mod_assign_get_assignments",
        {"courseids": {"0": 10}},
    )

    mod.client.async_post.reset_mock()
    mod.client.async_post.return_value = {"courses": []}

    with pytest.raises(KeyError, match="Mobile API"):
        await mod._fetch_assignments_mobile_api(courses)


@pytest.mark.asyncio
async def test_assign_web_api_fallback_converts_assign_modules():
    mod = make_assign_mod()
    course = Course(10, "Course")
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "assign",
                        "name": "Essay",
                        "timemodified": 123,
                        "timecreated": 12,
                        "contents": [{"type": "file", "filename": "brief.pdf"}],
                    },
                    {"id": 45, "modname": "url"},
                ]
            }
        ],
        11: [{"modules": [{"id": 55, "modname": "assign"}]}],
    }

    result = await mod._fetch_assignments_web_api([course, Course(12, "Missing")], core_contents)

    assert result == [
        {
            "id": 10,
            "shortname": "",
            "fullname": "Course",
            "assignments": [
                {
                    "id": 99,
                    "cmid": 44,
                    "course": 10,
                    "name": "Essay",
                    "intro": "",
                    "introformat": 1,
                    "introattachments": [{"type": "file", "filename": "brief.pdf"}],
                    "duedate": 0,
                    "cutoffdate": 0,
                    "allowsubmissionsfromdate": 0,
                    "grade": 0,
                    "timemodified": 123,
                    "timecreated": 12,
                    "submissiondrafts": 0,
                    "sendnotifications": 1,
                    "sendlatenotifications": 0,
                    "sendstudentnotifications": 1,
                    "requiresubmissionstatement": 0,
                    "requireallteammemberssubmit": 0,
                    "teamsubmission": 0,
                    "blindmarking": 0,
                    "hidegrader": 0,
                    "revealidentities": 0,
                    "attemptreopenmethod": "none",
                    "maxattempts": -1,
                    "markingworkflow": 0,
                    "markingallocation": 0,
                    "configs": [],
                }
            ],
        }
    ]

    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_assignments_web_api([Course(12, "Missing")], core_contents)


def test_assign_extract_assign_modules_builds_files_and_metadata():
    mod = make_assign_mod()
    assignments = [
        {
            "id": 99,
            "cmid": 44,
            "course": 10,
            "name": "Essay",
            "intro": "<p>Read first</p>",
            "introattachments": [{"filename": "brief.pdf", "filepath": "/", "type": "file"}],
            "duedate": 200,
            "grade": 100,
            "timemodified": 123,
        }
    ]

    result = mod.extract_assign_modules(assignments)

    module = result[44]
    assert module["id"] == 99
    assert module["name"] == "Essay"
    assert module["files"][0]["type"] == "assign_file"
    assert module["files"][1]["filename"] == "Introduction.html"
    metadata = json.loads(module["files"][2]["content"])
    assert metadata["assignment_id"] == 99
    assert metadata["settings"]["duedate"] == 200
    assert metadata["grading"]["grade"] == 100


@pytest.mark.asyncio
async def test_assign_add_submissions_honors_config_and_version_guards():
    mod = make_assign_mod(config=make_config(download_submissions=False))
    entries = {10: {44: {"id": 99, "files": []}}}

    with patch.object(AssignMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await mod.add_submissions(entries)
        runner.assert_not_called()

    old_mod = make_assign_mod(version=2015051100)
    with patch.object(AssignMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await old_mod.add_submissions(entries)
        runner.assert_not_called()

    with patch.object(AssignMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_assign_mod().add_submissions(entries)
        runner.assert_awaited_once()


@pytest.mark.asyncio
async def test_assign_add_foreign_submissions_adds_user_and_group_files():
    mod = make_assign_mod()
    assignments = {10: {44: {"id": 99, "files": []}}}
    mod.client.async_post.side_effect = [
        {
            "assignments": [
                {
                    "assignmentid": 99,
                    "submissions": [
                        {
                            "userid": 7,
                            "plugins": [
                                {"fileareas": [{"files": [{"filename": "mine.pdf", "filepath": "/"}]}]}
                            ],
                        },
                        {
                            "userid": 0,
                            "groupid": 3,
                            "plugins": [
                                {"editorfields": [{"description": "Group note", "text": "Done"}]}
                            ],
                        },
                    ],
                }
            ]
        },
        [
            {"id": 7, "fullname": "Alice Example", "idnumber": "A01", "groups": []},
            {"id": 8, "fullname": "Bob Example", "groups": [{"id": 3, "name": "Team Red"}]},
        ],
    ]

    await mod.add_foreign_submissions(assignments)

    files = assignments[10][44]["files"]
    assert files[0]["filename"] == "mine.pdf"
    assert files[0]["type"] == "submission_file"
    assert files[0]["filepath"].startswith("/all_submissions/")
    assert "Alice" in files[0]["filepath"]
    assert files[1]["filename"] == "Group note"
    assert files[1]["description"] == "Done"
    assert files[1]["filepath"].startswith("/all_submissions/")
    assert "Team" in files[1]["filepath"]


@pytest.mark.asyncio
async def test_assign_add_foreign_submissions_handles_no_entries_no_submissions_and_denied_users():
    mod = make_assign_mod()

    await mod.add_foreign_submissions({})
    mod.client.async_post.assert_not_called()

    assignments = {10: {44: {"id": 99, "files": []}}}
    mod.client.async_post.return_value = {"assignments": []}
    await mod.add_foreign_submissions(assignments)
    assert assignments[10][44]["files"] == []

    mod.client.async_post.reset_mock()
    mod.client.async_post.side_effect = [
        {"assignments": [{"assignmentid": 99, "submissions": []}]},
        RequestRejectedError("denied"),
    ]
    await mod.add_foreign_submissions(assignments)
    assert assignments[10][44]["files"] == []


@pytest.mark.asyncio
async def test_assign_get_assignment_grades_success_and_failure_paths():
    mod = make_assign_mod()
    mod.client.async_post.return_value = {"assignments": [{"grades": [{"grade": "95"}]}]}
    assert await mod._get_assignment_grades(99) == [{"grade": "95"}]

    mod.client.async_post.return_value = {"assignments": []}
    assert await mod._get_assignment_grades(99) == []

    mod.client.async_post.side_effect = RequestRejectedError("denied")
    assert await mod._get_assignment_grades(99) == []

    mod.client.async_post.side_effect = RuntimeError("bad response")
    assert await mod._get_assignment_grades(99) == []


@pytest.mark.asyncio
async def test_assign_load_submissions_appends_submission_files_and_grade_metadata():
    mod = make_assign_mod()
    assignment = {"id": 99, "timemodified": 123, "files": []}
    mod._fetch_submission_status_mobile_api = AsyncMock(return_value={"submission": "status"})
    mod._get_files_of_submission = Mock(return_value=[{"filename": "submission.pdf"}])
    mod._get_assignment_grades = AsyncMock(
        return_value=[
            {
                "id": 1,
                "userid": 7,
                "attemptnumber": 0,
                "timecreated": 10,
                "timemodified": 11,
                "grader": 2,
                "grade": "95",
                "gradefordisplay": "95 / 100",
            }
        ]
    )

    await mod.load_submissions(assignment)

    assert assignment["files"][0] == {"filename": "submission.pdf"}
    grades_file = assignment["files"][1]
    assert grades_file["filename"] == "grades.json"
    assert grades_file["filepath"] == "/submissions/"
    assert json.loads(grades_file["content"])["grades"][0]["gradefordisplay"] == "95 / 100"


def test_assign_submission_file_helpers_extract_files_editor_text_and_grade():
    mod = make_assign_mod()
    submission = {
        "lastattempt": {
            "submission": {
                "plugins": [
                    {"fileareas": [{"files": [{"filename": "answer.pdf", "filepath": "/draft"}]}]},
                    {"editorfields": [{"description": "Online text", "text": "<p>Answer</p>"}]},
                ]
            },
            "teamsubmission": {"plugins": []},
        },
        "feedback": {
            "plugins": [{"fileareas": [{"files": [{"filename": "feedback.pdf", "filepath": "/"}]}]}],
            "gradefordisplay": "90 / 100",
            "gradeddate": 456,
        },
    }

    files = mod._get_files_of_submission(submission)

    assert [file["filename"] for file in files] == ["answer.pdf", "Online text", "feedback.pdf", "grade"]
    assert files[0]["filepath"] == "/submissions/draft"
    assert files[0]["type"] == "submission_file"
    assert files[1]["description"] == "<p>Answer</p>"
    assert files[3]["description"] == "90 / 100"
    assert mod._get_grade_of_feedback({}, "/submissions/") == []


@pytest.mark.asyncio
async def test_assign_fetch_submission_status_returns_api_response_or_empty_submission():
    mod = make_assign_mod()
    mod.client.async_post.return_value = {"submission": {"id": 1}}
    assert await mod._fetch_submission_status_mobile_api(99, {"assignid": 99}) == {"submission": {"id": 1}}

    mod.client.async_post.side_effect = RuntimeError("network")
    assert await mod._fetch_submission_status_mobile_api(99, {"assignid": 99}) == {"submission": None}


def test_url_download_condition_respects_url_setting_and_deleted_url_files():
    deleted_url_file = SimpleNamespace(module_modname="url", deleted=True)
    other_file = SimpleNamespace(module_modname="resource", deleted=True)

    assert UrlMod.download_condition(make_config(download_urls=True), deleted_url_file) is True
    assert UrlMod.download_condition(make_config(download_urls=False), deleted_url_file) is False
    assert UrlMod.download_condition(make_config(download_urls=False), other_file) is True


def test_url_display_and_parameter_parsing():
    mod = make_url_mod()

    assert mod._get_display_type_name(UrlMod.DISPLAY_OPEN) == "Open"
    assert mod._get_display_type_name(999) == "Unknown"
    assert mod._parse_display_options("width=620&height=450&printintro=true&empty=") == {
        "width": 620,
        "height": 450,
        "printintro": True,
        "empty": "",
    }
    assert mod._parse_display_options("") == {}
    assert mod._parse_parameters("id=123&enabled=false&name=slides") == {
        "id": 123,
        "enabled": False,
        "name": "slides",
    }
    assert mod._parse_parameters("") == {}
    assert mod._parse_parameters("opaque-token") == {"format": "unknown", "raw": "opaque-token"}


def test_url_php_parameter_parsing_and_decode_helpers():
    mod = make_url_mod()

    assert mod._parse_parameters('a:3:{s:4:"name";s:5:"Alice";s:3:"age";s:2:"42";s:4:"flag";s:4:"true";}') == {
        "name": "Alice",
        "age": 42,
        "flag": True,
    }
    assert mod._decode_php_dict({b"outer": {b"inner": b"value"}, b"items": [b"a", b"b"], b"count": 2}) == {
        "outer": {"inner": "value"},
        "items": ["a", "b"],
        "count": 2,
    }

    parsed = mod._parse_parameters("a:not-valid")
    assert parsed["format"] == "php_serialized"
    assert parsed["raw"] == "a:not-valid"
    assert "Parse error" in parsed["error"]


@pytest.mark.asyncio
async def test_url_web_api_fallback_extracts_url_modules():
    mod = make_url_mod()
    courses = [Course(10, "Course"), Course(12, "Other")]
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "url",
                        "name": "External site",
                        "description": "<p>Open it</p>",
                        "timemodified": 123,
                        "visible": 0,
                        "section": 5,
                        "sectionnumber": 2,
                        "sectionname": "Links",
                        "availability": '{"op":"&"}',
                        "contents": [{"type": "url", "fileurl": "https://example.com"}],
                    }
                ]
            }
        ]
    }

    result = await mod._fetch_urls_web_api(courses, core_contents)

    assert result == [
        {
            "id": 99,
            "coursemodule": 44,
            "course": 10,
            "name": "External site",
            "intro": "<p>Open it</p>",
            "introformat": 1,
            "externalurl": "https://example.com",
            "display": UrlMod.DISPLAY_AUTO,
            "displayoptions": "",
            "parameters": "",
            "timemodified": 123,
            "timecreated": 0,
            "visible": 0,
            "uservisible": 1,
            "availability": '{"op":"&"}',
            "section_id": 5,
            "section_number": 2,
            "section_name": "Links",
            "_fallback": True,
            "_data_source": "core_course_get_contents",
        }
    ]

    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_urls_web_api([Course(99, "Missing")], core_contents)


@pytest.mark.asyncio
async def test_url_real_fetch_uses_mobile_api_and_core_contents_files():
    mod = make_url_mod()
    mod.client.async_post.return_value = {
        "urls": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "External site",
                "intro": "<p>Open it</p>",
                "introfiles": [{"filename": "intro.txt", "filepath": "/"}],
                "externalurl": "https://example.com",
                "display": UrlMod.DISPLAY_POPUP,
                "displayoptions": "width=500",
                "parameters": "x=1",
                "timemodified": 123,
            }
        ]
    }
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "contents": [
                            {"type": "url", "filename": "External link", "fileurl": "https://example.com"}
                        ],
                    }
                ]
            }
        ]
    }

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], core_contents)

    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == [
        "intro.txt",
        "Introduction.html",
        "metadata.json",
        "External link",
    ]
    metadata = json.loads(files[2]["content"])
    assert metadata["display"]["type_name"] == "Popup"
    assert metadata["display"]["options"] == {"width": 500}
    assert metadata["parameters"] == {"x": 1}


@pytest.mark.asyncio
async def test_url_real_fetch_returns_empty_when_disabled_and_uses_web_fallback():
    disabled_mod = make_url_mod(config=make_config(download_urls=False))
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}

    mod = make_url_mod()
    mod.client.async_post.side_effect = RequestRejectedError("mobile disabled")
    mod._fetch_urls_web_api = AsyncMock(
        return_value=[
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Fallback URL",
                "externalurl": "https://example.com",
                "display": UrlMod.DISPLAY_AUTO,
                "displayoptions": "",
                "parameters": "",
                "timemodified": 123,
            }
        ]
    )

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})

    assert result[10][44]["name"] == "Fallback URL"
    mod._fetch_urls_web_api.assert_awaited_once()
