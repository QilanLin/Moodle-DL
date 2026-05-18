import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from moodle_dl.moodle.mods.lti import LtiMod
from moodle_dl.moodle.mods.workshop import WorkshopMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course


def make_config(**values):
    config = Mock()
    config.get_download_ltis.return_value = values.get("download_ltis", True)
    config.get_download_workshops.return_value = values.get("download_workshops", True)
    config.get_download_metadata_files.return_value = values.get("download_metadata_files", True)
    return config


def make_mod(cls, config=None, version=2023100900, user_id=7):
    client = Mock()
    client.async_post = AsyncMock()
    return cls(client, version, user_id, {}, config or make_config())


def test_lti_and_workshop_download_conditions():
    deleted_lti = SimpleNamespace(module_modname="lti", deleted=True)
    live_cookie_mod = SimpleNamespace(module_modname="cookie_mod-kalvidres", deleted=False)
    deleted_cookie_mod = SimpleNamespace(module_modname="cookie_mod-helixmedia", deleted=True)
    other_file = SimpleNamespace(module_modname="resource", deleted=True)
    deleted_workshop = SimpleNamespace(module_modname="workshop", deleted=True)

    assert LtiMod.download_condition(make_config(download_ltis=True), deleted_lti) is True
    assert LtiMod.download_condition(make_config(download_ltis=False), deleted_lti) is False
    assert LtiMod.download_condition(make_config(download_ltis=False), other_file) is True
    assert LtiMod.download_condition(make_config(download_ltis=False), live_cookie_mod) is True
    assert LtiMod.download_condition(make_config(download_ltis=True), deleted_cookie_mod) is False

    assert WorkshopMod.download_condition(make_config(download_workshops=True), deleted_workshop) is True
    assert WorkshopMod.download_condition(make_config(download_workshops=False), deleted_workshop) is False
    assert WorkshopMod.download_condition(make_config(download_workshops=False), other_file) is True


def test_lti_launch_container_names_and_launch_form_escaping():
    mod = make_mod(LtiMod)

    assert mod._get_launch_container_name(LtiMod.LAUNCH_CONTAINER_NEW_WINDOW) == "New window"
    assert mod._get_launch_container_name(999) == "Unknown"

    form = mod._generate_launch_form(
        "https://tool.example/launch?x=<bad>",
        [
            {"name": "lti_message_type", "value": "basic-lti-launch-request"},
            {"name": "oauth_signature", "value": "secret&signature"},
            {"name": "context_id", "value": "course-1"},
            {"name": "resource_link_id", "value": "res-1"},
            {"name": "user_id", "value": "student-1"},
            {"name": "tool_consumer_instance_name", "value": "Moodle"},
            {"name": "ext_submit", "value": "Open <Tool>"},
            {"name": "custom_long", "value": "x" * 120},
        ],
        "Tool <Name>",
    )

    assert "<title>Launch LTI Tool: Tool &lt;Name&gt;</title>" in form
    assert 'action="https://tool.example/launch?x=&lt;bad&gt;"' in form
    assert 'value="secret&amp;signature"' in form
    assert 'value="Open &lt;Tool&gt;"' in form
    assert "OAuth Parameters" in form
    assert "Custom Parameters" in form
    assert "xxx..." in form

    default_button_form = mod._generate_launch_form("https://tool.example", [], "Tool")
    assert 'value="🚀 Launch Tool"' in default_button_form


@pytest.mark.asyncio
async def test_lti_web_api_fallback_extracts_https_and_http_tool_urls():
    mod = make_mod(LtiMod)
    courses = [Course(10, "Course")]
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "lti",
                        "name": "Secure Tool",
                        "description": "<p>Intro</p>",
                        "visible": 0,
                        "section": 5,
                        "sectionnumber": 2,
                        "sectionname": "Tools",
                        "availability": "{}",
                        "contents": [{"type": "url", "fileurl": "https://tool.example/launch"}],
                        "timemodified": 123,
                    },
                    {
                        "id": 45,
                        "instance": 100,
                        "modname": "lti",
                        "name": "Plain Tool",
                        "contents": [{"type": "url", "fileurl": "http://tool.example/launch"}],
                    },
                ]
            }
        ]
    }

    ltis = await mod._fetch_ltis_web_api(courses, core_contents)

    assert ltis[0]["securetoolurl"] == "https://tool.example/launch"
    assert ltis[0]["toolurl"] == ""
    assert ltis[0]["visible"] == 0
    assert ltis[0]["section_name"] == "Tools"
    assert ltis[0]["_fallback"] is True
    assert ltis[1]["toolurl"] == "http://tool.example/launch"
    assert ltis[1]["securetoolurl"] == ""

    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_ltis_web_api([Course(99, "Missing")], core_contents)


