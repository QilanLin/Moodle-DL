import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from moodle_dl.moodle.mods.lesson import LessonMod
from moodle_dl.moodle.mods.scorm import ScormMod
from moodle_dl.moodle.mods.wiki import WikiMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course


def make_config(**values):
    config = Mock()
    config.get_download_wikis.return_value = values.get("download_wikis", True)
    config.get_download_lessons.return_value = values.get("download_lessons", True)
    config.get_download_scorms.return_value = values.get("download_scorms", True)
    config.get_download_scorm_scos.return_value = values.get("download_scorm_scos", False)
    config.get_download_scorm_attempts.return_value = values.get("download_scorm_attempts", False)
    return config


def make_mod(cls, config=None, version=2023100900, user_id=7):
    client = Mock()
    client.async_post = AsyncMock()
    return cls(client, version, user_id, {}, config or make_config())


def test_wiki_lesson_scorm_download_conditions():
    deleted_wiki = SimpleNamespace(module_modname="wiki", deleted=True)
    deleted_lesson = SimpleNamespace(module_modname="lesson", deleted=True)
    deleted_scorm = SimpleNamespace(module_modname="scorm", deleted=True)
    other_file = SimpleNamespace(module_modname="resource", deleted=True)

    assert WikiMod.download_condition(make_config(download_wikis=True), deleted_wiki) is True
    assert WikiMod.download_condition(make_config(download_wikis=False), deleted_wiki) is False
    assert WikiMod.download_condition(make_config(download_wikis=False), other_file) is True

    assert LessonMod.download_condition(make_config(download_lessons=True), deleted_lesson) is True
    assert LessonMod.download_condition(make_config(download_lessons=False), deleted_lesson) is False
    assert LessonMod.download_condition(make_config(download_lessons=False), other_file) is True

    assert ScormMod.download_condition(make_config(download_scorms=True), deleted_scorm) is True
    assert ScormMod.download_condition(make_config(download_scorms=False), deleted_scorm) is False
    assert ScormMod.download_condition(make_config(download_scorms=False), other_file) is True


@pytest.mark.asyncio
async def test_wiki_mobile_and_web_api_fetch_paths():
    mod = make_mod(WikiMod)
    courses = [Course(10, "Course")]
    mod.client.async_post.return_value = {"wikis": [{"id": 99, "coursemodule": 44, "course": 10}]}

    assert await mod._fetch_wikis_mobile_api(courses) == [{"id": 99, "coursemodule": 44, "course": 10}]
    mod.client.async_post.assert_awaited_once_with(
        "mod_wiki_get_wikis_by_courses",
        {"courseids": {"0": 10}},
    )

    mod.client.async_post.reset_mock()
    mod.client.async_post.return_value = {"wikis": []}
    with pytest.raises(KeyError, match="Mobile API"):
        await mod._fetch_wikis_mobile_api(courses)

    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "wiki",
                        "name": "Knowledge Base",
                        "timemodified": 123,
                        "timecreated": 100,
                        "contents": [{"type": "file", "filename": "intro.pdf"}],
                    }
                ]
            }
        ]
    }
    wikis = await mod._fetch_wikis_web_api(courses, core_contents)

    assert wikis[0]["id"] == 99
    assert wikis[0]["coursemodule"] == 44
    assert wikis[0]["introfiles"] == [{"type": "file", "filename": "intro.pdf"}]
    assert wikis[0]["wikimode"] == "collaborative"

    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_wikis_web_api([Course(99, "Missing")], core_contents)


@pytest.mark.asyncio
async def test_wiki_real_fetch_builds_metadata_and_uses_fallback():
    mod = make_mod(WikiMod)
    mod._fetch_wikis_mobile_api = AsyncMock(
        return_value=[
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Knowledge Base",
                "intro": "<p>Welcome</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                "wikimode": "individual",
                "firstpagetitle": "Start",
                "timemodified": 123,
            }
        ]
    )
    mod.add_wiki_contents = AsyncMock()

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})

    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == ["intro.pdf", "Introduction.html", "metadata.json"]
    metadata = json.loads(files[-1]["content"])
    assert metadata["wiki_id"] == 99
    assert metadata["settings"]["wikimode"] == "individual"
    mod.add_wiki_contents.assert_awaited_once()

    disabled_mod = make_mod(WikiMod, make_config(download_wikis=False))
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}

    fallback_mod = make_mod(WikiMod)
    fallback_mod._fetch_wikis_mobile_api = AsyncMock(side_effect=RuntimeError("mobile disabled"))
    fallback_mod._fetch_wikis_web_api = AsyncMock(return_value=[])
    fallback_mod.add_wiki_contents = AsyncMock()
    assert await fallback_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}
    fallback_mod._fetch_wikis_web_api.assert_awaited_once()


