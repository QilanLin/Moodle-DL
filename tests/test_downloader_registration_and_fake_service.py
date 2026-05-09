from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from moodle_dl.downloader import extractors
from moodle_dl.downloader.fake_download_service import FakeDownloadService
from moodle_dl.types import Course, File, MoodleDlOpts
from moodle_dl.utils import PathTools


def make_file(content_type, filename, module_modname="resource", deleted=0):
    return File(
        module_id=1,
        section_name="Week",
        section_id=1,
        module_name="Module",
        content_filepath="/",
        content_filename=filename,
        content_fileurl="https://example.test/file",
        content_filesize=1,
        content_timemodified=123,
        module_modname=module_modname,
        content_type=content_type,
        content_isexternalfile=False,
        deleted=deleted,
    )


def test_add_additional_extractors_registers_moodle_extractors_and_preserves_existing():
    existing_extractor = object()
    existing_instance = object()
    ydl = SimpleNamespace(
        _ies={"Existing": existing_extractor},
        _ies_instances={"Existing": existing_instance},
    )

    result = extractors.add_additional_extractors(ydl)

    assert result is None
    assert ydl._ies["Existing"] is existing_extractor
    assert ydl._ies_instances["Existing"] is existing_instance
    for extractor_class in extractors.ALL_ADDITIONAL_EXTRACTORS:
        ie_key = extractor_class.ie_key()
        assert isinstance(ydl._ies[ie_key], extractor_class)
        assert ydl._ies_instances[ie_key] is ydl._ies[ie_key]


def test_fake_download_service_records_saved_paths_without_downloading(tmp_path):
    original_restricted = PathTools.restricted_filenames
    config = Mock()
    config.get_restricted_filenames.return_value = True
    config.get_download_path.return_value = str(tmp_path)
    database = Mock()
    opts = MoodleDlOpts()
    files = [
        make_file("description", "description"),
        make_file("html", "page"),
        make_file("directory_placeholder", "directory"),
        make_file("application/pdf", "paper.pdf"),
        make_file("url", "link", module_modname="url"),
        make_file("application/pdf", "deleted.pdf", deleted=1),
    ]
    course = Course(10, "Course", files)

    try:
        with (
            patch(
                "moodle_dl.downloader.fake_download_service.Task.gen_path",
                side_effect=lambda _download_path, _course, file: str(tmp_path / file.content_type),
            ),
            patch("moodle_dl.downloader.fake_download_service.platform.system", return_value="Darwin"),
        ):
            service = FakeDownloadService([course], config, opts, database)
            assert service.get_failed_tasks() == []
            service.run()

        assert PathTools.restricted_filenames is True
        database.batch_delete_files.assert_called_once_with([course])
        assert database.save_file.call_count == 5
        database.save_file.assert_any_call(files[0], 10, "Course")
        saved_paths = {file.content_type: Path(file.saved_to) for file in files if not file.deleted}
        assert saved_paths["description"].name == "description.md"
        assert saved_paths["html"].name == "page.html"
        assert saved_paths["directory_placeholder"] == tmp_path / "directory_placeholder"
        assert saved_paths["application/pdf"].name == "paper.pdf"
        assert saved_paths["url"].name == "link.URL"
        assert files[-1].saved_to == ""
    finally:
        PathTools.restricted_filenames = original_restricted


def test_fake_download_service_uses_desktop_shortcut_on_non_macos(tmp_path):
    config = Mock()
    config.get_restricted_filenames.return_value = False
    config.get_download_path.return_value = str(tmp_path)
    database = Mock()
    url_file = make_file("url", "link", module_modname="url")
    course = Course(10, "Course", [url_file])

    with (
        patch("moodle_dl.downloader.fake_download_service.Task.gen_path", return_value=str(tmp_path)),
        patch("moodle_dl.downloader.fake_download_service.platform.system", return_value="Linux"),
    ):
        FakeDownloadService([course], config, MoodleDlOpts(), database).run()

    assert Path(url_file.saved_to).name == "link.desktop"