@pytest.mark.asyncio
async def test_lti_web_api_fallback_handles_blank_url_content():
    mod = make_mod(LtiMod)
    courses = [Course(10, "Course")]
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "lti",
                        "name": "Blank Tool",
                        "contents": [{"type": "url"}],
                    }
                ]
            }
        ]
    }

    ltis = await mod._fetch_ltis_web_api(courses, core_contents)

    assert ltis[0]["toolurl"] == ""
    assert ltis[0]["securetoolurl"] == ""


@pytest.mark.asyncio
async def test_lti_real_fetch_builds_launch_files_metadata_and_shortcut():
    mod = make_mod(LtiMod)
    mod.client.async_post.side_effect = [
        {
            "ltis": [
                {
                    "id": 99,
                    "coursemodule": 44,
                    "course": 10,
                    "name": "External Tool",
                    "intro": "<p>Use this</p>",
                    "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                    "securetoolurl": "https://tool.example/launch",
                    "launchcontainer": LtiMod.LAUNCH_CONTAINER_EMBED,
                    "showtitlelaunch": 1,
                    "instructorchoiceacceptgrades": 1,
                    "grade": 100,
                    "password": "secret",
                    "timemodified": 123,
                }
            ]
        },
        {
            "endpoint": "https://tool.example/launch",
            "parameters": [{"name": "lti_message_type", "value": "basic-lti-launch-request"}],
        },
    ]

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})

    files = result[10][44]["files"]
    filenames = [file["filename"] for file in files]
    assert filenames[:2] == ["intro.pdf", "Introduction.html"]
    assert "Launch" in filenames[2]
    assert "Parameters" in filenames[2]
    assert filenames[2].endswith(".json")
    assert "Launch" in filenames[3]
    assert "Form" in filenames[3]
    assert filenames[3].endswith(".html")
    assert filenames[4] == "metadata.json"
    assert "External" in filenames[5]
    assert "Tool" in filenames[5]
    params = json.loads(files[2]["content"])
    assert params["endpoint"] == "https://tool.example/launch"
    assert "basic-lti-launch-request" in files[3]["html"]
    metadata = json.loads(files[4]["content"])
    assert metadata["tool_configuration"]["has_password"] is True
    assert metadata["launch_settings"]["launch_container"]["name"] == "Embed"
    assert metadata["launch_data"]["parameter_count"] == 1
    assert files[5]["content_fileurl"] == "https://tool.example/launch"

    disabled_mod = make_mod(LtiMod, make_config(download_ltis=False))
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}

    fallback_mod = make_mod(LtiMod)
    fallback_mod.client.async_post.side_effect = RequestRejectedError("mobile disabled")
    fallback_mod._fetch_ltis_web_api = AsyncMock(return_value=[])
    assert await fallback_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}
    fallback_mod._fetch_ltis_web_api.assert_awaited_once()


@pytest.mark.asyncio
async def test_lti_real_fetch_skips_launch_sidecars_when_metadata_files_disabled():
    mod = make_mod(LtiMod, make_config(download_metadata_files=False))
    mod.client.async_post.side_effect = [
        {
            "ltis": [
                {
                    "id": 99,
                    "coursemodule": 44,
                    "course": 10,
                    "name": "External Tool",
                    "securetoolurl": "https://tool.example/launch",
                    "timemodified": 123,
                }
            ]
        },
        {
            "endpoint": "https://tool.example/launch",
            "parameters": [{"name": "lti_message_type", "value": "basic-lti-launch-request"}],
        },
    ]

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})

    filenames = [file["filename"] for file in result[10][44]["files"]]
    assert "Launch Parameters.json" not in filenames
    assert "Launch Form.html" not in filenames
    assert "metadata.json" in filenames
    assert "External Tool" in filenames


@pytest.mark.asyncio
async def test_lti_real_fetch_adds_leganto_pdf_file_for_reading_list_launch():
    mod = make_mod(LtiMod)
    mod.client.async_post.side_effect = [
        {
            "ltis": [
                {
                    "id": 99,
                    "coursemodule": 44,
                    "course": 10,
                    "name": "Reading List",
                    "timemodified": 123,
                }
            ]
        },
        {
            "endpoint": "https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1",
            "parameters": [{"name": "id_token", "value": "signed-token"}],
        },
    ]

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})

    files = result[10][44]["files"]
    leganto_file = next(file for file in files if file["type"] == "leganto_pdf")
    payload = json.loads(leganto_file["content"])

    assert leganto_file["filename"] == "Reading List.pdf"
    assert leganto_file["content_fileurl"] == "https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1"
    assert payload == {
        "endpoint": "https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1",
        "parameters": [{"name": "id_token", "value": "signed-token"}],
    }