@pytest.mark.asyncio
async def test_wiki_content_loading_and_fetch_wrappers():
    mod = make_mod(WikiMod)
    wiki = {"id": 99, "name": "Knowledge Base", "files": []}
    mod.client.async_post.return_value = {
        "subwikis": [
            {"id": 1, "wikiid": 99, "groupid": 3, "userid": 0},
            {"id": 2, "wikiid": 99, "groupid": 0, "userid": 8},
        ]
    }
    mod._get_subwiki_contents = AsyncMock(side_effect=[[{"filename": "group"}], [{"filename": "user"}]])

    await mod.load_wiki_contents(wiki)
    assert wiki["files"] == [{"filename": "group"}, {"filename": "user"}]

    mod.client.async_post.side_effect = RequestRejectedError("denied")
    wiki = {"id": 99, "name": "Knowledge Base", "files": []}
    await mod.load_wiki_contents(wiki)
    assert wiki["files"] == []

    mod.client.async_post.side_effect = None
    mod.client.async_post.return_value = {"pages": [{"id": 10}]}
    assert await mod._fetch_wiki_pages_mobile_api(99, 0, 0, 1) == [{"id": 10}]
    mod.client.async_post.side_effect = RuntimeError("unavailable")
    assert await mod._fetch_wiki_pages_mobile_api(99, 0, 0, 1) == []

    mod.client.async_post.side_effect = None
    mod.client.async_post.return_value = {"page": {"cachedcontent": "body"}}
    assert await mod._fetch_wiki_page_contents_mobile_api(10) == {"page": {"cachedcontent": "body"}}
    mod.client.async_post.side_effect = RuntimeError("unavailable")
    assert await mod._fetch_wiki_page_contents_mobile_api(10) == {"page": {}}

    mod.client.async_post.side_effect = None
    mod.client.async_post.return_value = {"files": [{"filename": "attachment.pdf"}]}
    assert await mod._fetch_wiki_subwiki_files_mobile_api(99, 0, 0, 1) == [{"filename": "attachment.pdf"}]
    mod.client.async_post.side_effect = RuntimeError("unavailable")
    assert await mod._fetch_wiki_subwiki_files_mobile_api(99, 0, 0, 1) == []


@pytest.mark.asyncio
async def test_wiki_subwiki_and_page_content_files():
    mod = make_mod(WikiMod)
    mod._fetch_wiki_pages_mobile_api = AsyncMock(return_value=[{"id": 10, "title": "Home"}])
    mod._fetch_wiki_subwiki_files_mobile_api = AsyncMock(
        return_value=[{"filename": "attachment.pdf", "filepath": "/docs/"}]
    )
    mod._get_page_content = AsyncMock(return_value=[{"filename": "Home"}])

    files = await mod._get_subwiki_contents({"id": 1, "wikiid": 99, "groupid": 3}, "Knowledge Base")
    assert files[0]["filename"] == "attachment.pdf"
    assert files[0]["filepath"] == "/group_3/docs/"
    assert files[0]["type"] == "wiki_file"
    assert files[1] == {"filename": "Home"}

    mod._fetch_wiki_pages_mobile_api = AsyncMock(side_effect=RuntimeError("no pages"))
    assert await mod._get_subwiki_contents({"id": 1, "wikiid": 99}, "Knowledge Base") == []

    page_mod = make_mod(WikiMod)
    page_mod._fetch_wiki_page_contents_mobile_api = AsyncMock(
        return_value={
            "page": {
                "cachedcontent": "<p>Body</p>",
                "timemodified": 123,
                "tags": [{"displayname": "Guide"}, {"rawname": "Draft"}],
            }
        }
    )
    page_files = await page_mod._get_page_content({"id": 10, "title": "Home Page"}, "/collaborative", "Wiki")
    assert "Home" in page_files[0]["filename"]
    assert "Page" in page_files[0]["filename"]
    assert page_files[0]["filepath"] == "/collaborative/pages/"
    assert "<h1>Home Page</h1>" in page_files[0]["html"]
    assert "Home" in page_files[1]["filename"]
    assert "Page" in page_files[1]["filename"]
    assert "tags" in page_files[1]["filename"]
    assert "- Guide" in page_files[1]["description"]

    page_mod._fetch_wiki_page_contents_mobile_api = AsyncMock(return_value={"page": {"cachedcontent": ""}})
    assert await page_mod._get_page_content({"id": 10, "title": "Empty"}, "/collaborative", "Wiki") == []
    page_mod._fetch_wiki_page_contents_mobile_api = AsyncMock(side_effect=RequestRejectedError("denied"))
    assert await page_mod._get_page_content({"id": 10, "title": "Denied"}, "/collaborative", "Wiki") == []


