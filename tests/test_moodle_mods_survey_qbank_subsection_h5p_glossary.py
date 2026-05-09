import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from moodle_dl.moodle.mods.glossary import GlossaryMod
from moodle_dl.moodle.mods.h5pactivity import H5PActivityMod
from moodle_dl.moodle.mods.qbank import QbankMod
from moodle_dl.moodle.mods.subsection import SubsectionMod
from moodle_dl.moodle.mods.survey import SurveyMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course


def make_config(**values):
    config = Mock()
    config.get_download_glossaries.return_value = values.get("download_glossaries", True)
    config.get_download_h5pactivities.return_value = values.get("download_h5pactivities", True)
    config.get_download_h5p_attempts.return_value = values.get("download_h5p_attempts", False)
    config.get_download_qbanks.return_value = values.get("download_qbanks", True)
    config.get_download_subsections.return_value = values.get("download_subsections", True)
    config.get_download_surveys.return_value = values.get("download_surveys", True)
    return config


def make_mod(cls, config=None, version=2023100900, user_id=7):
    client = Mock()
    client.async_post = AsyncMock()
    return cls(client, version, user_id, {}, config or make_config())


def core_module(modname, **overrides):
    module = {
        "id": 44,
        "instance": 99,
        "modname": modname,
        "name": modname.title(),
        "description": "<p>Intro</p>",
        "visible": 1,
        "timemodified": 123,
    }
    module.update(overrides)
    return module


def test_download_conditions_for_survey_qbank_subsection_h5p_glossary():
    other_file = SimpleNamespace(module_modname="resource", deleted=True)
    deleted_survey = SimpleNamespace(module_modname="survey", deleted=True)
    deleted_qbank = SimpleNamespace(module_modname="qbank", deleted=True)
    deleted_subsection = SimpleNamespace(module_modname="subsection", deleted=True)
    deleted_h5p = SimpleNamespace(module_modname="h5pactivity", deleted=True)
    deleted_glossary = SimpleNamespace(module_modname="glossary", deleted=True)

    assert SurveyMod.download_condition(make_config(download_surveys=True), deleted_survey) is True
    assert SurveyMod.download_condition(make_config(download_surveys=False), deleted_survey) is False
    assert SurveyMod.download_condition(make_config(download_surveys=False), other_file) is True

    assert QbankMod.download_condition(make_config(download_qbanks=True), deleted_qbank) is True
    assert QbankMod.download_condition(make_config(download_qbanks=False), deleted_qbank) is False
    assert QbankMod.download_condition(make_config(download_qbanks=False), other_file) is True

    assert SubsectionMod.download_condition(make_config(download_subsections=True), deleted_subsection) is True
    assert SubsectionMod.download_condition(make_config(download_subsections=False), deleted_subsection) is False
    assert SubsectionMod.download_condition(make_config(download_subsections=False), other_file) is True

    assert H5PActivityMod.download_condition(make_config(download_h5pactivities=True), deleted_h5p) is True
    assert H5PActivityMod.download_condition(make_config(download_h5pactivities=False), deleted_h5p) is False
    assert H5PActivityMod.download_condition(make_config(download_h5pactivities=False), other_file) is True

    assert GlossaryMod.download_condition(make_config(download_glossaries=True), deleted_glossary) is True
    assert GlossaryMod.download_condition(make_config(download_glossaries=False), deleted_glossary) is False
    assert GlossaryMod.download_condition(make_config(download_glossaries=False), other_file) is True


