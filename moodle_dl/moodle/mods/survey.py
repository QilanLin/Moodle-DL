# -*- coding: utf-8 -*-
import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class SurveyMod(MoodleMod):
    """
    Survey module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/survey/

    Survey is a predefined questionnaire module for gathering structured
    feedback from users using standardized question sets.

    Supports:
    - Survey metadata via mod_survey_get_surveys_by_courses API
    - Questions via mod_survey_get_questions API
    - Survey settings and completion tracking
    """

    MOD_NAME = 'survey'
    MOD_PLURAL_NAME = 'surveys'
    MOD_MIN_VERSION = 2015111600  # 3.0 - mod_survey_get_surveys_by_courses introduced

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_surveys() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Survey modules from courses

        Process:
        1. Get all surveys via mod_survey_get_surveys_by_courses API
        2. For each survey, get questions via mod_survey_get_questions
        3. Export comprehensive metadata including questions
        """

        result = {}

        if not self.config.get_download_surveys():
            return result

        # 首先尝试使用 Mobile API
        try:
            response = await self.client.async_post(
                'mod_survey_get_surveys_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            surveys = response.get('surveys', [])
        except (RequestRejectedError, Exception) as e:
            # Mobile API 失败，尝试 Web API fallback
            logging.debug(f"Mobile API 获取 Survey 模块失败: {e}，尝试使用 Web API fallback...")
            surveys = await self._fetch_surveys_web_api(courses, core_contents)

        for survey in surveys:
            course_id = survey.get('course', 0)
            module_id = survey.get('coursemodule', 0)
            survey_id = survey.get('id', 0)
            survey_name = survey.get('name', 'Survey')

            survey_files = []

            # Get intro/description if available
            intro = survey.get('intro', '')
            intro_file = self.create_intro_file(intro)
            if intro_file:
                survey_files.append(intro_file)

            # Get survey questions
            questions_data = await self._get_survey_questions(survey_id)

            # Create comprehensive metadata
            metadata = {
                'survey_id': survey_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': survey_name,
                'intro': intro,
                'settings': {
                    'template': survey.get('template', 0),
                    'days': survey.get('days', 0),
                    'questions': survey.get('questions', ''),
                },
                'completion': {
                    'surveydone': survey.get('surveydone', 0),
                },
                'timestamps': {
                    'timecreated': survey.get('timecreated', 0),
                    'timemodified': survey.get('timemodified', 0),
                },
                'questions': questions_data,
                'question_count': len(questions_data),
                'features': self.get_features(
                    purpose='communication',
                    completion_has_rules=True
                ),
                'note': 'Survey is a predefined questionnaire module. '
                + 'This export includes survey settings and standardized questions.',
            }

            survey_files.append(self.create_metadata_file(metadata))

            # Export questions as separate file
            if questions_data:
                survey_files.append(
                    {
                        'filename': 'questions.json',
                        'filepath': '/',
                        'timemodified': 0,
                        'content': json.dumps(questions_data, indent=2, ensure_ascii=False),
                        'type': 'content',
                    }
                )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': module_id,
                    'name': survey_name,
                    'files': survey_files,
                },
            )

            logging.debug(
                f"Found survey module: {survey_name} (ID: {module_id}, "
                f"Questions: {len(questions_data)}) in course {course_id}"
            )

        return result

    async def _get_survey_questions(self, survey_id: int) -> List[Dict]:
        """
        Get all questions for a survey

        Returns list of question data
        """
        try:
            response = await self.client.async_post(
                'mod_survey_get_questions',
                {'surveyid': survey_id},
            )

            questions = response.get('questions', [])

            return [
                {
                    'id': question.get('id', 0),
                    'name': question.get('name', ''),
                    'text': question.get('text', ''),
                    'shorttext': question.get('shorttext', ''),
                    'multi': question.get('multi', ''),
                    'intro': question.get('intro', ''),
                    'type': question.get('type', 0),
                    'options': question.get('options', ''),
                    'parent': question.get('parent', 0),
                }
                for question in questions
            ]

        except Exception as e:
            logging.debug(f"Could not fetch questions for survey {survey_id}: {e}")
            return []

    async def _fetch_surveys_web_api(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> List[Dict]:
        """
        使用 Web API fallback 获取 Survey 模块信息。
        
        这是 mod_survey_get_surveys_by_courses 的 fallback 实现。
        通过 core_course_get_contents 获取 survey 模块信息。
        
        Return: 转换为与 Mobile API 相同格式的 survey 列表
        """
        logging.debug('🌐 使用 Web API fallback 获取 Survey 模块信息...')
        
        surveys = []
        
        # 从 core_contents 中提取 survey 模块
        modules_by_course = self.extract_modules_from_core_contents(courses, core_contents, 'survey')
        
        for course in courses:
            course_id = course.id
            if course_id not in modules_by_course:
                continue
            
            for module in modules_by_course[course_id]:
                # 将 Web API 的 survey 模块转换为 Mobile API 的格式
                survey = {
                    'id': module.get('instance', 0),
                    'coursemodule': module.get('id', 0),
                    'course': course_id,
                    'name': module.get('name', 'Survey'),
                    'intro': module.get('description', ''),
                    'introformat': 1,
                    'template': 0,
                    'days': 0,
                    'questions': '',
                    'surveydone': 0,
                    'timecreated': module.get('timecreated', 0),
                    'timemodified': module.get('timemodified', 0),
                }
                surveys.append(survey)
        
        if not surveys:
            logging.warning('⚠️ Web API fallback 未找到任何 Survey 模块')
            raise ValueError('Web API 未能检索任何 Survey 模块信息')
        
        logging.debug(f'✅ Web API fallback 成功获取 {len(surveys)} 个 Survey 模块')
        return surveys
