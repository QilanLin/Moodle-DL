import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from moodle_dl.moodle.mods.folder import FolderMod
from moodle_dl.moodle.mods.label import LabelMod
from moodle_dl.moodle.mods.page import PageMod
from moodle_dl.moodle.mods.resource import ResourceMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course


def make_config(**values):
    config = Mock()
    config.get_download_resources.return_value = values.get("download_resources", True)
    config.get_download_labels.return_value = values.get("download_labels", True)
    return config


def make_mod(cls, config=None):
    client = Mock()
    client.async_post = AsyncMock()
    return cls(client, 2023100900, 7, {}, config or make_config())


def test_resource_folder_page_label_download_conditions():
    deleted_resource = SimpleNamespace(module_modname="resource", deleted=True)
    deleted_label = SimpleNamespace(module_modname="label", deleted=True)
    other_file = SimpleNamespace(module_modname="url", deleted=True)

    assert ResourceMod.download_condition(make_config(download_resources=True), deleted_resource) is True
    assert ResourceMod.download_condition(make_config(download_resources=False), deleted_resource) is False
    assert ResourceMod.download_condition(make_config(download_resources=False), other_file) is True

    assert FolderMod.download_condition(make_config(download_resources=True), other_file) is True
    assert FolderMod.download_condition(make_config(download_resources=False), other_file) is False

    assert PageMod.download_condition(make_config(download_resources=True), other_file) is True
    assert PageMod.download_condition(make_config(download_resources=False), other_file) is False

    assert LabelMod.download_condition(make_config(download_labels=True), deleted_label) is True
    assert LabelMod.download_condition(make_config(download_labels=False), deleted_label) is False
    assert LabelMod.download_condition(make_config(download_labels=False), other_file) is True


def test_resource_display_options_file_details_and_helpers():
    mod = make_mod(ResourceMod)
    options = mod._parse_display_options(
        'a:3:{s:10:"printintro";i:1;s:8:"showsize";i:1;s:8:"showdate";i:1;}'
    )
    assert options["printintro"] is True
    assert options["showsize"] is True
    assert options["showdate"] is True
    assert options["_raw"]

    files = [
        {
            "filename": "lecture.pdf",
            "filesize": 2048,
            "mimetype": "application/pdf",
            "timemodified": 1000,
            "timecreated": 100,
            "repositorytype": "local",
            "isexternalfile": True,
        },
        {"filename": "appendix.txt", "filesize": 512, "mimetype": "text/plain"},
    ]
    details = mod._get_file_details(files, options)

    assert details["size_bytes"] == 2560
    assert details["size_human"] == "2.5 KB"
    assert details["mimetype"] == "application/pdf"
    assert details["extension"] == "pdf"
    assert details["type_description"] == "PDF document"
    assert details["modified_date"] == 1000
    assert details["repository_type"] == "local"
    assert details["is_reference"] is True
    assert mod._get_file_details([], options) == {}
    assert mod._bytes_to_human(1024 * 1024) == "1.0 MB"
    assert mod._get_mimetype_description("application/x-custom") == "application/x-custom"


@pytest.mark.asyncio
async def test_resource_web_api_fallback_extracts_content_files():
    mod = make_mod(ResourceMod)
    core_contents = {
        10: [
            {
                "modules": [
                    {
                        "id": 44,
                        "instance": 99,
                        "modname": "resource",
                        "name": "Slides",
                        "description": "<p>Read</p>",
                        "timemodified": 123,
                        "contents": [
                            {
                                "type": "file",
                                "filename": "slides.pdf",
                                "filepath": "/",
                                "filesize": 100,
                                "fileurl": "https://example.test/slides.pdf",
                                "timemodified": 120,
                                "mimetype": "application/pdf",
                            },
                            {"type": "url", "filename": "ignored"},
                        ],
                    }
                ]
            }
        ]
    }

    resources = await mod._fetch_resources_web_api([Course(10, "Course")], core_contents)

    assert resources[0]["id"] == 99
    assert resources[0]["coursemodule"] == 44
    assert resources[0]["contentfiles"][0]["filename"] == "slides.pdf"

    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_resources_web_api([Course(99, "Missing")], core_contents)


@pytest.mark.asyncio
async def test_resource_real_fetch_mobile_and_fallback_paths():
    mod = make_mod(ResourceMod)
    mod.client.async_post.return_value = {
        "resources": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Slides",
                "intro": "<p>Read</p>",
                "contentfiles": [
                    {
                        "filename": "slides.pdf",
                        "filepath": "/",
                        "filesize": 100,
                        "fileurl": "https://example.test/slides.pdf",
                        "timemodified": 120,
                        "mimetype": "application/pdf",
                    }
                ],
                "display": ResourceMod.DISPLAY_DOWNLOAD,
                "displayoptions": 'a:1:{s:8:"showsize";i:1;}',
                "revision": 2,
                "timemodified": 123,
            }
        ]
    }
    core_contents = {10: [{"modules": [{"id": 44, "contents": [{"filename": "extra.txt", "type": "resource_file"}]}]}]}

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], core_contents)

    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == [
        "Introduction.html",
        "slides.pdf",
        "extra.txt",
        "metadata.json",
    ]
    metadata = json.loads(files[-1]["content"])
    assert metadata["display"]["mode_name"] == "Download"
    assert metadata["file_info"]["total_size"] == 100
    assert metadata["settings"]["revision"] == 2

    disabled_mod = make_mod(ResourceMod, make_config(download_resources=False))
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}

    fallback_mod = make_mod(ResourceMod)
    fallback_mod.client.async_post.side_effect = RequestRejectedError("mobile disabled")
    fallback_mod._fetch_resources_web_api = AsyncMock(
        return_value=[{"id": 1, "coursemodule": 2, "course": 10, "name": "Fallback", "contentfiles": []}]
    )
    fallback = await fallback_mod.real_fetch_mod_entries([Course(10, "Course")], {})
    assert fallback[10][2]["name"] == "Fallback"