@pytest.mark.asyncio
async def test_qbank_and_subsection_core_content_exports():
    core_contents = {
        10: [
            {
                "id": 3,
                "name": "Week 1",
                "modules": [
                    core_module(
                        "qbank",
                        name="Question Bank",
                        contents=[{"filename": "questions.xml", "filepath": "/"}],
                    ),
                    core_module(
                        "subsection",
                        id=45,
                        instance=100,
                        name="Nested Topic",
                        target=9,
                        contents=[{"filename": "note.txt", "filepath": "/"}],
                    ),
                    core_module("resource"),
                ],
            }
        ]
    }

    qbank_result = await make_mod(QbankMod).real_fetch_mod_entries([Course(10, "Course")], core_contents)
    qbank_files = qbank_result[10][44]["files"]
    qbank_metadata = json.loads(qbank_files[-1]["content"])
    assert qbank_result[10][44]["name"] == "Question Bank"
    assert any(file["filename"] == "questions.xml" for file in qbank_files)
    assert qbank_metadata["section_reference"] == {"section_id": 3, "section_name": "Week 1"}
    assert qbank_metadata["supported_features"]["CAN_DISPLAY"] is False

    subsection_result = await make_mod(SubsectionMod).real_fetch_mod_entries([Course(10, "Course")], core_contents)
    subsection_files = subsection_result[10][45]["files"]
    subsection_metadata = json.loads(subsection_files[-1]["content"])
    assert subsection_result[10][45]["name"] == "Nested Topic"
    assert any(file["filename"] == "note.txt" for file in subsection_files)
    assert subsection_metadata["section_reference"]["target_section_id"] == 9
    assert subsection_metadata["navigation"]["type"] == "Nested section marker"

    assert await make_mod(QbankMod, make_config(download_qbanks=False)).real_fetch_mod_entries(
        [Course(10, "Course")], core_contents
    ) == {}
    assert await make_mod(SubsectionMod, make_config(download_subsections=False)).real_fetch_mod_entries(
        [Course(10, "Course")], core_contents
    ) == {}


@pytest.mark.asyncio
async def test_survey_questions_web_fallback_and_real_fetch():
    mod = make_mod(SurveyMod)
    mod.client.async_post.return_value = {
        "questions": [
            {
                "id": 1,
                "name": "Question",
                "text": "How are you?",
                "shorttext": "Mood",
                "multi": "1",
                "intro": "Intro",
                "type": 2,
                "options": "A,B",
                "parent": 0,
            }
        ]
    }
    questions = await mod._get_survey_questions(99)
    assert questions[0]["shorttext"] == "Mood"
    assert questions[0]["options"] == "A,B"

    mod.client.async_post.side_effect = RuntimeError("questions unavailable")
    assert await mod._get_survey_questions(99) == []

    core_contents = {10: [{"modules": [core_module("survey", name="Survey")]}]}
    web_surveys = await mod._fetch_surveys_web_api([Course(10, "Course")], core_contents)
    assert web_surveys[0]["name"] == "Survey"
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_surveys_web_api([Course(99, "Missing")], core_contents)

    real_mod = make_mod(SurveyMod)
    real_mod.client.async_post.return_value = {
        "surveys": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Survey",
                "intro": "<p>Instructions</p>",
                "template": 3,
                "surveydone": 1,
            }
        ]
    }
    real_mod._get_survey_questions = AsyncMock(return_value=[{"id": 1, "name": "Question"}])
    result = await real_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    metadata = json.loads(next(file["content"] for file in files if file["filename"] == "metadata.json"))
    assert metadata["question_count"] == 1
    assert metadata["completion"]["surveydone"] == 1
    assert any(file["filename"] == "questions.json" for file in files)

    fallback_mod = make_mod(SurveyMod)
    fallback_mod.client.async_post.side_effect = RequestRejectedError("denied")
    fallback_mod._get_survey_questions = AsyncMock(return_value=[])
    fallback_result = await fallback_mod.real_fetch_mod_entries([Course(10, "Course")], core_contents)
    assert fallback_result[10][44]["name"] == "Survey"

    assert await make_mod(SurveyMod, make_config(download_surveys=False)).real_fetch_mod_entries(
        [Course(10, "Course")], core_contents
    ) == {}


