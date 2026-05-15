import json
import warnings
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import pytest
from yt_dlp.utils import ExtractorError

from moodle_dl.downloader.extractors.echo360 import Echo360IE
from moodle_dl.downloader.extractors.googledrive import GoogleDriveIE
from moodle_dl.downloader.extractors.helixmedia_lti import HelixmediaLtiIE
from moodle_dl.downloader.extractors.kalvidres_embedded import KalvidresEmbeddedIE
from moodle_dl.downloader.extractors.kalvidres_lti import KalvidresLtiIE
from moodle_dl.downloader.extractors.opencast_lti import OpencastLtiIE
from moodle_dl.downloader.extractors.owncloud import OwnCloudIE
from moodle_dl.downloader.extractors.sharepoint import SharePointIE
from moodle_dl.downloader.extractors.sharepointfiles import SharePointFilesIE


class UrlHandle:
    def __init__(self, url="https://example.test/final", headers=None, status=200):
        self._url = url
        self.headers = headers or {}
        self.status = status

    def geturl(self):
        return self._url


def make_ie(cls):
    ie = cls()
    ie._downloader = SimpleNamespace(
        _format_err=lambda text, _style: text,
        Styles=SimpleNamespace(EMPHASIS=None),
        params={},
    )
    return ie


def test_google_drive_helpers_and_real_extract_new_video_info():
    ie = make_ie(GoogleDriveIE)
    video_id = "a" * 28

    assert GoogleDriveIE._extract_url(
        f'<iframe src="https://drive.google.com/file/d/{video_id}/preview"></iframe>'
    ) == f"https://drive.google.com/file/d/{video_id}"
    assert ie._get_subtitles(video_id, "subs") is None

    captions_xml = ElementTree.fromstring(
        """
        <transcript_list>
          <format fmt_code="vtt" />
          <format fmt_code="srv3" />
          <format fmt_code="ttml" default="true" />
          <track lang_code="en" />
          <track />
          <target lang_code="fr" />
        </transcript_list>
        """
    )
    ie._captions_xml = None
    ie._caption_formats_ext = []
    ie._download_xml = Mock(return_value=captions_xml)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        subtitles = ie._get_subtitles(video_id, "subs", "en")
        automatic = ie._get_automatic_captions(video_id, "subs", "en")

    assert not [warning for warning in caught if "truth value" in str(warning.message)]
    assert sorted(subtitles) == ["en"]
    assert [fmt["ext"] for fmt in subtitles["en"]] == ["vtt", "srv3"]
    assert sorted(automatic) == ["fr"]
    assert "tlang=fr" in automatic["fr"][0]["url"]

    video_info = (
        "title=Video.mp4&fmt_stream_map=18%7Chttps%3A%2F%2Fcdn.test%2Fv.mp4"
        "&fmt_list=18%2F640x360%2F9%2F0%2F115&length_seconds=10&hl=en"
        "&ttsurl=https%3A%2F%2Fcaption.test%3Fvid%3Dsubs"
    )
    ie._download_webpage = Mock(return_value=video_info)
    ie._request_webpage = Mock(
        return_value=UrlHandle(
            "https://download.test/file",
            headers={"Content-Disposition": 'attachment; filename="Video.mp4"'},
        )
    )
    ie._sort_formats = Mock()
    ie.extract_subtitles = Mock(return_value={"en": [{"url": "sub.vtt"}]})
    ie.extract_automatic_captions = Mock(return_value={})

    result = ie._real_extract(f"https://drive.google.com/file/d/{video_id}/view")

    assert result["id"] == video_id
    assert result["title"] == "Video.mp4"
    assert result["duration"] == 10
    assert result["thumbnail"] == f"https://drive.google.com/thumbnail?id={video_id}"
    assert result["subtitles"] == {"en": [{"url": "sub.vtt"}]}
    assert {fmt["format_id"] for fmt in result["formats"]} == {"18", "source"}
    stream = next(fmt for fmt in result["formats"] if fmt["format_id"] == "18")
    assert stream["width"] == 640
    assert stream["height"] == 360


