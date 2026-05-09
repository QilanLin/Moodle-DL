import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
import requests

from moodle_dl.exceptions import MoodleAPIError, MoodleAuthError, MoodleNetworkError
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.types import MoodleDlOpts, MoodleURL


def make_helper(tmp_path=None, *, token="token", log_responses=False):
    config = Mock()
    if tmp_path is not None:
        config.get_misc_files_path.return_value = str(tmp_path)
    opts = MoodleDlOpts()
    opts.max_parallel_api_calls = 1
    opts.log_responses = log_responses
    return RequestHelper(config, opts, MoodleURL(False, "moodle.example.test", "/"), token)


class FakeCookieJar(list):
    def __init__(self, path):
        super().__init__([SimpleNamespace(expires=None)])
        self.path = path
        self.load = Mock()
        self.save = Mock()


class AsyncResponse:
    def __init__(self, status=200, text='{"ok": true}', url="https://moodle.example.test/rest"):
        self.status = status
        self._text = text
        self.url = url
        self.request_info = None
        self.history = ()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text

    async def json(self):
        return json.loads(self._text)


class AsyncSession:
    def __init__(self, post_results):
        self.post_results = list(post_results)
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        result = self.post_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_init_creates_response_log_and_log_response_appends_entries(tmp_path):
    helper = make_helper(tmp_path, log_responses=True)

    log_file = tmp_path / "responses.log"
    assert log_file.read_text(encoding="utf-8") == "JSON Log:\n\n"

    helper.log_response("core_test", {"wstoken": "secret"}, "https://example.test", {"ok": True})
    content = log_file.read_text(encoding="utf-8")
    assert "Function: core_test" in content
    assert '"ok": true' in content

    before = log_file.read_text(encoding="utf-8")
    helper.log_response("tool_mobile_get_autologin_key", {}, "https://example.test", {"skip": True})
    assert log_file.read_text(encoding="utf-8") == before


def test_post_url_saves_cookie_jar_and_censors_network_failures(tmp_path):
    helper = make_helper()
    response = Mock()
    session = Mock()
    session.post.return_value = response
    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text("# cookies", encoding="utf-8")

    with (
        patch("moodle_dl.moodle.request_helper.SslHelper.custom_requests_session", return_value=session),
        patch("moodle_dl.moodle.request_helper.MoodleDLCookieJar", FakeCookieJar),
    ):
        returned_response, returned_session = helper.post_URL(
            "https://moodle.example.test/login",
            {"username": "alice", "remember": True},
            str(cookie_path),
        )

    assert returned_response is response
    assert returned_session is session
    assert session.cookies.load.call_args.kwargs == {"ignore_discard": True, "ignore_expires": True}
    assert session.cookies[0].expires == 2147483647
    session.cookies.save.assert_called_once_with(ignore_discard=True, ignore_expires=True)
    assert session.post.call_args.kwargs["data"] == "username=alice&remember=1"

    failing_session = Mock()
    failing_session.post.side_effect = requests.ConnectionError("offline")
    with patch("moodle_dl.moodle.request_helper.SslHelper.custom_requests_session", return_value=failing_session):
        with patch.object(helper, "log_failed_request") as log_failed:
            with pytest.raises(MoodleNetworkError, match="offline"):
                helper.post_URL("https://moodle.example.test/login", {"password": "secret"})
    log_failed.assert_called_once()


def test_get_url_cookie_jar_and_network_error(tmp_path):
    helper = make_helper()
    response = Mock()
    session = Mock()
    session.get.return_value = response
    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text("# cookies", encoding="utf-8")

    with (
        patch("moodle_dl.moodle.request_helper.SslHelper.custom_requests_session", return_value=session),
        patch("moodle_dl.moodle.request_helper.MoodleDLCookieJar", FakeCookieJar),
    ):
        returned_response, returned_session = helper.get_URL("https://moodle.example.test/page", str(cookie_path))

    assert returned_response is response
    assert returned_session is session
    session.cookies.load.assert_called_once_with(ignore_discard=True, ignore_expires=True)
    session.cookies.save.assert_called_once_with(ignore_discard=True, ignore_expires=True)

    failing_session = Mock()
    failing_session.get.side_effect = requests.Timeout("slow")
    with patch("moodle_dl.moodle.request_helper.SslHelper.custom_requests_session", return_value=failing_session):
        with patch.object(helper, "log_failed_request") as log_failed:
            with pytest.raises(MoodleNetworkError, match="slow"):
                helper.get_URL("https://moodle.example.test/page")
    log_failed.assert_called_once_with("https://moodle.example.test/page", None)


