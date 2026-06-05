"""Failure-path tests for the third-party site extractors.

These tests exercise the unhappy / error / edge cases of every extractor in
``moodle_dl/downloader/extractors/`` and complement the happy-path tests in
``tests/test_downloader_extractors_more.py``.

The mock-helper style (``UrlHandle`` + ``make_ie``) follows the convention
established by the existing extractor tests.
"""

import json
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from yt_dlp.utils import ExtractorError

from moodle_dl.downloader.extractors.echo360 import Echo360IE
from moodle_dl.downloader.extractors.googledrive import GoogleDriveIE
from moodle_dl.downloader.extractors.kalvidres_embedded import KalvidresEmbeddedIE
from moodle_dl.downloader.extractors.kalvidres_lti import KalvidresLtiIE
from moodle_dl.downloader.extractors.opencast_lti import OpencastLtiIE
from moodle_dl.downloader.extractors.owncloud import OwnCloudIE
from moodle_dl.downloader.extractors.sharepoint import SharePointIE
from moodle_dl.downloader.extractors.sharepointfiles import SharePointFilesIE


# ---------------------------------------------------------------------------
# Reusable helpers (mirrors the style of test_downloader_extractors_more.py)
# ---------------------------------------------------------------------------


class UrlHandle:
    def __init__(self, url="https://example.test/final", headers=None, status=200):
        self._url = url
        self.headers = headers or {}
        self.status = status

    def geturl(self):
        return self._url


def make_ie(cls):
    """Construct an extractor instance with a minimal ``_downloader`` stub."""
    ie = cls()
    ie._downloader = SimpleNamespace(
        _format_err=lambda text, _style: text,
        Styles=SimpleNamespace(EMPHASIS=None),
        params={},
        report_warning=lambda *_a, **_kw: None,
    )
    return ie


# ---------------------------------------------------------------------------
# googledrive.py
# ---------------------------------------------------------------------------


def test_google_drive_failure_invalid_url_raises():
    """An obviously malformed URL should not match the extractor at all.

    The GoogleDriveIE regex requires a 28+ character base64-style id; a URL
    that does not match means ``_match_id`` returns ``None`` and
    ``_real_extract`` blows up with an ``ExtractorError`` (or AttributeError
    when the matcher raises earlier).
    """
    ie = make_ie(GoogleDriveIE)
    ie._download_webpage = Mock(return_value="title=ok")
    with pytest.raises((ExtractorError, AttributeError, TypeError, Exception)):
        ie._real_extract("https://drive.google.com/file/d/short/view")


def test_google_drive_failure_sharing_readonly_missing_reason():
    """``reason`` is set but the share is restricted (no title).

    In this state the new method returns ``reason=...`` without ``title=``,
    triggering the fallback path. The fallback webpage also reports a reason,
    so the extractor should raise an ``ExtractorError`` with that reason.
    """
    ie = make_ie(GoogleDriveIE)
    video_id = "f" * 28
    ie._download_webpage = Mock(
        side_effect=[
            "reason=You+need+access",  # primary get_video_info response
            '["title","X"] ["reason","Access+denied"]',  # fallback webpage
        ]
    )
    ie._request_webpage = Mock(return_value=None)
    ie._sort_formats = Mock()

    with pytest.raises(ExtractorError, match=r"Access\+denied"):
        ie._real_extract(f"https://drive.google.com/file/d/{video_id}/view")


def test_google_drive_failure_404_from_get_video_info():
    """A 404 from ``get_video_info`` propagates as an ExtractorError."""
    ie = make_ie(GoogleDriveIE)
    video_id = "1" * 28
    ie._download_webpage = Mock(
        side_effect=ExtractorError("HTTP Error 404: Not Found", expected=True)
    )
    with pytest.raises(ExtractorError, match="404"):
        ie._real_extract(f"https://drive.google.com/file/d/{video_id}/view")


def test_google_drive_failure_private_no_auth():
    """Private share, no auth - the new method returns ``reason=`` only.

    With no ``title`` and the fallback webpage that ALSO reports a reason,
    the extractor must surface the reason as an ``ExtractorError``.
    """
    ie = make_ie(GoogleDriveIE)
    video_id = "2" * 28
    ie._download_webpage = Mock(
        side_effect=[
            "reason=Sign+in+required",  # primary get_video_info response
            '["title","X"] ["reason","Sign+in+required"]',  # fallback webpage
        ]
    )
    ie._request_webpage = Mock(return_value=None)
    ie._sort_formats = Mock()

    with pytest.raises(ExtractorError, match=r"Sign\+in\+required"):
        ie._real_extract(f"https://drive.google.com/file/d/{video_id}/view")


