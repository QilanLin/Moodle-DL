import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class FeedbackMod(MoodleMod):
    """
    Feedback module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/feedback/

    Feedback is a survey/questionnaire module for collecting user feedback
    with customizable questions and response analysis.

    Supports:
    - Feedback metadata via mod_feedback_get_feedbacks_by_courses API
    - Question items via mod_feedback_get_items API
    - Response analysis via mod_feedback_get_analysis API
    - Feedback settings and completion tracking
    """

    MOD_NAME = 'feedback'
    MOD_PLURAL_NAME = 'feedbacks'

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_feedbacks() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Feedback modules from courses

        Process:
        1. Get all feedbacks via mod_feedback_get_feedbacks_by_courses API
        2. For each feedback, get question items via mod_feedback_get_items
        3. Try to get analysis data if available
        4. Export comprehensive metadata including questions and responses
        """

        result = {}

        if not self.config.get_download_feedbacks():
            return result

        # Get all feedbacks via API
        response = await self.client.async_post(
            'mod_feedback_get_feedbacks_by_courses',
            self.get_data_for_mod_entries_endpoint(courses),
        )

        feedbacks = response.get('feedbacks', [])

        for feedback in feedbacks:
            course_id = feedback.get('course', 0)
            module_id = feedback.get('coursemodule', 0)
            feedback_id = feedback.get('id', 0)
            feedback_name = feedback.get('name', 'Feedback')

            feedback_files = []

            # Get intro/description if available
            intro = feedback.get('intro', '')
            if intro:
                feedback_files.append(
                    {
                        'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                        'filepath': '/',
                        'description': intro,
                        'type': 'description',
                        'timemodified': 0,
                    }
                )

            # Get feedback items (questions)
            items_data = await self._get_feedback_items(feedback_id)

            # Try to get analysis data
            analysis_data = await self._get_feedback_analysis(feedback_id)

            # Create comprehensive metadata
            metadata = {
                'feedback_id': feedback_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': feedback_name,
                'intro': intro,
                'settings': {
                    'anonymous': feedback.get('anonymous', 0),
                    'email_notification': feedback.get('email_notification', 0),
                    'multiple_submit': feedback.get('multiple_submit', 0),
                    'autonumbering': feedback.get('autonumbering', 0),
                    'site_after_submit': feedback.get('site_after_submit', ''),
                    'page_after_submit': feedback.get('page_after_submit', ''),
                    'page_after_submitformat': feedback.get('page_after_submitformat', 1),
                    'publish_stats': feedback.get('publish_stats', 0),
                    'completionsubmit': feedback.get('completionsubmit', 0),
                },
                'timestamps': {
                    'timeopen': feedback.get('timeopen', 0),
                    'timeclose': feedback.get('timeclose', 0),
                    'timemodified': feedback.get('timemodified', 0),
                },
                'items': items_data,
                'analysis': analysis_data,
                'features': {
                    'groups': True,
                    'groupings': True,
                    'intro_support': True,
                    'completion_tracks_views': True,
                    'completion_has_rules': True,
                    'show_description': True,
                    'purpose': 'communication',
                },
                'note': 'Feedback is a customizable survey/questionnaire module. '
                + 'This export includes feedback settings, questions, and response analysis.',
            }

            feedback_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': 0,
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            # Export questions as separate file
            if items_data:
                feedback_files.append(
                    {
                        'filename': 'questions.json',
                        'filepath': '/',
                        'timemodified': 0,
                        'content': json.dumps(items_data, indent=2, ensure_ascii=False),
                        'type': 'content',
                    }
                )

                # Process item files (attached files to questions)
                for item in items_data:
                    item_files = item.get('itemfiles', [])
                    if item_files:
                        item_id = item.get('id', 0)
                        item_name = item.get('name', 'item')

                        # Set file properties and organize by item
                        for item_file in item_files:
                            item_file['filepath'] = f'/items/item_{item_id}_{PT.to_valid_name(item_name, is_file=False)}/'
                            self.set_props_of_file(item_file, type='feedback_item_file')
                            feedback_files.append(item_file)

            # Export analysis as separate file if available
            if analysis_data and analysis_data.get('itemsdata'):
                feedback_files.append(
                    {
                        'filename': 'analysis.json',
                        'filepath': '/',
                        'timemodified': 0,
                        'content': json.dumps(analysis_data, indent=2, ensure_ascii=False),
                        'type': 'content',
                    }
                )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': module_id,
                    'name': feedback_name,
                    'files': feedback_files,
                },
            )

            logging.debug(
                f"Found feedback module: {feedback_name} (ID: {module_id}, "
                f"Questions: {len(items_data)}) in course {course_id}"
            )

        return result

    async def _get_feedback_items(self, feedback_id: int) -> List[Dict]:
        """
        Get all question items for a feedback

        Returns list of question data
        """
        try:
            response = await self.client.async_post(
                'mod_feedback_get_items',
                {'feedbackid': feedback_id},
            )

            items = response.get('items', [])

            return [
                {
                    'id': item.get('id', 0),
                    'typ': item.get('typ', ''),
                    'name': item.get('name', ''),
                    'label': item.get('label', ''),
                    'presentation': item.get('presentation', ''),
                    'hasvalue': item.get('hasvalue', 0),
                    'position': item.get('position', 0),
                    'required': item.get('required', 0),
                    'dependitem': item.get('dependitem', 0),
                    'dependvalue': item.get('dependvalue', ''),
                    'options': item.get('options', ''),
                    'itemfiles': item.get('itemfiles', []),
                }
                for item in items
            ]

        except Exception as e:
            logging.debug(f"Could not fetch items for feedback {feedback_id}: {e}")
            return []

    async def _get_feedback_analysis(self, feedback_id: int) -> Dict:
        """
        Get analysis data for a feedback

        Returns analysis data including response statistics
        """
        try:
            response = await self.client.async_post(
                'mod_feedback_get_analysis',
                {
                    'feedbackid': feedback_id,
                    'groupid': 0,  # All groups
                },
            )

            return {
                'completedcount': response.get('completedcount', 0),
                'itemscount': response.get('itemscount', 0),
                'itemsdata': response.get('itemsdata', []),
                'warnings': response.get('warnings', []),
            }

        except Exception as e:
            logging.debug(f"Could not fetch analysis for feedback {feedback_id}: {e}")
            return {}
