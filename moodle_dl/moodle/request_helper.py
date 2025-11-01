import asyncio
import json
import logging
import os
import urllib
from time import sleep
from typing import Dict

import aiohttp
import requests
from requests.exceptions import RequestException

from moodle_dl.config import ConfigHelper
from moodle_dl.exceptions import MoodleAPIError, MoodleAuthError, MoodleNetworkError, RequestRejectedError
from moodle_dl.types import MoodleDlOpts, MoodleURL
from moodle_dl.utils import MoodleDLCookieJar
from moodle_dl.utils import PathTools as PT
from moodle_dl.utils import SslHelper


class RequestHelper:
    """
    Functions for sending out requests to the Moodle System.
    """

    RQ_HEADER = {
        'User-Agent': (
            'Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36'
            + ' (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile'
        ),
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    MAX_RETRIES = 5

    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts, moodle_url: MoodleURL, token: str):
        self.token = token
        self.moodle_url = moodle_url
        self.config = config
        self.opts = opts

        self.url_base = moodle_url.url_base

        # Semaphore for async requests
        # Keep in mind Semaphore needs to be initialized in the same async loop as it is used
        self.semaphore = asyncio.Semaphore(opts.max_parallel_api_calls)

        self.log_responses_to = None
        if opts.log_responses:
            self.log_responses_to = PT.make_path(config.get_misc_files_path(), 'responses.log')
            with open(self.log_responses_to, 'w', encoding='utf-8') as response_log_file:
                response_log_file.write('JSON Log:\n\n')

    def post_URL(self, url: str, data: Dict[str, str] = None, cookie_jar_path: str = None):
        """
        Sends a POST request to a specific URL, including saving of cookies in cookie jar.
        @param url: The url to which the request is sent. (the moodle base url is not added to the given URL)
        @param data: The optional data is added to the POST body.
        @param cookie_jar_path: Path to the cookies file.
        @return: The resulting response object and the session object.
        """

        data_urlencoded = ""
        if data is not None:
            data_urlencoded = self.recursive_urlencode(data)

        session = SslHelper.custom_requests_session(
            self.opts.skip_cert_verify, self.opts.allow_insecure_ssl, self.opts.use_all_ciphers
        )
        if cookie_jar_path is not None:
            session.cookies = MoodleDLCookieJar(cookie_jar_path)

            if os.path.exists(cookie_jar_path):
                session.cookies.load(ignore_discard=True, ignore_expires=True)
        try:
            response = session.post(url, data=data_urlencoded, headers=self.RQ_HEADER, timeout=60)
        except RequestException as error:
            self.log_failed_request(url, data)
            raise MoodleNetworkError(f"网络连接错误: {str(error)}") from None

        if cookie_jar_path is not None:
            for cookie in session.cookies:
                cookie.expires = 2147483647

            session.cookies.save(ignore_discard=True, ignore_expires=True)

        return response, session

    def get_URL(self, url: str, cookie_jar_path: str = None):
        """
        Sends a GET request to a specific URL of the Moodle system, including additional cookies
        (cookies are updated after the request)
        @param url: The url to which the request is sent. (the moodle base url is not added to the given URL)
        @param cookie_jar_path: The optional cookies to add to the request
        @return: The resulting Response object.
        """

        session = SslHelper.custom_requests_session(
            self.opts.skip_cert_verify, self.opts.allow_insecure_ssl, self.opts.use_all_ciphers
        )
        if cookie_jar_path is not None:
            session.cookies = MoodleDLCookieJar(cookie_jar_path)

            if os.path.exists(cookie_jar_path):
                session.cookies.load(ignore_discard=True, ignore_expires=True)
            session.cookies = session.cookies
        try:
            response = session.get(url, headers=self.RQ_HEADER, timeout=60)
        except RequestException as error:
            self.log_failed_request(url, None)
            raise MoodleNetworkError(f"网络连接错误: {str(error)}") from None

        if cookie_jar_path is not None:
            session.cookies.save(ignore_discard=True, ignore_expires=True)

        return response, session

    async def async_post(self, function: str, data: Dict[str, str] = None, timeout: int = 60) -> Dict:
        """
        Sends async a POST request to the REST endpoint of the Moodle system
        使用指数退避的智能重试机制

        @param function: The Web service function to be called.
        @param data: The optional data is added to the POST body.
        @return: The JSON response returned by the Moodle system, already checked for errors.
        """

        if self.token is None:
            raise ValueError('The required token is not set!')

        data = self._get_POST_DATA(function, self.token, data)
        data_urlencoded = self.recursive_urlencode(data)
        url = self._get_REST_POST_URL(self.url_base, function)
        ssl_context = SslHelper.get_ssl_context(
            self.opts.skip_cert_verify, self.opts.allow_insecure_ssl, self.opts.use_all_ciphers
        )

        base_delay = 1  # 初始延迟1秒
        attempt = 0
        resp_json = None

        async with self.semaphore, aiohttp.ClientSession() as session:
            while attempt < self.MAX_RETRIES:
                try:
                    async with session.post(
                        url,
                        data=data_urlencoded,
                        headers=self.RQ_HEADER,
                        timeout=timeout,
                        ssl=ssl_context,
                        raise_for_status=True,
                    ) as resp:
                        resp_json = await resp.json()
                    self.check_json_for_moodle_error(resp_json, url, data)
                    self.log_response(function, data, str(resp.url), resp_json)
                    break

                # 认证错误 - 不可重试
                except aiohttp.client_exceptions.ClientResponseError as req_err:
                    if req_err.status in [401, 403]:  # pylint: disable=no-member
                        # 401 Unauthorized, 403 Forbidden
                        raise MoodleAuthError(f"认证失败 (HTTP {req_err.status}): {req_err}") from None
                    elif req_err.status == 404:
                        # 404 Not Found - API 不存在
                        raise MoodleAPIError(f"API 不存在 (HTTP 404): {req_err}") from None
                    elif req_err.status in [408, 409, 429, 503]:
                        # 408 (timeout), 409 (conflict), 429 (too many requests), 503 (service unavailable)
                        # 这些是可重试的网络错误
                        pass  # 继续到重试逻辑
                    else:
                        # 其他 HTTP 错误 - 不可重试
                        raise MoodleAPIError(f"HTTP 错误 ({req_err.status}): {req_err}") from None

                # API 响应格式错误 - 不可重试
                except aiohttp.client_exceptions.ContentTypeError as req_err:
                    raise MoodleAPIError('Moodle Mobile API 返回了无效的内容类型，API 可能不可用') from None

                # JSON 解析错误 - 不可重试
                except (json.JSONDecodeError, ValueError) as req_err:
                    raise MoodleAPIError(f"API 返回无效 JSON: {req_err}") from None

                # 网络错误 - 可重试
                except (
                    aiohttp.client_exceptions.ClientError,
                    asyncio.exceptions.TimeoutError,
                    OSError,
                ) as req_err:
                    # 这些都是可重试的网络错误
                    pass  # 继续到重试逻辑

                # 执行重试逻辑（只有可重试的错误才会到达这里）
                attempt += 1
                if attempt < self.MAX_RETRIES:
                    # 指数退避：1s, 2s, 4s, 8s, 16s
                    delay = base_delay * (2 ** (attempt - 1))
                    logging.warning(
                        "网络错误，%d秒后重试 (尝试 %d/%d): %s",
                        delay,
                        attempt,
                        self.MAX_RETRIES,
                        req_err,
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    # 最后一次尝试失败
                    raise MoodleNetworkError(f"网络错误，已重试 {self.MAX_RETRIES} 次: {req_err}") from None

        return resp_json

    def post(self, function: str, data: Dict[str, str] = None, timeout: int = 60) -> Dict:
        """
        Sends a POST request to the REST endpoint of the Moodle system
        使用指数退避的智能重试机制

        @param function: The Web service function to be called.
        @param data: The optional data is added to the POST body.
        @return: The JSON response returned by the Moodle system, already checked for errors.
        """

        if self.token is None:
            raise ValueError('The required Token is not set!')

        data = self._get_POST_DATA(function, self.token, data)
        data_urlencoded = self.recursive_urlencode(data)
        url = self._get_REST_POST_URL(self.url_base, function)

        session = SslHelper.custom_requests_session(
            self.opts.skip_cert_verify, self.opts.allow_insecure_ssl, self.opts.use_all_ciphers
        )

        base_delay = 1  # 初始延迟1秒
        attempt = 0
        response = None

        while attempt < self.MAX_RETRIES:
            try:
                response = session.post(url, data=data_urlencoded, headers=self.RQ_HEADER, timeout=timeout)
                break

            # 网络错误 - 可重试
            except (requests.ConnectionError, requests.Timeout) as req_err:
                attempt += 1
                if attempt < self.MAX_RETRIES:
                    # 指数退避：1s, 2s, 4s, 8s, 16s
                    delay = base_delay * (2 ** (attempt - 1))
                    logging.warning(
                        "网络错误，%d秒后重试 (尝试 %d/%d): %s",
                        delay,
                        attempt,
                        self.MAX_RETRIES,
                        str(req_err),
                    )
                    sleep(delay)
                    continue
                else:
                    # 最后一次尝试失败
                    raise MoodleNetworkError(f"网络错误，已重试 {self.MAX_RETRIES} 次: {req_err}") from None

            # 其他请求异常 - 通常不可重试
            except RequestException as req_err:
                raise MoodleAPIError(f"请求异常: {req_err}") from None

        json_result = self._initial_parse(response, url, data)
        self.log_response(function, data, response.url, json_result)

        return json_result

    def log_response(self, function: str, data: Dict[str, str], url: str, json_result: Dict):
        if self.opts.log_responses and function not in ['tool_mobile_get_autologin_key']:
            with open(self.log_responses_to, 'a', encoding='utf-8') as response_log_file:
                response_log_file.write(f'URL: {url}\n')
                response_log_file.write(f'Function: {function}\n\n')
                response_log_file.write(f'Data: {data}\n\n')
                response_log_file.write(json.dumps(json_result, indent=4, ensure_ascii=False))
                response_log_file.write('\n\n\n')

    @staticmethod
    def _get_REST_POST_URL(url_base: str, function: str) -> str:
        """
        Generates an URL for a REST-POST request
        @params: The necessary parameters for a REST URL
        @return: A formatted URL
        """
        url = f'{url_base}webservice/rest/server.php?moodlewsrestformat=json&wsfunction={function}'

        return url

    @staticmethod
    def _get_POST_DATA(function: str, token: str, data_obj: str) -> str:
        """
        Generates the data for a REST-POST request
        @params: The necessary parameters for a REST URL
        @return: A URL-encoded data string
        """
        data = {'moodlewssettingfilter': 'true', 'moodlewssettingfileurl': 'true'}

        if data_obj is not None:
            data.update(data_obj)

        data.update({'wsfunction': function, 'wstoken': token})

        return data

    def get_login(self, data: Dict[str, str]) -> object:
        """
        Sends a POST request to the login endpoint of the Moodle system to
        obtain a token in JSON format.
        @param data: The data is inserted into the Post-Body as arguments. This
        should contain the login data.
        @return: The JSON response returned by the Moodle System, already
        checked for errors.
        """
        session = SslHelper.custom_requests_session(
            self.opts.skip_cert_verify, self.opts.allow_insecure_ssl, self.opts.use_all_ciphers
        )
        try:
            response = session.post(
                f'{self.url_base}login/token.php',
                data=urllib.parse.urlencode(data),
                headers=self.RQ_HEADER,
                timeout=60,
            )
        except RequestException as error:
            raise MoodleNetworkError(f"网络连接错误: {str(error)}") from None

        return self._initial_parse(response, f'{self.url_base}login/token.php', data)

    @staticmethod
    def _check_response_code(response):
        """
        检查 HTTP 响应状态码，并根据状态码抛出相应的异常
        """
        # Normally Moodle answer with response 200
        if response.status_code == 200:
            return

        status_code = response.status_code

        # 认证和权限错误
        if status_code in [401, 403]:
            raise MoodleAuthError(
                f'认证或权限错误 (HTTP {status_code})'
                + f'\nHeader: {response.headers}'
                + f'\nResponse: {response.text}'
            )

        # API 错误
        raise MoodleAPIError(
            f'Moodle 系统返回了意外的错误！'
            + f' 状态码: {status_code}'
            + f'\nHeader: {response.headers}'
            + f'\nResponse: {response.text}'
        )

    def _initial_parse(self, response, url: str, data: Dict) -> object:
        """
        首次解析 REST 请求的结果，检查已知错误

        @param response: The JSON response of the Moodle system
        @return: The parsed JSON object
        """

        self._check_response_code(response)

        # Try to parse the JSON
        try:
            resp_json = response.json()
        except ValueError:
            raise MoodleAPIError('Moodle Mobile API 当前似乎不可用（JSON 解析失败）') from None
        except Exception as error:
            raise MoodleAPIError(
                '解析 JSON 响应时发生意外错误！'
                + f' Moodle 响应: {response.text}.'
                + f'\n错误: {error}'
            ) from None

        self.check_json_for_moodle_error(resp_json, url, data)
        return resp_json

    def log_failed_request(self, url: str, data: Dict):
        if data is not None and isinstance(data, dict):
            data = data.copy()
            for censor in ['privatetoken', 'password', 'wstoken']:
                if censor in data:
                    data[censor] = 'censored'
        logging.debug('Details about the failed request:\nURL: %s\nBody: %s', url, data)

    def check_json_for_moodle_error(self, resp_json: Dict, url: str, data: Dict):
        """
        检查 Moodle API 响应中的错误，并抛出相应的异常类型
        """
        # Check for known errors
        if 'error' in resp_json:
            self.log_failed_request(url, data)
            error_code = resp_json.get('errorcode', '')

            # 认证相关错误
            if error_code in ['invalidtoken', 'requireloginerror', 'accessdenied', 'nopermission']:
                raise MoodleAuthError(
                    f"认证或权限错误: {resp_json.get('error', '')} (错误代码: {error_code})"
                )

            # 其他 API 错误
            raise MoodleAPIError(
                'Moodle 系统拒绝了请求。'
                + f" 详情: {resp_json.get('error', '')} (错误代码: {error_code},"
                + f" 堆栈跟踪: {resp_json.get('stacktrace', '')}, 调试信息: {resp_json.get('debuginfo', '')},"
                + f" 复现链接: {resp_json.get('reproductionlink', '')})"
            )

        if 'exception' in resp_json:
            self.log_failed_request(url, data)
            error_code = resp_json.get('errorcode', '')

            # 认证相关错误
            if error_code in ['invalidtoken', 'requireloginerror', 'accessdenied', 'nopermission']:
                if error_code == 'invalidtoken':
                    raise MoodleAuthError(
                        'Moodle token 已过期。'
                        + ' 请运行 "moodle-dl -nt -u USERNAME -pw PASSWORD" 或 "moodle-dl -nt -sso" 来创建新的 token'
                    )
                raise MoodleAuthError(
                    f"认证或权限错误: {resp_json.get('message', '')} (错误代码: {error_code})"
                )

            # 其他 API 错误
            raise MoodleAPIError(
                'Moodle 系统拒绝了请求。'
                + f" 详情: {resp_json.get('exception', '')} (错误代码: {error_code},"
                + f" 消息: {resp_json.get('message', '')})"
            )

    @staticmethod
    def recursive_urlencode(data):
        """URL-encode a multidimensional dictionary.
        @param data: the data to be encoded
        @return: the url encoded data
        """

        def recursion(data, base=None):
            if base is None:
                base = []
            pairs = []

            for key, value in data.items():
                new_base = base + [key]
                if hasattr(value, 'values'):
                    pairs += recursion(value, new_base)
                else:
                    new_pair = None
                    if len(new_base) > 1:
                        first = urllib.parse.quote(new_base.pop(0))
                        rest = map(urllib.parse.quote, new_base)
                        new_pair = f"{first}[{']['.join(rest)}]={urllib.parse.quote(str(value))}"
                    else:
                        new_pair = f'{urllib.parse.quote(str(key))}={urllib.parse.quote(str(value))}'
                    pairs.append(new_pair)
            return pairs

        return '&'.join(recursion(data))


# 为了向后兼容，显式导出异常类型
# 这些异常类型现在定义在 moodle_dl.exceptions 模块中
__all__ = [
    'RequestHelper',
    'RequestRejectedError',  # 向后兼容
    'MoodleNetworkError',
    'MoodleAPIError',
    'MoodleAuthError',
]
