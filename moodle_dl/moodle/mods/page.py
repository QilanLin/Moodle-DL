# -*- coding: utf-8 -*-
import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.mods.page_content_filter import PageContentFilter
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class PageMod(MoodleMod):
    MOD_NAME = 'page'
    MOD_PLURAL_NAME = 'pages'
    MOD_MIN_VERSION = 2017051500  # 3.3

    # Display mode constants (RESOURCELIB_DISPLAY_*)
    DISPLAY_AUTO = 0
    DISPLAY_EMBED = 1
    DISPLAY_FRAME = 2
    DISPLAY_NEW = 3
    DISPLAY_DOWNLOAD = 4
    DISPLAY_OPEN = 5
    DISPLAY_POPUP = 6

    DISPLAY_MODES = {
        DISPLAY_AUTO: {'name': 'AUTOMATIC', 'description': 'Automatic - best option for file type'},
        DISPLAY_EMBED: {'name': 'EMBED', 'description': 'Embed - display in page'},
        DISPLAY_FRAME: {'name': 'FRAME', 'description': 'Open in frame'},
        DISPLAY_NEW: {'name': 'NEW', 'description': 'Open in new window'},
        DISPLAY_DOWNLOAD: {'name': 'DOWNLOAD', 'description': 'Force download'},
        DISPLAY_OPEN: {'name': 'OPEN', 'description': 'Open directly (default for page)'},
        DISPLAY_POPUP: {'name': 'POPUP', 'description': 'Open in popup window'},
    }

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        # 下载条件: 检查是否启用了资源下载 (页面作为资源模块)
        return config.get_download_resources()

    @staticmethod
    def _parse_display_options(displayoptions_str: str) -> Dict:
        """
        Parse display options string into structured dictionary

        Format: "printintro=1,printlastmodified=1,popupwidth=620,popupheight=450"
        """
        if not displayoptions_str:
            return {}

        options = {}
        for pair in displayoptions_str.split(','):
            if '=' in pair:
                key, value = pair.split('=', 1)
                key = key.strip()
                value = value.strip()

                # Try to convert to int if possible
                try:
                    options[key] = int(value)
                except ValueError:
                    options[key] = value

        return options

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        # 首先尝试使用 Mobile API
        try:
            response = await self.client.async_post(
                'mod_page_get_pages_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
            pages = response.get('pages', [])
        except (RequestRejectedError, Exception) as e:
            # Mobile API 失败，尝试 Web API fallback
            logging.debug(f"Mobile API 获取 Page 模块失败: {e}，尝试使用 Web API fallback...")
            pages = await self._fetch_pages_web_api(courses, core_contents)

        result = {}
        for page in pages:
            course_id = page.get('course', 0)
            module_id = page.get('coursemodule', 0)
            page_id = page.get('id', 0)
            page_name = page.get('name', 'unnamed page')
            page_content = page.get('content', '')
            page_intro = page.get('intro', '')

            page_files = self.get_introfiles(page, 'page_file', additional_keys=['contentfiles'])
            page_files = PageContentFilter.filter_adjacent_kaltura_helper_icons(
                page_files, page_content, page_intro
            )

            intro_file = self.create_intro_file(page_intro)
            if intro_file:
                page_files.append(intro_file)

            if page_content != '':
                page_files.append(
                    {
                        'filename': page_name,
                        'filepath': '/',
                        'html': page_content,
                        'filter_urls_during_search_containing': ['/mod_page/content/'],
                        'no_hash': True,
                        'type': 'html',
                        'timemodified': page.get('timemodified', 0),
                        'filesize': len(page_content),
                    }
                )

            # Parse display mode and options
            display_mode = page.get('display', 5)
            display_mode_info = self.DISPLAY_MODES.get(display_mode, {
                'name': f'UNKNOWN_{display_mode}',
                'description': f'Unknown display mode {display_mode}'
            })

            displayoptions_str = page.get('displayoptions', '')
            displayoptions_parsed = self._parse_display_options(displayoptions_str)

            # Create comprehensive metadata
            metadata = {
                'page_id': page_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': page_name,
                'intro': page_intro,
                'content': page_content,
                'settings': {
                    'contentformat': page.get('contentformat', 1),
                    'legacyfiles': page.get('legacyfiles', 0),
                    'legacyfileslast': page.get('legacyfileslast'),
                    'display': display_mode,
                    'display_mode_name': display_mode_info['name'],
                    'display_mode_description': display_mode_info['description'],
                    'displayoptions_raw': displayoptions_str,
                    'displayoptions_parsed': displayoptions_parsed,
                    'revision': page.get('revision', 1),
                    'printheading': page.get('printheading', 1),
                    'printlastmodified': page.get('printlastmodified', 1),
                },
                'timestamps': {
                    'timemodified': page.get('timemodified', 0),
                },
                'features': self.get_features(purpose='content'),
                'note': 'Page is a simple content module for displaying HTML content. '
                + 'This export includes the full HTML content, settings, and parsed display options.',
            }

            page_files.append(self.create_metadata_file(metadata))

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': page_id,
                    'name': page_name,
                    'files': page_files,
                },
            )

        return result

    async def _fetch_pages_web_api(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> List[Dict]:
        """
        使用 Web API fallback 获取 Page 模块信息。
        
        这是 mod_page_get_pages_by_courses 的 fallback 实现。
        通过 core_course_get_contents 获取 page 模块信息。
        
        Return: 转换为与 Mobile API 相同格式的 page 列表
        """
        logging.debug('🌐 使用 Web API fallback 获取 Page 模块信息...')
        
        pages = []
        
        # 从 core_contents 中提取 page 模块
        modules_by_course = self.extract_modules_from_core_contents(courses, core_contents, 'page')
        
        for course in courses:
            course_id = course.id
            if course_id not in modules_by_course:
                continue
            
            for module in modules_by_course[course_id]:
                # 将 Web API 的 page 模块转换为 Mobile API 的格式
                page = {
                    'id': module.get('instance', 0),
                    'coursemodule': module.get('id', 0),
                    'course': course_id,
                    'name': module.get('name', 'Page'),
                    'intro': module.get('description', ''),
                    'introformat': 1,
                    'content': '',
                    'contentformat': 1,
                    'display': self.DISPLAY_AUTO,
                    'displayoptions': '',
                    'revision': 0,
                    'timemodified': module.get('timemodified', 0),
                }
                pages.append(page)
        
        if not pages:
            logging.warning('⚠️ Web API fallback 未找到任何 Page 模块')
            raise ValueError('Web API 未能检索任何 Page 模块信息')
        
        logging.debug(f'✅ Web API fallback 成功获取 {len(pages)} 个 Page 模块')
        return pages
