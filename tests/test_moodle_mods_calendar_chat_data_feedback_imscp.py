import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from moodle_dl.moodle.mods.calendar import CalendarMod
from moodle_dl.moodle.mods.chat import ChatMod
from moodle_dl.moodle.mods.data import DataMod
from moodle_dl.moodle.mods.feedback import FeedbackMod
from moodle_dl.moodle.mods.imscp import ImscpMod
from moodle_dl.moodle.moodle_constants import course_events_module_id, course_events_section_id
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course


def make_config(**values):
    config = Mock()
    config.get_download_calendars.return_value = values.get("download_calendars", True)
    config.get_download_chats.return_value = values.get("download_chats", True)
    config.get_download_databases.return_value = values.get("download_databases", True)
    config.get_download_feedbacks.return_value = values.get("download_feedbacks", True)
    config.get_download_imscps.return_value = values.get("download_imscps", True)
    return config


def make_mod(cls, config=None, version=2023100900, user_id=7, last_timestamps=None):
    client = Mock()
    client.async_post = AsyncMock()
    return cls(client, version, user_id, last_timestamps or {}, config or make_config())


def core_module(modname, **overrides):
    module = {
        "id": 44,
        "instance": 99,
        "modname": modname,
        "name": modname.title(),
        "description": "<p>Intro</p>",
        "timemodified": 123,
    }
    module.update(overrides)
    return module


def test_download_conditions_for_calendar_chat_data_feedback_imscp():
    other_file = SimpleNamespace(module_modname="resource", deleted=True, content_type="resource")
    deleted_calendar = SimpleNamespace(module_modname="calendar", deleted=True)
    deleted_chat = SimpleNamespace(module_modname="chat", deleted=True)
    deleted_feedback = SimpleNamespace(module_modname="feedback", deleted=True)
    deleted_imscp = SimpleNamespace(module_modname="imscp", deleted=True)
    database_file = SimpleNamespace(content_type="database_file")

    assert CalendarMod.download_condition(make_config(download_calendars=True), deleted_calendar) is True
    assert CalendarMod.download_condition(make_config(download_calendars=False), deleted_calendar) is False
    assert CalendarMod.download_condition(make_config(download_calendars=False), other_file) is True

    assert ChatMod.download_condition(make_config(download_chats=True), deleted_chat) is True
    assert ChatMod.download_condition(make_config(download_chats=False), deleted_chat) is False
    assert ChatMod.download_condition(make_config(download_chats=False), other_file) is True

    assert DataMod.download_condition(make_config(download_databases=True), database_file) is True
    assert DataMod.download_condition(make_config(download_databases=False), database_file) is False
    assert DataMod.download_condition(make_config(download_databases=False), other_file) is True

    assert FeedbackMod.download_condition(make_config(download_feedbacks=True), deleted_feedback) is True
    assert FeedbackMod.download_condition(make_config(download_feedbacks=False), deleted_feedback) is False
    assert FeedbackMod.download_condition(make_config(download_feedbacks=False), other_file) is True

    assert ImscpMod.download_condition(make_config(download_imscps=True), deleted_imscp) is True
    assert ImscpMod.download_condition(make_config(download_imscps=False), deleted_imscp) is False
    assert ImscpMod.download_condition(make_config(download_imscps=False), other_file) is True


