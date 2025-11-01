import json
import logging
from datetime import datetime
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class H5PActivityMod(MoodleMod):
    """
    H5P Activity module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/h5pactivity/services/h5pactivity.ts

    Supports:
    - Downloading H5P content packages
    - Downloading deployed H5P files
    - User attempts and results (optional)
    - Metadata export
    """

    MOD_NAME = 'h5pactivity'
    MOD_PLURAL_NAME = 'h5pactivities'
    MOD_MIN_VERSION = 2019111800  # 3.8

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_h5pactivities() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all H5P activities from courses

        Process:
        1. Get H5P activities by courses
        2. Download H5P packages and deployed files
        3. Optionally download user attempts and results
        """

        result = {}
        if not self.config.get_download_h5pactivities():
            return result

        # Get all H5P activities for the courses
        try:
            response = await self.client.async_post(
                'mod_h5pactivity_get_h5pactivities_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            h5pactivities = response.get('h5pactivities', [])
        except RequestRejectedError:
            logging.debug("No access to H5P activities or WS not available")
            return result
        except Exception as e:
            logging.debug("Error getting H5P activities: %s", str(e))
            return result

        for h5p in h5pactivities:
            course_id = h5p.get('course', 0)
            module_id = h5p.get('coursemodule', 0)
            h5p_name = h5p.get('name', 'unnamed h5p activity')

            # Get intro files
            h5p_files = h5p.get('introfiles', [])
            self.set_props_of_files(h5p_files, type='h5p_introfile')

            # Add intro description
            h5p_intro = h5p.get('intro', '')
            intro_file = self.create_intro_file(h5p_intro)
            if intro_file:
                intro_file['filename'] = 'H5P intro'
                h5p_files.append(intro_file)

            # Add H5P package files
            package_files = h5p.get('package', [])
            if package_files:
                for pkg in package_files:
                    pkg['filepath'] = '/package/'
                    self.set_props_of_file(pkg, type='h5p_package')
                    h5p_files.append(pkg)

            # Add deployed file (the actual H5P content)
            deployed_file = h5p.get('deployedfile', {})
            if deployed_file and deployed_file.get('fileurl'):
                deployed_file['filepath'] = '/deployed/'
                self.set_props_of_file(deployed_file, type='h5p_file')
                h5p_files.append(deployed_file)

            # Create metadata file
            metadata = {
                'h5p_id': h5p.get('id', 0),
                'course_id': course_id,
                'name': h5p_name,
                'grade': h5p.get('grade', None),
                'grade_method': h5p.get('grademethod', 0),
                'enable_tracking': h5p.get('enabletracking', 0) == 1,
                'display_options': h5p.get('displayoptions', 0),
                'timestamps': {
                    'created': h5p.get('timecreated', 0),
                    'modified': h5p.get('timemodified', 0),
                },
            }

            h5p_files.append(
                self.create_metadata_file(metadata, timemodified=h5p.get('timemodified', 0))
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': h5p.get('id', 0),
                    'name': h5p_name,
                    'files': h5p_files,
                },
            )

        # Optionally load user attempts
        if self.config.get_download_h5p_attempts():
            await self.add_h5p_attempts(result)

        return result

    async def add_h5p_attempts(self, h5pactivities: Dict[int, Dict[int, Dict]]):
        """
        Fetch H5P user attempts and results
        @param h5pactivities: Dictionary of all H5P activities
        """
        await self.run_async_load_function_on_mod_entries(h5pactivities, self.load_h5p_attempts)

    async def load_h5p_attempts(self, h5p: Dict):
        """
        Load user attempts and results for an H5P activity
        """
        h5p_id = h5p.get('id', 0)

        try:
            # Get user attempts
            attempts_response = await self.client.async_post(
                'mod_h5pactivity_get_user_attempts',
                {'h5pactivityid': h5p_id, 'userid': self.user_id},
            )

            attempts = attempts_response.get('usersattempts', [])
            if not attempts or len(attempts) == 0:
                return

            user_attempts = attempts[0] if attempts else {}
            attempt_list = user_attempts.get('attempts', [])

            if not attempt_list:
                return

            # Get detailed results for each attempt
            for attempt in attempt_list:
                attempt_id = attempt.get('id', 0)
                attempt_files = await self._get_attempt_results(h5p_id, attempt_id, attempt)
                h5p['files'] += attempt_files

        except RequestRejectedError:
            logging.debug("No access to attempts for H5P activity %d", h5p_id)
        except Exception as e:
            logging.debug("Error getting attempts for H5P activity %d: %s", h5p_id, str(e))

    async def _get_attempt_results(self, h5p_id: int, attempt_id: int, attempt: Dict) -> List[Dict]:
        """
        Get detailed results for an attempt

        @param h5p_id: H5P activity ID
        @param attempt_id: Attempt ID
        @param attempt: Attempt summary data
        @return: List of file dictionaries
        """
        result = []

        try:
            # Get detailed results
            results_response = await self.client.async_post(
                'mod_h5pactivity_get_results',
                {'h5pactivityid': h5p_id, 'attemptids': [attempt_id]},
            )

            attempts_data = results_response.get('attemptsdata', [])
            if not attempts_data:
                return result

            attempt_data = attempts_data[0]

            # Create results summary
            attempt_num = attempt.get('attempt', 0)
            score_raw = attempt_data.get('scored', {}).get('rawscore', 0)
            score_max = attempt_data.get('scored', {}).get('maxscore', 0)
            score_scaled = attempt_data.get('scored', {}).get('scaledscore', 0)
            duration = attempt.get('duration', 0)
            completion = attempt.get('completion', False)
            success = attempt.get('success', False)

            # Build summary text
            summary = f"# H5P Attempt {attempt_num}\n\n"
            summary += f"**Score:** {score_raw} / {score_max}"
            if score_scaled > 0:
                summary += f" ({score_scaled * 100:.1f}%)"
            summary += "\n\n"
            summary += f"**Duration:** {duration} seconds\n"
            summary += f"**Completed:** {'Yes' if completion else 'No'}\n"
            summary += f"**Success:** {'Yes' if success else 'No'}\n\n"

            # Add timestamp
            time_modified = attempt.get('timemodified', 0)
            if time_modified > 0:
                time_str = datetime.fromtimestamp(time_modified).strftime('%Y-%m-%d %H:%M:%S')
                summary += f"**Time:** {time_str}\n\n"

            # Add detailed results if available
            results = attempt_data.get('results', [])
            if results:
                summary += "## Detailed Results\n\n"
                for idx, res in enumerate(results, 1):
                    description = res.get('description', 'Question')
                    response = res.get('response', '')
                    correct = res.get('correctpattern', '')

                    summary += f"### {idx}. {description}\n\n"
                    if response:
                        summary += f"**Your response:** {response}\n\n"
                    if correct:
                        summary += f"**Correct pattern:** {correct}\n\n"

            # Create attempt file
            result.append(
                {
                    'filename': PT.to_valid_name(f'Attempt_{attempt_num}', is_file=True),
                    'filepath': '/attempts/',
                    'timemodified': time_modified,
                    'description': summary,
                    'type': 'description',
                }
            )

        except Exception as e:
            logging.debug("Error getting results for attempt %d: %s", attempt_id, str(e))

        return result
