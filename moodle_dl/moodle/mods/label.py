# -*- coding: utf-8 -*-
import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class LabelMod(MoodleMod):
    """
    Label module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/label/services/label.ts

    Supports:
    - Label HTML content export
    - Embedded media and attachments
    - Text and image display
    """

    MOD_NAME = 'label'
    MOD_PLURAL_NAME = 'labels'
    MOD_MIN_VERSION = 2015111600  # 3.0

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_labels() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Label modules from courses

        Process:
        1. Get labels by courses
        2. Export label HTML content
        3. Download embedded media and attachments
        """

        result = {}
        if not self.config.get_download_labels():
            return result

        # 首先尝试使用 Mobile API
        try:
            response = await self.client.async_post(
                'mod_label_get_labels_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            labels = response.get('labels', [])
        except (RequestRejectedError, Exception) as e:
            # Mobile API 失败，尝试 Web API fallback
            logging.debug(f"Mobile API 获取 Label 模块失败: {e}，尝试使用 Web API fallback...")
            labels = await self._fetch_labels_web_api(courses, core_contents)

        for label in labels:
            course_id = label.get('course', 0)
            module_id = label.get('coursemodule', 0)
            label_name = label.get('name', 'Label')

            # Get intro files (embedded media and attachments)
            # Copy the list to avoid modifying the original label dict
            label_files = self.get_introfiles(label, 'label_file', copy=True)

            # Get the HTML content
            label_intro = label.get('intro', '')

            # Create HTML/Markdown content file
            if label_intro:
                # Clean up the label name for filename
                safe_name = PT.to_valid_name(label_name if label_name != 'Label' else 'Content', is_file=True)

                label_files.append(
                    {
                        'filename': safe_name,
                        'filepath': '/',
                        'description': label_intro,
                        'type': 'description',
                        'timemodified': label.get('timemodified', 0),
                    }
                )

            # Create metadata file
            metadata = {
                'label_id': label.get('id', 0),
                'course_id': course_id,
                'name': label_name,
                'timestamps': {
                    'time_modified': label.get('timemodified', 0),
                },
                'content_length': len(label_intro),
                'has_files': len(label.get('introfiles', [])) > 0,
            }

            label_files.append(
                self.create_metadata_file(metadata, timemodified=label.get('timemodified', 0))
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': label.get('id', 0),
                    'name': label_name,
                    'files': label_files,
                },
            )

        return result

    async def _fetch_labels_web_api(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> List[Dict]:
        """
        使用 Web API fallback 获取 Label 模块信息。
        
        这是 mod_label_get_labels_by_courses 的 fallback 实现。
        通过 core_course_get_contents 获取 label 模块信息。
        
        Return: 转换为与 Mobile API 相同格式的 label 列表
        """
        logging.debug('🌐 使用 Web API fallback 获取 Label 模块信息...')
        
        labels = []
        
        # 从 core_contents 中提取 label 模块
        modules_by_course = self.extract_modules_from_core_contents(courses, core_contents, 'label')
        
        for course in courses:
            course_id = course.id
            if course_id not in modules_by_course:
                continue
            
            for module in modules_by_course[course_id]:
                # 将 Web API 的 label 模块转换为 Mobile API 的格式
                label = {
                    'id': module.get('instance', 0),
                    'coursemodule': module.get('id', 0),
                    'course': course_id,
                    'name': module.get('name', 'Label'),
                    'intro': module.get('description', ''),
                    'introformat': 1,
                    'timemodified': module.get('timemodified', 0),
                }
                labels.append(label)
        
        if not labels:
            logging.warning('⚠️ Web API fallback 未找到任何 Label 模块')
            raise ValueError('Web API 未能检索任何 Label 模块信息')
        
        logging.debug(f'✅ Web API fallback 成功获取 {len(labels)} 个 Label 模块')
        return labels