@pytest.mark.asyncio
async def test_lesson_web_fallback_access_and_question_helpers():
    mod = make_mod(LessonMod)
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "lesson",
                        "name": "Lesson",
                        "description": "<p>Intro</p>",
                        "timemodified": 123,
                    }
                ]
            }
        ]
    }

    lessons = await mod._fetch_lessons_web_api([Course(10, "Course")], core_contents)
    assert lessons[0]["id"] == 99
    assert lessons[0]["coursemodule"] == 44
    assert lessons[0]["intro"] == "<p>Intro</p>"
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_lessons_web_api([Course(99, "Missing")], core_contents)

    mod.client.async_post.return_value = {
        "canmanage": True,
        "attemptscount": 2,
        "preventaccessreasons": ["closed"],
        "warnings": [{"warning": "x"}],
    }
    access = await mod._get_lesson_access_info(99)
    assert access["canmanage"] is True
    assert access["attemptscount"] == 2
    assert access["preventaccessreasons"] == ["closed"]
    mod.client.async_post.side_effect = RuntimeError("unavailable")
    assert await mod._get_lesson_access_info(99) == {}

    qa_mod = make_mod(LessonMod)
    assert await qa_mod._get_questions_and_answers({"lesson_id": 0}) == []
    qa_mod.client.async_post.return_value = {
        "attempts": [
            {
                "id": 5,
                "title": "Question One",
                "contents": "<p>What?</p>",
                "useranswer": "A",
                "correctanswer": "B",
                "response": "Try again",
                "earned": 1,
                "total": 2,
                "timeseen": 456,
            }
        ]
    }
    files = await qa_mod._get_questions_and_answers(
        {"lesson_id": 99, "userstats": {"lessonsclosed": 1}}
    )
    assert files[0]["filename"].startswith("Q5")
    assert "Question" in files[0]["filename"]
    assert "One" in files[0]["filename"]
    assert "## Question" in files[0]["description"]
    assert "1 / 2" in files[0]["description"]
    qa_mod.client.async_post.side_effect = RequestRejectedError("denied")
    assert await qa_mod._get_questions_and_answers({"lesson_id": 99}) == []


@pytest.mark.asyncio
async def test_lesson_real_fetch_attempt_page_and_attempt_files():
    mod = make_mod(LessonMod)
    mod.client.async_post.return_value = {
        "lessons": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Lesson",
                "intro": "<p>Intro</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                "mediafiles": [{"filename": "media.mp4", "filepath": "/"}],
                "grade": 10,
                "timemodified": 123,
            }
        ]
    }
    mod._get_lesson_access_info = AsyncMock(return_value={"attemptscount": 1})
    mod.add_lessons_files = AsyncMock()

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    assert [file["filename"] for file in files[:2]] == ["intro.pdf", "media.mp4"]
    assert "Lesson" in files[2]["filename"]
    assert "intro" in files[2]["filename"]
    assert files[3]["filename"] == "metadata.json"
    metadata = json.loads(files[-1]["content"])
    assert metadata["lesson_id"] == 99
    assert metadata["access_information"] == {"attemptscount": 1}

    disabled_mod = make_mod(LessonMod, make_config(download_lessons=False))
    disabled_mod.client.async_post.return_value = {"lessons": []}
    disabled_mod.add_lessons_files = AsyncMock()
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}

    page_mod = make_mod(LessonMod)
    page_mod.client.async_post.return_value = {
        "contentfiles": [{"filename": "page.pdf", "filepath": "/"}],
        "pagecontent": "<p>Page</p><script>alert(1)</script>",
    }
    page_files = await page_mod.load_attempt_page({"page": {"id": 5, "lessonid": 99}})
    assert page_files[0]["type"] == "lesson_file"
    assert page_files[1]["content"] == "<p>Page</p>"
    page_mod.client.async_post.side_effect = RequestRejectedError("denied")
    assert await page_mod.load_attempt_page({"page": {"id": 5, "lessonid": 99}}) == []

    attempt_mod = make_mod(LessonMod)
    attempt_mod._get_questions_and_answers = AsyncMock(return_value=[{"filename": "Q1"}])
    attempt_mod.run_async_collect_function_on_list = AsyncMock(
        return_value=[
            {"_is_page_content": True, "content": "<p>Page one</p>"},
            {"filename": "asset.pdf", "fileurl": "https://moodle/page_contents/5/asset.pdf", "filesize": 10},
            {"filename": "asset.pdf", "fileurl": "https://moodle/page_contents/6/asset.pdf", "filesize": 10},
        ]
    )
    attempt_files = await attempt_mod._get_files_of_attempt(
        {
            "lesson_name": "Lesson",
            "lesson_id": 99,
            "userstats": {"completed": 100, "gradeinfo": {"earned": 8, "total": 10}},
            "answerpages": [{"page": {"id": 1, "lessonid": 99, "timemodified": 200}}],
        }
    )
    assert [file["filename"] for file in attempt_files] == ["grade", "Q1", "asset.pdf", "Lesson"]
    assert attempt_files[-1]["type"] == "html"


