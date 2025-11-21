# -*- coding: utf-8 -*-
import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class FolderMod(MoodleMod):
    MOD_NAME = 'folder'
    MOD_PLURAL_NAME = 'folders'
    MOD_MIN_VERSION = 2017051500  # 3.3

    # Display mode constants for folder
    DISPLAY_MODES = {
        0: {'name': 'INLINE', 'description': 'Display folder contents inline on course page'},
        1: {'name': 'SEPARATE', 'description': 'Display folder on separate page'},
    }

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        # 下载条件: 检查是否启用了资源下载 (文件夹作为资源模块)
        return config.get_download_resources()

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        # 首先尝试使用 Mobile API
        try:
            response = await self.client.async_post(
                'mod_folder_get_folders_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
            folders = response.get('folders', [])
        except (RequestRejectedError, Exception) as e:
            # Mobile API 失败，尝试 Web API fallback
            logging.debug(f"Mobile API 获取 Folder 模块失败: {e}，尝试使用 Web API fallback...")
            folders = await self._fetch_folders_web_api(courses, core_contents)

        result = {}
        for folder in folders:
            course_id = folder.get('course', 0)
            module_id = folder.get('coursemodule', 0)
            folder_id = folder.get('id', 0)
            folder_name = folder.get('name', 'unnamed folder')
            folder_intro = folder.get('intro', '')
            folder_time_modified = folder.get('timemodified', 0)

            folder_files = self.get_introfiles(folder, 'folder_file')

            intro_file = self.create_intro_file(folder_intro, folder_time_modified)
            if intro_file:
                intro_file['filter_urls_during_search_containing'] = ['/mod_folder/intro']
                folder_files.append(intro_file)

            # Get folder contents from core_contents
            folder_contents = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])
            folder_files += folder_contents

            # Parse display mode
            display_mode = folder.get('display', 0)
            display_mode_info = self.DISPLAY_MODES.get(display_mode, {
                'name': f'UNKNOWN_{display_mode}',
                'description': f'Unknown display mode {display_mode}'
            })

            # Create comprehensive metadata
            metadata = {
                'folder_id': folder_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': folder_name,
                'intro': folder_intro,
                'settings': {
                    'revision': folder.get('revision', 1),
                    'display': display_mode,
                    'display_mode_name': display_mode_info['name'],
                    'display_mode_description': display_mode_info['description'],
                    'showexpanded': folder.get('showexpanded', 1),
                    'showdownloadfolder': folder.get('showdownloadfolder', 1),
                    'forcedownload': folder.get('forcedownload', 1),
                },
                'file_count': len(folder_contents),
                'download_options': {
                    'can_download_folder': folder.get('showdownloadfolder', 1) == 1,
                    'folder_zip_available': folder.get('showdownloadfolder', 1) == 1,
                    'force_download_files': folder.get('forcedownload', 1) == 1,
                },
                'timestamps': {
                    'timemodified': folder_time_modified,
                },
                'features': self.get_features(purpose='content'),
                'note': 'Folder is a simple container for organizing files. '
                + 'This export includes all files, folder settings, and display mode documentation.',
            }

            folder_files.append(self.create_metadata_file(metadata))

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': folder_id,
                    'name': folder_name,
                    'timemodified': folder_time_modified,
                    'files': folder_files,
                },
            )

        return result

    async def _fetch_folders_web_api(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> List[Dict]:
        """
        使用 Web API fallback 获取 Folder 模块信息。
        
        这是 mod_folder_get_folders_by_courses 的 fallback 实现。
        通过 core_course_get_contents 获取 folder 模块信息。
        
        Return: 转换为与 Mobile API 相同格式的 folder 列表
        """
        logging.debug('🌐 使用 Web API fallback 获取 Folder 模块信息...')
        
        folders = []
        
        # 从 core_contents 中提取 folder 模块
        modules_by_course = self.extract_modules_from_core_contents(courses, core_contents, 'folder')
        
        for course in courses:
            course_id = course.id
            if course_id not in modules_by_course:
                continue
            
            for module in modules_by_course[course_id]:
                # 将 Web API 的 folder 模块转换为 Mobile API 的格式
                folder = {
                    'id': module.get('instance', 0),
                    'coursemodule': module.get('id', 0),
                    'course': course_id,
                    'name': module.get('name', 'Folder'),
                    'intro': module.get('description', ''),
                    'introformat': 1,
                    'display': 0,
                    'showexpanded': 0,
                    'timemodified': module.get('timemodified', 0),
                }
                folders.append(folder)
        
        if not folders:
            logging.warning('⚠️ Web API fallback 未找到任何 Folder 模块')
            raise ValueError('Web API 未能检索任何 Folder 模块信息')
        
        logging.debug(f'✅ Web API fallback 成功获取 {len(folders)} 个 Folder 模块')
        return folders
