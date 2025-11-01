import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.moodle_constants import moodle_html_footer, moodle_html_header
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class QuizMod(MoodleMod):
    MOD_NAME = 'quiz'
    MOD_PLURAL_NAME = 'quizzes'
    MOD_MIN_VERSION = 2016052300  # 3.1

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_quizzes() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        quizzes = (
            await self.client.async_post(
                'mod_quiz_get_quizzes_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('quizzes', [])

        result = {}
        for quiz in quizzes:
            course_id = quiz.get('course', 0)
            module_id = quiz.get('coursemodule', 0)
            quiz_id = quiz.get('id', 0)
            quiz_name = quiz.get('name', 'unnamed quiz')
            quiz_intro = quiz.get('intro', '')

            quiz_files = quiz.get('introfiles', [])
            self.set_props_of_files(quiz_files, type='quiz_introfile')

            if quiz_intro != '':
                quiz_files.append(
                    {
                        'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                        'filepath': '/',
                        'description': quiz_intro,
                        'type': 'description',
                        'timemodified': 0,
                    }
                )

            # Get additional quiz information if download is enabled
            access_info = {}
            best_grade = {}
            feedback = ''
            if self.config.get_download_quizzes():
                access_info = await self._get_quiz_access_info(quiz_id)
                best_grade = await self._get_user_best_grade(quiz_id)
                if best_grade.get('hasgrade', False):
                    feedback = await self._get_quiz_feedback(quiz_id, best_grade.get('grade', 0))

            # Create comprehensive metadata
            metadata = {
                'quiz_id': quiz_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': quiz_name,
                'intro': quiz_intro,
                'settings': {
                    'timeopen': quiz.get('timeopen', 0),
                    'timeclose': quiz.get('timeclose', 0),
                    'timelimit': quiz.get('timelimit', 0),
                    'overduehandling': quiz.get('overduehandling', 'autosubmit'),
                    'graceperiod': quiz.get('graceperiod', 0),
                    'preferredbehaviour': quiz.get('preferredbehaviour', 'deferredfeedback'),
                    'canredoquestions': quiz.get('canredoquestions', 0),
                    'attempts': quiz.get('attempts', 0),
                    'attemptonlast': quiz.get('attemptonlast', 0),
                    'grademethod': quiz.get('grademethod', 1),
                    'decimalpoints': quiz.get('decimalpoints', 2),
                    'questiondecimalpoints': quiz.get('questiondecimalpoints', -1),
                    'reviewattempt': quiz.get('reviewattempt', 0),
                    'reviewcorrectness': quiz.get('reviewcorrectness', 0),
                    'reviewmarks': quiz.get('reviewmarks', 0),
                    'reviewspecificfeedback': quiz.get('reviewspecificfeedback', 0),
                    'reviewgeneralfeedback': quiz.get('reviewgeneralfeedback', 0),
                    'reviewrightanswer': quiz.get('reviewrightanswer', 0),
                    'reviewoverallfeedback': quiz.get('reviewoverallfeedback', 0),
                    'questionsperpage': quiz.get('questionsperpage', 1),
                    'navmethod': quiz.get('navmethod', 'free'),
                    'shuffleanswers': quiz.get('shuffleanswers', 1),
                    'sumgrades': quiz.get('sumgrades', 0),
                    'grade': quiz.get('grade', 0),
                    'browsersecurity': quiz.get('browsersecurity', '-'),
                    'allowofflineattempts': quiz.get('allowofflineattempts', 0),
                    'autosaveperiod': quiz.get('autosaveperiod', 0),
                    'hasfeedback': quiz.get('hasfeedback', 0),
                    'hasquestions': quiz.get('hasquestions', 0),
                },
                'access_information': access_info,
                'user_grade': best_grade,
                'feedback': feedback,
                'timestamps': {
                    'timeopen': quiz.get('timeopen', 0),
                    'timeclose': quiz.get('timeclose', 0),
                    'timemodified': quiz.get('timemodified', 0),
                },
                'features': {
                    'groups': True,
                    'groupings': True,
                    'intro_support': True,
                    'completion_tracks_views': False,
                    'grade_has_grade': True,
                    'grade_outcomes': True,
                    'backup_moodle2': True,
                    'show_description': True,
                    'purpose': 'assessment',
                },
                'note': 'Quiz is an assessment module with questions and grading. '
                + 'This export includes quiz settings, access rules, and user performance data.',
            }

            quiz_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': 0,
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': quiz_id,
                    'name': quiz_name,
                    'files': quiz_files,
                },
            )

        await self.add_quizzes_files(result)
        return result

    async def add_quizzes_files(self, quizzes: Dict[int, Dict[int, Dict]]):
        """
        Fetches for the quizzes list the quizzes files
        @param quizzes: Dictionary of all quizzes, indexed by courses, then module id
        """
        if not self.config.get_download_quizzes():
            return

        if self.version < 2016052300:  # 3.1
            return

        await self.run_async_load_function_on_mod_entries(quizzes, self.load_quiz_files)

    async def load_quiz_files(self, quiz: Dict):
        data = {'quizid': quiz.get('id', 0), 'userid': self.user_id, 'status': 'all'}
        attempts = (await self.client.async_post('mod_quiz_get_user_attempts', data)).get('attempts', [])
        quiz_name = quiz.get('name', '')
        for attempt in attempts:
            attempt['_quiz_name'] = quiz_name

        quiz['files'] += await self.run_async_collect_function_on_list(
            attempts,
            self.load_files_of_attempt,
            'attempt',
            {'collect_id': 'id', 'collect_name': '_quiz_name'},
        )

    async def load_files_of_attempt(self, attempt: Dict) -> List[Dict]:
        result = []

        attempt_id = attempt.get('id', 0)
        attempt_state = attempt.get('state', 'unknown')
        quiz_name = attempt.get('_quiz_name', '')

        attempt_filename = PT.to_valid_name(
            quiz_name + ' (attempt ' + str(attempt_id) + ' ' + attempt_state + ')', is_file=False
        )

        data = {'attemptid': attempt_id}
        try:
            if attempt_state == 'finished':
                questions = (await self.client.async_post('mod_quiz_get_attempt_review', data)).get('questions', [])
            elif attempt_state == 'inprogress':
                questions = (await self.client.async_post('mod_quiz_get_attempt_summary', data)).get('questions', [])
            else:
                return result
        except RequestRejectedError:
            logging.debug("No access rights for quiz attempt %d", attempt_id)
            return result

        # build quiz HTML
        quiz_html = moodle_html_header
        for question in questions:
            question_html = question.get('html', '').split('<script>')[0]
            if question_html is None:
                question_html = ''
            quiz_html += question_html + '\n'

            question_files = question.get('responsefileareas', [])
            self.set_props_of_files(question_files, type='quiz_file')
            result.extend(question_files)

        quiz_html += moodle_html_footer
        result.append(
            {
                'filename': attempt_filename,
                'filepath': '/',
                'timemodified': attempt.get('timemodified', 0),
                'html': quiz_html,
                'type': 'html',
                'no_search_for_urls': True,
            }
        )

        return result

    async def _get_quiz_access_info(self, quiz_id: int) -> Dict:
        """
        Get quiz access information including rules and warnings

        Returns access rules, active rule names, prevent access reasons, and warnings
        """
        try:
            response = await self.client.async_post(
                'mod_quiz_get_quiz_access_information',
                {'quizid': quiz_id}
            )
            return {
                'accessrules': response.get('accessrules', []),
                'activerulenames': response.get('activerulenames', []),
                'preventaccessreasons': response.get('preventaccessreasons', []),
                'canattempt': response.get('canattempt', False),
                'canmanage': response.get('canmanage', False),
                'canpreview': response.get('canpreview', False),
                'canreviewmyattempts': response.get('canreviewmyattempts', True),
                'canviewreports': response.get('canviewreports', False),
                'warnings': response.get('warnings', []),
            }
        except Exception as e:
            logging.debug(f"Could not fetch access information for quiz {quiz_id}: {e}")
            return {}

    async def _get_user_best_grade(self, quiz_id: int) -> Dict:
        """
        Get user's best grade for quiz

        Returns whether user has a grade, the grade value, and grade to pass
        """
        try:
            response = await self.client.async_post(
                'mod_quiz_get_user_best_grade',
                {'quizid': quiz_id, 'userid': self.user_id}
            )
            return {
                'hasgrade': response.get('hasgrade', False),
                'grade': response.get('grade'),
                'gradetopass': response.get('gradetopass'),
            }
        except Exception as e:
            logging.debug(f"Could not fetch best grade for quiz {quiz_id}: {e}")
            return {}

    async def _get_quiz_feedback(self, quiz_id: int, grade: float) -> str:
        """
        Get feedback for a specific grade

        Returns the feedback text for the given grade
        """
        try:
            response = await self.client.async_post(
                'mod_quiz_get_quiz_feedback_for_grade',
                {'quizid': quiz_id, 'grade': grade}
            )
            return response.get('feedbacktext', '')
        except Exception as e:
            logging.debug(f"Could not fetch feedback for quiz {quiz_id} grade {grade}: {e}")
            return ''