@pytest.mark.asyncio
async def test_lti_real_fetch_handles_missing_launch_data():
    mod = make_mod(LtiMod)
    mod.client.async_post.side_effect = [
        {
            "ltis": [
                {
                    "id": 99,
                    "coursemodule": 44,
                    "course": 10,
                    "name": "External Tool",
                    "toolurl": "http://tool.example",
                }
            ]
        },
        RuntimeError("launch data unavailable"),
    ]

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]

    assert files[0]["filename"] == "metadata.json"
    assert "External" in files[1]["filename"]
    assert "Tool" in files[1]["filename"]
    metadata = json.loads(files[0]["content"])
    assert "launch_data" not in metadata


@pytest.mark.asyncio
async def test_workshop_real_fetch_builds_instruction_metadata_and_guards():
    mod = make_mod(WorkshopMod)
    mod.client.async_post.return_value = {
        "workshops": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Peer Review",
                "intro": "<p>Intro</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                "instructauthors": "Submit carefully",
                "instructreviewers": "Assess fairly",
                "conclusion": "Thanks",
                "grade": 80,
                "phase": 20,
                "timemodified": 123,
            }
        ]
    }
    mod._get_workshop_access_info = AsyncMock(return_value={"canview": True})
    mod.add_workshops_files = AsyncMock()

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})

    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == [
        "intro.pdf",
        "Workshop intro",
        "Instructions for submission",
        "Instructions for assessment",
        "Conclusion",
        "metadata.json",
    ]
    metadata = json.loads(files[-1]["content"])
    assert metadata["settings"]["grade"] == 80
    assert metadata["settings"]["phase"] == 20
    assert metadata["access_information"] == {"canview": True}
    mod.add_workshops_files.assert_awaited_once()

@pytest.mark.asyncio
async def test_workshop_add_files_guards_and_web_fallback():
    entries = {10: {44: {"id": 99, "files": []}}}

    disabled_mod = make_mod(WorkshopMod, make_config(download_workshops=False))
    with pytest.MonkeyPatch.context() as monkeypatch:
        runner = AsyncMock()
        monkeypatch.setattr(WorkshopMod, "run_async_load_function_on_mod_entries", runner)
        await disabled_mod.add_workshops_files(entries)
        runner.assert_not_called()

    old_mod = make_mod(WorkshopMod, version=2016052300)
    with pytest.MonkeyPatch.context() as monkeypatch:
        runner = AsyncMock()
        monkeypatch.setattr(WorkshopMod, "run_async_load_function_on_mod_entries", runner)
        await old_mod.add_workshops_files(entries)
        runner.assert_not_called()

    current_mod = make_mod(WorkshopMod)
    with pytest.MonkeyPatch.context() as monkeypatch:
        runner = AsyncMock()
        monkeypatch.setattr(WorkshopMod, "run_async_load_function_on_mod_entries", runner)
        await current_mod.add_workshops_files(entries)
        runner.assert_awaited_once()

    fallback = await current_mod._fetch_workshops_web_api(
        [Course(10, "Course")],
        {
            10: [
                {
                    "modules": [
                        {
                            "id": 45,
                            "instance": 100,
                            "modname": "workshop",
                            "name": "Fallback Workshop",
                            "description": "Desc",
                            "timemodified": 222,
                        }
                    ]
                }
            ]
        },
    )
    assert fallback[0]["name"] == "Fallback Workshop"
    assert fallback[0]["grade"] == 100
    assert await current_mod._fetch_workshops_web_api([Course(99, "Missing")], {}) == []


