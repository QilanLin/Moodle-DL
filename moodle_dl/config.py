import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from moodle_dl.types import DownloadOptions, MoodleDlOpts, MoodleURL
from moodle_dl.utils import PathTools as PT


class ConfigHelper:
    "Handles the saving, formatting and loading of the local configuration."

    class NoConfigError(ValueError):
        """An Exception which gets thrown if config could not be loaded."""

        pass

    def __init__(self, opts: MoodleDlOpts):
        self._whole_config = {}
        self.opts = opts
        self.config_path = str(Path(opts.path) / 'config.json')
        self._auth_manager = None
        self._db_file = None

        # 初始化认证管理器(用于存储 tokens 到数据库)
        # 数据库必须初始化成功,否则抛出异常,不使用 fallback
        self._db_file = str(Path(opts.path) / 'moodle_state.db')
        from moodle_dl.auth_session_manager import AuthSessionManager
        self._auth_manager = AuthSessionManager(self._db_file)

        if not self._auth_manager:
            raise RuntimeError(
                f'❌ 认证管理器初始化失败.数据库必须可用.\n'
                f'检查 {self._db_file} 是否存在且可写.'
            )

    def is_present(self) -> bool:
        # Tests if a configuration file exists
        return os.path.isfile(self.config_path)

    def load(self):
        # TODO: Load config into dataclass, so we can access that class instead of using getters
        # Opens the configuration file and parse it to a JSON object
        try:
            with open(self.config_path, 'r', encoding='utf-8') as config_file:
                config_raw = config_file.read()
                self._whole_config = json.loads(config_raw)
        except (IOError, OSError) as err_load:
            raise ConfigHelper.NoConfigError(f'Configuration could not be loaded from {self.config_path}\n{err_load!s}')

    def _save(self):
        # TODO: Use dataclass and write that back to file, so that all options are always present
        config_formatted = json.dumps(self._whole_config, indent=4)
        # Saves the JSON object back to file
        with os.fdopen(
            os.open(self.config_path, flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode=0o600),
            mode='w',
            encoding='utf-8',
        ) as config_file:
            config_file.write(config_formatted)

    def get_property(self, key: str) -> any:
        # return a property if configured
        try:
            return self._whole_config[key]
        except KeyError:
            raise ValueError(f'The {key}-Property is not yet configured!')

    def get_property_or(self, key: str, default: any = None) -> any:
        # return a property if configured
        try:
            return self._whole_config[key]
        except KeyError:
            return default

    def has_property(self, key: str) -> bool:
        """Check if a property exists in the configuration"""
        return key in self._whole_config

    def get_download_option(self, option_name: str, default: bool = False) -> bool:
        """
        Generic getter for download_* configuration options.

        Args:
            option_name: The name of the option (e.g., 'submissions', 'quizzes')
                        Will be prefixed with 'download_'
            default: Default value if not configured

        Returns:
            bool: Whether the option should be downloaded

        Example:
            get_download_option('submissions') -> reads 'download_submissions'
        """
        return self.get_property_or(f'download_{option_name}', default)

    def set_property(self, key: str, value: any):
        # sets a property in the JSON object
        self._whole_config.update({key: value})
        self._save()

    def remove_property(self, key):
        # removes a property from the JSON object
        self._whole_config.pop(key, None)
        #                           ^ behavior if the key is not present
        self._save()

    # ---------------------------- GETTERS ------------------------------------

    # Download option getters - using generic method to avoid code duplication
    def get_download_submissions(self) -> bool:
        return self.get_download_option('submissions')

    def get_download_descriptions(self) -> bool:
        return self.get_download_option('descriptions')

    def get_download_links_in_descriptions(self) -> bool:
        return self.get_download_option('links_in_descriptions')

    def get_download_databases(self) -> bool:
        return self.get_download_option('databases')

    def get_download_forums(self) -> bool:
        return self.get_download_option('forums')

    def get_download_quizzes(self) -> bool:
        return self.get_download_option('quizzes')

    def get_download_lessons(self) -> bool:
        return self.get_download_option('lessons')

    def get_download_workshops(self) -> bool:
        return self.get_download_option('workshops')

    def get_download_books(self) -> bool:
        return self.get_download_option('books')

    def get_download_bigbluebuttonbns(self) -> bool:
        return self.get_download_option('bigbluebuttonbns')

    def get_download_wikis(self) -> bool:
        return self.get_download_option('wikis')

    def get_download_glossaries(self) -> bool:
        return self.get_download_option('glossaries')

    def get_download_h5pactivities(self) -> bool:
        return self.get_download_option('h5pactivities')

    def get_download_h5p_attempts(self) -> bool:
        return self.get_download_option('h5p_attempts')

    def get_download_imscps(self) -> bool:
        return self.get_download_option('imscps')

    def get_download_scorms(self) -> bool:
        return self.get_download_option('scorms')

    def get_download_scorm_scos(self) -> bool:
        return self.get_download_option('scorm_scos')

    def get_download_scorm_attempts(self) -> bool:
        return self.get_download_option('scorm_attempts')

    def get_download_subsections(self) -> bool:
        return self.get_download_option('subsections')

    def get_download_qbanks(self) -> bool:
        return self.get_download_option('qbanks')

    def get_download_resources(self) -> bool:
        # Resource modules are one of the most commonly used in Moodle for file uploads
        return self.get_download_option('resources', default=True)

    def get_download_urls(self) -> bool:
        return self.get_download_option('urls')

    def get_download_labels(self) -> bool:
        return self.get_download_option('labels')

    def get_download_chats(self) -> bool:
        return self.get_download_option('chats')

    def get_download_choices(self) -> bool:
        return self.get_download_option('choices')

    def get_download_feedbacks(self) -> bool:
        return self.get_download_option('feedbacks')

    def get_download_surveys(self) -> bool:
        return self.get_download_option('surveys')

    def get_download_ltis(self) -> bool:
        # LTI (external tool) module is always enabled for complete metadata export
        # This method is kept for backward compatibility but always returns True
        # The old cookie-based handling for 'lti' has been replaced with the full LTI module
        # Note: kalvidres and helixmedia still use cookie-based handling with dedicated extractors
        return True

    def get_download_calendars(self) -> bool:
        return self.get_download_option('calendars')

    def get_auth_manager(self):
        """获取 AuthSessionManager 实例(用于数据库操作)"""
        return self._auth_manager

    def get_userid_and_version(self) -> Tuple[str, int]:
        # return the userid and a version
        try:
            user_id = self.get_property('userid')
            version = int(self.get_property('version'))
            return user_id, version
        except ValueError:
            return None, None

    def get_do_not_ask_to_save_userid_and_version(self) -> bool:
        return self.get_property_or('do_not_ask_to_save_userid_and_version', False)

    def get_download_course_ids(self) -> str:
        # return a stored list of course ids hat should be downloaded
        return self.get_property_or('download_course_ids', [])

    def get_download_public_course_ids(self) -> str:
        # return a stored list of public course ids hat should be downloaded
        return self.get_property_or('download_public_course_ids', [])

    def get_token(self) -> str:
        """
        获取 token

        v2:从数据库读取有效的 token
        如果数据库中无有效 token,则从 JSON 配置读取
        (不做 fallback,数据库读取失败直接抛出异常)
        """
        # 优先从数据库读取有效的 token session
        session = self._auth_manager.get_valid_session(session_type='token')
        if session and session.get('token_value'):
            return session['token_value']

        # 如果数据库无有效 token,从 JSON 配置读取
        try:
            return self.get_property('token')
        except ValueError:
            raise ValueError('Token not yet configured!')

    def get_privatetoken(self) -> str:
        """
        获取 private token

        v2:从数据库读取有效的 private token
        如果数据库中无有效 private token,则从 JSON 配置读取
        """
        # 优先从数据库读取有效的 token session
        session = self._auth_manager.get_valid_session(session_type='token')
        if session and session.get('private_token_value'):
            return session['private_token_value']

        # 如果数据库无有效 private token,从 JSON 配置读取
        return self.get_property_or('privatetoken', None)

    def get_moodle_URL(self) -> MoodleURL:
        moodle_domain = self.get_moodle_domain()
        moodle_path = self.get_moodle_path()
        use_http = self.get_use_http()
        return MoodleURL(use_http, moodle_domain, moodle_path)

    def get_moodle_domain(self) -> str:
        # return a stored moodle_domain
        try:
            return self.get_property('moodle_domain')
        except ValueError:
            raise ValueError('Not yet configured!')

    def get_moodle_path(self) -> str:
        # return a stored moodle_path
        try:
            return self.get_property('moodle_path')
        except ValueError:
            raise ValueError('Not yet configured!')

    def get_options_of_courses(self) -> Dict:
        # return a stored dictionary of options for courses
        return self.get_property_or('options_of_courses', {})

    def get_dont_download_course_ids(self) -> List:
        # return a stored list of ids that should not be downloaded
        return self.get_property_or('dont_download_course_ids', [])

    def get_download_linked_files(self) -> bool:
        # return if linked files should be downloaded
        return self.get_property_or('download_linked_files', False)

    def get_download_domains_whitelist(self) -> List:
        # return a list of white listed domains that should be downloaded
        return self.get_property_or('download_domains_whitelist', [])

    def get_download_domains_blacklist(self) -> List:
        # return a list of black listed domains that should not be downloaded
        return self.get_property_or('download_domains_blacklist', [])

    def get_cookies_text(self) -> str:
        """
        获取 cookies 文本
        
        v2:优先从数据库读取 cookie_batch 会话的 cookies
        如果数据库中没有或读取失败,回退到读取 txt 文件
        """
        # v2: 尝试从数据库读取 cookies(新方式)
        try:
            # 查找最新的有效的 cookie batch 会话
            session = self._auth_manager.get_valid_session(session_type='cookie_batch')
            if session:
                session_id = session['session_id']
                cookies = self._auth_manager.get_session_cookies(session_id)
                
                # 将 cookies 转换为 Netscape cookies.txt 格式
                if cookies:
                    cookie_lines = []
                    cookie_lines.append('# Netscape HTTP Cookie File')
                    cookie_lines.append('# Generated by Moodle-DL Auth Session Manager')
                    cookie_lines.append('')
                    
                    for cookie in cookies:
                        # 格式: domain	flag	path	secure	expires	name	value
                        domain = cookie.get('domain', '')
                        # 如果域名不以.开头且不是localhost,添加.
                        if domain and not domain.startswith('.') and domain != 'localhost':
                            domain = '.' + domain
                        
                        flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                        path = cookie.get('path', '/')
                        secure = 'TRUE' if cookie.get('secure', 0) else 'FALSE'
                        expires = str(cookie.get('expires', 0))
                        name = cookie.get('name', '')
                        value = cookie.get('value', '')
                        
                        if name and value:
                            cookie_line = f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}"
                            cookie_lines.append(cookie_line)
                    
                    cookies_text = '\n'.join(cookie_lines)
                    if cookies_text.strip():
                        return cookies_text
        except Exception as e:
            # 数据库读取失败,记录日志并回退到文件读取
            logging.error(f'从数据库读取 cookies 失败: {e},回退到文件读取')
        
        # v1: 从 txt 文件读取 cookies(向后兼容)
        cookies_path = PT.get_cookies_path(self.get_misc_files_path())
        if os.path.isfile(cookies_path):
            with open(cookies_path, 'r', encoding='utf-8') as cookie_file:
                return cookie_file.read()
        
        return None

    def get_yt_dlp_options(self) -> Dict:
        # return additional yt-dlp options
        return self.get_property_or('yt_dlp_options', {})

    def get_video_passwords(self) -> Dict:
        # return dict with passwords that get passed to yt-dlp
        return self.get_property_or('video_passwords', {})

    def get_external_file_downloaders(self) -> Dict:
        # return dict with configured external downloaders
        return self.get_property_or('external_file_downloaders', {})

    def get_exclude_file_extensions(self) -> Dict:
        # return a list of file extensions that should not be downloaded
        try:
            exclude_file_extensions = self.get_property('exclude_file_extensions')
            if not isinstance(exclude_file_extensions, list):
                exclude_file_extensions = [exclude_file_extensions]
            return exclude_file_extensions
        except ValueError:
            return []

    def get_max_file_size(self) -> int:
        # return the max size in bytes of files that should not be downloaded
        # default: 0 -> all file sizes
        return self.get_property_or('max_file_size', 0)

    def get_download_also_with_cookie(self) -> Dict:
        # return if files for which a cookie is required should be downloaded
        return self.get_property_or('download_also_with_cookie', False)

    def get_write_links(self) -> Dict:
        # returns what kind of shortcuts should be created
        write_links = {
            'url': self.get_property_or('write_url_link', False),
            'webloc': self.get_property_or('write_webloc_link', False),
            'desktop': self.get_property_or('write_desktop_link', False),
        }
        if self.get_property_or('write_link', True):
            link_type = (
                'webloc' if sys.platform == 'darwin' else 'desktop' if sys.platform.startswith('linux') else 'url'
            )
            write_links[link_type] = True

        return write_links

    def get_download_options(self, opts: MoodleDlOpts) -> DownloadOptions:
        # return the option dictionary for downloading files

        return DownloadOptions(
            token=self.get_token(),
            moodle_url=self.get_moodle_URL().url_base,
            download_linked_files=self.get_download_linked_files(),
            download_domains_whitelist=self.get_download_domains_whitelist(),
            download_domains_blacklist=self.get_download_domains_blacklist(),
            cookies_text=self.get_cookies_text(),
            yt_dlp_options=self.get_yt_dlp_options(),
            video_passwords=self.get_video_passwords(),
            external_file_downloaders=self.get_external_file_downloaders(),
            restricted_filenames=self.get_restricted_filenames(),
            write_links=self.get_write_links(),
            download_path=self.get_download_path(),
            global_opts=opts,
        )

    def get_restricted_filenames(self) -> Dict:
        # return the filenames should be restricted
        return self.get_property_or('restricted_filenames', False)

    def get_use_http(self) -> bool:
        # return a stored boolean if http should be used instead of https
        return self.get_property_or('use_http', False)

    def get_download_path(self) -> str:
        # return path of download location
        return self.get_property_or('download_path', self.opts.path)

    def get_misc_files_path(self) -> str:
        # return path of misc files
        return self.get_property_or('misc_files_path', self.opts.path)

    # ---------------------------- SETTERS ------------------------------------

    def set_moodle_URL(self, moodle_url: MoodleURL):
        self.set_property('moodle_domain', moodle_url.domain)
        self.set_property('moodle_path', moodle_url.path)
        if moodle_url.use_http is True:
            self.set_property('use_http', moodle_url.use_http)
        else:
            if self.get_use_http():
                self.set_property('use_http', moodle_url.use_http)

    def set_tokens(self, moodle_token: str, moodle_privatetoken: str):
        """
        设置 token 和 private token

        v2:同时保存到数据库和 JSON 配置
        - JSON:向后兼容
        - 数据库:存储到 auth_sessions 表,形成版本链

        数据库操作失败时直接抛出异常(不做 fallback)
        """
        # 1. 保存到数据库(优先执行,失败则抛出异常)
        # 尝试获取已有的 token session
        old_session = self._auth_manager.get_valid_session(session_type='token')

        if old_session:
            # 创建新版本的 session
            self._auth_manager.refresh_session(
                old_session_id=old_session['session_id'],
                new_token=moodle_token,
                new_private_token=moodle_privatetoken
            )
        else:
            # 创建新 session
            self._auth_manager.create_session(
                session_type='token',
                source='api_login',
                token=moodle_token,
                private_token=moodle_privatetoken
            )

        # 2. 保存到 JSON 配置(向后兼容)
        self.set_property('token', moodle_token)
        if moodle_privatetoken is not None:
            self.set_property('privatetoken', moodle_privatetoken)