@pytest.mark.asyncio
async def test_scorm_real_fetch_sco_attempts_and_web_fallback():
    mod = make_mod(ScormMod, make_config(download_scorm_scos=True))
    mod.client.async_post.return_value = {
        "scorms": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "SCORM Package",
                "intro": "<p>Intro</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                "packageurl": "https://example.test/pkg.zip",
                "packagesize": 2048,
                "version": "SCORM_12",
                "maxgrade": 100,
                "timemodified": 123,
            }
        ]
    }
    mod.add_scorm_scos = AsyncMock()

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    assert files[0]["filename"] == "intro.pdf"
    assert "SCORM" in files[1]["filename"]
    assert "intro" in files[1]["filename"]
    assert "SCORM" in files[2]["filename"]
    assert "Package" in files[2]["filename"]
    assert files[2]["filename"].endswith(".zip")
    assert files[3]["filename"] == "metadata.json"
    assert files[2]["type"] == "scorm_package"
    metadata = json.loads(files[-1]["content"])
    assert metadata["scorm_id"] == 99
    assert metadata["grade"]["max_grade"] == 100
    mod.add_scorm_scos.assert_awaited_once()

    disabled_mod = make_mod(ScormMod, make_config(download_scorms=False))
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}

    fallback = await mod._fetch_scorms_web_api(
        [Course(10, "Course")],
        {10: [{"modules": [{"id": 45, "instance": 100, "modname": "scorm", "name": "Fallback"}]}]},
    )
    assert fallback[0]["name"] == "Fallback"
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_scorms_web_api([Course(99, "Missing")], {})


@pytest.mark.asyncio
async def test_scorm_summary_and_user_attempt_helpers():
    mod = make_mod(ScormMod, make_config(download_scorm_attempts=True))
    scos = [
        {"id": 1, "title": "Course", "identifier": "org", "scormtype": "organization"},
        {"id": 2, "title": "Intro SCO", "identifier": "sco", "scormtype": "sco", "launch": "index.html"},
        {"id": 3, "title": "Asset", "scormtype": "asset"},
    ]
    summary = mod._create_sco_summary(scos)
    assert summary["filename"] == "SCO_Structure"
    assert "Total SCOs: 3" in summary["description"]
    assert "Intro SCO" in summary["description"]
    assert mod._create_sco_summary([]) is None

    attempt = mod._create_attempt_summary(
        1,
        [
            {
                "scoid": 2,
                "defaultdata": [{"element": "cmi.core.score.max", "value": "100"}],
                "userdata": [
                    {"element": "cmi.core.lesson_status", "value": "completed"},
                    {"element": "custom.note", "value": "seen"},
                ],
            }
        ],
        scos,
    )
    assert attempt["filename"] == "Attempt_1"
    assert "Lesson Status" in attempt["description"]
    assert "custom.note: seen" in attempt["description"]
    assert mod._create_attempt_summary(1, [], scos) is None

    scorm = {"id": 99, "files": []}
    mod.client.async_post.side_effect = [
        {"scoes": scos},
        {"data": [{"scoid": 2, "userdata": [{"element": "cmi.core.lesson_status", "value": "done"}]}]},
        {"data": []},
    ]
    await mod.load_scorm_scos(scorm)
    assert [file["filename"] for file in scorm["files"]] == ["SCO_Structure", "Attempt_1"]

    denied_mod = make_mod(ScormMod)
    denied_mod.client.async_post.side_effect = RequestRejectedError("denied")
    denied_scorm = {"id": 99, "files": []}
    await denied_mod.load_scorm_scos(denied_scorm)
    assert denied_scorm["files"] == []