# ---------------------------------------------------------------------------
# owncloud.py
# ---------------------------------------------------------------------------


def test_owncloud_failure_http_401_propagates():
    """A 401 from the share page must propagate (we don't auto-retry)."""
    ie = make_ie(OwnCloudIE)
    ie._download_webpage_handle = Mock(
        side_effect=ExtractorError("HTTP Error 401: Unauthorized", expected=True)
    )
    with pytest.raises(ExtractorError, match="401"):
        ie._real_extract("https://ruhr-uni-bochum.sciebo.de/s/abc")


def test_owncloud_failure_http_403_propagates():
    """A 403 from the share page must propagate."""
    ie = make_ie(OwnCloudIE)
    ie._download_webpage_handle = Mock(
        side_effect=ExtractorError("HTTP Error 403: Forbidden", expected=True)
    )
    with pytest.raises(ExtractorError, match="403"):
        ie._real_extract("https://ruhr-uni-bochum.sciebo.de/s/abc")


def test_owncloud_failure_login_redirect():
    """The browser was bounced to the login page, the share API never
    returned the hidden ``filename``/``downloadURL`` fields.

    The result is then missing a title, and the URL falls back to a
    derived ``/s/download`` path (not a real download URL).
    """
    ie = make_ie(OwnCloudIE)
    login_webpage = "<html>please log in</html>"
    ie._download_webpage_handle = Mock(
        return_value=(login_webpage, UrlHandle("https://ruhr-uni-bochum.sciebo.de/login"))
    )

    result = ie._real_extract("https://ruhr-uni-bochum.sciebo.de/s/secret")
    # No filename hidden input -> title is None; no downloadURL -> fallback
    # URL is the path-joined "/s/download" (not a real download URL).
    assert result["title"] is None
    assert result["url"] == "https://ruhr-uni-bochum.sciebo.de/s/download"
    # determine_ext(None) -> "unknown_video" (we accept anything non-None).
    assert result["ext"]


def test_owncloud_failure_webdav_path_broken():
    """The webdav path is broken: only ``filename`` is present, no
    ``downloadURL`` and no usable path to ``download``.

    The result must not crash, must surface a title but no real downloadURL.
    """
    ie = make_ie(OwnCloudIE)
    bad_webpage = '<html><body><input type="hidden" name="filename" value="x.mp4" /></body></html>'
    ie._download_webpage_handle = Mock(
        return_value=(bad_webpage, UrlHandle("https://ruhr-uni-bochum.sciebo.de/s/zzz"))
    )
    # The current implementation falls back to a path-joined "/download" URL.
    result = ie._real_extract("https://ruhr-uni-bochum.sciebo.de/s/zzz")
    assert result["title"] == "x.mp4"
    assert result["ext"] == "mp4"
    # Fallback URL is the path-joined "/s/download" since downloadURL is missing.
    assert result["url"] == "https://ruhr-uni-bochum.sciebo.de/s/download"


def test_owncloud_failure_password_missing_raises():
    """Password protected share, no password configured -> clear error."""
    ie = make_ie(OwnCloudIE)
    ie.get_param = Mock(return_value=None)
    pwd_page = '<label for="password">Password</label><input type="hidden" name="requesttoken" value="tok">'
    with pytest.raises(ExtractorError, match="protected by a password"):
        ie._verify_video_password(pwd_page, "https://cloud.example/s/x", "x")


def test_owncloud_failure_password_wrong_raises():
    """Password protected share, wrong password -> error contains warning."""
    ie = make_ie(OwnCloudIE)
    ie.get_param = Mock(return_value="bad")
    ie._download_webpage = Mock(
        return_value='<label for="password">Pwd</label><div class="warning">Wrong password</div>'
    )
    pwd_page = '<label for="password">Pwd</label><input type="hidden" name="requesttoken" value="tok">'
    with pytest.raises(ExtractorError, match="Wrong password"):
        ie._verify_video_password(pwd_page, "https://cloud.example/s/x", "x")


# ---------------------------------------------------------------------------
# opencast_lti.py
# ---------------------------------------------------------------------------


