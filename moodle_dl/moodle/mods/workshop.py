import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class WorkshopMod(MoodleMod):
    MOD_NAME = 'workshop'
    MOD_PLURAL_NAME = 'workshops'
    MOD_MIN_VERSION = 2017111300  # 3.4

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_workshops() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        workshops = (
            await self.client.async_post(
                'mod_workshop_get_workshops_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('workshops', [])

        result = {}
        for workshop in workshops:
            course_id = workshop.get('course', 0)
            module_id = workshop.get('coursemodule', 0)
            workshop_id = workshop.get('id', 0)
            workshop_name = workshop.get('name', 'unnamed workshop')

            workshop_files = self.get_introfiles(
                workshop, 'workshop_file', additional_keys=['instructauthorsfiles', 'instructreviewersfiles', 'conclusionfiles']
            )

            workshop_intro = workshop.get('intro', '')
            intro_file = self.create_intro_file(workshop_intro)
            if intro_file:
                intro_file['filename'] = 'Workshop intro'
                workshop_files.append(intro_file)

            workshop_instruct_authors = workshop.get('instructauthors', '')
            if workshop_instruct_authors != '':
                workshop_files.append(
                    {
                        'filename': 'Instructions for submission',
                        'filepath': '/',
                        'description': workshop_instruct_authors,
                        'type': 'description',
                    }
                )

            workshop_instruct_reviewers = workshop.get('instructreviewers', '')
            if workshop_instruct_reviewers != '':
                workshop_files.append(
                    {
                        'filename': 'Instructions for assessment',
                        'filepath': '/',
                        'description': workshop_instruct_reviewers,
                        'type': 'description',
                    }
                )

            workshop_conclusion = workshop.get('conclusion', '')
            if workshop_conclusion != '':
                workshop_files.append(
                    {
                        'filename': 'Conclusion',
                        'filepath': '/',
                        'description': workshop_conclusion,
                        'type': 'description',
                    }
                )

            # Get workshop access information if download is enabled
            access_info = {}
            if self.config.get_download_workshops():
                access_info = await self._get_workshop_access_info(workshop_id)

            # Create comprehensive workshop metadata
            metadata = {
                'workshop_id': workshop_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': workshop_name,
                'intro': workshop_intro,
                'settings': {
                    # Grading settings
                    'grade': workshop.get('grade', 100),
                    'gradinggrade': workshop.get('gradinggrade', 20),
                    'strategy': workshop.get('strategy', 'accumulative'),
                    'evaluation': workshop.get('evaluation', 'best'),
                    'gradedecimals': workshop.get('gradedecimals', 2),
                    # Submission settings
                    'nattachments': workshop.get('nattachments', 1),
                    'attachmentextensions': workshop.get('attachmentextensions', ''),
                    'submissionfiletypes': workshop.get('submissionfiletypes', ''),
                    'maxbytes': workshop.get('maxbytes', 0),
                    'latesubmissions': workshop.get('latesubmissions', 0),
                    # Assessment settings
                    'useselfassessment': workshop.get('useselfassessment', 0),
                    'overallfeedbackmode': workshop.get('overallfeedbackmode', 1),
                    'overallfeedbackfiles': workshop.get('overallfeedbackfiles', 0),
                    'overallfeedbackmaxbytes': workshop.get('overallfeedbackmaxbytes', 0),
                    'overallfeedbackfiletypes': workshop.get('overallfeedbackfiletypes', ''),
                    # Example submissions settings
                    'useexamples': workshop.get('useexamples', 0),
                    'examplesmode': workshop.get('examplesmode', 0),
                    # Availability settings
                    'submissionstart': workshop.get('submissionstart', 0),
                    'submissionend': workshop.get('submissionend', 0),
                    'assessmentstart': workshop.get('assessmentstart', 0),
                    'assessmentend': workshop.get('assessmentend', 0),
                    # Phase settings
                    'phase': workshop.get('phase', 0),
                    'phaseswitchassessment': workshop.get('phaseswitchassessment', 0),
                    # Feedback settings
                    'conclusion': workshop.get('conclusion', ''),
                    'conclusionformat': workshop.get('conclusionformat', 1),
                },
                'access_information': access_info,
                'timestamps': {
                    'timemodified': workshop.get('timemodified', 0),
                    'timecreated': workshop.get('timecreated', 0),
                },
                'features': self.get_features(
                    purpose='assessment',
                    completion_tracks_views=False,
                    grade_has_grade=True
                ),
                'note': 'Workshop is a peer assessment activity with flexible grading strategies. '
                + 'This export includes comprehensive settings, workflow phases, submissions, and peer assessments.',
            }

            # Add metadata file
            workshop_files.append(
                self.create_metadata_file(metadata, timemodified=workshop.get('timemodified', 0))
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': workshop_id,
                    'name': workshop_name,
                    'files': workshop_files,
                },
            )

        await self.add_workshops_files(result)
        return result

    async def add_workshops_files(self, workshops: Dict[int, Dict[int, Dict]]):
        """
        Fetches for the workshops list the forum posts
        @param workshops: Dictionary of all workshops, indexed by courses, then module id
        """
        if not self.config.get_download_workshops():
            return

        if self.version < 2017111300:  # 3.4
            return

        await self.run_async_load_function_on_mod_entries(workshops, self.load_workshop_files)

    async def load_workshop_files(self, workshop: Dict):
        workshop_id = workshop.get('id', 0)
        data = {'workshopid': workshop_id, 'userid': self.user_id}

        try:
            submissions = (await self.client.async_post('mod_workshop_get_submissions', data)).get('submissions', [])
        except RequestRejectedError:
            logging.debug("No access rights for workshop %d", workshop_id)
            return

        try:
            assessments = (await self.client.async_post('mod_workshop_get_reviewer_assessments', data)).get(
                'assessments', []
            )
        except RequestRejectedError:
            assessments = []
        submissions += await self.run_async_collect_function_on_list(
            assessments,
            self.load_foreign_submission,
            'foreign submission',
            {'collect_id': 'submissionid', 'collect_name': 'title'},
        )

        try:
            grades = await self.client.async_post('mod_workshop_get_grades', data)
        except RequestRejectedError:
            grades = {}

        # Get user plan (workflow phases and tasks)
        try:
            user_plan = await self.client.async_post('mod_workshop_get_user_plan', data)
        except RequestRejectedError:
            user_plan = {}
        except Exception as e:
            logging.debug(f"Could not fetch user plan for workshop {workshop_id}: {e}")
            user_plan = {}

        workshop_files = self._get_files_of_workshop(submissions, grades, user_plan)
        workshop['files'] += workshop_files

    async def load_foreign_submission(self, assessment: Dict) -> Dict:
        # assessment_id = assessment.get('id', 0)
        # assessment_reviewer_id = assessment.get('reviewerid', 0)

        assessment_files = assessment.get('feedbackcontentfiles', [])
        assessment_files += assessment.get('feedbackattachmentfiles', [])

        feedback_author = assessment.get('feedbackauthor', '')
        if feedback_author != '':
            assessment_files.append(
                {
                    'filename': 'Feedback for the author',
                    'filepath': '/',
                    'description': feedback_author,
                    'type': 'description',
                }
            )

        feedback_reviewer = assessment.get('feedbackreviewer', '')
        if feedback_reviewer != '':
            assessment_files.append(
                {
                    'filename': 'Feedback for the reviewer',
                    'filepath': '/',
                    'description': feedback_reviewer,
                    'type': 'description',
                }
            )
        assessment_submission_id = assessment.get('submissionid', 0)
        # Get submissions of assessments
        data = {'submissionid': assessment_submission_id}
        try:
            submission = (await self.client.async_post('mod_workshop_get_submission', data)).get('submission', {})
            submission['files'] = assessment_files
            return submission
        except RequestRejectedError:
            logging.debug("No access rights for workshop submission %d", assessment_submission_id)
            return None

    def _get_files_of_workshop(self, submissions: List[Dict], grades: Dict, user_plan: Dict) -> List:
        result = []

        # Export user plan (workflow phases and tasks)
        if user_plan and user_plan.get('userplan'):
            user_plan_data = user_plan.get('userplan', {})
            phases = user_plan_data.get('phases', [])

            # Create comprehensive user plan metadata
            plan_metadata = {
                'phases': phases,
                'examples': user_plan_data.get('examples', []),
                'phase_count': len(phases),
                'note': 'Workshop user plan shows workflow phases (Setup, Submission, Assessment, etc.) and tasks for the current user.',
            }

            result.append(
                {
                    'filename': PT.to_valid_name('user_plan', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': 0,
                    'content': json.dumps(plan_metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

        # Grades
        assessment_long_str_grade = grades.get('assessmentlongstrgrade', '')
        if assessment_long_str_grade != '':
            result.append(
                {
                    'filename': 'Assessment grade',
                    'filepath': '/',
                    'description': assessment_long_str_grade,
                    'type': 'description',
                }
            )

        submission_long_str_grade = grades.get('submissionlongstrgrade', '')
        if submission_long_str_grade != '':
            result.append(
                {
                    'filename': 'Submission grade',
                    'filepath': '/',
                    'description': submission_long_str_grade,
                    'type': 'description',
                }
            )

        # Own and foreign submissions
        for submission in submissions:
            submission_content = submission.get('content', 0)

            filepath = f"/submissions {submission.get('id', 0)}/"

            submission_files = submission.get('contentfiles', [])
            submission_files += submission.get('attachmentfiles', [])
            self.set_props_of_files(submission_files, type='workshop_file')
            self.set_base_file_path_of_files(submission_files, filepath)

            submission_files += submission.get('files', [])  # Already pares files

            if submission_content != '':
                submission_files.append(
                    {
                        'filename': submission.get('title', 0),
                        'filepath': filepath,
                        'description': submission_content,
                        'timemodified': submission.get('timemodified', 0),
                        'type': 'description',
                    }
                )
            result += submission_files

        return result

    async def _get_workshop_access_info(self, workshop_id: int) -> Dict:
        """
        Get workshop access information including permissions and availability

        Returns user capabilities and access restrictions
        """
        try:
            response = await self.client.async_post(
                'mod_workshop_get_workshop_access_information',
                {'workshopid': workshop_id}
            )
            return {
                'canview': response.get('canview', False),
                'canaddinstance': response.get('canaddinstance', False),
                'canswitchphase': response.get('canswitchphase', False),
                'caneditdimensions': response.get('caneditdimensions', False),
                'cansubmit': response.get('cansubmit', False),
                'canpeerassess': response.get('canpeerassess', False),
                'canmanageexamples': response.get('canmanageexamples', False),
                'canallocate': response.get('canallocate', False),
                'canpublishsubmissions': response.get('canpublishsubmissions', False),
                'canviewauthornames': response.get('canviewauthornames', False),
                'canviewreviewernames': response.get('canviewreviewernames', False),
                'canviewallsubmissions': response.get('canviewallsubmissions', False),
                'canviewpublishedsubmissions': response.get('canviewpublishedsubmissions', False),
                'canviewauthorpublished': response.get('canviewauthorpublished', False),
                'canviewallassessments': response.get('canviewallassessments', False),
                'canoverridegrades': response.get('canoverridegrades', False),
                'canignoredeadlines': response.get('canignoredeadlines', False),
                'candeletesubmissions': response.get('candeletesubmissions', False),
                'creatingsubmissionallowed': response.get('creatingsubmissionallowed', False),
                'modifyingsubmissionallowed': response.get('modifyingsubmissionallowed', False),
                'assessingallowed': response.get('assessingallowed', False),
                'assessingexamplesallowed': response.get('assessingexamplesallowed', False),
                'warnings': response.get('warnings', []),
            }
        except Exception as e:
            logging.debug(f"Could not fetch access information for workshop {workshop_id}: {e}")
            return {}