@pytest.mark.asyncio
async def test_calendar_action_events_filtering_and_fallback():
    event = {
        "id": 1,
        "courseid": 10,
        "name": "Deadline",
        "description": "<p>Submit</p>",
        "modulename": "assign",
        "timestart": 1700000000,
        "timeduration": 3600,
        "timemodified": 1700000100,
        "attachments": [{"filename": "handout.pdf", "filepath": "/"}],
    }
    other_course_event = {**event, "id": 2, "courseid": 999, "name": "Other"}
    mod = make_mod(CalendarMod, last_timestamps={"calendar": {course_events_module_id: 100}})
    mod.client.async_post.return_value = {"events": [event, other_course_event]}
    core_contents = {}

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], core_contents)

    assert mod.client.async_post.await_args.args[0] == "core_calendar_get_action_events_by_timesort"
    assert mod.client.async_post.await_args.args[1]["timesortfrom"] == 100
    assert list(result[10]) == [course_events_module_id]
    files = result[10][course_events_module_id]["files"]
    assert any(file["type"] == "html" and "Deadline" in file["html"] for file in files)
    assert any(
        file["filename"] == "handout.pdf"
        and file["type"] == "calendar_attachment"
        and file["filepath"].startswith("/events/")
        for file in files
    )
    calendar_metadata = json.loads(files[-1]["content"])
    assert calendar_metadata["event_count"] == 1
    assert calendar_metadata["events"][0]["module_name"] == "assign"
    assert core_contents[10][-1]["id"] == course_events_section_id

    fallback_mod = make_mod(CalendarMod)
    fallback_mod.client.async_post.side_effect = [RuntimeError("action failed"), {"events": [event]}]
    fallback_result = await fallback_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    called_functions = [call.args[0] for call in fallback_mod.client.async_post.await_args_list]
    assert called_functions == ["core_calendar_get_action_events_by_timesort", "core_calendar_get_calendar_events"]
    assert fallback_result[10][course_events_module_id]["name"] == "Events"

    disabled = make_mod(CalendarMod, make_config(download_calendars=False))
    assert await disabled.real_fetch_mod_entries([Course(10, "Course")], {}) == {}
    assert CalendarMod.sort_by_courseid([event, other_course_event]) == {10: [event], 999: [other_course_event]}


@pytest.mark.asyncio
async def test_chat_sessions_web_fallback_and_real_fetch():
    core_contents = {10: [{"modules": [core_module("chat", name="Room")]}]}
    mod = make_mod(ChatMod)

    chats = await mod._fetch_chats_web_api([Course(10, "Course")], core_contents)
    assert chats[0]["id"] == 99
    assert chats[0]["chatmethod"] == "ajax"
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_chats_web_api([Course(99, "Missing")], core_contents)

    session_mod = make_mod(ChatMod)

    async def chat_session_side_effect(wsfunction, data):
        if wsfunction == "mod_chat_get_sessions":
            return {
                "sessions": [
                    {
                        "sessionstart": 100,
                        "sessionend": 160,
                        "iscomplete": True,
                        "sessionusers": [{"userid": 1, "messagecount": 2}],
                    },
                    {"sessionstart": 200, "sessionend": 0, "sessionusers": []},
                ]
            }
        if data["sessionstart"] == 100:
            return {"messages": [{"id": 3, "userid": 1, "message": "Hello", "timestamp": 110}]}
        raise RuntimeError("messages blocked")

    session_mod.client.async_post.side_effect = chat_session_side_effect
    sessions = await session_mod._get_chat_sessions(99)
    assert sessions[0]["duration_seconds"] == 60
    assert sessions[0]["message_count"] == 1
    assert sessions[0]["messages"][0]["message"] == "Hello"
    assert sessions[1]["duration_seconds"] is None
    assert sessions[1]["message_count"] == 0
    assert "Messages could not be retrieved" in sessions[1]["note"]

    real_mod = make_mod(ChatMod)
    real_mod.client.async_post.return_value = {
        "chats": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Room",
                "intro": "<p>Welcome</p>",
                "chatmethod": "normal",
            }
        ]
    }
    real_mod._get_chat_sessions = AsyncMock(return_value=[{"sessionstart": 123, "messages": []}])
    result = await real_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    metadata = json.loads(next(file["content"] for file in files if file["filename"] == "metadata.json"))
    assert metadata["settings"]["chatmethod"] == "normal"
    assert metadata["sessions"] == [{"sessionstart": 123, "messages": []}]
    assert any(file["filename"] == "session_123.json" for file in files)

    fallback_real = make_mod(ChatMod)
    fallback_real.client.async_post.side_effect = RequestRejectedError("denied")
    fallback_real._get_chat_sessions = AsyncMock(return_value=[])
    fallback_result = await fallback_real.real_fetch_mod_entries([Course(10, "Course")], core_contents)
    assert fallback_result[10][44]["name"] == "Room"

    disabled = make_mod(ChatMod, make_config(download_chats=False))
    assert await disabled.real_fetch_mod_entries([Course(10, "Course")], {}) == {}