def test_opencast_lti_failure_lti_token_expired_content_empty():
    """A 200 with empty content (LTI token expired and we ended up on a
    generic page) should still be tolerated - the extractor returns whatever
    URL it received. The test ensures no spurious exception is raised.
    """
    ie = make_ie(OpencastLtiIE)
    launch_page = """
    <form id="ltiLaunchForm" action="https://opencast.test/start">
      <input type="hidden" name="token" value="expired">
    </form>
    """
    ie._download_webpage = Mock(return_value=launch_page)
    ie._download_webpage_handle = Mock(
        return_value=("", UrlHandle("https://opencast.test/sso/refresh", status=200))
    )
    result = ie._real_extract("https://moodle.test/course/mod/lti/view.php?id=11")
    assert result["url"] == "https://opencast.test/sso/refresh"


def test_opencast_lti_failure_status_not_200_raises():
    """Anything other than 200 from the launch is fatal."""
    ie = make_ie(OpencastLtiIE)
    launch_page = """
    <form id="ltiLaunchForm" action="https://opencast.test/start">
      <input type="hidden" name="token" value="x">
    </form>
    """
    ie._download_webpage = Mock(return_value=launch_page)
    ie._download_webpage_handle = Mock(
        return_value=("", UrlHandle("https://opencast.test/error", status=500))
    )
    with pytest.raises(ExtractorError, match="Unable to launch opencast app"):
        ie._real_extract("https://moodle.test/course/mod/lti/view.php?id=11")


def test_opencast_lti_failure_video_unavailable_not_found():
    """Server returned a 'not found' page (still 200) - we just propagate
    the final URL, but it must not be the original launch endpoint.
    """
    ie = make_ie(OpencastLtiIE)
    launch_page = """
    <form id="ltiLaunchForm" action="https://opencast.test/start">
      <input type="hidden" name="token" value="x">
    </form>
    """
    ie._download_webpage = Mock(return_value=launch_page)
    ie._download_webpage_handle = Mock(
        return_value=("", UrlHandle("https://opencast.test/error/not-found", status=200))
    )
    result = ie._real_extract("https://moodle.test/course/mod/lti/view.php?id=999")
    assert "not-found" in result["url"]


# ---------------------------------------------------------------------------
# kalvidres_lti.py
# ---------------------------------------------------------------------------


def test_kalvidres_lti_failure_iframe_missing_in_view_page():
    """The view page does not contain a kaltura-player-iframe element."""
    ie = make_ie(KalvidresLtiIE)
    ie._download_webpage = Mock(return_value="<html>no iframe here</html>")
    with pytest.raises(ExtractorError, match="Unable to extract kalvidres launch url"):
        ie._real_extract("https://moodle.test/mod/kalvidres/view.php?id=7")


def test_kalvidres_lti_failure_kaltura_url_not_extractable():
    """Kaltura auth failed: the redirect page does not expose a kaltura URL."""
    ie = make_ie(KalvidresLtiIE)
    view_page = (
        '<iframe class="kaltura-player-iframe" src="https://moodle.test/launch?x=1"></iframe>'
    )
    launch_page = """
    <form id="ltiLaunchForm" action="https://kalvidres.test/start">
      <input type="hidden" name="token" value="bad">
    </form>
    """
    ie._download_webpage = Mock(side_effect=[view_page, launch_page])
    ie._download_webpage_handle = Mock(
        side_effect=[
            ("window.location.href = 'https://kalvidres.test/redirect'", UrlHandle()),
            ("<html>no kaltura here</html>", UrlHandle()),
        ]
    )
    with pytest.raises(ExtractorError, match="Unable to extract kaltura url"):
        ie._real_extract("https://moodle.test/mod/kalvidres/view.php?id=7")


def test_kalvidres_lti_failure_redirect_url_missing():
    """The launch step returns a page with no ``window.location.href`` redirect."""
    ie = make_ie(KalvidresLtiIE)
    view_page = (
        '<iframe class="kaltura-player-iframe" src="https://moodle.test/launch"></iframe>'
    )
    launch_page = """
    <form id="ltiLaunchForm" action="https://kalvidres.test/start">
      <input type="hidden" name="token" value="x">
    </form>
    """
    ie._download_webpage = Mock(side_effect=[view_page, launch_page])
    ie._download_webpage_handle = Mock(
        side_effect=[
            ("<html>no redirect script</html>", UrlHandle()),
        ]
    )
    with pytest.raises(ExtractorError, match="Unable to extract kalvidres redirect url"):
        ie._real_extract("https://moodle.test/mod/kalvidres/view.php?id=7")


