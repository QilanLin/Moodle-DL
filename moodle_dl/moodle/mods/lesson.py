import logging
import re
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.moodle_constants import moodle_html_footer, moodle_html_header
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class LessonMod(MoodleMod):
    MOD_NAME = 'lesson'
    MOD_PLURAL_NAME = 'lessons'
    MOD_MIN_VERSION = 2017051500  # 3.3

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_lessons() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        lessons = (
            await self.client.async_post(
                'mod_lesson_get_lessons_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('lessons', [])

        result = {}
        for lesson in lessons:
            course_id = lesson.get('course', 0)
            lesson_files = lesson.get('introfiles', [])
            lesson_files += lesson.get('mediafiles', [])
            self.set_props_of_files(lesson_files, type='lesson_introfile')

            lesson_intro = lesson.get('intro', '')
            if lesson_intro != '':
                lesson_files.append(
                    {
                        'filename': 'Lesson intro',
                        'filepath': '/',
                        'description': lesson_intro,
                        'type': 'description',
                    }
                )

            self.add_module(
                result,
                course_id,
                lesson.get('coursemodule', 0),
                {
                    'id': lesson.get('id', 0),
                    'name': lesson.get('name', 'unnamed lesson'),
                    'files': lesson_files,
                },
            )

        await self.add_lessons_files(result)
        return result

    async def add_lessons_files(self, lessons: Dict[int, Dict[int, Dict]]):
        """
        Fetches for the lessons list the lessons files
        @param lessons: Dictionary of all lessons, indexed by courses, then module id
        """
        if not self.config.get_download_lessons():
            return

        if self.version < 2017051500:  # 3.3
            return

        await self.run_async_load_function_on_mod_entries(lessons, self.load_lesson_files)

    async def load_lesson_files(self, lesson: Dict):
        """
        加载课程文件（基于官方 Moodle Mobile App 改进）

        改进点：
        - 下载学生答案
        - 下载教师反馈
        - 完整的问题-答案-反馈记录

        参考：moodleapp/src/addons/mod/lesson/services/lesson.ts
        """
        # load only the last attempt
        # TODO: We could load all attempts, if needed
        lesson_id = lesson.get('id', 0)
        data = {'lessonid': lesson_id, 'userid': self.user_id, 'lessonattempt': 0}
        try:
            user_attempt = await self.client.async_post('mod_lesson_get_user_attempt', data)
            user_attempt['lesson_name'] = lesson.get('name', '')
            user_attempt['lesson_id'] = lesson_id
        except RequestRejectedError:
            logging.debug("No access rights for lesson %d", lesson_id)
            return
        lesson['files'] += await self._get_files_of_attempt(user_attempt)

    async def load_attempt_page(self, attempt_page: Dict) -> List[Dict]:
        "Load files and content of an answer page in an attempt"
        page_id = attempt_page.get('page', {}).get('id', 0)
        data = {'lessonid': attempt_page.get('page', {}).get('lessonid', 0), 'pageid': page_id, 'returncontents': 1}
        try:
            page_result = await self.client.async_post('mod_lesson_get_page_data', data)
        except RequestRejectedError:
            logging.debug("No access rights for lesson attempt page %d", page_id)
            return []

        page_files = page_result.get('contentfiles', [])
        self.set_props_of_files(page_files, type='lesson_file')

        page_content = page_result.get('pagecontent', '')
        if page_content is None:
            page_content = ''
        page_files.append(
            {
                '_is_page_content': True,
                'content': page_content.split('<script>')[0],
            }
        )
        return page_files

    async def _get_files_of_attempt(self, attempt: Dict) -> List[Dict]:
        result = []

        # Calculate last modified
        completed = attempt.get('userstats', {}).get('completed', 0)
        for answerpage in attempt.get('answerpages', []):
            answerpage_page = answerpage.get('page', {})
            last_modified = answerpage_page.get('timemodified', 0)
            if last_modified == 0:
                last_modified = answerpage_page.get('timecreated', 0)
            if completed < last_modified:
                completed = last_modified

        # Create grade file
        grade = attempt.get('userstats', {}).get('gradeinfo', {}).get('earned', None)
        grade_total = attempt.get('userstats', {}).get('gradeinfo', {}).get('total', None)
        if grade is not None and grade_total is not None:
            result.append(
                {
                    'filename': 'grade',
                    'filepath': '/',
                    'timemodified': completed,
                    'description': str(grade) + ' / ' + str(grade_total),
                    'type': 'description',
                }
            )

        # 新增：下载问题答案和反馈（基于官方 Mobile App）
        # 参考：mod_lesson_get_questions_attempts API
        result += await self._get_questions_and_answers(attempt)

        attempt_pages_and_files = await self.run_async_collect_function_on_list(
            attempt.get('answerpages', []),
            self.load_attempt_page,
            'attempt page',
            {'collect_id': 'page.id', 'collect_name': 'page.lessonid'},
        )

        # build lesson HTML and add unique files
        lesson_html = moodle_html_header
        lesson_is_empty = True
        for page_or_file in attempt_pages_and_files:
            if page_or_file.get('_is_page_content', False):
                page_content = page_or_file.get('content', '')
                if page_content != '':
                    lesson_is_empty = False
                lesson_html += page_content + '\n'
            else:
                new_page_file = True
                clean_file_url = re.sub(r"\/page_contents\/\d+\/", "/", page_or_file.get('fileurl', ''))
                page_or_file['_clean_file_url'] = clean_file_url
                for attempt_file in result:
                    if attempt_file.get('_clean_file_url', '') == clean_file_url:
                        # sometimes the teacher adds the same file for multiple answer pages with a
                        # different timestamp
                        if (attempt_file.get('filesize', 0) == page_or_file.get('filesize', 0)) and (
                            attempt_file.get('filename', '') == page_or_file.get('filename', '')
                        ):
                            new_page_file = False
                            break
                if new_page_file:
                    result.append(page_or_file)

        # The generation code for the original review page is here:
        # https://github.com/moodle/moodle/blob/511a87f5fc357f18a4c53911f6e6c7f7b526246e/mod/lesson/report.php#L278-L366
        if not lesson_is_empty:
            lesson_html += moodle_html_footer
            result.append(
                {
                    'filename': PT.to_valid_name(attempt['lesson_name'], is_file=False),
                    'filepath': '/',
                    'timemodified': completed,
                    'html': lesson_html,
                    'filter_urls_during_search_containing': ['/mod_lesson/page_contents/'],
                    'type': 'html',
                    'no_search_for_urls': True,
                }
            )

        return result

    async def _get_questions_and_answers(self, attempt: Dict) -> List[Dict]:
        """
        获取课程中的问题、学生答案和教师反馈

        基于官方 Moodle Mobile App 的实现
        参考：moodleapp/src/addons/mod/lesson/services/lesson.ts

        API: mod_lesson_get_questions_attempts

        @param attempt: The user's lesson attempt data
        @return: List of file dictionaries containing question-answer-feedback records
        """
        result = []
        lesson_id = attempt.get('lesson_id', 0)
        if lesson_id == 0:
            return result

        # Get the attempt number
        # Note: Moodle API uses 'retake' number, not 'nquestions'
        attempt_number = attempt.get('userstats', {}).get('lessonsclosed', 0)

        try:
            # Call API to get question attempts
            data = {
                'lessonid': lesson_id,
                'attempt': attempt_number,
                'correct': True,  # Include correct answers
                'pageid': 0,  # 0 means all pages
                'userid': self.user_id,
            }

            questions_data = await self.client.async_post('mod_lesson_get_questions_attempts', data)

        except RequestRejectedError:
            logging.debug("No access to questions for lesson %d", lesson_id)
            return result
        except Exception as e:
            logging.debug("Error getting questions for lesson %d: %s", lesson_id, str(e))
            return result

        # Process each question attempt
        attempts = questions_data.get('attempts', [])
        if not attempts:
            return result

        for question_attempt in attempts:
            # Build Markdown content with question, answer, feedback, score
            question_title = question_attempt.get('title', 'Question')

            markdown_content = f"# {question_title}\n\n"

            # Add question content
            question_html = question_attempt.get('contents', '')
            if question_html:
                markdown_content += f"## Question\n\n{question_html}\n\n"

            # Add user's answer
            user_answer = question_attempt.get('useranswer', '')
            if user_answer:
                markdown_content += f"## Your Answer\n\n{user_answer}\n\n"

            # Add correct answer if available
            correct_answer = question_attempt.get('correctanswer', '')
            if correct_answer:
                markdown_content += f"## Correct Answer\n\n{correct_answer}\n\n"

            # Add feedback/response
            response = question_attempt.get('response', '')
            if response:
                markdown_content += f"## Feedback\n\n{response}\n\n"

            # Add score if available
            if 'earned' in question_attempt:
                earned = question_attempt.get('earned', 0)
                total = question_attempt.get('total', 0)
                markdown_content += f"## Score\n\n{earned} / {total}\n\n"

            # Create file entry
            filename = PT.to_valid_name(f"Q{question_attempt.get('id', 0)}_{question_title}", is_file=True)
            result.append({
                'filename': filename,
                'filepath': '/answers/',
                'timemodified': question_attempt.get('timeseen', 0),
                'description': markdown_content,
                'type': 'description',
            })

        return result