def test_google_drive_caption_edge_cases():
    ie = make_ie(GoogleDriveIE)
    video_id = "b" * 28

    assert ie._get_captions_by_type(video_id, None, "subtitles") is None
    assert ie._get_captions_by_type(video_id, "subs", None) is None
    assert ie._get_subtitles(video_id, "subs") is None
    assert ie._get_automatic_captions(video_id, "subs") is None

    ie._captions_xml = None
    ie._download_xml = Mock(return_value=None)
    assert ie._get_subtitles(video_id, "subs", "en") is None

    ie._captions_xml = None
    ie._download_xml = Mock(return_value=None)
    assert ie._get_automatic_captions(video_id, "subs", "en") is None

    ie._captions_xml = ElementTree.fromstring("<transcript_list><format fmt_code='vtt' /></transcript_list>")
    ie._download_xml = Mock()
    assert ie._get_automatic_captions(video_id, "subs", "en") is None
    ie._download_xml.assert_not_called()

    ie._captions_xml = ElementTree.fromstring("<transcript_list><track /></transcript_list>")
    ie._download_xml = Mock()
    assert ie._get_automatic_captions(video_id, "subs", "en") is None
    ie._download_xml.assert_not_called()


def test_google_drive_real_extract_old_webpage_fallback():
    ie = make_ie(GoogleDriveIE)
    video_id = "c" * 28
    webpage = """
    <meta property="og:image" content="https://thumb.test/image.jpg">
    ["title","Fallback.mp4"]
    ["length_seconds","12"]
    ["fmt_stream_map","18|https:\\/\\/cdn.test\\/fallback.mp4,broken-entry"]
    ["fmt_list","18/320x180/9/0/115"]
    ["hl","en"]
    ["ttsurl","https://caption.test?vid=subs"]
    """
    ie._download_webpage = Mock(side_effect=["reason=Needs+fallback", webpage])
    ie._request_webpage = Mock(
        return_value=UrlHandle(
            "https://download.test/fallback",
            headers={"Content-Disposition": 'attachment; filename="Fallback.mp4"'},
        )
    )
    ie._sort_formats = Mock()
    ie.extract_subtitles = Mock(return_value={})
    ie.extract_automatic_captions = Mock(return_value={})

    result = ie._real_extract(f"https://drive.google.com/file/d/{video_id}/view")

    assert result["title"] == "Fallback.mp4"
    assert result["duration"] == 12
    assert result["thumbnail"] == "https://thumb.test/image.jpg"
    assert {fmt["format_id"] for fmt in result["formats"]} == {"18", "source"}
    assert ie._download_webpage.call_args_list[1].args == (f"http://docs.google.com/file/d/{video_id}", video_id)
    ie.extract_subtitles.assert_called_once_with(video_id, "subs", "en")


def test_google_drive_real_extract_uses_confirmation_download():
    ie = make_ie(GoogleDriveIE)
    video_id = "d" * 28
    ie._download_webpage = Mock(return_value="title=Video.bin&length_seconds=5&hl=en")
    ie._request_webpage = Mock(
        side_effect=[
            UrlHandle("https://drive.google.com/uc?id=file", headers={}),
            UrlHandle(
                "https://download.test/confirmed",
                headers={"Content-Disposition": 'attachment; filename="Video.bin"'},
            ),
        ]
    )
    ie._webpage_read_content = Mock(return_value="<a href='download?confirm=abc123&id=file'>download</a>")
    ie._sort_formats = Mock()
    ie.extract_subtitles = Mock(return_value={})
    ie.extract_automatic_captions = Mock(return_value={})

    result = ie._real_extract(f"https://drive.google.com/file/d/{video_id}/view")

    assert [fmt["format_id"] for fmt in result["formats"]] == ["source"]
    assert result["formats"][0]["url"] == "https://download.test/confirmed"
    assert "confirm=abc123" in ie._request_webpage.call_args_list[1].args[0]