# ---------------------------------------------------------------------------
# kalvidres_embedded.py
# ---------------------------------------------------------------------------


def test_kalvidres_embedded_failure_no_source_parameter():
    """The lti_launch URL is missing the ``source`` parameter."""
    ie = make_ie(KalvidresEmbeddedIE)
    with pytest.raises(ExtractorError, match="source parameter"):
        ie._real_extract("https://moodle.test/filter/kaltura/lti_launch.php?id=1")


def test_kalvidres_embedded_failure_entryid_regex_no_match():
    """The source URL is present but the entryid regex does not match."""
    ie = make_ie(KalvidresEmbeddedIE)
    bad_source = "https%3A%2F%2Fkaf.test%2Fbrowseandembed%2Findex%2Fno-entryid-here%2Fshow"
    ie._download_webpage = Mock(return_value='{"partnerId": 12345}')
    with pytest.raises(ExtractorError, match="entry ID"):
        ie._real_extract(
            f"https://moodle.test/filter/kaltura/lti_launch.php?source={bad_source}"
        )


def test_kalvidres_embedded_failure_partnerid_regex_no_match():
    """The browse page is present but has no ``partnerId`` JSON field."""
    ie = make_ie(KalvidresEmbeddedIE)
    source = (
        "https%3A%2F%2Fkaf.test%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_entry%2Fshow"
    )
    ie._download_webpage = Mock(return_value="<html>no json here</html>")
    with pytest.raises(ExtractorError, match="partner ID"):
        ie._real_extract(
            f"https://moodle.test/filter/kaltura/lti_launch.php?source={source}"
        )


# ---------------------------------------------------------------------------
# sharepoint.py
# ---------------------------------------------------------------------------


def test_sharepoint_failure_g_fileinfo_missing():
    """The player config (``g_fileInfo``) cannot be located on the page."""
    ie = make_ie(SharePointIE)
    ie._download_webpage = Mock(return_value="<html>no g_fileInfo here</html>")
    # _search_json is the real method; with the default it tries to parse the
    # webpage and raises ExtractorError. We need to bind it on the instance.
    with patch.object(SharePointIE, "_search_json", side_effect=ExtractorError("Unable to extract JSON", expected=True)):
        with pytest.raises(ExtractorError):
            ie._real_extract("https://tenant.sharepoint.com/:v:/g/a/b/videoid")


def test_sharepoint_failure_extract_m3u8_raises():
    """The player config is present, but ``_extract_m3u8_formats`` errors
    out. ``_extract_mpd_formats`` is the fatal path in the implementation
    (it is not called with ``fatal=False``). We expect the mpd error to
    surface.
    """
    ie = make_ie(SharePointIE)
    ie._download_webpage = Mock(return_value="<html>...</html>")
    ie._search_json = Mock(
        return_value={
            "name": "Video.mp4",
            ".transformUrl": "https://tenant.sharepoint.com/path/media.ashx?foo=bar",
            ".ctag": "ctag",
            "MediaServiceFastMetadata": None,
        }
    )
    ie._extract_m3u8_formats = Mock(side_effect=ExtractorError("m3u8 boom", expected=True))
    ie._extract_mpd_formats = Mock(side_effect=ExtractorError("dash boom", expected=True))
    with pytest.raises(ExtractorError):
        ie._real_extract("https://tenant.sharepoint.com/:v:/g/a/b/videoid")


# ---------------------------------------------------------------------------
# sharepointfiles.py
# ---------------------------------------------------------------------------


def test_sharepointfiles_failure_wopi_context_missing():
    """The page does not contain ``_wopiContextJson``."""
    ie = make_ie(SharePointFilesIE)
    ie._download_webpage = Mock(return_value="<html>no _wopiContextJson</html>")
    with patch.object(
        SharePointFilesIE,
        "_search_json",
        side_effect=ExtractorError("Unable to extract JSON", expected=True),
    ):
        with pytest.raises(ExtractorError):
            ie._real_extract("https://tenant.sharepoint.com/:p:/g/a/b/fileid")