@pytest.mark.asyncio
async def test_h5p_real_fetch_attempt_results_and_web_fallback():
    real_mod = make_mod(H5PActivityMod, make_config(download_h5p_attempts=True))
    real_mod.client.async_post.return_value = {
        "h5pactivities": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "H5P",
                "intro": "<p>Play</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                "package": [{"filename": "activity.h5p", "filepath": "/"}],
                "deployedfile": {"filename": "index.html", "fileurl": "https://example.test/h5p/index.html"},
                "grade": 10,
                "enabletracking": 1,
                "timemodified": 123,
            }
        ]
    }
    real_mod.add_h5p_attempts = AsyncMock()

    result = await real_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    metadata = json.loads(next(file["content"] for file in files if file["filename"] == "metadata.json"))
    assert any(file["filename"] == "activity.h5p" and file["type"] == "h5p_package" for file in files)
    assert any(file["filename"] == "index.html" and file["type"] == "h5p_file" for file in files)
    assert metadata["enable_tracking"] is True
    real_mod.add_h5p_attempts.assert_awaited_once()

    attempt_mod = make_mod(H5PActivityMod)
    attempt_mod.client.async_post.return_value = {
        "attemptsdata": [
            {
                "scored": {"rawscore": 2, "maxscore": 4, "scaledscore": 0.5},
                "results": [{"description": "Question", "response": "A", "correctpattern": "B"}],
            }
        ]
    }
    attempt_files = await attempt_mod._get_attempt_results(
        99,
        7,
        {"id": 7, "attempt": 1, "duration": 30, "completion": True, "success": False, "timemodified": 1700000000},
    )
    assert "50.0%" in attempt_files[0]["description"]
    assert "Correct pattern" in attempt_files[0]["description"]

    attempt_mod.client.async_post.return_value = {"attemptsdata": []}
    assert await attempt_mod._get_attempt_results(99, 7, {"attempt": 1}) == []
    attempt_mod.client.async_post.side_effect = RuntimeError("results unavailable")
    assert await attempt_mod._get_attempt_results(99, 7, {"attempt": 1}) == []

    core_contents = {
        10: [
            {
                "modules": [
                    core_module(
                        "h5pactivity",
                        name="H5P",
                        contents=[{"filename": "fallback.h5p", "filepath": "/"}],
                    )
                ]
            }
        ]
    }
    fallback_mod = make_mod(H5PActivityMod)
    fallback_mod.client.async_post.side_effect = RequestRejectedError("denied")
    fallback_result = await fallback_mod.real_fetch_mod_entries([Course(10, "Course")], core_contents)
    assert any(file["filename"] == "fallback.h5p" for file in fallback_result[10][44]["files"])
    with pytest.raises(ValueError, match="Web API"):
        await fallback_mod._fetch_h5pactivities_web_api([Course(99, "Missing")], core_contents)

    assert await make_mod(H5PActivityMod, make_config(download_h5pactivities=False)).real_fetch_mod_entries(
        [Course(10, "Course")], core_contents
    ) == {}