def test_google_drive_real_extract_raises_for_new_and_old_error_responses():
    video_id = "e" * 28
    new_error = make_ie(GoogleDriveIE)
    new_error._download_webpage = Mock(return_value="title=Video.mp4&reason=Denied")
    new_error._request_webpage = Mock(return_value=None)
    new_error._sort_formats = Mock()

    with pytest.raises(ExtractorError, match="Denied"):
        new_error._real_extract(f"https://drive.google.com/file/d/{video_id}/view")

    old_error = make_ie(GoogleDriveIE)
    old_error._download_webpage = Mock(
        side_effect=[
            "reason=Needs+fallback",
            '["title","Fallback.mp4"] ["reason","Old denied"]',
        ]
    )
    old_error._request_webpage = Mock(return_value=None)
    old_error._sort_formats = Mock()

    with pytest.raises(ExtractorError, match="Old denied"):
        old_error._real_extract(f"https://drive.google.com/file/d/{video_id}/view")


def test_echo360_query_and_mediapackage_parsing():
    ie = make_ie(Echo360IE)
    assert ie._replace_url_query("https://cdn.test/a.m3u8?old=1", "token=abc") == "https://cdn.test/a.m3u8?token=abc"
    assert ie._replace_url_query("https://cdn.test/a.m3u8?old=1", None) == "https://cdn.test/a.m3u8?old=1"

    ie.report_warning = Mock()
    query_strings = [
        {"uriPattern": "[", "queryString": "broken=1"},
        {"uriPattern": r"https://cdn\.test/master\.m3u8", "queryString": "token=abc"},
    ]
    assert ie._get_query_string("https://cdn.test/master.m3u8?old=1", query_strings) == "token=abc"
    ie.report_warning.assert_called_once()

    ie._extract_m3u8_formats = Mock(return_value=[{"url": "https://cdn.test/segment.m3u8"}])
    video = {
        "sourceQueryStrings": {"queryStrings": query_strings},
        "mediaName": "Lecture",
        "playableAudioVideo": {
            "mediaId": "media-1",
            "duration": "PT12.5S",
            "playableMedias": [
                {"uri": None},
                {"uri": "https://cdn.test/master.m3u8?old=1", "isHls": True, "isLive": False},
            ],
        },
    }

    result = ie._parse_mediapackage(video)
    assert result["id"] == "media-1"
    assert result["title"] == "Lecture"
    assert result["duration"] == 12.5
    assert result["formats"][0]["url"] == "https://cdn.test/segment.m3u8"
    assert result["formats"][0]["extra_param_to_segment_url"] is None

    ie._download_json = Mock(return_value={"data": {"ok": True}})
    assert ie._call_api("echo360.org.uk", "public-1", "media-1", "token") == {"data": {"ok": True}}
    assert ie._download_json.call_args.kwargs["headers"] == {"Authorization": "Bearer token"}


def test_helixmedia_real_extract_and_extension_detection():
    ie = make_ie(HelixmediaLtiIE)
    assert ie.urlhandle_detect_ext(
        UrlHandle(headers={"Content-Disposition": 'attachment; filename="lecture.webm"'})
    ) == "webm"
    assert ie.urlhandle_detect_ext(UrlHandle(headers={"Content-Type": "video/mp4"})) == "mp4"

    launch_page = """
    <form id="ltiLaunchForm" action="https://helix.test/start">
      <input type="hidden" name="token" value="abc">
    </form>
    """
    player_json = (
        '{"tracks":[{"file":"https://cdn.test/thumb.vtt"}],'
        '"sources":[{"file":"https://cdn.test/video.mp4"},{"file":"https://cdn.test/master.m3u8"}]}'
    )
    model_json = json.dumps(
        {
            "VideoTitle": "Title",
            "VideoDescription": "Creator",
            "VideoId": 123,
            "DownloadUrl": "https://cdn.test/download",
            "PlayScreenVm": {"VodPlayerModel": {"PlayerJson": player_json}},
        }
    )
    ie._download_webpage = Mock(side_effect=[launch_page, f"var model = {model_json};"])
    ie._download_webpage_handle = Mock(
        return_value=("", UrlHandle("https://media.test/view?UploadSessionId=abc"))
    )
    ie._extract_m3u8_formats = Mock(return_value=[{"url": "https://cdn.test/hls.m3u8", "format_id": "hls"}])
    ie._request_webpage = Mock(
        return_value=UrlHandle(headers={"Content-Disposition": 'attachment; filename="download.mov"'})
    )
    ie._sort_formats = Mock()

    result = ie._real_extract("https://moodle.test/course/mod/helixmedia/view.php?id=5")

    assert result["id"] == "123"
    assert result["title"] == "Title"
    assert result["creator"] == "Creator"
    assert result["thumbnail"] == "https://cdn.test/thumb.jpg"
    assert {fmt.get("ext") for fmt in result["formats"]} >= {"mp4", "mov"}
    assert any(fmt.get("format_id") == "hls" for fmt in result["formats"])

    failing = make_ie(HelixmediaLtiIE)
    failing._download_webpage = Mock(return_value=launch_page)
    failing._download_webpage_handle = Mock(return_value=("", UrlHandle("https://media.test/no-session")))
    with pytest.raises(ExtractorError, match="Unable to launch"):
        failing._real_extract("https://moodle.test/course/mod/helixmedia/view.php?id=5")


