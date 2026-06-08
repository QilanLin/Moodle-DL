# -*- coding: utf-8 -*-
import base64
import logging
import re
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.kaltura_patterns import MODULE_COOKIE_KALVIDRES
from moodle_dl.file_classifier import is_optional_metadata_file
from moodle_dl.moodle.cookie_handler import CookieHandler
from moodle_dl.moodle.core_handler import CoreHandler
from moodle_dl.moodle.mods import (
    fetch_mods_files,
    get_all_mods,
    get_all_mods_classes,
    get_mod_plurals,
)
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.moodle.result_builder import ResultBuilder
from moodle_dl.network_throttle import NetworkThrottle
from moodle_dl.types import Course, MoodleDlOpts, MoodleURL
from moodle_dl.utils import determine_ext, is_base_64


class MoodleService:
    def __init__(
        self,
        config: ConfigHelper,
        opts: MoodleDlOpts,
        network_throttle: Optional[NetworkThrottle] = None,
    ):
        self.config = config
        self.opts = opts
        self.network_throttle = network_throttle

    def obtain_login_token(self, username: str, password: str, moodle_url: MoodleURL) -> Tuple[str, Optional[str]]:
        "Send the login credentials to the Moodle-System and extracts the resulting Login-Token"
        login_data = {'username': username, 'password': password, 'service': 'moodle_mobile_app'}
        response = RequestHelper(self.config, self.opts, moodle_url, None).get_login(login_data)

        if 'token' not in response:
            # = we didn't get an error page (checked by the RequestHelper) but
            # somehow we don't have the needed token
            raise RuntimeError('Invalid response received from the Moodle System!  No token was received.')

        if 'privatetoken' not in response:
            return response.get('token', ''), None
        # Safely get privatetoken, handle None case
        privatetoken = response.get('privatetoken', '')
        return response.get('token', ''), privatetoken if privatetoken else None

    @staticmethod
    def extract_token(address: str) -> Optional[Tuple[str, Optional[str]]]:
        """
        Extracts a token from a returned URL
        See https://github.com/moodle/moodle/blob/master/admin/tool/mobile/launch.php for details
        """
        splitted = address.split('token=')

        if len(splitted) < 2:
            if is_base_64(address):
                decoded = str(base64.b64decode(address))
            else:
                logging.error(
                    '这可能不是正确的 URL。在输入中未找到令牌。'
                    + '你是否只插入了令牌而不是完整的 URL？'
                )
                return None
        else:
            decoded = str(base64.b64decode(splitted[1]))

        splitted = decoded.split(':::')
        if len(splitted) < 2:
            logging.error('无法解码令牌。你是否没有复制完整的 URL？')
            return None

        token = re.sub(r'[^A-Za-z0-9]+', '', splitted[1])

        if len(splitted) < 3:
            return token, None

        secret_token = re.sub(r'[^A-Za-z0-9]+', '', splitted[2])
        return (token, secret_token)

    def get_courses_list(self, core_handler: CoreHandler, user_id: int) -> List[Course]:
        download_course_ids = self.config.get_download_course_ids()
        download_public_course_ids = self.config.get_download_public_course_ids()
        dont_download_course_ids = self.config.get_dont_download_course_ids()

        # Determine filter mode based on which property exists in config
        use_whitelist = None
        if self.config.has_property('download_course_ids'):
            use_whitelist = True  # Whitelist mode (even if empty list)
        elif self.config.has_property('dont_download_course_ids'):
            use_whitelist = False  # Blacklist mode

        courses_list = core_handler.fetch_courses(user_id)
        courses = []
        # Filter unselected courses
        for course in courses_list:
            if MoodleService.should_download_course(course.id, download_course_ids, dont_download_course_ids, use_whitelist):
                courses.append(course)

        public_courses_list = core_handler.fetch_courses_info(download_public_course_ids)
        courses.extend(public_courses_list)
        return courses

    def get_user_id_and_version(self, core_handler: CoreHandler) -> Tuple[int, int]:
        user_id_raw, version = self.config.get_userid_and_version()
        if user_id_raw is None or version is None:
            logging.info('正在下载账户信息')
            user_id, version = core_handler.fetch_userid_and_version()
            logging.debug('检测到 Moodle 版本：%d', version)
        else:
            # Convert user_id from str to int (config returns str, but we need int)
            try:
                user_id = int(user_id_raw) if isinstance(user_id_raw, str) else user_id_raw
            except (ValueError, TypeError):
                logging.warning(f'无法将 user_id "{user_id_raw}" 转换为 int，尝试从服务器获取')
                user_id, version = core_handler.fetch_userid_and_version()
            core_handler.version = version
        return user_id, version

    async def fetch_state(self, database: StateRecorder) -> List[Course]:
        """
        Fetch the current status of the configured Moodle account and compare it with the last known state.
        
        原子化编排器：协调 5 个阶段的状态获取
        1. 初始化处理器和认证
        2. 获取课程基本信息
        3. 加载课程内容和模块
        4. 合并数据和添加块信息
        5. 检测变化并应用过滤
        
        It does not change the known state, nor does it download the files.
        @return: List with detected changes between the new and old state
        """
        logging.debug('正在获取当前 Moodle 状态...')
        
        # 阶段 1: 初始化所有处理器
        request_helper, core_handler, user_id, version = await self._initialize_handlers()
        
        # 阶段 1b: 可选的 Cookie 处理
        cookie_handler = self._setup_cookie_handler(request_helper, version, user_id)
        
        # 阶段 2: 获取课程列表
        courses = self.get_courses_list(core_handler, user_id)
        
        # 阶段 3: 加载内容和模块
        await self._load_course_contents_and_modules(core_handler, courses, user_id, database, request_helper)
        
        # 阶段 4: 合并结果并添加块信息
        await self._merge_results_and_add_blocks(core_handler, courses, request_helper)
        
        # 阶段 5: 检测变化并应用过滤
        changes = self._detect_and_filter_changes(database, courses, cookie_handler)
        
        return changes
    
    async def _initialize_handlers(self) -> Tuple[RequestHelper, CoreHandler, int, int]:
        """
        初始化所有必需的处理器（RequestHelper, CoreHandler）。
        
        Returns:
            (request_helper, core_handler, user_id, version)
        """
        token = self.config.get_token()
        moodle_url = self.config.get_moodle_URL()

        request_helper = RequestHelper(self.config, self.opts, moodle_url, token)
        request_helper.set_network_throttle(self.network_throttle)
        core_handler = CoreHandler(request_helper)
        user_id, version = self.get_user_id_and_version(core_handler)
        
        return request_helper, core_handler, user_id, version
    
    def _setup_cookie_handler(self, request_helper: 'RequestHelper', version: int, user_id: int) -> 'CookieHandler':
        """
        可选地初始化 CookieHandler（如果配置要求）。
        
        Args:
            request_helper: RequestHelper 实例
            version: Moodle 版本
            user_id: 用户 ID
            
        Returns:
            CookieHandler 实例或 None
        """
        cookie_handler = None
        if self.config.get_download_also_with_cookie():
            privatetoken = self.config.get_privatetoken()
            cookie_handler = CookieHandler(request_helper, version, self.config, self.opts)
            cookie_handler.check_and_fetch_cookies(privatetoken, user_id)
        
        return cookie_handler
    
    async def _load_course_contents_and_modules(
        self, 
        core_handler: 'CoreHandler', 
        courses: List[Course], 
        user_id: int,
        database: StateRecorder,
        request_helper: 'RequestHelper'
    ):
        """
        加载课程内容（sections）和模块文件。
        
        此函数直接修改 courses 列表（添加 files 属性）。
        """
        core_contents = await core_handler.async_load_core_contents(courses)
        mods = get_all_mods(request_helper, len(courses), user_id, database.get_last_timestamp_per_mod_module(), self.config)
        fetched_mods_files = await fetch_mods_files(mods, courses, core_contents)
        
        logging.debug('正在合并 API 结果...')
        moodle_url = self.config.get_moodle_URL()
        result_builder = ResultBuilder(moodle_url, len(courses), get_mod_plurals(), token=self.config.get_token())
        result_builder.add_files_to_courses(courses, core_contents, fetched_mods_files)
        
        # Debug: 验证 Kalvidres 文件数量
        self._log_kalvidres_count_after_merge(courses, 'AFTER add_files_to_courses()')
    
    async def _merge_results_and_add_blocks(
        self,
        core_handler: 'CoreHandler',
        courses: List[Course],
        request_helper: 'RequestHelper'
    ):
        """
        添加课程块（sidebar widgets）到课程对象。
        
        此函数直接修改 courses 列表（添加 blocks 属性）。
        """
        logging.debug('正在获取课程 blocks...')
        moodle_url = self.config.get_moodle_URL()
        result_builder = ResultBuilder(moodle_url, len(courses), get_mod_plurals(), token=self.config.get_token())
        
        for course in courses:
            try:
                course_blocks = core_handler.fetch_course_blocks(course.id)
                if course_blocks:
                    result_builder.add_blocks_to_course(course, course_blocks)
                    logging.debug(f'已为课程 {course.id} "{course.fullname}" 获取 {len(course_blocks)} 个 blocks')
            except Exception as e:
                logging.debug(f'获取课程 {course.id} 的 blocks 失败: {e}')
                # 即使块获取失败，继续处理
    
    def _log_kalvidres_count_after_merge(self, courses: List[Course], stage: str):
        """
        日志记录：检查每个课程中 Kalvidres 文件的数量。
        
        用于调试 Kalvidres 集成。
        """
        for course in courses:
            kalvidres_count = len([f for f in course.files if f.module_modname == MODULE_COOKIE_KALVIDRES])
            if kalvidres_count > 0:
                logging.info(f'✨ Course "{course.fullname}" has {kalvidres_count} Kaltura videos {stage}')
    
    def _detect_and_filter_changes(
        self,
        database: StateRecorder,
        courses: List[Course],
        cookie_handler: 'CookieHandler' = None
    ) -> List[Course]:
        """
        检测与数据库中已知状态的变化，并应用过滤。
        
        Args:
            database: StateRecorder 实例（用于变化检测）
            courses: 所有课程列表
            cookie_handler: CookieHandler 实例（可选）
            
        Returns:
            经过过滤的变化课程列表
        """
        logging.debug('正在检查变化...')
        changes = database.changes_of_new_version(courses)
        
        # Debug: 检查 Kalvidres 文件在变化中
        for change in changes:
            kalvidres_in_changes = len([f for f in change.files if f.module_modname == MODULE_COOKIE_KALVIDRES])
            if kalvidres_in_changes > 0:
                logging.info(f'📝 Changes for "{change.fullname}" contains {kalvidres_in_changes} Kaltura videos')
        
        # 应用课程选项
        changes = self.add_options_to_courses(changes)
        
        # 应用过滤
        changes = self.filter_courses(changes, self.config, cookie_handler, courses)
        
        return changes

    def add_options_to_courses(self, courses: List[Course]):
        "Updates the courses with their options"
        options_of_courses = self.config.get_options_of_courses()
        for course in courses:
            options = options_of_courses.get(str(course.id), None)
            if options is not None:
                course.overwrite_name_with = options.get('overwrite_name_with', None)
                course.create_directory_structure = options.get('create_directory_structure', True)
                excluded_sections_raw = options.get("excluded_sections", [])
                course.excluded_sections = ConfigHelper.normalize_id_list(excluded_sections_raw)

        return courses

    @staticmethod
    def _load_filter_config(config: ConfigHelper) -> dict:
        """Load all filter configuration parameters.
        
        只负责从配置中获取所有参数。
        
        Returns:
            dict with all filter configuration
        """
        return {
            'download_course_ids': config.get_download_course_ids(),
            'dont_download_course_ids': config.get_dont_download_course_ids(),
            'download_public_course_ids': config.get_download_public_course_ids(),
            'download_descriptions': config.get_download_descriptions(),
            'download_links_in_descriptions': config.get_download_links_in_descriptions(),
            'download_metadata_files': config.get_download_metadata_files(),
            'exclude_file_extensions': config.get_exclude_file_extensions(),
            'max_file_size': config.get_max_file_size(),
            'use_whitelist': (
                True if config.has_property('download_course_ids')
                else (False if config.has_property('dont_download_course_ids') else None)
            ),
        }

    @staticmethod
    def _verify_and_setup_cookies(
        config: ConfigHelper, 
        cookie_handler: CookieHandler = None
    ) -> bool:
        """Verify cookies and determine if they should be used for downloads.
        
        只负责 cookie 的验证和配置。
        
        Returns:
            bool indicating if cookies should be used for downloads
        """
        download_with_cookie = config.get_download_also_with_cookie()
        logging.info(f'🍪 Cookie download config value: {download_with_cookie}')
        
        if cookie_handler is None:
            return download_with_cookie
        
        cookies_are_valid = cookie_handler.test_cookies()
        logging.info(f'🍪 Cookie validation result: {cookies_are_valid}')
        
        if not cookies_are_valid and download_with_cookie:
            logging.warning(
                'Autologin cookies failed validation, but download_also_with_cookie is enabled. '
                'Will attempt to use cookies from Cookies.txt file (e.g., browser-exported cookies). '
                'For kalvidres/kaltura videos, export cookies from your browser after logging in.'
            )
            return True
        elif cookies_are_valid:
            return True
        else:
            return False

    @staticmethod
    def _check_course_availability(course: Course, courses_list: List[Course] = None) -> bool:
        """Check if course is available online.
        
        只负责检查课程是否在线。
        
        Returns:
            bool indicating if course is available
        """
        if courses_list is None:
            return True
        
        for online_course in courses_list:
            if online_course.id == course.id:
                return True
        
        logging.warning('ID 为 %d 的 Moodle 课程在线上已不可用。', course.id)
        return False

    @staticmethod
    def _check_module_download_conditions(file, all_mods_classes, config: ConfigHelper) -> tuple:
        """Check if file passes module download conditions.
        
        只负责检查模块条件。
        
        Returns:
            tuple (conditions_met: bool, failing_mod: str or None)
        """
        for mod in all_mods_classes:
            if not mod.download_condition(config, file):
                return False, mod.MOD_NAME
        return True, None

    @staticmethod
    def _is_optional_metadata_file(file) -> bool:
        return is_optional_metadata_file(file)

    @staticmethod
    def _check_file_filter_conditions(
        file, 
        filter_config: dict,
        download_with_cookie: bool,
        course
    ) -> bool:
        """Check if file passes all other filter conditions.
        
        只负责检查文件的各种过滤条件（扩展名、大小、section 等）。
        
        Returns:
            bool indicating if file should be included
        """
        return (
            # Filter Description Files (except the forum posts)
            (
                filter_config['download_descriptions']
                or file.content_type != 'description'
                or (
                    file.module_modname == 'forum'
                    and file.content_type == 'description'
                    and file.content_filename != 'Forum intro'
                )
            )
            # Filter Files that require a Cookie
            and (download_with_cookie or (not file.module_modname.startswith('cookie_mod-')))
            # Filter optional generated metadata/launch sidecars
            and (
                filter_config.get('download_metadata_files', True)
                or not MoodleService._is_optional_metadata_file(file)
            )
            # Exclude files whose file extension is blacklisted
            and (determine_ext(file.content_filename) not in filter_config['exclude_file_extensions'])
            # Exclude files that are in excluded sections
            and (MoodleService.should_download_section(file.section_id, course.excluded_sections))
            # Exclude files that are bigger than max_file_size
            and (filter_config['max_file_size'] == 0 or file.content_filesize < filter_config['max_file_size'])
        )

    @staticmethod
    def _filter_course_files(
        course_files: list,
        config: ConfigHelper,
        filter_config: dict,
        download_with_cookie: bool,
        course: Course,
        all_mods_classes
    ) -> list:
        """Filter course files based on all conditions.
        
        只负责对文件进行过滤，应用所有过滤条件。
        
        Returns:
            list of filtered files
        """
        filtered_files = []
        kalvidres_filtered_count = 0
        
        for file in course_files:
            # Check module conditions
            modules_conditions_met, failing_mod = MoodleService._check_module_download_conditions(
                file, all_mods_classes, config
            )
            
            is_kalvidres = file.module_modname == MODULE_COOKIE_KALVIDRES
            if is_kalvidres and not modules_conditions_met:
                kalvidres_filtered_count += 1
                logging.debug(f'❌ Kalvidres file "{file.content_filename}" filtered by module: {failing_mod}')
            
            # Check other filter conditions
            if (
                modules_conditions_met
                and MoodleService._check_file_filter_conditions(
                    file, filter_config, download_with_cookie, course
                )
            ):
                filtered_files.append(file)
            elif is_kalvidres:
                logging.debug(f'❌ Kalvidres file "{file.content_filename}" filtered by other conditions')
        
        if kalvidres_filtered_count > 0:
            logging.warning(f'⚠️  Filtered out {kalvidres_filtered_count} Kaltura videos due to module download conditions')
        
        kalvidres_passed = len([f for f in filtered_files if f.module_modname == MODULE_COOKIE_KALVIDRES])
        if kalvidres_passed > 0:
            logging.info(f'✅ {kalvidres_passed} Kaltura videos passed all filters for course "{course.fullname}"')
        
        return filtered_files

    @staticmethod
    def _should_keep_description_url(
        file,
        course_files: list
    ) -> bool:
        """Determine if a description URL should be kept.
        
        只负责判断描述 URL 是否应该保留。
        
        Returns:
            bool indicating if URL should be kept
        """
        for test_file in course_files:
            if file.content_fileurl == test_file.content_fileurl:
                if test_file.content_type != 'description-url':
                    # If a URL in a description also exists as a real link,
                    # then ignore this URL
                    return False
                if file.module_id > test_file.module_id:
                    # Always use the link from the older description
                    return False
        return True

    @staticmethod
    def _filter_description_urls(course_files: list, download_links: bool) -> list:
        """Filter description URLs based on settings and duplicates.
        
        只负责过滤描述 URL。
        
        Returns:
            list of files after URL filtering
        """
        if not download_links:
            return [f for f in course_files if f.content_type != 'description-url']
        
        filtered_files = []
        for file in course_files:
            if file.content_type != 'description-url':
                filtered_files.append(file)
            elif MoodleService._should_keep_description_url(file, course_files):
                filtered_files.append(file)
        
        return filtered_files

    @staticmethod
    def filter_courses(
        changes: List[Course],
        config: ConfigHelper,
        cookie_handler: CookieHandler = None,
        courses_list: List[Course] = None,
    ) -> List[Course]:
        """
        Filters the changes course list from courses that should not get downloaded.
        
        Process pipeline:
        1. Load and verify filter configuration
        2. For each course: apply course-level filters
        3. For each file: apply file-level filters
        4. Apply description URL filters
        5. Return filtered results
        
        @param config: ConfigHelper to obtain all the different filter configs
        @param cookie_handler: CookieHandler to check if the cookie is valid
        @param courses_list: A list of all courses that are available online
        @return: filtered changes course list
        """
        # Step 1: Load configuration
        filter_config = MoodleService._load_filter_config(config)
        download_with_cookie = MoodleService._verify_and_setup_cookies(config, cookie_handler)
        logging.info(f'🍪 Final download_also_with_cookie value: {download_with_cookie}')
        
        all_mods_classes = get_all_mods_classes()
        filtered_changes = []
        
        # Step 2-5: Process each course
        for course in changes:
            # Step 2: Course-level filters
            if not MoodleService.should_download_course(
                course.id,
                filter_config['download_course_ids'] + filter_config['download_public_course_ids'],
                filter_config['dont_download_course_ids'],
                filter_config['use_whitelist']
            ):
                continue
            
            if not MoodleService._check_course_availability(course, courses_list):
                continue
            
            # Debug: Count kalvidres files before filtering
            kalvidres_before = len([f for f in course.files if f.module_modname == MODULE_COOKIE_KALVIDRES])
            if kalvidres_before > 0:
                logging.info(f'📊 Course "{course.fullname}" has {kalvidres_before} Kaltura videos BEFORE filtering')
            
            # Step 3: File-level filters
            course.files = MoodleService._filter_course_files(
                course.files, config, filter_config, download_with_cookie, course, all_mods_classes
            )
            
            # Step 4: Description URL filters
            course.files = MoodleService._filter_description_urls(
                course.files, filter_config['download_links_in_descriptions']
            )
            
            # Step 5: Add course if it has files
            if len(course.files) > 0:
                filtered_changes.append(course)
        
        return filtered_changes

    @staticmethod
    def should_download_course(
        course_id: int, download_course_ids: List[int], dont_download_course_ids: List[int],
        use_whitelist: bool = None
    ) -> bool:
        """
        Checks if a course should be downloaded.

        Args:
            course_id: The course ID to check
            download_course_ids: List of course IDs in whitelist
            dont_download_course_ids: List of course IDs in blacklist
            use_whitelist: If True, use whitelist mode; if False, use blacklist mode;
                          if None, auto-detect based on lists

        Modes:
            - Whitelist mode: Only download courses in download_course_ids (even if empty list)
            - Blacklist mode: Download all courses except those in dont_download_course_ids
            - Default (no configuration): Download all courses
        """
        # Auto-detect mode if not specified
        if use_whitelist is None:
            # If blacklist has items, it's blacklist mode
            if len(dont_download_course_ids) > 0:
                use_whitelist = False
            # If whitelist has items, it's whitelist mode
            elif len(download_course_ids) > 0:
                use_whitelist = True
            # Both empty = default behavior (download all)
            else:
                return True

        # Blacklist mode: download all except blacklisted courses
        if not use_whitelist:
            return course_id not in dont_download_course_ids

        # Whitelist mode: only download whitelisted courses (even if list is empty)
        return course_id in download_course_ids

    @staticmethod
    def should_download_section(section_id: int, dont_download_sections_ids: List[int]) -> bool:
        "Checks if a section is not in blacklist"
        return section_id not in dont_download_sections_ids or len(dont_download_sections_ids) == 0

    @staticmethod
    def split_moodle_url(moodle_url: str) -> Tuple[str, str]:
        """
        Splits a given Moodle URL into the domain and the installation path
        @return: moodle_domain, moodle_path as strings
        """
        moodle_uri = urlparse(moodle_url)
        moodle_domain = moodle_uri.netloc
        moodle_path = moodle_uri.path
        if not moodle_path.endswith('/'):
            moodle_path = moodle_path + '/'

        if moodle_path == '':
            moodle_path = '/'

        return moodle_domain, moodle_path