@pytest.mark.asyncio
async def test_data_entries_fields_guards_and_real_fetch():
    entries = {
        "ratinginfo": {"scaleid": 1},
        "entries": [
            {
                "id": 7,
                "dataid": 99,
                "userid": 5,
                "fullname": "Alice",
                "groupid": 2,
                "timecreated": 1700000000,
                "timemodified": 1700000100,
                "approved": True,
                "tags": [{"displayname": "important"}],
                "contents": [
                    {
                        "files": [
                            {"filename": "thumb_skip.png", "filepath": "/"},
                            {"filename": "paper.pdf", "filepath": "/docs/"},
                        ]
                    }
                ],
            }
        ],
    }

    files = DataMod._get_files_of_db_entries(entries)
    metadata = json.loads(files[0]["content"])
    assert metadata["entry_id"] == 7
    assert metadata["rating"] == {"scaleid": 1}
    assert "important" in files[1]["description"]
    assert [file["filename"] for file in files] == [files[0]["filename"], files[1]["filename"], "paper.pdf"]
    assert files[2]["type"] == "database_file"
    assert files[2]["filepath"] == "/entry_7/docs/"

    with patch.object(DataMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_mod(DataMod, make_config(download_databases=False)).add_database_files({10: {44: {}}})
        runner.assert_not_called()
    with patch.object(DataMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_mod(DataMod, version=2015051100).add_database_files({10: {44: {}}})
        runner.assert_not_called()
    with patch.object(DataMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_mod(DataMod).add_database_files({10: {44: {}}})
        runner.assert_awaited_once()

    load_mod = make_mod(DataMod)
    load_mod.client.async_post.return_value = {"timeavailable": False}
    database = {"id": 99, "files": []}
    await load_mod.load_database_files(database)
    assert database["files"] == []

    load_mod.client.async_post.side_effect = [{"timeavailable": True}, entries]
    await load_mod.load_database_files(database)
    assert database["files"][-1]["filename"] == "paper.pdf"

    fields_mod = make_mod(DataMod)
    fields_mod.client.async_post.return_value = {
        "fields": [{"id": 1, "dataid": 99, "type": "text", "name": "Title", "required": 1, "param1": "x"}]
    }
    assert await fields_mod._get_database_fields(99) == [
        {
            "id": 1,
            "dataid": 99,
            "type": "text",
            "name": "Title",
            "description": "",
            "required": 1,
            "param1": "x",
            "param2": "",
            "param3": "",
        }
    ]
    fields_mod.client.async_post.side_effect = RuntimeError("fields unavailable")
    assert await fields_mod._get_database_fields(99) == []

    real_mod = make_mod(DataMod)
    real_mod.client.async_post.return_value = {
        "databases": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Database",
                "intro": "<p>About</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
            }
        ]
    }
    real_mod._get_database_fields = AsyncMock(return_value=[{"id": 1, "name": "Title"}])
    real_mod.add_database_files = AsyncMock()
    result = await real_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    result_files = result[10][44]["files"]
    result_metadata = json.loads(next(file["content"] for file in result_files if file["filename"] == "metadata.json"))
    assert result_metadata["schema"]["field_count"] == 1
    assert any(file["filename"] == "schema.json" for file in result_files)
    assert result_files[0]["type"] == "database_introfile"
    real_mod.add_database_files.assert_awaited_once()

    web_mod = make_mod(DataMod)
    web_databases = await web_mod._fetch_databases_web_api(
        [Course(10, "Course")], {10: [{"modules": [core_module("data", name="Database")]}]}
    )
    assert web_databases[0]["name"] == "Database"
    with pytest.raises(ValueError, match="Web API"):
        await web_mod._fetch_databases_web_api([Course(99, "Missing")], {})


@pytest.mark.asyncio
async def test_feedback_items_analysis_files_and_fallback():
    mod = make_mod(FeedbackMod)
    mod.client.async_post.return_value = {
        "items": [
            {
                "id": 1,
                "typ": "textarea",
                "name": "Question",
                "label": "Q1",
                "presentation": "long",
                "required": 1,
                "itemfiles": [{"filename": "prompt.png", "filepath": "/"}],
            }
        ]
    }
    items = await mod._get_feedback_items(99)
    assert items[0]["typ"] == "textarea"
    assert items[0]["itemfiles"][0]["filename"] == "prompt.png"

    mod.client.async_post.side_effect = RuntimeError("items unavailable")
    assert await mod._get_feedback_items(99) == []
    assert await mod._get_feedback_access_information(99) is None

    analysis_mod = make_mod(FeedbackMod)
    analysis_mod._get_feedback_access_information = AsyncMock(return_value=None)
    analysis_mod.client.async_post.return_value = {
        "completedcount": 3,
        "itemscount": 1,
        "itemsdata": [{"id": 1}],
        "warnings": ["notice"],
    }
    assert await analysis_mod._get_feedback_analysis(99) == {
        "completedcount": 3,
        "itemscount": 1,
        "itemsdata": [{"id": 1}],
        "warnings": ["notice"],
    }

    real_mod = make_mod(FeedbackMod)
    real_mod.client.async_post.return_value = {
        "feedbacks": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Feedback",
                "intro": "<p>Tell us</p>",
                "anonymous": 1,
            }
        ]
    }
    real_mod._get_feedback_items = AsyncMock(
        return_value=[
            {
                "id": 1,
                "name": "Question",
                "itemfiles": [{"filename": "prompt.png", "filepath": "/"}],
            }
        ]
    )
    real_mod._get_feedback_analysis = AsyncMock(return_value={"completedcount": 1, "itemsdata": [{"id": 1}]})
    result = await real_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    metadata = json.loads(next(file["content"] for file in files if file["filename"] == "metadata.json"))
    assert metadata["settings"]["anonymous"] == 1
    assert any(file["filename"] == "questions.json" for file in files)
    assert any(file["filename"] == "analysis.json" for file in files)
    assert any(file["filename"] == "prompt.png" and file["type"] == "feedback_item_file" for file in files)

    web_feedbacks = await mod._fetch_feedbacks_web_api(
        [Course(10, "Course")], {10: [{"modules": [core_module("feedback", name="Feedback")]}]}
    )
    assert web_feedbacks[0]["name"] == "Feedback"
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_feedbacks_web_api([Course(99, "Missing")], {})

    disabled = make_mod(FeedbackMod, make_config(download_feedbacks=False))
    assert await disabled.real_fetch_mod_entries([Course(10, "Course")], {}) == {}


