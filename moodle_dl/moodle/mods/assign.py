import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class AssignMod(MoodleMod):
    MOD_NAME = 'assign'
    MOD_PLURAL_NAME = 'assignments'
    MOD_MIN_VERSION = 2012120300  # 2.4

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        # 下载条件: 作业本身或提交内容
        # 如果启用了作业下载，则下载所有作业相关内容
        # 如果仅启用了提交下载，则只下载提交内容
        return config.get_download_submissions() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        获取所有作业信息，支持 Mobile API 和 Web API fallback。
        
        优先使用 mod_assign_get_assignments (Mobile API)，失败时使用 core_course_get_contents (Web API)。
        """
        try:
            # 首先尝试使用 Mobile API
            assign_courses = await self._fetch_assignments_mobile_api(courses)
        except (RequestRejectedError, KeyError, Exception) as e:
            logging.warning(f'❌ Mobile API 获取作业失败: {e}，尝试使用 Web API fallback...')
            # Fallback 到 Web API
            assign_courses = await self._fetch_assignments_web_api(courses, core_contents)

        result = {}
        for assign_course in assign_courses:
            course_id = assign_course.get('id', 0)
            result[course_id] = self.extract_assign_modules(assign_course.get('assignments', []))

        await self.add_submissions(result)
        await self.add_foreign_submissions(result)
        return result

    async def _fetch_assignments_mobile_api(self, courses: List[Course]) -> List[Dict]:
        """
        使用 Mobile API (mod_assign_get_assignments) 获取作业。
        
        Return: 包含 assignments 的课程列表
        """
        logging.debug('📱 使用 Mobile API 获取作业信息...')
        response = await self.client.async_post(
            'mod_assign_get_assignments',
            self.get_data_for_mod_entries_endpoint(courses)
        )
        assign_courses = response.get('courses', [])
        
        if not assign_courses:
            raise KeyError('Mobile API 返回空的作业列表')
        
        logging.debug(f'✅ Mobile API 成功获取 {len(assign_courses)} 个课程的作业信息')
        return assign_courses

    async def _fetch_assignments_web_api(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> List[Dict]:
        """
        使用 Web API (core_course_get_contents) 获取作业信息。
        
        这是 mod_assign_get_assignments 的 fallback 实现。
        通过解析 core_course_get_contents 返回的数据中的 assign 模块来获取作业信息。
        
        Return: 转换为与 Mobile API 相同格式的课程列表
        """
        logging.debug('🌐 使用 Web API fallback 获取作业信息...')
        
        assign_courses = []
        
        for course in courses:
            course_id = course.id
            
            # 从 core_contents 中获取该课程的所有模块
            if course_id not in core_contents:
                logging.debug(f'⚠️ 课程 {course_id} 不在 core_contents 中，跳过')
                continue
            
            sections = core_contents[course_id]
            assignments = []
            
            # 遍历所有 section，查找 assign 模块
            for section in sections:
                modules = section.get('modules', [])
                for module in modules:
                    if module.get('modname') == 'assign':
                        # 将 Web API 的 assign 模块转换为 Mobile API 的格式
                        assignment = self._convert_web_api_assign_to_mobile(module, course_id)
                        assignments.append(assignment)
            
            if assignments:
                assign_courses.append({
                    'id': course_id,
                    'shortname': course.shortname if hasattr(course, 'shortname') else '',
                    'fullname': course.fullname,
                    'assignments': assignments
                })
        
        if not assign_courses:
            logging.warning('⚠️ Web API fallback 未找到任何作业')
            raise ValueError('Web API 未能检索任何作业信息')
        
        logging.debug(f'✅ Web API fallback 成功获取 {len(assign_courses)} 个课程的作业信息')
        return assign_courses

    def _convert_web_api_assign_to_mobile(self, web_api_module: Dict, course_id: int) -> Dict:
        """
        将 Web API 的 assign 模块格式转换为 Mobile API 的格式。
        
        Web API 提供的数据较少，但包含关键的作业信息。
        """
        # 提取 Web API 中的基本信息
        module_contents = web_api_module.get('contents', [])
        introattachments = [c for c in module_contents if c.get('type') == 'file']
        
        # 构建 Mobile API 格式的作业数据
        assignment = {
            'id': web_api_module.get('instance', 0),
            'cmid': web_api_module.get('id', 0),
            'course': course_id,
            'name': web_api_module.get('name', ''),
            'intro': '',  # Web API 不直接提供 intro
            'introformat': 1,
            'introattachments': introattachments,
            'duedate': 0,
            'cutoffdate': 0,
            'allowsubmissionsfromdate': 0,
            'grade': 0,
            'timemodified': web_api_module.get('timemodified', 0),
            'timecreated': web_api_module.get('timecreated', 0),
            # 其他字段设为默认值，因为 Web API 不提供详细的作业设置
            'submissiondrafts': 0,
            'sendnotifications': 1,
            'sendlatenotifications': 0,
            'sendstudentnotifications': 1,
            'requiresubmissionstatement': 0,
            'requireallteammemberssubmit': 0,
            'teamsubmission': 0,
            'blindmarking': 0,
            'hidegrader': 0,
            'revealidentities': 0,
            'attemptreopenmethod': 'none',
            'maxattempts': -1,
            'markingworkflow': 0,
            'markingallocation': 0,
            'configs': [],
        }
        
        return assignment

    def extract_assign_modules(self, assignments: List[Dict]) -> Dict[int, Dict]:
        result = {}
        for assign in assignments:
            assign_files = self.get_introfiles(assign, 'assign_file', additional_keys=['introattachments'])

            assign_intro = assign.get('intro', '')
            intro_file = self.create_intro_file(assign_intro)
            if intro_file:
                assign_files.append(intro_file)

            # Create comprehensive assignment metadata
            assign_metadata = {
                'assignment_id': assign.get('id', 0),
                'course_id': assign.get('course', 0),
                'module_id': assign.get('cmid', 0),
                'name': assign.get('name', ''),
                'intro': assign_intro,
                'settings': {
                    'allowsubmissionsfromdate': assign.get('allowsubmissionsfromdate', 0),
                    'duedate': assign.get('duedate', 0),
                    'cutoffdate': assign.get('cutoffdate', 0),
                    'gradingduedate': assign.get('gradingduedate', 0),
                    'alwaysshowdescription': assign.get('alwaysshowdescription', 0),
                    'submissiondrafts': assign.get('submissiondrafts', 0),
                    'sendnotifications': assign.get('sendnotifications', 0),
                    'sendlatenotifications': assign.get('sendlatenotifications', 0),
                    'sendstudentnotifications': assign.get('sendstudentnotifications', 1),
                    'requiresubmissionstatement': assign.get('requiresubmissionstatement', 0),
                    'requireallteammemberssubmit': assign.get('requireallteammemberssubmit', 0),
                    'teamsubmission': assign.get('teamsubmission', 0),
                    'blindmarking': assign.get('blindmarking', 0),
                    'hidegrader': assign.get('hidegrader', 0),
                    'revealidentities': assign.get('revealidentities', 0),
                    'attemptreopenmethod': assign.get('attemptreopenmethod', 'none'),
                    'maxattempts': assign.get('maxattempts', -1),
                    'markingworkflow': assign.get('markingworkflow', 0),
                    'markingallocation': assign.get('markingallocation', 0),
                },
                'grading': {
                    'grade': assign.get('grade', 0),
                    'gradingmethod': 'simple',  # Can be 'simple', 'rubric', 'guide'
                },
                'submissions': {
                    'submissionattachments': assign.get('submissionattachments', 0),
                    'maxsubmissionsizebytes': assign.get('maxsubmissionsizebytes', 0),
                },
                'notifications': {
                    'sendnotifications': assign.get('sendnotifications', 0),
                    'sendlatenotifications': assign.get('sendlatenotifications', 0),
                },
                'timestamps': {
                    'timemodified': assign.get('timemodified', 0),
                    'timecreated': assign.get('timecreated', 0),
                },
                'configs': assign.get('configs', []),
            }

            # Add metadata file
            assign_files.append(
                self.create_metadata_file(assign_metadata, timemodified=assign.get('timemodified', 0))
            )

            result[assign.get('cmid', 0)] = {
                'id': assign.get('id', 0),
                'name': assign.get('name', ''),
                'timemodified': assign.get('timemodified', 0),
                'files': assign_files,
            }
        return result

    async def add_submissions(self, assignments: Dict[int, Dict[int, Dict]]):
        """
        Fetches for the assignments list additionally the submissions
        @param assignments: Dictionary of all assignments, indexed by courses, then module id
        """
        if not self.config.get_download_submissions():
            return

        if self.version < 2016052300:  # 3.1
            return

        await self.run_async_load_function_on_mod_entries(assignments, self.load_submissions)

    async def add_foreign_submissions(self, assignments: Dict[int, Dict[int, Dict]]):
        """
        Fetches for the assignments list additionally the submissions of other students
        @param assignments: Dictionary of all assignments, indexed by courses, then module id
        """
        if not self.config.get_download_submissions():
            return

        if self.version < 2013051400:  # 2.5
            return

        # get submissions of all students for all assignments (only teachers can see that)
        indexed_assignment_ids = self.get_indexed_ids_of_mod_instances(assignments)
        if len(indexed_assignment_ids) <= 0:
            return

        assignments_with_all_submissions = (
            await self.client.async_post('mod_assign_get_submissions', {'assignmentids': indexed_assignment_ids})
        ).get('assignments', [])

        if len(assignments_with_all_submissions) <= 0:
            return

        for course_id, modules in assignments.items():
            found_assignment_in_course = False
            for assignment in assignments_with_all_submissions:
                for _module_id, module in modules.items():
                    if assignment['assignmentid'] == module['id']:
                        found_assignment_in_course = True
                        break
                if found_assignment_in_course:
                    break
            if not found_assignment_in_course:
                continue
            # TODO: Extract the API call to get enrolled users, if we need the information also in another mod
            try:
                course_users = await self.client.async_post('core_enrol_get_enrolled_users', {'courseid': course_id})
            except RequestRejectedError:
                logging.debug("No access rights for enrolled users list of course %d", course_id)
                return

            for assignment in assignments_with_all_submissions:
                found_module = None
                for _module_id, module in modules.items():
                    if assignment['assignmentid'] == module['id']:
                        found_module = module
                        break
                if found_module is None:
                    continue

                for submission in assignment.get('submissions', []):
                    user_id = submission.get('userid', 0)
                    group_id = submission.get('groupid', 0)
                    subfolder = None
                    if user_id == 0:
                        # Its a group submission
                        found_users = []
                        group_name = None
                        for user in course_users:
                            for group in user.get('groups', []):
                                if group.get('id', 0) == group_id:
                                    found_users.append(user)
                                    if group_name is None:
                                        group_name = group.get('name')
                                    break
                        if len(found_users) == 0:
                            # should not happen
                            continue
                        all_usernames = ' & '.join(
                            (f"{user.get('fullname')} ({user.get('idnumber') or user.get('id', 0)})")
                            for user in found_users
                        )
                        subfolder = PT.to_valid_name(
                            f"{group_name or 'Unnamed group'} ({group_id}): {all_usernames}", is_file=False
                        )
                    else:
                        # Its a user submission
                        found_user = None
                        for user in course_users:
                            if user.get('id', 0) == user_id:
                                found_user = user
                                break
                        if found_user is None:
                            # should not happen
                            continue
                        subfolder = PT.to_valid_name(
                            f"{found_user.get('fullname')} ({found_user.get('idnumber') or found_user.get('id', 0)})",
                            is_file=False,
                        )
                    found_module['files'] += self._get_files_of_plugins(submission, f'/all_submissions/{subfolder}/')

    async def _get_assignment_grades(self, assign_id: int) -> List[Dict]:
        """
        Get detailed grading information for an assignment
        Returns grades for all participants (teachers) or own grade (students)
        """
        try:
            response = await self.client.async_post(
                'mod_assign_get_grades',
                {
                    'assignmentids': [assign_id],
                    'since': 0  # Get all grades, not just recent ones
                }
            )
            assignments = response.get('assignments', [])
            if assignments:
                return assignments[0].get('grades', [])
            return []
        except RequestRejectedError:
            logging.debug(f"No access to grades for assignment {assign_id}")
            return []
        except Exception as e:
            logging.debug(f"Could not fetch grades for assignment {assign_id}: {e}")
            return []

    async def load_submissions(self, assign: Dict):
        "Fetches for a given assign module the submissions"
        data = {'userid': self.user_id, 'assignid': assign.get('id', 0)}
        submission = await self._fetch_submission_status_mobile_api(assign.get('id', 0), data)
        assign['files'] += self._get_files_of_submission(submission)

        # Get detailed grades information
        assign_id = assign.get('id', 0)
        grades = await self._get_assignment_grades(assign_id)
        if grades:
            # Export grades as JSON metadata
            grades_metadata = {
                'assignment_id': assign_id,
                'total_grades': len(grades),
                'grades': [
                    {
                        'id': grade.get('id', 0),
                        'userid': grade.get('userid', 0),
                        'attemptnumber': grade.get('attemptnumber', 0),
                        'timecreated': grade.get('timecreated', 0),
                        'timemodified': grade.get('timemodified', 0),
                        'grader': grade.get('grader', 0),
                        'grade': grade.get('grade', ''),
                        'gradefordisplay': grade.get('gradefordisplay', ''),
                    }
                    for grade in grades
                ],
            }

            assign['files'].append(
                {
                    'filename': PT.to_valid_name('grades', is_file=True) + '.json',
                    'filepath': '/submissions/',
                    'timemodified': assign.get('timemodified', 0),
                    'content': json.dumps(grades_metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

    def _get_files_of_submission(self, submission: Dict) -> List[Dict]:
        result = []
        # get own submission
        last_attempt = submission.get('lastattempt', {})
        last_submission = last_attempt.get('submission', {})
        last_team_submission = last_attempt.get('teamsubmission', {})
        # We could also extract previous attempts, but for now we are only interested in last attempt
        # Multiple attempts on assignments are very raw and therefore not implemented yet

        # get teachers feedback
        feedback = submission.get('feedback', {})

        base_file_path = '/submissions/'
        result += self._get_files_of_plugins(last_submission, base_file_path)
        result += self._get_files_of_plugins(last_team_submission, base_file_path)
        result += self._get_files_of_plugins(feedback, base_file_path)
        result += self._get_grade_of_feedback(feedback, base_file_path)

        return result

    def _get_grade_of_feedback(self, feedback: Dict, base_file_path: str) -> List[Dict]:
        grade_for_display = feedback.get('gradefordisplay')
        graded_date = feedback.get('gradeddate')
        if graded_date is None or grade_for_display is None:
            return []

        return [
            {
                'filename': 'grade',
                'filepath': base_file_path,
                'timemodified': graded_date,
                'description': grade_for_display,
                'type': 'description',
            }
        ]

    def _get_files_of_plugins(self, obj: Dict, base_file_path: str) -> List[Dict]:
        result = []
        plugins = obj.get('plugins', [])

        for plugin in plugins:
            # We could use the plugin name in the file structure, but it is most of the time unnecessary information
            for file_area in plugin.get('fileareas', []):
                files = file_area.get('files', [])
                self.set_props_of_files(files, type='submission_file')
                self.set_base_file_path_of_files(files, base_file_path)
                result += files

            for editor_field in plugin.get('editorfields', []):
                filename = editor_field.get('description', '')
                description = editor_field.get('text', '')
                if filename != '' and description != '':
                    result.append(
                        {
                            'filename': filename,
                            'description': description,
                            'type': 'description',
                            'filepath': base_file_path,
                        }
                    )

        return result

    async def _fetch_submission_status_mobile_api(
        self, assignment_id: int, data: Dict
    ) -> Dict:
        """
        使用 Mobile API (mod_assign_get_submission_status) 获取学生提交状态，支持优雅降级。
        
        这是学生 API，用于获取当前用户的作业提交状态。
        基本不会失败，但仍然添加异常处理以增强健壮性。
        
        Args:
            assignment_id: 作业 ID
            data: 请求参数 {'userid': user_id, 'assignid': assignment_id}
            
        Return: 提交状态数据
        """
        logging.debug(f'📱 使用 Mobile API 获取作业提交状态 (assignment_id={assignment_id})...')
        try:
            submission = await self.client.async_post('mod_assign_get_submission_status', data)
            
            if submission.get('submission'):
                logging.debug(f'✅ Mobile API 成功获取提交状态')
            else:
                logging.debug(f'⚠️ Mobile API 返回空的提交状态 (assignment_id={assignment_id})')
            
            return submission
        except Exception as e:
            logging.warning(
                f'❌ Mobile API 获取提交状态失败 (assignment_id={assignment_id}): {e}'
                f'\n   💡 这是学生 API，基本不应该失败'
                f'\n   建议: 检查网络连接和 Mobile API 是否可用'
            )
            return {'submission': None}