def test_kalvidres_embedded_extracts_kaltura_url_and_errors():
    ie = make_ie(KalvidresEmbeddedIE)
    with pytest.raises(ExtractorError, match="source parameter"):
        ie._real_extract("https://moodle.test/filter/kaltura/lti_launch.php?id=1")

    source = "https%3A%2F%2Fkaf.test%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_entry%2Fshow"
    missing_partner = make_ie(KalvidresEmbeddedIE)
    missing_partner._download_webpage = Mock(return_value="<html></html>")
    with pytest.raises(ExtractorError, match="partner ID"):
        missing_partner._real_extract(f"https://moodle.test/filter/kaltura/lti_launch.php?source={source}")

    ie._download_webpage = Mock(return_value='{"partnerId": 12345}')
    result = ie._real_extract(f"https://moodle.test/filter/kaltura/lti_launch.php?source={source}")
    assert result == {"_type": "url", "url": "kaltura:12345:1_entry", "ie_key": "Kaltura"}


def test_kalvidres_lti_real_extract_and_errors():
    view_page = '<iframe class="kaltura-player-iframe" src="https://moodle.test/launch?x=1&amp;y=2"></iframe>'
    launch_page = """
    <form id="ltiLaunchForm" action="https://kalvidres.test/start">
      <input type="hidden" name="token" value="abc">
    </form>
    """
    ie = make_ie(KalvidresLtiIE)
    ie._download_webpage = Mock(side_effect=[view_page, launch_page])
    ie._download_webpage_handle = Mock(
        side_effect=[
            ("window.location.href = 'https://kalvidres.test/redirect'", UrlHandle()),
            ("<html>kaltura page</html>", UrlHandle()),
        ]
    )
    with patch(
        "moodle_dl.downloader.extractors.kalvidres_lti.KalturaIE._extract_url",
        return_value="kaltura:123:entry",
    ):
        assert ie._real_extract("https://moodle.test/mod/kalvidres/view.php?id=7") == {
            "_type": "url",
            "url": "kaltura:123:entry",
            "ie_key": "Kaltura",
        }

    missing_iframe = make_ie(KalvidresLtiIE)
    missing_iframe._download_webpage = Mock(return_value="<html></html>")
    with pytest.raises(ExtractorError, match="launch url"):
        missing_iframe._real_extract("https://moodle.test/mod/kalvidres/view.php?id=7")