def test_post_raises_api_error_for_non_retry_request_exception():
    helper = make_helper()
    session = Mock()
    session.post.side_effect = requests.RequestException("bad request")

    with patch("moodle_dl.moodle.request_helper.SslHelper.custom_requests_session", return_value=session):
        with pytest.raises(MoodleAPIError, match="请求异常"):
            helper.post("core_test", {})


def test_check_response_code_uses_ip_diagnostics_for_403():
    helper = make_helper()
    response = Mock(status_code=403, text="Forbidden")

    with patch(
        "moodle_dl.moodle.request_helper.IPValidator.diagnose_403_error",
        return_value=(True, "diagnosed whitelist issue"),
    ) as diagnose:
        with pytest.raises(MoodleAuthError, match="diagnosed whitelist issue"):
            helper._check_response_code(response)

    diagnose.assert_called_once_with("HTTP 403", "Forbidden", "moodle.example.test")


@pytest.mark.asyncio
async def test_async_post_success_parses_text_json_and_logs_response(tmp_path):
    helper = make_helper(tmp_path, log_responses=True)
    session = AsyncSession([AsyncResponse(text='{"value": 3}')])

    with (
        patch("moodle_dl.moodle.request_helper.aiohttp.ClientSession", return_value=session),
        patch("moodle_dl.moodle.request_helper.SslHelper.get_ssl_context", return_value=None),
    ):
        result = await helper.async_post("core_test", {"courseid": 1})

    assert result == {"value": 3}
    post_url, post_kwargs = session.post_calls[0]
    assert post_url.endswith("wsfunction=core_test")
    assert "courseid=1" in post_kwargs["data"]
    assert "Function: core_test" in (tmp_path / "responses.log").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_async_post_status_and_json_errors():
    helper = make_helper()

    with (
        patch(
            "moodle_dl.moodle.request_helper.aiohttp.ClientSession",
            return_value=AsyncSession([AsyncResponse(status=401, text="Unauthorized")]),
        ),
        patch("moodle_dl.moodle.request_helper.SslHelper.get_ssl_context", return_value=None),
    ):
        with pytest.raises(MoodleAuthError, match="401"):
            await helper.async_post("core_test", {})

    with (
        patch(
            "moodle_dl.moodle.request_helper.aiohttp.ClientSession",
            return_value=AsyncSession([AsyncResponse(status=200, text="not-json")]),
        ),
        patch("moodle_dl.moodle.request_helper.SslHelper.get_ssl_context", return_value=None),
    ):
        with pytest.raises(MoodleAPIError, match="无效 JSON"):
            await helper.async_post("core_test", {})


@pytest.mark.asyncio
async def test_async_post_retries_client_errors_then_succeeds():
    helper = make_helper()
    session = AsyncSession([aiohttp.ClientConnectionError("offline"), AsyncResponse(text='{"ok": true}')])

    with (
        patch("moodle_dl.moodle.request_helper.aiohttp.ClientSession", return_value=session),
        patch("moodle_dl.moodle.request_helper.SslHelper.get_ssl_context", return_value=None),
        patch("moodle_dl.moodle.request_helper.asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
    ):
        result = await helper.async_post("core_test", {})

    assert result == {"ok": True}
    assert len(session.post_calls) == 2
    sleep_mock.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_async_post_requires_token():
    helper = make_helper(token=None)
    with pytest.raises(ValueError, match="token"):
        await helper.async_post("core_test", {})
