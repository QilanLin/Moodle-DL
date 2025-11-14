import logging
import os
from typing import Dict

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.request_helper import RequestHelper, RequestRejectedError
from moodle_dl.types import MoodleDlOpts
from moodle_dl.utils import PathTools as PT
from moodle_dl.exceptions import MoodleAPIError


class CookieHandler:
    """
    Fetches and saves the cookies of Moodle.
    """

    def __init__(self, request_helper: RequestHelper, version: int, config: ConfigHelper, opts: MoodleDlOpts):
        self.client = request_helper
        self.version = version
        self.config = config
        self.opts = opts
        self.cookies_path = PT.get_cookies_path(config.get_misc_files_path())

        self.moodle_test_url = self.client.url_base

    def fetch_autologin_key(self, privatetoken: str) -> Dict[str, str]:
        if self.version < 2016120500:  # 3.2
            return None

        logging.info('Downloading autologin key')

        extra_data = {'privatetoken': privatetoken}

        try:
            autologin_key_result = self.client.post('tool_mobile_get_autologin_key', extra_data)
            return autologin_key_result
        except (RequestRejectedError, MoodleAPIError) as e:
            # 某些Moodle实例（如KCL）限制此API仅限移动应用访问
            # 在这种情况下，我们返回None，让cookie管理使用其他方法
            logging.debug("无法获取autologin key (可能此Moodle实例限制了API访问): %s", e)
            return None

    def test_cookies(self) -> bool:
        """
        Test if cookies are valid
        支持多种验证方式，更加容错
        @return: True if valid
        """

        logging.debug('Testing cookies using this URL: %s', self.moodle_test_url)

        response, dummy = self.client.get_URL(self.moodle_test_url, self.cookies_path)
        response_text = response.text
        response_url = response.url

        # 方法 1：检查是否有 logout 链接（最直接的有效标记）
        if response_text.find('login/logout.php') >= 0:
            logging.debug('✅ 验证成功（方法1）：找到 logout 链接')
            return True

        # 方法 2：检查是否被重定向到登录页（cookies 无效的标志）
        if 'login/index.php' in response_url or 'enrol/index.php' in response_url:
            logging.debug(f'❌ 验证失败（方法2）：被重定向到登录/注册页: {response_url}')
            return False

        # 方法 3：检查页面是否含有 Moodle 特定的内容标记
        moodle_markers = [
            'moodle',
            'course',
            'dashboard',
        ]
        if any(marker.lower() in response_text.lower() for marker in moodle_markers):
            logging.debug('✅ 验证成功（方法3）：页面包含 Moodle 标记')
            return True

        # 方法 4：检查是否有错误提示（未登录的标志）
        error_markers = [
            'not logged in',
            'login required',
            'guest access',
            'please log in',
        ]
        if any(marker.lower() in response_text.lower() for marker in error_markers):
            logging.debug('❌ 验证失败（方法4）：页面显示未登录错误')
            return False

        # 方法 5：基于响应长度（如果被重定向到登录页，响应会很短）
        if len(response_text) < 100:
            logging.debug(f'⚠️  验证不确定（方法5）：响应内容很短 ({len(response_text)} 字符)')
            return False

        # 如果以上方法都不能确定，假设 cookies 有效
        # 理由：如果 cookies 真的过期了，会被重定向到登录页（方法2会检测到）
        logging.debug('✅ 验证成功（默认）：无法判定为过期，假设 cookies 有效')
        return True

    def check_and_fetch_cookies(self, privatetoken: str, userid: str) -> bool:
        if os.path.exists(self.cookies_path):
            if self.test_cookies():
                logging.debug('Cookies are still valid')
                return True

            logging.info('Moodle cookie has expired, an attempt is made to generate a new cookie.')

        if privatetoken is None:
            error_msg = (
                'Moodle Cookies are not retrieved because no private token is set.'
                + ' To set a private token, use the `--new-token` option (if necessary also with `--sso`).'
            )
            logging.debug(error_msg)
            # 🔄 回退到从浏览器导出 cookies
            return self._try_refresh_from_browser()

        autologin_key = self.fetch_autologin_key(privatetoken)

        if autologin_key is None:
            logging.debug('Failed to download autologin key!')
            # 🔄 回退到从浏览器导出 cookies（API 失败时的备选方案）
            return self._try_refresh_from_browser()

        logging.info('Downloading cookies')

        post_data = {'key': autologin_key.get('key', ''), 'userid': userid}
        url = autologin_key.get('autologinurl', '')

        cookies_response, _ = self.client.post_URL(url, post_data, self.cookies_path)

        logging.debug('Autologin redirected to %s', cookies_response.url)

        if self.test_cookies():
            return True

        logging.debug('Failed to generate cookies!')
        # 🔄 回退到从浏览器导出 cookies
        return self._try_refresh_from_browser()

    def _try_refresh_from_browser(self) -> bool:
        """
        从浏览器刷新 cookies 的回退方案

        当 autologin key API 失败时（如被限制、无 privatetoken 等），
        尝试从用户浏览器中导出 cookies。

        @return: 成功返回 True，失败返回 False
        """
        try:
            # 使用 CookieManager 从浏览器导出 cookies
            from moodle_dl.cookie_manager import CookieManager
            from moodle_dl.utils import Log

            cookie_manager = CookieManager(
                config=self.config,
                moodle_domain=self.client.moodle_url.domain,
                cookies_path=self.cookies_path
            )

            Log.info('🔄 正在尝试从浏览器导出cookies...')

            if cookie_manager.refresh_cookies(auto_get_token=False):
                # 验证新导出的 cookies
                if self.test_cookies():
                    Log.success('✅ 成功从浏览器刷新cookies！')
                    return True
                else:
                    Log.warning('⚠️  从浏览器导出了cookies，但验证失败')
                    return False
            else:
                Log.warning('⚠️  从浏览器导出cookies失败')
                return False

        except Exception as e:
            logging.debug(f'从浏览器刷新cookies时出错: {e}')
            return False
