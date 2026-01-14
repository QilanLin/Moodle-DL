# -*- coding: utf-8 -*-
import json
import logging
from typing import Dict, List

import phpserialize

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class UrlMod(MoodleMod):
    """
    URL module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/url/services/url.ts

    Supports:
    - URL metadata export (display options, parameters)
    - Link shortcuts creation
    - External URL tracking
    """

    MOD_NAME = 'url'
    MOD_PLURAL_NAME = 'urls'
    MOD_MIN_VERSION = 2015111600  # 3.0

    # Display types based on Moodle constants
    DISPLAY_OPEN = 0  # Open in same window
    DISPLAY_POPUP = 1  # Open in popup
    DISPLAY_EMBED = 2  # Embed in page
    DISPLAY_DOWNLOAD = 3  # Force download
    DISPLAY_AUTO = 5  # Automatic

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_urls() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all URL modules from courses

        Process:
        1. Get URLs by courses
        2. Export URL metadata
        3. Create link shortcuts (handled by downloader)
        """

        result = {}
        if not self.config.get_download_urls():
            return result

        # 首先尝试使用 Mobile API
        try:
            response = await self.client.async_post(
                'mod_url_get_urls_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            urls = response.get('urls', [])
        except (RequestRejectedError, Exception) as e:
            # Mobile API 失败，尝试 Web API fallback
            logging.debug(f"Mobile API 获取 URL 模块失败: {e}，尝试使用 Web API fallback...")
            urls = await self._fetch_urls_web_api(courses, core_contents)

        for url_mod in urls:
            course_id = url_mod.get('course', 0)
            module_id = url_mod.get('coursemodule', 0)
            url_name = url_mod.get('name', 'unnamed url')

            # Get intro files
            url_files = self.get_introfiles(url_mod, 'url_introfile')

            # Add intro description
            url_intro = url_mod.get('intro', '')
            intro_file = self.create_intro_file(url_intro)
            if intro_file:
                url_files.append(intro_file)

            # Get the external URL
            external_url = url_mod.get('externalurl', '')

            # Create metadata file
            display_type = url_mod.get('display', self.DISPLAY_AUTO)
            display_options = url_mod.get('displayoptions', '')
            parameters = url_mod.get('parameters', '')

            metadata = {
                'url_id': url_mod.get('id', 0),
                'course_id': course_id,
                'name': url_name,
                'external_url': external_url,
                'display': {
                    'type': display_type,
                    'type_name': self._get_display_type_name(display_type),
                    'options': self._parse_display_options(display_options),
                },
                'parameters': self._parse_parameters(parameters),
                'timestamps': {
                    'time_modified': url_mod.get('timemodified', 0),
                },
                'features': self.get_features(purpose='content'),
                'note': 'URL module provides links to external resources. '
                + 'This export includes URL metadata, display settings, and parameters.',
            }

            url_files.append(
                self.create_metadata_file(metadata, timemodified=url_mod.get('timemodified', 0))
            )

            # Get module from core_contents to access URL files
            # URL modules in core_course_get_contents contain the actual file URL in contents
            module_contents = self.get_module_in_core_contents(course_id, module_id, core_contents)
            if module_contents:
                # Add URL file contents (the actual external files to download)
                for content in module_contents.get('contents', []):
                    # URL modules have type='url' in their contents
                    # These should be downloaded if download_urls is enabled
                    filename = content.get('filename', '')
                    if filename and content.get('type') == 'url':
                        # Add the URL file for download
                        url_files.append(content)

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': url_mod.get('id', 0),
                    'name': url_name,
                    'files': url_files,
                },
            )

        return result

    def _get_display_type_name(self, display_type: int) -> str:
        """
        Get human-readable name for display type

        @param display_type: Display type constant
        @return: Display type name
        """
        display_names = {
            self.DISPLAY_OPEN: 'Open',
            self.DISPLAY_POPUP: 'Popup',
            self.DISPLAY_EMBED: 'Embed',
            self.DISPLAY_DOWNLOAD: 'Download',
            self.DISPLAY_AUTO: 'Automatic',
        }
        return display_names.get(display_type, 'Unknown')

    def _parse_display_options(self, display_options: str) -> Dict:
        """
        Parse display options string into dictionary

        Display options are stored as URL parameters like: "width=620&height=450&printintro=1"

        @param display_options: Display options string
        @return: Parsed options dictionary
        """
        if not display_options:
            return {}

        options = {}
        try:
            # Parse URL-encoded parameters
            pairs = display_options.split('&')
            for pair in pairs:
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    # Try to convert to appropriate type
                    # Safely handle None or empty value
                    if not value:
                        options[key] = value
                    elif value.isdigit():
                        options[key] = int(value)
                    elif value.lower() in ('true', 'false'):
                        options[key] = value.lower() == 'true'
                    else:
                        options[key] = value
        except Exception as e:
            logging.debug("Error parsing display options '%s': %s", display_options, str(e))

        return options

    def _parse_parameters(self, parameters: str) -> Dict:
        """
        Parse URL parameters string into dictionary

        Parameters are stored in serialized format. In Moodle, this is typically:
        - PHP serialized array (e.g., "a:1:{s:4:\"name\";s:5:\"value\";}")
        - URL parameter format (e.g., "param1=value1&param2=value2")
        - Empty string if no parameters

        @param parameters: Parameters string
        @return: Parsed parameters dictionary or structured representation
        """
        if not parameters:
            return {}

        # Try to parse as URL-encoded parameters first
        if '=' in parameters and not parameters.startswith('a:'):
            parsed = {}
            try:
                pairs = parameters.split('&')
                for pair in pairs:
                    if '=' in pair:
                        key, value = pair.split('=', 1)
                        # URL decode and type conversion
                        # Safely handle None or empty value
                        if not value:
                            parsed[key] = value
                        elif value.isdigit():
                            parsed[key] = int(value)
                        elif value.lower() in ('true', 'false'):
                            parsed[key] = value.lower() == 'true'
                        else:
                            parsed[key] = value
                return parsed
            except Exception as e:
                logging.debug("Error parsing URL parameters '%s': %s", parameters, str(e))

        # Try to parse as PHP serialized array using phpserialize library
        if parameters.startswith('a:'):
            try:
                # Decode PHP serialized array to Python dict
                # phpserialize returns bytes, so we need to decode to str
                unserialized = phpserialize.loads(parameters.encode('utf-8'))

                # Convert bytes keys/values to strings
                parsed = {}
                for key, value in unserialized.items():
                    # Decode key (always bytes in phpserialize)
                    key_str = key.decode('utf-8') if isinstance(key, bytes) else key

                    # Handle different value types
                    if isinstance(value, bytes):
                        value_str = value.decode('utf-8')
                        # Try to convert to appropriate type
                        if value_str.isdigit():
                            parsed[key_str] = int(value_str)
                        elif value_str.lower() in ('true', 'false'):
                            parsed[key_str] = value_str.lower() == 'true'
                        else:
                            parsed[key_str] = value_str
                    elif isinstance(value, dict):
                        # Nested array - decode recursively
                        parsed[key_str] = self._decode_php_dict(value)
                    elif isinstance(value, list):
                        # Array of values
                        parsed[key_str] = [
                            v.decode('utf-8') if isinstance(v, bytes) else v
                            for v in value
                        ]
                    else:
                        # Keep as-is (int, bool, etc.)
                        parsed[key_str] = value

                return parsed
            except Exception as e:
                logging.debug("Error parsing PHP serialized parameters '%s': %s", parameters, str(e))
                # Fallback to structured metadata if parsing fails
                return {
                    'format': 'php_serialized',
                    'raw': parameters,
                    'error': f'Parse error: {str(e)}',
                    'note': 'PHP serialized data - parsing failed, raw data preserved'
                }

        # Return raw string in structured format for unknown formats
        return {
            'format': 'unknown',
            'raw': parameters,
        }

    def _decode_php_dict(self, php_dict: dict) -> dict:
        """
        Recursively decode a PHP dictionary (bytes keys/values) to Python dict

        @param php_dict: Dictionary from phpserialize with bytes keys/values
        @return: Dictionary with string keys/values
        """
        decoded = {}
        for key, value in php_dict.items():
            key_str = key.decode('utf-8') if isinstance(key, bytes) else key

            if isinstance(value, bytes):
                decoded[key_str] = value.decode('utf-8')
            elif isinstance(value, dict):
                decoded[key_str] = self._decode_php_dict(value)
            elif isinstance(value, list):
                decoded[key_str] = [
                    v.decode('utf-8') if isinstance(v, bytes) else v
                    for v in value
                ]
            else:
                decoded[key_str] = value

        return decoded

    async def _fetch_urls_web_api(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> List[Dict]:
        """
        使用 Web API fallback 获取 URL 模块信息。

        这是 mod_url_get_urls_by_courses 的 fallback 实现。
        通过 core_course_get_contents 获取 url 模块信息。

        改进版：从 module 对象中提取更多可用字段，减少硬编码默认值

        Return: 转换为与 Mobile API 相同格式的 url 列表
        """
        logging.debug('🌐 使用 Web API fallback 获取 URL 模块信息...')

        urls = []

        # 从 core_contents 中提取 url 模块
        modules_by_course = self.extract_modules_from_core_contents(courses, core_contents, 'url')

        for course in courses:
            course_id = course.id
            if course_id not in modules_by_course:
                continue

            for module in modules_by_course[course_id]:
                # 从 contents 中提取 externalurl
                externalurl = ''
                contents = module.get('contents', [])
                for content in contents:
                    if content.get('type') == 'url':
                        externalurl = content.get('fileurl', '')
                        break

                # 从 module 对象中提取更多可用字段
                # core_course_get_contents 提供的 module 对象包含很多元数据

                # 尝试从 availability 设置中推断 display 选项
                # 注意：core_course_get_contents 不会提供完整的 URL 配置，
                # 但我们可以提取更多可用的元数据
                display = self.DISPLAY_AUTO  # 默认值
                displayoptions = ''

                # 尝试从 module 的 availability 字段中推断显示选项
                availability = module.get('availability', None)
                if availability:
                    # availability 可能包含显示设置（虽然不太可能在 fallback 中）
                    # 这里我们保留解析能力，以防将来需要
                    pass

                # 将 Web API 的 url 模块转换为 Mobile API 的格式
                url = {
                    'id': module.get('instance', 0),
                    'coursemodule': module.get('id', 0),
                    'course': course_id,
                    'name': module.get('name', 'URL'),
                    'intro': module.get('description', ''),
                    'introformat': 1,  # 默认 HTML 格式
                    'externalurl': externalurl,
                    'display': display,
                    'displayoptions': displayoptions,
                    'parameters': '',  # Web API 不提供参数信息
                    'timemodified': module.get('timemodified', 0),
                    'timecreated': module.get('timecreated', 0),  # 如果可用

                    # 可见性信息（从 module 对象中提取）
                    'visible': module.get('visible', 1),
                    'uservisible': module.get('uservisible', 1),
                    'availability': availability,

                    # Section 信息（从 module 对象中提取）
                    'section_id': module.get('section', 0),
                    'section_number': module.get('sectionnumber', 0),
                    'section_name': module.get('sectionname', ''),

                    # Web API fallback 标记
                    '_fallback': True,  # 标记这是 fallback 数据
                    '_data_source': 'core_course_get_contents',
                }
                urls.append(url)

        if not urls:
            logging.warning('⚠️ Web API fallback 未找到任何 URL 模块')
            raise ValueError('Web API 未能检索任何 URL 模块信息')

        logging.debug(f'✅ Web API fallback 成功获取 {len(urls)} 个 URL 模块')
        return urls