@pytest.mark.asyncio
async def test_folder_real_fetch_and_web_fallback():
    mod = make_mod(FolderMod)
    mod.client.async_post.return_value = {
        "folders": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Folder",
                "intro": "<p>Files</p>",
                "introfiles": [{"filename": "intro.png", "filepath": "/"}],
                "display": 9,
                "showdownloadfolder": 0,
                "forcedownload": 0,
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
                        "contents": [{"filename": "inside.pdf", "filepath": "/", "type": "folder_file"}],
                    }
                ]
            }
        ]
    }

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], core_contents)
    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == [
        "intro.png",
        "Introduction.html",
        "inside.pdf",
        "metadata.json",
    ]
    metadata = json.loads(files[-1]["content"])
    assert metadata["settings"]["display_mode_name"] == "UNKNOWN_9"
    assert metadata["download_options"]["can_download_folder"] is False

    fallback = await mod._fetch_folders_web_api(
        [Course(10, "Course")],
        {
            10: [
                {
                    "modules": [
                        {
                            "id": 45,
                            "instance": 100,
                            "modname": "folder",
                            "name": "Fallback Folder",
                            "description": "Desc",
                            "timemodified": 222,
                        }
                    ]
                }
            ]
        },
    )
    assert fallback[0]["name"] == "Fallback Folder"

    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_folders_web_api([Course(99, "Missing")], {})


@pytest.mark.asyncio
async def test_page_parse_real_fetch_and_web_fallback():
    mod = make_mod(PageMod)
    assert PageMod._parse_display_options("printintro=1,popupwidth=620,title=notes") == {
        "printintro": 1,
        "popupwidth": 620,
        "title": "notes",
    }
    assert PageMod._parse_display_options("") == {}

    mod.client.async_post.return_value = {
        "pages": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Page",
                "intro": "<p>Intro</p>",
                "content": "<h1>Body</h1>",
                "introfiles": [{"filename": "intro.png", "filepath": "/"}],
                "contentfiles": [{"filename": "embedded.png", "filepath": "/"}],
                "display": 99,
                "displayoptions": "popupwidth=620",
                "revision": 3,
                "timemodified": 123,
            }
        ]
    }

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == [
        "intro.png",
        "embedded.png",
        "Introduction.html",
        "Page",
        "metadata.json",
    ]
    assert files[3]["filter_urls_during_search_containing"] == ["/mod_page/content/"]
    metadata = json.loads(files[-1]["content"])
    assert metadata["settings"]["display_mode_name"] == "UNKNOWN_99"
    assert metadata["settings"]["displayoptions_parsed"] == {"popupwidth": 620}

    fallback = await mod._fetch_pages_web_api(
        [Course(10, "Course")],
        {10: [{"modules": [{"id": 45, "instance": 100, "modname": "page", "name": "Fallback Page"}]}]},
    )
    assert fallback[0]["display"] == PageMod.DISPLAY_AUTO

    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_pages_web_api([Course(99, "Missing")], {})


@pytest.mark.asyncio
async def test_label_real_fetch_and_web_fallback():
    mod = make_mod(LabelMod)
    mod.client.async_post.return_value = {
        "labels": [
            {
                "id": 99,
                "coursemodule": 44,
                "course": 10,
                "name": "Label",
                "intro": "<p>Announcement</p>",
                "introfiles": [{"filename": "image.png", "filepath": "/"}],
                "timemodified": 123,
            }
        ]
    }

    result = await mod.real_fetch_mod_entries([Course(10, "Course")], {})
    files = result[10][44]["files"]
    assert [file["filename"] for file in files] == ["image.png", "Content", "metadata.json"]
    assert files[0]["type"] == "label_file"
    assert files[1]["description"] == "<p>Announcement</p>"
    metadata = json.loads(files[-1]["content"])
    assert metadata["has_files"] is True
    assert metadata["content_length"] == len("<p>Announcement</p>")

    disabled_mod = make_mod(LabelMod, make_config(download_labels=False))
    assert await disabled_mod.real_fetch_mod_entries([Course(10, "Course")], {}) == {}

    fallback = await mod._fetch_labels_web_api(
        [Course(10, "Course")],
        {10: [{"modules": [{"id": 45, "instance": 100, "modname": "label", "description": "Fallback"}]}]},
    )
    assert fallback[0]["name"] == "Label"
    assert fallback[0]["intro"] == "Fallback"

    with pytest.raises(ValueError, match="Web API"):
        await mod._fetch_labels_web_api([Course(99, "Missing")], {})
