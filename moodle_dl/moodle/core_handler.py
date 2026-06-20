# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import math
import os
from typing import Dict, List, Optional, Tuple

from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.types import Course
from moodle_dl.utils import run_with_final_message


class CoreHandler:
    """
    Fetches and parses the various endpoints in Moodle.
    """

    def __init__(self, request_helper: RequestHelper):
        self.client = request_helper
        # oldest supported Moodle version
        self.version = 2011120500

    def _build_api_options(self, options: List[Dict[str, str]]) -> Dict[str, str]:
        """
        构建 Moodle Web Service API 的选项参数。

        Moodle REST API 期望选项参数格式为:
        options[0][name]=excludemodules&options[0][value]=true&options[1][name]=excludecontents&options[1][value]=true

        这个辅助方法将选项列表转换为所需的字典格式。

        Args:
            options: 选项字典列表，例如 [{'name': 'excludemodules', 'value': 'true'}]

        Returns:
            Dict[str, str]: 序列化后的选项字典
        """
        result = {}
        for idx, option in enumerate(options):
            result[f'options[{idx}][name]'] = option['name']
            result[f'options[{idx}][value]'] = option['value']
        return result

    def fetch_userid_and_version(self) -> Tuple[int, int]:
        """
        Ask the Moodle system for the user id.
        @return: Tuple of (userid as int, version as int)
        """
        result = self.client.post('core_webservice_get_site_info')

        if 'userid' not in result:
            raise RuntimeError('Error could not receive your user ID!')
        userid_raw = result.get('userid')

        # Validate userid is not None or empty
        if userid_raw is None or userid_raw == '':
            raise RuntimeError(f'Invalid userid received from API: {userid_raw}')

        version = result.get('version', '2011120500')

        try:
            # Convert userid to int
            userid = int(userid_raw)
            version = int(version.split('.')[0])
        except (ValueError, TypeError) as e:
            raise RuntimeError(f'Error could not parse userid "{userid_raw}" or version string "{version}": {e}')

        self.version = version
        return userid, version

    def fetch_courses(self, userid: int) -> List[Course]:
        """
        Queries the Moodle system for all courses the user is enrolled in.
        
        🔧 API SELECTION STRATEGY:
        
        This uses the Mobile API (core_enrol_get_users_courses) which requires enrollment.
        
        Key Design Decision:
        - Uses Mobile API by default (faster for enrolled users)
        - Falls back to Web API (core_course_get_courses) if enrollment check fails
        
        Permission Layers in Moodle:
        1. ENROLLMENT LAYER: "Am I enrolled in this course?"
           └─ Mobile API checks this (core_enrol_get_users_courses)
           └─ Returns only enrolled courses
        
        2. CAPABILITY LAYER: "Do I have permission to view this course?"
           └─ Web API checks this (core_course_get_courses)
           └─ Returns courses where user has moodle/course:view capability
           └─ Includes courses where user is NOT enrolled but has viewing rights (e.g., as teacher/TA)
        
        For teachers/TAs/admins: Use Web API as fallback since they may not be enrolled
        but still need to view courses they teach.
        
        @param userid: the user id
        @return: A list of courses
        """
        data = {'userid': userid}

        courses = self.client.post('core_enrol_get_users_courses', data)

        results = []
        for course in courses:
            results.append(Course(course.get('id', 0), course.get('fullname', '')))
            # We could also extract here the course summary and intro files
        return results

    def fetch_all_visible_courses(self, log_all_courses_to: Optional[str] = None) -> List[Course]:
        """
        Queries the Moodle system for all courses available on the system and returns:
        @return: A list of all visible courses
        """
        if self.version < 2016120500:  # 3.2
            return []

        result = self.client.post('core_course_get_courses_by_field', timeout=1200)
        if log_all_courses_to is not None:
            with open(log_all_courses_to, 'w', encoding='utf-8') as log_file:
                log_file.write(json.dumps(result, indent=4, ensure_ascii=False))
        courses = result.get('courses', [])

        results = []
        for course in courses:
            if course.get('visible', 0) == 1:
                results.append(Course(course.get('id', 0), course.get('fullname', '')))
        return results

    def fetch_courses_info(self, course_ids: List[int]) -> List[Course]:
        """
        Queries the Moodle system for info about courses in a list.
        @param course_ids: A list of courses ids
        @return: A list of courses
        """
        if len(course_ids) == 0 or self.version < 2016120500:  # 3.2
            return []

        data = {
            "field": "ids",
            "value": ",".join(list(map(str, course_ids))),
        }

        result = self.client.post('core_course_get_courses_by_field', data)
        courses = result.get('courses', [])

        results = []
        for course in courses:
            results.append(Course(course.get('id', 0), course.get('fullname', '')))
            # We could also extract here the course summary and intro files
        return results

    def fetch_sections(self, course_id: int) -> List[Dict]:
        """
        Fetches the Sections List for a course from the Moodle system
        @param course_id: The id of the requested course.
        @return: A List of all section dictionaries
        """
        data = {'courseid': course_id}
        if self.version >= 2015051100:  # Moodle 2.9+
            # 使用辅助方法构建选项参数，提高可维护性
            options = [
                {'name': 'excludemodules', 'value': 'true'},
                {'name': 'excludecontents', 'value': 'true'},
            ]
            data.update(self._build_api_options(options))

        course_sections = self.client.post('core_course_get_contents', data)

        # 🔍 检查并记录 API 警告信息
        if 'warnings' in course_sections and course_sections['warnings']:
            logging.warning(f'API warnings in fetch_sections for course {course_id}:')
            for warning in course_sections['warnings']:
                logging.warning(f'  - {warning.get("message", "Unknown warning")} (item: {warning.get("item", "N/A")})')

        sections = []
        for section in course_sections:
            sections.append({"id": section.get("id"), "name": section.get("name")})

        return sections

    async def async_load_core_contents(self, courses: List[Course]) -> Dict[int, List[Dict]]:
        """
        为给定的课程列表加载 section 数据
        
        Args:
            courses: 需要加载核心内容的课程列表
        
        Returns:
            Dict[int, List[Dict]]: 课程 ID 到 section 列表的映射
        """
        total_courses = len(courses)

        if total_courses == 0:
            return {}
        ctr_digits = int(math.log10(total_courses)) + 1

        async_features = []
        for ctr, course in enumerate(courses):
            # Example: [ 5/16] 已加载课程核心 123 "Best course"
            loaded_message = (
                f'[%(ctr){ctr_digits}d/%(total){ctr_digits}d] 已加载课程核心 %(course_id)d "%(course_name)s"'
            )

            async_features.append(
                run_with_final_message(
                    self.async_load_course_core,
                    course,
                    loaded_message,
                    {
                        'ctr': ctr + 1,
                        'total': total_courses,
                        'course_id': course.id,
                        'course_name': course.fullname,
                    },
                )
            )

        # 🔧 Hang fix: bound the gather with wait_for so a single
        # stuck course load doesn't hang the whole download.
        # The default is 60s per course; the operator can override
        # with the ``MOODLE_DL_LOAD_TIMEOUT`` env var.
        load_timeout = float(
            os.environ.get('MOODLE_DL_LOAD_TIMEOUT', '60')
        )
        cores = await asyncio.wait_for(
            asyncio.gather(*async_features, return_exceptions=False),
            timeout=load_timeout,
        )

        result = {}
        for idx, course in enumerate(courses):
            result[course.id] = cores[idx]
        return result

    async def async_load_course_core(self, course: Course) -> List[Dict]:
        """
        异步加载课程的核心内容（sections 和 modules）

        Args:
            course: 要加载的课程对象

        Returns:
            List[Dict]: 课程的核心内容列表
        """
        data = {'courseid': course.id}
        # Keep section modules in the response. ResultBuilder uses them to map
        # fetched module data back into the real course sections.
        # Requesting excludemodules=true here collapses files into
        # "<modplural> not on main page" synthetic sections.

        result = await self.client.async_post('core_course_get_contents', data)

        # 🔍 检查并记录 API 警告信息
        if 'warnings' in result and result['warnings']:
            logging.warning(f'API warnings in async_load_course_core for course {course.id} ({course.fullname}):')
            for warning in result['warnings']:
                logging.warning(f'  - {warning.get("message", "Unknown warning")} (item: {warning.get("item", "N/A")})')

        return result

    def fetch_course_blocks(self, course_id: int) -> List[Dict]:
        """
        Fetches the course blocks (sidebar widgets) for a course from the Moodle system.
        These blocks can contain important information like Key Contacts, announcements, etc.

        @param course_id: The id of the requested course.
        @return: A list of all block dictionaries
        """
        if self.version < 2017051500:  # 3.3 - API introduced in Moodle 3.3
            return []

        data = {'courseid': course_id, 'returncontents': 1}

        try:
            result = self.client.post('core_block_get_course_blocks', data)
            return result.get('blocks', [])
        except Exception:
            # If the API call fails (e.g., not supported), return empty list
            return []
