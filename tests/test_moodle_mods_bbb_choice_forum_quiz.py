import json
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from moodle_dl.moodle.mods.bigbluebuttonbn import BigbluebuttonbnMod
from moodle_dl.moodle.mods.choice import ChoiceMod
from moodle_dl.moodle.mods.forum import ForumMod
from moodle_dl.moodle.mods.quiz import QuizMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course


def make_config(**values):
    config = Mock()
    config.get_download_bigbluebuttonbns.return_value = values.get("download_bigbluebuttonbns", True)
    config.get_download_choices.return_value = values.get("download_choices", True)
    config.get_download_forums.return_value = values.get("download_forums", True)
    config.get_download_quizzes.return_value = values.get("download_quizzes", True)
    return config


def make_mod(cls, config=None, version=2023100900, user_id=7, last_timestamps=None):
    client = Mock()
    client.async_post = AsyncMock()
    return cls(client, version, user_id, last_timestamps or {}, config or make_config())


def test_download_conditions_for_bbb_choice_forum_quiz():
    other_file = SimpleNamespace(module_modname="resource", deleted=True)
    deleted_bbb = SimpleNamespace(module_modname="bigbluebuttonbn", deleted=True)
    deleted_choice = SimpleNamespace(module_modname="choice", deleted=True)
    deleted_forum = SimpleNamespace(module_modname="forum", deleted=True)
    deleted_quiz = SimpleNamespace(module_modname="quiz", deleted=True)

    assert BigbluebuttonbnMod.download_condition(make_config(download_bigbluebuttonbns=True), deleted_bbb) is True
    assert BigbluebuttonbnMod.download_condition(make_config(download_bigbluebuttonbns=False), deleted_bbb) is False
    assert BigbluebuttonbnMod.download_condition(make_config(download_bigbluebuttonbns=False), other_file) is True

    assert ChoiceMod.download_condition(make_config(download_choices=True), deleted_choice) is True
    assert ChoiceMod.download_condition(make_config(download_choices=False), deleted_choice) is False
    assert ChoiceMod.download_condition(make_config(download_choices=False), other_file) is True

    assert ForumMod.download_condition(make_config(download_forums=True), deleted_forum) is True
    assert ForumMod.download_condition(make_config(download_forums=False), deleted_forum) is False
    assert ForumMod.download_condition(make_config(download_forums=False), other_file) is True

    assert QuizMod.download_condition(make_config(download_quizzes=True), deleted_quiz) is True
    assert QuizMod.download_condition(make_config(download_quizzes=False), deleted_quiz) is False
    assert QuizMod.download_condition(make_config(download_quizzes=False), other_file) is True


def test_bbb_status_and_timestamp_helpers():
    mod = make_mod(BigbluebuttonbnMod)

    assert mod._get_status_name({"statusrunning": True}) == "Running"
    assert mod._get_status_name({"statusclosed": True}) == "Closed"
    assert mod._get_status_name({"statusopen": True}) == "Open"
    assert mod._get_status_name({}) == "Unknown"
    assert mod._format_timestamp(0) == {"unix": 0, "readable": "N/A"}
    assert mod._format_timestamp(1700000000)["unix"] == 1700000000


@pytest.mark.asyncio
async def test_bbb_web_fallback_and_real_fetch_metadata():
    mod = make_mod(BigbluebuttonbnMod)
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "bigbluebuttonbn",
                        "name": "Live Room",
                        "description": "<p>Join</p>",
                        "timecreated": 100,
                        "timemodified": 123,
                    }
                ]
            }
        ]
    }

    fallback = await mod._fetch_bigbluebuttonbns_web_api([Course(10, "Course")], core_contents)
    assert fallback[0]["id"] == 99
    assert fallback[0]["coursemodule"] == 44
    assert fallback[0]["intro"] == "<p>Join</p>"
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_bigbluebuttonbns_web_api([Course(99, "Missing")], core_contents)

    mod.client.async_post.side_effect = [
        {
            "bigbluebuttonbns": [
                {
                    "id": 99,
                    "coursemodule": 44,
                    "course": 10,
                    "name": "Live Room",
                    "intro": "<p>Join</p>",
                    "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                    "meetingid": "meet-99",
                    "timemodified": 123,
                }
            ]
        },
        {
            "statusrunning": True,
            "statusmessage": "In progress",
            "presentations": [{"name": "Slides.pdf", "url": "https://example.test/slides.pdf"}],
            "features": [{"name": "recording", "isenabled": True}],
            "participantcount": 12,
            "canjoin": True,
        },
        {
            "status": True,
            "tabledata": {
                "data": json.dumps(
                    [
                        {
                            "id": "rec-1",
                            "name": "Lecture",
                            "duration": "30",
                            "playback": {"type": "presentation", "url": "https://example.test/rec"},
                        }
                    ]
                ),
                "locale": "en",
                "ping_interval": 30,
            },
        },
    ]

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    assert [file["filename"] for file in files[:3]] == ["intro.pdf", "Introduction.html", "Slides.pdf"]
    assert "Recording" in files[3]["filename"]
    assert "Lecture" in files[3]["filename"]
    assert files[4]["filename"] == "metadata.json"
    metadata = json.loads(files[-1]["content"])
    assert metadata["meeting_details"]["status"] == "Running"
    assert metadata["meeting_details"]["features"] == {"recording": True}
    assert metadata["recordings"]["total_count"] == 1

    disabled_mod = make_mod(BigbluebuttonbnMod, make_config(download_bigbluebuttonbns=False))
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}