def test_opencast_lti_real_extract_and_status_error():
    launch_page = """
    <form id="ltiLaunchForm" action="https://opencast.test/start">
      <input type="hidden" name="token" value="abc">
    </form>
    """
    ie = make_ie(OpencastLtiIE)
    ie._download_webpage = Mock(return_value=launch_page)
    ie._download_webpage_handle = Mock(return_value=("", UrlHandle("https://opencast.test/watch/1", status=200)))
    assert ie._real_extract("https://moodle.test/course/mod/lti/view.php?id=11") == {
        "_type": "url",
        "url": "https://opencast.test/watch/1",
    }

    failing = make_ie(OpencastLtiIE)
    failing._download_webpage = Mock(return_value=launch_page)
    failing._download_webpage_handle = Mock(return_value=("", UrlHandle(status=403)))
    with pytest.raises(ExtractorError, match="Unable to launch"):
        failing._real_extract("https://moodle.test/course/mod/lti/view.php?id=11")


def test_owncloud_extract_and_password_validation():
    webpage = """
    <input type="hidden" name="filename" value="video.mp4">
    <input type="hidden" name="downloadURL" value="https://cloud.test/download">
    """
    ie = make_ie(OwnCloudIE)
    ie._download_webpage_handle = Mock(return_value=(webpage, UrlHandle("https://cloud.test/s/share")))
    result = ie._real_extract("https://ruhr-uni-bochum.sciebo.de/s/share")
    assert result == {
        "id": "share",
        "title": "video.mp4",
        "url": "https://cloud.test/download",
        "ext": "mp4",
    }

    password_page = '<label for="password">Password</label><input type="hidden" name="requesttoken" value="tok">'
    no_password = make_ie(OwnCloudIE)
    no_password.get_param = Mock(return_value=None)
    with pytest.raises(ExtractorError, match="protected by a password"):
        no_password._verify_video_password(password_page, "https://cloud.test/s/share", "share")

    wrong_password = make_ie(OwnCloudIE)
    wrong_password.get_param = Mock(return_value="bad")
    wrong_password._download_webpage = Mock(
        return_value='<label for="password">Password</label><div class="warning">Wrong</div>'
    )
    with pytest.raises(ExtractorError, match="Wrong"):
        wrong_password._verify_video_password(password_page, "https://cloud.test/s/share", "share")

    ok_password = make_ie(OwnCloudIE)
    ok_password.get_param = Mock(return_value="good")
    ok_password._download_webpage = Mock(return_value=webpage)
    assert ok_password._verify_video_password(password_page, "https://cloud.test/s/share", "share") == webpage


def test_sharepoint_video_and_file_extractors():
    sharepoint = make_ie(SharePointIE)
    sharepoint._download_webpage = Mock(return_value="<html></html>")
    sharepoint._search_json = Mock(
        return_value={
            "name": "Video.mp4",
            ".transformUrl": "https://tenant.sharepoint.com/path/media.ashx?foo=bar",
            ".ctag": "ctag",
            "MediaServiceFastMetadata": json.dumps({"media": {"duration": 12340000}}),
        }
    )
    sharepoint._extract_m3u8_formats = Mock(
        side_effect=[
            [{"url": "https://cdn.test/hls.m3u8"}],
            [{"url": "https://cdn.test/hls-vnext.m3u8"}],
        ]
    )
    sharepoint._extract_mpd_formats = Mock(return_value=[{"url": "https://cdn.test/dash.mpd"}])

    result = sharepoint._real_extract("https://tenant.sharepoint.com/:v:/g/a/b/videoid?e=1")
    assert result["id"] == "videoid"
    assert result["title"] == "Video.mp4"
    assert result["duration"] == 1.234
    assert len(result["formats"]) == 3
    assert all(fmt["preference"] == -2 for fmt in result["formats"][1:])

    files = make_ie(SharePointFilesIE)
    files._download_webpage = Mock(return_value="<html></html>")
    files._search_json = Mock(
        return_value={
            "FileGetUrl": "https://tenant.sharepoint.com/download",
            "FileName": "Slides.pptx",
            "LastModified": 1675785393000,
            "FileSize": "123",
        }
    )
    file_result = files._real_extract("https://tenant.sharepoint.com/:p:/g/a/b/fileid?e=1")
    assert file_result["id"] == "fileid"
    assert file_result["title"] == "Slides.pptx"
    assert file_result["formats"] == [
        {"url": "https://tenant.sharepoint.com/download", "ext": "pptx", "filesize": 123}
    ]