@pytest.mark.asyncio
async def test_imscp_helpers_real_fetch_and_web_fallback():
    mod = make_mod(ImscpMod)
    toc_items = [{"href": "page.html", "title": "Intro", "level": "0", "subitems": [{"href": "s.html"}]}]

    assert mod._is_special_file("imsmanifest.xml") is True
    assert mod._is_special_file("page.html") is False
    assert mod._parse_toc("") == []
    assert mod._parse_toc(json.dumps(toc_items)) == toc_items
    assert mod._parse_toc("not-json") == []
    toc_html = mod._generate_toc_html(toc_items)
    assert "Table of Contents" in toc_html
    assert 'href="page.html"' in toc_html
    assert mod._create_flat_toc_list(toc_items) == [
        {"href": "page.html", "title": "Intro", "level": "0", "has_subitems": True},
        {"href": "s.html", "title": "", "level": "1", "has_subitems": False},
    ]

    core_contents = {
        10: [
            {
                "modules": [
                    core_module(
                        "imscp",
                        name="Package",
                        contents=[
                            {"filename": "toc.json", "content": json.dumps(toc_items)},
                            {"filename": "imsmanifest.xml", "filepath": "/"},
                            {"filename": "page.html", "filepath": "/content/"},
                        ],
                    )
                ]
            }
        ]
    }
    mod.client.async_post.return_value = {
        "imscps": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Package",
                "intro": "<p>About</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                "revision": 2,
                "keepold": 1,
                "structure": "tree",
                "timemodified": 123,
            }
        ]
    }

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], core_contents)
    files = result[10][44]["files"]
    filenames = [file["filename"] for file in files]
    assert "intro.pdf" in filenames
    assert "imsmanifest.xml" not in filenames
    assert "page.html" in filenames
    assert any("Table" in filename and filename.endswith(".html") for filename in filenames)
    metadata = json.loads(files[-1]["content"])
    assert metadata["package_info"]["revision"] == 2
    assert metadata["table_of_contents"]["total_items"] == 1
    assert metadata["content_summary"]["has_toc"] is True

    fallback_mod = make_mod(ImscpMod)
    fallback_mod.client.async_post.side_effect = RequestRejectedError("denied")
    fallback_result = await fallback_mod.real_fetch_mod_entries([Course(10, "Course")], core_contents)
    assert fallback_result[10][44]["name"] == "Package"

    with pytest.raises(ValueError, match="Web API"):
        await fallback_mod._fetch_imscps_web_api([Course(99, "Missing")], core_contents)

    disabled = make_mod(ImscpMod, make_config(download_imscps=False))
    assert await disabled.real_fetch_mod_entries([Course(10, "Course")], core_contents) == {}