def test_choice_names_and_markdown_formatters():
    mod = make_mod(ChoiceMod)

    assert mod._get_show_results_name(ChoiceMod.RESULTS_ALWAYS) == "Always"
    assert mod._get_show_results_name(999) == "Unknown"
    assert mod._get_display_mode_name(0) == "Horizontal"
    assert mod._get_display_mode_name(999) == "Unknown"

    options_md = mod._format_options(
        "Preference",
        [
            {"text": "A", "maxanswers": 2, "countanswers": 1},
            {"text": "B", "countanswers": 3, "disabled": True},
        ],
    )
    assert "# Preference - Available Options" in options_md
    assert "- **Limit:** 2 answers" in options_md
    assert "- **Status:** Disabled" in options_md

    results_md = mod._format_results(
        "Preference",
        [
            {"text": "A", "userresponses": [{"fullname": "Alice"}]},
            {"text": "B", "userresponses": [{"fullname": "Bob"}, {"fullname": "Cara"}]},
        ],
    )
    assert "**Total responses:** 3" in results_md
    assert "- **Votes:** 1 (33.3%)" in results_md
    assert "  - Alice" in results_md


@pytest.mark.asyncio
async def test_choice_web_fallback_and_real_fetch():
    mod = make_mod(ChoiceMod)
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "choice",
                        "name": "Pick one",
                        "description": "<p>Choose</p>",
                        "timecreated": 100,
                        "timemodified": 123,
                    }
                ]
            }
        ]
    }

    choices = await mod._fetch_choices_web_api([Course(10, "Course")], core_contents)
    assert choices[0]["name"] == "Pick one"
    assert choices[0]["display"] == 0
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_choices_web_api([Course(99, "Missing")], core_contents)

    mod.client.async_post.side_effect = [
        {
            "choices": [
                {
                    "id": 99,
                    "coursemodule": 44,
                    "course": 10,
                    "name": "Pick one",
                    "showresults": ChoiceMod.RESULTS_AFTER_ANSWER,
                    "display": 1,
                    "timemodified": 123,
                }
            ]
        },
        {"options": [{"text": "A", "countanswers": 1}], "canedit": True, "candelete": False},
        {"options": [{"text": "A", "userresponses": [{"fullname": "Alice"}]}], "publishinfo": True},
    ]

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == ["Options.md", "Results.md", "metadata.json"]
    metadata = json.loads(files[-1]["content"])
    assert metadata["settings"]["show_results"]["name"] == "After answering"
    assert metadata["settings"]["display_mode"]["name"] == "Vertical"
    assert metadata["options_summary"]["total_options"] == 1
    assert metadata["results_summary"]["total_answers"] == 1

    disabled_mod = make_mod(ChoiceMod, make_config(download_choices=False))
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}