def test_sharepointfiles_failure_401_propagates():
    """A 401 from the sharepoint page must propagate."""
    ie = make_ie(SharePointFilesIE)
    ie._download_webpage = Mock(
        side_effect=ExtractorError("HTTP Error 401: Unauthorized", expected=True)
    )
    with pytest.raises(ExtractorError, match="401"):
        ie._real_extract("https://tenant.sharepoint.com/:p:/g/a/b/fileid")


def test_sharepointfiles_failure_403_propagates():
    """A 403 from the sharepoint page must propagate."""
    ie = make_ie(SharePointFilesIE)
    ie._download_webpage = Mock(
        side_effect=ExtractorError("HTTP Error 403: Forbidden", expected=True)
    )
    with pytest.raises(ExtractorError, match="403"):
        ie._real_extract("https://tenant.sharepoint.com/:w:/g/a/b/docid")


def test_sharepointfiles_failure_metadata_missing_keys():
    """The metadata is found but lacks the expected keys."""
    ie = make_ie(SharePointFilesIE)
    ie._download_webpage = Mock(return_value="<html></html>")
    ie._search_json = Mock(return_value={})  # empty metadata
    result = ie._real_extract("https://tenant.sharepoint.com/:p:/g/a/b/fileid")
    assert result["title"] is None
    assert result["modified_timestamp"] is None
    # Formats should still be a list with one entry, just with None values.
    assert isinstance(result["formats"], list)
    assert len(result["formats"]) == 1
    assert result["formats"][0]["url"] is None
    assert result["formats"][0]["filesize"] is None


# ---------------------------------------------------------------------------
# echo360.py
# ---------------------------------------------------------------------------


def test_echo360_failure_session_token_request_fails():
    """Session endpoint returns 401/403: ExtractorError must propagate."""
    ie = make_ie(Echo360IE)
    url = "https://echo360.org.uk/media/1d8392aa-a3e7-4e78-94cf-b6532c27208c/public"
    ie._download_webpage = Mock(return_value="<html>player</html>")
    ie._search_json = Mock(
        return_value={"sessionId": "session-1", "publicLinkId": "public-1", "mediaId": "media-1"}
    )
    ie._request_webpage = Mock(
        side_effect=ExtractorError("HTTP Error 401: Unauthorized", expected=True)
    )
    with pytest.raises(ExtractorError, match="401"):
        ie._real_extract(url)


def test_echo360_failure_call_api_empty_data():
    """``_call_api`` returns a payload without a ``data`` field.

    The implementation indexes ``['data']`` directly, so a missing key
    triggers a ``KeyError`` which propagates out.
    """
    ie = make_ie(Echo360IE)
    url = "https://echo360.org.uk/media/1d8392aa-a3e7-4e78-94cf-b6532c27208c/public"
    ie._download_webpage = Mock(return_value="<html>player</html>")
    ie._search_json = Mock(
        return_value={"sessionId": "session-1", "publicLinkId": "public-1", "mediaId": "media-1"}
    )
    ie._request_webpage = Mock(return_value=UrlHandle(headers={"Token": "tok"}))
    ie._call_api = Mock(return_value={})  # no 'data' key
    with pytest.raises(KeyError):
        ie._real_extract(url)


def test_echo360_failure_video_404():
    """The public page returns 404 -> ExtractorError propagates."""
    ie = make_ie(Echo360IE)
    url = "https://echo360.org.uk/media/1d8392aa-a3e7-4e78-94cf-b6532c27208c/public"
    ie._download_webpage = Mock(
        side_effect=ExtractorError("HTTP Error 404: Not Found", expected=True)
    )
    with pytest.raises(ExtractorError, match="404"):
        ie._real_extract(url)


def test_echo360_failure_session_token_header_missing():
    """Session endpoint returns 200 but the ``Token`` header is missing.

    The current implementation indexes ``urlh.headers['Token']`` directly,
    so this triggers a ``KeyError`` which propagates out - documenting
    the brittle contract.
    """
    ie = make_ie(Echo360IE)
    url = "https://echo360.org.uk/media/1d8392aa-a3e7-4e78-94cf-b6532c27208c/public"
    ie._download_webpage = Mock(return_value="<html>player</html>")
    ie._search_json = Mock(
        return_value={"sessionId": "session-1", "publicLinkId": "public-1", "mediaId": "media-1"}
    )
    ie._request_webpage = Mock(return_value=UrlHandle(headers={}))  # no Token
    with pytest.raises(KeyError):
        ie._real_extract(url)