@pytest.mark.asyncio
async def test_glossary_helpers_fetchers_real_fetch_and_fallback():
    mod = make_mod(GlossaryMod)
    assert mod._get_glossary_browse_modes({"browsemodes": ["cat", "author"]}) == ["author", "cat"]
    assert mod._get_glossary_browse_modes({"browsemodes": []}) == ["letter", "date", "author", "cat"]
    assert mod._build_glossary_entries_request(99, "author", 5, 10)[0] == "mod_glossary_get_entries_by_author"
    assert mod._build_glossary_entries_request(99, "cat", 5, 10)[1]["categoryid"] == 0
    with pytest.raises(ValueError, match="Unsupported"):
        mod._build_glossary_entries_request(99, "unknown", 0, 10)
    assert mod._is_invalid_browse_mode_error(Exception("invalidBrowseMode")) is True

    entry_files = mod._create_entry_files(
        {
            "id": 7,
            "glossaryid": 99,
            "concept": "Term One",
            "definition": "<p>Meaning</p>",
            "userfullname": "Alice",
            "aliases": ["Alias A", {"alias": "Alias B"}],
            "categoryname": "Concepts",
            "tags": [{"id": 1, "displayname": "tag"}],
            "attachments": [{"filename": "attachment.pdf", "filepath": "/"}],
            "definitioninlinefiles": [{"filename": "inline.png", "filepath": "/"}],
            "timemodified": 1700000000,
        }
    )
    assert "Alias A" in entry_files[0]["description"]
    assert entry_files[2]["type"] == "glossary_file"
    assert entry_files[3]["type"] == "glossary_file"
    entry_metadata = json.loads(entry_files[1]["content"])
    assert entry_metadata["aliases"] == ["Alias A", "Alias B"]
    assert entry_metadata["attachments_count"] == 1
    assert entry_metadata["inline_files_count"] == 1

    category_mod = make_mod(GlossaryMod)
    category_mod.client.async_post.return_value = {"categories": [{"id": 1, "glossaryid": 99, "name": "Concepts"}]}
    assert await category_mod._get_glossary_categories(99) == [
        {"id": 1, "glossaryid": 99, "name": "Concepts", "usedynalink": 1}
    ]
    category_mod.client.async_post.side_effect = RuntimeError("categories unavailable")
    assert await category_mod._get_glossary_categories(99) == []

    author_mod = make_mod(GlossaryMod)
    author_mod.client.async_post.return_value = {
        "entries": [
            {"userid": 1, "userfullname": "Alice"},
            {"userid": 1, "userfullname": "Alice"},
            {"userid": 2, "userfullname": "Bob"},
        ]
    }
    assert sorted(await author_mod._get_authors_list(99), key=lambda author: author["id"]) == [
        {"name": "Alice", "id": 1},
        {"name": "Bob", "id": 2},
    ]
    author_mod.client.async_post.side_effect = RuntimeError("authors unavailable")
    assert await author_mod._get_authors_list(99) == []

    date_mod = make_mod(GlossaryMod)
    date_mod.client.async_post.return_value = {
        "entries": [{"timecreated": 20}, {"timecreated": 10}],
    }
    date_info = await date_mod._get_entries_by_date_info(99)
    assert date_info["api_available"] is True
    assert date_info["newest_entry_time"] == 20
    date_mod.client.async_post.return_value = {"entries": []}
    assert await date_mod._get_entries_by_date_info(99) == {"api_available": True, "sample_count": 0}
    date_mod.client.async_post.side_effect = RuntimeError("date unavailable")
    assert (await date_mod._get_entries_by_date_info(99))["api_available"] is False

    paged_mod = make_mod(GlossaryMod)
    paged_mod.client.async_post.side_effect = [
        {"count": 2, "entries": [{"id": 1}]},
        {"count": 2, "entries": [{"id": 2}]},
    ]
    assert await paged_mod._fetch_glossary_entries_by_mode(99, "date") == [{"id": 1}, {"id": 2}]
    assert [call.args[1]["from"] for call in paged_mod.client.async_post.await_args_list] == [0, 1]

    core_contents = {10: [{"modules": [core_module("glossary", name="Glossary")]}]}
    web_glossaries = await mod._fetch_glossaries_web_api([Course(10, "Course")], core_contents)
    assert web_glossaries[0]["name"] == "Glossary"
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_glossaries_web_api([Course(99, "Missing")], core_contents)

    real_mod = make_mod(GlossaryMod)
    real_mod.client.async_post.return_value = {
        "glossaries": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Glossary",
                "intro": "<p>Terms</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                "entries": 2,
                "browsemodes": ["letter"],
            }
        ]
    }
    real_mod._get_glossary_categories = AsyncMock(return_value=[{"id": 1, "name": "Concepts"}])
    real_mod.add_glossary_entries = AsyncMock()
    result = await real_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    metadata = json.loads(next(file["content"] for file in files if file["filename"] == "metadata.json"))
    assert metadata["categories"] == [{"id": 1, "name": "Concepts"}]
    assert any(file["filename"] == "categories.json" for file in files)
    assert result[10][44]["entries"] == 2
    real_mod.add_glossary_entries.assert_awaited_once()

    assert await make_mod(GlossaryMod, make_config(download_glossaries=False)).real_fetch_mod_entries(
        [Course(10, "Course")], core_contents
    ) == {}
