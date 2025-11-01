import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
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

        # Get all surveys via API
        response = await self.client.async_post(
            'mod_survey_get_surveys_by_courses',
            self.get_data_for_mod_entries_endpoint(courses),
        )

        surveys = response.get('surveys', [])

        for survey in surveys:
            course_id = survey.get('course', 0)
            module_id = survey.get('coursemodule', 0)
            survey_id = survey.get('id', 0)
            survey_name = survey.get('name', 'Survey')

            survey_files = []

            # Get intro/description if available
            intro = survey.get('intro', '')
            if intro:
                survey_files.append(
                    {
                        'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                        'filepath': '/',
                        'description': intro,
                        'type': 'description',
                        'timemodified': 0,
                    }
                )

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
                'timestamps': {
                    'timemodified': survey.get('timemodified', 0),
                },
                'questions': questions_data,
                'question_count': len(questions_data),
                'features': {
                    'groups': True,
                    'groupings': True,
                    'intro_support': True,
                    'completion_tracks_views': True,
                    'completion_has_rules': True,
                    'show_description': True,
                    'purpose': 'communication',
                },
                'note': 'Survey is a predefined questionnaire module. '
                + 'This export includes survey settings and standardized questions.',
            }

            survey_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': 0,
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

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