@pytest.mark.asyncio
async def test_workshop_submission_fetchers_foreign_submission_and_access_info():
    mod = make_mod(WorkshopMod)
    mod.client.async_post.return_value = {"submissions": [{"id": 1, "title": "Mine"}]}
    assert await mod._fetch_workshop_submissions_mobile_api({"workshopid": 99}) == [{"id": 1, "title": "Mine"}]

    mod.client.async_post.return_value = {"submissions": []}
    with pytest.raises(KeyError, match="Mobile API"):
        await mod._fetch_workshop_submissions_mobile_api({"workshopid": 99})

    assert await mod._fetch_workshop_submissions_web_api(99) == []

    assessment = {
        "submissionid": 5,
        "feedbackcontentfiles": [{"filename": "content-feedback.pdf"}],
        "feedbackattachmentfiles": [{"filename": "attachment-feedback.pdf"}],
        "feedbackauthor": "Good submission",
        "feedbackreviewer": "Good review",
    }
    mod.client.async_post.return_value = {"submission": {"id": 5, "title": "Peer submission"}}
    submission = await mod.load_foreign_submission(assessment)
    assert submission["id"] == 5
    assert [file["filename"] for file in submission["files"]] == [
        "content-feedback.pdf",
        "attachment-feedback.pdf",
        "Feedback for the author",
        "Feedback for the reviewer",
    ]

    mod.client.async_post.side_effect = RequestRejectedError("denied")
    assert await mod.load_foreign_submission({"submissionid": 5}) is None

    access_mod = make_mod(WorkshopMod)
    access_mod.client.async_post.return_value = {
        "canview": True,
        "cansubmit": True,
        "canpeerassess": True,
        "warnings": [{"warning": "x"}],
    }
    access = await access_mod._get_workshop_access_info(99)
    assert access["canview"] is True
    assert access["cansubmit"] is True
    assert access["warnings"] == [{"warning": "x"}]
    access_mod.client.async_post.side_effect = RuntimeError("unavailable")
    assert await access_mod._get_workshop_access_info(99) == {}


def test_workshop_files_of_workshop_exports_plan_grades_and_submissions():
    mod = make_mod(WorkshopMod)
    files = mod._get_files_of_workshop(
        [
            {
                "id": 5,
                "title": "Submission",
                "content": "<p>Body</p>",
                "contentfiles": [{"filename": "body.pdf", "filepath": "/draft"}],
                "attachmentfiles": [{"filename": "attach.pdf", "filepath": "/"}],
                "files": [{"filename": "foreign-feedback.txt", "filepath": "/"}],
                "timemodified": 123,
            }
        ],
        {
            "assessmentlongstrgrade": "Assessment: 90",
            "submissionlongstrgrade": "Submission: 95",
        },
        {"userplan": {"phases": [{"title": "Submission phase"}], "examples": [{"id": 1}]}},
    )

    assert [file["filename"] for file in files] == [
        "user_plan.json",
        "Assessment grade",
        "Submission grade",
        "body.pdf",
        "attach.pdf",
        "foreign-feedback.txt",
        "Submission",
    ]
    plan = json.loads(files[0]["content"])
    assert plan["phase_count"] == 1
    assert files[3]["type"] == "workshop_file"
    assert files[3]["filepath"] == "/submissions 5/draft"
    assert files[-1]["description"] == "<p>Body</p>"


@pytest.mark.asyncio
async def test_workshop_load_workshop_files_success_and_empty_fallback():
    mod = make_mod(WorkshopMod)
    workshop = {"id": 99, "files": []}
    mod._fetch_workshop_submissions_mobile_api = AsyncMock(return_value=[{"id": 1, "title": "Mine"}])
    mod.run_async_collect_function_on_list = AsyncMock(return_value=[{"id": 2, "title": "Peer"}])
    mod.client.async_post.side_effect = [
        {"assessments": [{"submissionid": 2, "title": "Peer"}]},
        {"assessmentlongstrgrade": "A"},
        {"userplan": {"phases": []}},
    ]
    mod._get_files_of_workshop = Mock(return_value=[{"filename": "summary"}])

    await mod.load_workshop_files(workshop)

    assert workshop["files"] == [{"filename": "summary"}]
    mod._get_files_of_workshop.assert_called_once()
    submissions, grades, user_plan = mod._get_files_of_workshop.call_args.args
    assert submissions == [{"id": 1, "title": "Mine"}, {"id": 2, "title": "Peer"}]
    assert grades == {"assessmentlongstrgrade": "A"}
    assert user_plan == {"userplan": {"phases": []}}

    fallback_mod = make_mod(WorkshopMod)
    fallback_mod._fetch_workshop_submissions_mobile_api = AsyncMock(side_effect=KeyError("none"))
    fallback_mod._fetch_workshop_submissions_web_api = AsyncMock(return_value=[])
    fallback_workshop = {"id": 99, "files": []}
    await fallback_mod.load_workshop_files(fallback_workshop)
    assert fallback_workshop["files"] == []