@pytest.mark.asyncio
async def test_forum_real_fetch_add_posts_guards_and_discussion_fetchers():
    mod = make_mod(ForumMod)
    mod.client.async_post.return_value = [
        {
            "id": 99,
            "cmid": 44,
            "course": 10,
            "name": "Forum",
            "intro": "<p>Discuss</p>",
            "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
            "type": "qanda",
            "numdiscussions": 3,
            "timemodified": 123,
        }
    ]
    mod.add_forum_posts = AsyncMock()

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == ["intro.pdf", "Forum intro", "metadata.json"]
    metadata = json.loads(files[-1]["content"])
    assert metadata["settings"]["type"] == "qanda"
    assert metadata["counts"]["discussions"] == 3
    mod.add_forum_posts.assert_awaited_once()

    entries = {10: {44: {"id": 99, "name": "Forum", "files": []}}}
    with patch.object(ForumMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_mod(ForumMod, make_config(download_forums=False)).add_forum_posts(entries)
        runner.assert_not_called()
    with patch.object(ForumMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_mod(ForumMod, version=2013051400).add_forum_posts(entries)
        runner.assert_not_called()
    with patch.object(ForumMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_mod(ForumMod).add_forum_posts(entries)
        runner.assert_awaited_once()

    old_mod = make_mod(ForumMod, version=2013051400)
    with pytest.raises(NotImplementedError):
        await old_mod._fetch_discussions_mobile_api({"id": 99, "name": "Forum"}, 0, 0, [])

    fetch_mod = make_mod(ForumMod)
    fetch_mod.client.async_post.return_value = {
        "discussions": [
            {"subject": "New", "timemodified": 200, "discussion": 1, "created": 100},
            {"subject": "Old", "timemodified": 50, "discussion": 2, "created": 40},
        ]
    }
    latest, done = await fetch_mod._fetch_discussions_mobile_api({"id": 99, "name": "Forum"}, 0, 100, [])
    assert latest == [{"subject": "New", "timemodified": 200, "discussion_id": 1, "created": 100}]
    assert done is True

    fetch_mod.client.async_post.return_value = {"discussions": []}
    assert await fetch_mod._fetch_discussions_mobile_api({"id": 99, "name": "Forum"}, 1, 0, []) == ([], True)
    assert await fetch_mod._fetch_discussions_web_api({"id": 99}, 0) == []


@pytest.mark.asyncio
async def test_forum_posts_loading_and_inline_deduplication():
    mod = make_mod(ForumMod)
    mod._fetch_discussion_posts_mobile_api = AsyncMock(
        return_value=[
            {
                "id": 5,
                "parentid": 2,
                "message": "<p>Hello</p>",
                "timecreated": 200,
                "author": {"fullname": "Alice"},
                "urls": {"view": "https://example.test/post/5"},
                "attachments": [
                    {
                        "filename": "file.pdf",
                        "filepath": "/",
                        "url": "https://example.test/pluginfile.php/1/forum/attachment/file.pdf",
                    }
                ],
            }
        ]
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        files = await mod.load_files_of_discussion({"discussion_id": 10, "subject": "Topic", "created": 1700000000})

    assert not [warning for warning in caught if "utcfromtimestamp" in str(warning.message)]
    assert "5" in files[0]["filename"]
    assert "Alice" in files[0]["filename"]
    assert "response" in files[0]["filename"]
    assert "2" in files[0]["filename"]
    assert files[0]["filepath"] == "23-11-14 Topic"
    assert files[0]["type"] == "description"
    assert files[1]["type"] == "forum_file"
    assert "/webservice/pluginfile.php/" in files[1]["fileurl"]

    mod._fetch_discussion_posts_mobile_api = AsyncMock(side_effect=RuntimeError("mobile disabled"))
    mod._fetch_discussion_posts_web_api = AsyncMock(return_value=[])
    assert await mod.load_files_of_discussion({"discussion_id": 10, "subject": "Topic"}) == []

    fetch_mod = make_mod(ForumMod)
    fetch_mod.client.async_post.return_value = {"posts": [{"id": 1}]}
    assert await fetch_mod._fetch_discussion_posts_mobile_api({"discussionid": 10}) == [{"id": 1}]
    fetch_mod.client.async_post.return_value = {"posts": []}
    with pytest.raises(KeyError, match="Mobile API"):
        await fetch_mod._fetch_discussion_posts_mobile_api({"discussionid": 10})
    assert await fetch_mod._fetch_discussion_posts_web_api(10) == []

    post_files = [{"filename": "same.png", "filesize": 10, "fileurl": "https://x/attachment/same.png"}]
    inline = [
        {"filename": "same.png", "filesize": 10, "fileurl": "https://x/post/same.png"},
        {"filename": "new.png", "filesize": 11, "fileurl": "https://x/post/new.png"},
    ]
    mod.add_legacy_inline_files(inline, post_files)
    assert [file["filename"] for file in post_files] == ["same.png", "new.png"]


@pytest.mark.asyncio
async def test_quiz_helpers_web_fallback_real_fetch_and_attempt_loading():
    mod = make_mod(QuizMod)
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "quiz",
                        "name": "Quiz",
                        "description": "<p>Intro</p>",
                        "timemodified": 123,
                    }
                ]
            }
        ]
    }

    quizzes = await mod._fetch_quizzes_web_api([Course(10, "Course")], core_contents)
    assert quizzes[0]["id"] == 99
    assert quizzes[0]["coursemodule"] == 44
    assert quizzes[0]["intro"] == "<p>Intro</p>"
    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_quizzes_web_api([Course(99, "Missing")], core_contents)

    mod.client.async_post.side_effect = [
        {"accessrules": ["password"], "canattempt": True, "warnings": []},
        {"hasgrade": True, "grade": 8.5, "gradetopass": 5},
        {"someoptions": {"marks": True}, "alloptions": {"feedback": True}},
        {"feedbacktext": "Good work"},
    ]
    assert (await mod._get_quiz_access_info(99))["canattempt"] is True
    assert (await mod._get_user_best_grade(99))["grade"] == 8.5
    assert (await mod._get_combined_review_options(99))["someoptions"] == {"marks": True}
    assert await mod._get_quiz_feedback(99, 8.5) == "Good work"

    error_mod = make_mod(QuizMod)
    error_mod.client.async_post.side_effect = RuntimeError("unavailable")
    assert await error_mod._get_quiz_access_info(99) == {}
    assert await error_mod._get_user_best_grade(99) == {}
    assert await error_mod._get_combined_review_options(99) == {}
    assert await error_mod._get_quiz_feedback(99, 5) == ""

    real_mod = make_mod(QuizMod)
    real_mod.client.async_post.return_value = {
        "quizzes": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Quiz",
                "intro": "<p>Intro</p>",
                "introfiles": [{"filename": "intro.pdf", "filepath": "/"}],
                "grade": 10,
                "timemodified": 123,
            }
        ]
    }
    real_mod._get_quiz_access_info = AsyncMock(return_value={"canattempt": True})
    real_mod._get_user_best_grade = AsyncMock(return_value={"hasgrade": True, "grade": 9})
    real_mod._get_combined_review_options = AsyncMock(return_value={"someoptions": {}})
    real_mod._get_quiz_feedback = AsyncMock(return_value="Great")
    real_mod.add_quizzes_files = AsyncMock()

    result = await real_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == ["intro.pdf", "Introduction.html", "metadata.json"]
    metadata = json.loads(files[-1]["content"])
    assert metadata["user_grade"] == {"hasgrade": True, "grade": 9}
    assert metadata["feedback"] == "Great"

    entries = {10: {44: {"id": 99, "files": []}}}
    with patch.object(QuizMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_mod(QuizMod, make_config(download_quizzes=False)).add_quizzes_files(entries)
        runner.assert_not_called()
    with patch.object(QuizMod, "run_async_load_function_on_mod_entries", new_callable=AsyncMock) as runner:
        await make_mod(QuizMod, version=2015051100).add_quizzes_files(entries)
        runner.assert_not_called()


@pytest.mark.asyncio
async def test_quiz_load_quiz_files_and_attempt_files():
    mod = make_mod(QuizMod)
    quiz = {"id": 99, "name": "Quiz", "files": []}
    mod.client.async_post.return_value = {
        "attempts": [{"id": 5, "state": "finished"}, {"id": 6, "state": "inprogress"}]
    }
    mod.run_async_collect_function_on_list = AsyncMock(return_value=[{"filename": "attempt"}])

    await mod.load_quiz_files(quiz)
    assert quiz["files"] == [{"filename": "attempt"}]
    attempts = mod.run_async_collect_function_on_list.call_args.args[0]
    assert attempts[0]["_quiz_name"] == "Quiz"

    attempt_mod = make_mod(QuizMod)
    attempt_mod.client.async_post.return_value = {
        "questions": [
            {
                "html": "<p>Question</p><script>alert(1)</script>",
                "responsefileareas": [{"filename": "answer.pdf", "filepath": "/"}],
            }
        ]
    }
    files = await attempt_mod.load_files_of_attempt(
        {"id": 5, "state": "finished", "_quiz_name": "Quiz", "timemodified": 123}
    )
    assert files[0]["filename"] == "answer.pdf"
    assert files[0]["type"] == "quiz_file"
    assert files[1]["filename"].startswith("Quiz")
    assert "5" in files[1]["filename"]
    assert "finished" in files[1]["filename"]
    assert "alert(1)" not in files[1]["html"]

    attempt_mod.client.async_post.return_value = {"questions": []}
    summary_files = await attempt_mod.load_files_of_attempt({"id": 6, "state": "inprogress", "_quiz_name": "Quiz"})
    assert summary_files[-1]["filename"].startswith("Quiz")
    assert "6" in summary_files[-1]["filename"]
    assert "inprogress" in summary_files[-1]["filename"]
    assert await attempt_mod.load_files_of_attempt({"id": 7, "state": "abandoned", "_quiz_name": "Quiz"}) == []

    attempt_mod.client.async_post.side_effect = RequestRejectedError("denied")
    assert await attempt_mod.load_files_of_attempt({"id": 5, "state": "finished", "_quiz_name": "Quiz"}) == []
