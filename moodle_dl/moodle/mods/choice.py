import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class ChoiceMod(MoodleMod):
    """
    Choice module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/choice/services/choice.ts

    Supports:
    - Choice activity settings and configuration
    - Available options (voting choices)
    - Results and statistics export
    - User responses tracking
    """

    MOD_NAME = 'choice'
    MOD_PLURAL_NAME = 'choices'
    MOD_MIN_VERSION = 2015111600  # 3.0

    # Show results constants
    RESULTS_NOT = 0  # Never show results
    RESULTS_AFTER_ANSWER = 1  # Show results after user answers
    RESULTS_AFTER_CLOSE = 2  # Show results after choice is closed
    RESULTS_ALWAYS = 3  # Always show results

    # Display mode constants
    DISPLAY_HORIZONTAL = 1
    DISPLAY_VERTICAL = 2

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_choices() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    def _get_show_results_name(self, show_results: int) -> str:
        """Get human-readable name for showresults setting"""
        names = {
            self.RESULTS_NOT: 'Never',
            self.RESULTS_AFTER_ANSWER: 'After answer',
            self.RESULTS_AFTER_CLOSE: 'After close',
            self.RESULTS_ALWAYS: 'Always',
        }
        return names.get(show_results, 'Unknown')

    def _get_display_mode_name(self, display: int) -> str:
        """Get human-readable name for display mode"""
        names = {
            self.DISPLAY_HORIZONTAL: 'Horizontal',
            self.DISPLAY_VERTICAL: 'Vertical',
        }
        return names.get(display, 'Unknown')

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Choice modules from courses

        Process:
        1. Get choices by courses
        2. Get choice options (available voting choices)
        3. Get choice results and statistics
        4. Export metadata and results
        """

        result = {}
        if not self.config.get_download_choices():
            return result

        # Get all choices for the courses
        try:
            response = await self.client.async_post(
                'mod_choice_get_choices_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            choices = response.get('choices', [])
        except RequestRejectedError:
            logging.debug("No access to Choice modules or WS not available")
            return result
        except Exception as e:
            logging.debug("Error getting Choice modules: %s", str(e))
            return result

        for choice in choices:
            course_id = choice.get('course', 0)
            module_id = choice.get('coursemodule', 0)
            choice_id = choice.get('id', 0)
            choice_name = choice.get('name', 'Choice')

            choice_files = []

            # Get choice options
            options_data = None
            try:
                options_response = await self.client.async_post(
                    'mod_choice_get_choice_options',
                    {'choiceid': choice_id},
                )
                options_data = options_response
            except Exception as e:
                logging.debug("Error getting choice options for choice %s: %s", choice_id, str(e))

            # Get choice results
            results_data = None
            try:
                results_response = await self.client.async_post(
                    'mod_choice_get_choice_results',
                    {'choiceid': choice_id},
                )
                results_data = results_response
            except Exception as e:
                logging.debug("Error getting choice results for choice %s: %s", choice_id, str(e))

            # Create options file if available
            if options_data and options_data.get('options'):
                options = options_data.get('options', [])
                options_content = self._format_options(choice_name, options)

                choice_files.append(
                    {
                        'filename': PT.to_valid_name('Options', is_file=True) + '.md',
                        'filepath': '/',
                        'content': options_content,
                        'type': 'content',
                        'timemodified': choice.get('timemodified', 0),
                    }
                )

            # Create results file if available
            if results_data and results_data.get('options'):
                results = results_data.get('options', [])
                results_content = self._format_results(choice_name, results)

                choice_files.append(
                    {
                        'filename': PT.to_valid_name('Results', is_file=True) + '.md',
                        'filepath': '/',
                        'content': results_content,
                        'type': 'content',
                        'timemodified': choice.get('timemodified', 0),
                    }
                )

            # Create comprehensive metadata file
            metadata = {
                'choice_id': choice_id,
                'course_id': course_id,
                'name': choice_name,
                'intro': choice.get('intro', ''),
                'settings': {
                    'publish': choice.get('publish', False),
                    'show_results': {
                        'value': choice.get('showresults', 0),
                        'name': self._get_show_results_name(choice.get('showresults', 0)),
                    },
                    'display_mode': {
                        'value': choice.get('display', 0),
                        'name': self._get_display_mode_name(choice.get('display', 0)),
                    },
                    'allow_update': choice.get('allowupdate', False),
                    'allow_multiple': choice.get('allowmultiple', False),
                    'limit_answers': choice.get('limitanswers', False),
                    'show_unavailable': choice.get('showunanswered', False),
                    'include_inactive_users': choice.get('includeinactive', False),
                },
                'timing': {
                    'time_open': choice.get('timeopen', 0),
                    'time_close': choice.get('timeclose', 0),
                },
                'timestamps': {
                    'time_created': choice.get('timecreated', 0),
                    'time_modified': choice.get('timemodified', 0),
                },
                'completion': choice.get('completionsubmit', False),
            }

            # Add options summary to metadata
            if options_data:
                metadata['options_summary'] = {
                    'total_options': len(options_data.get('options', [])),
                    'can_edit': options_data.get('canedit', False),
                    'can_delete': options_data.get('candelete', False),
                }

            # Add results summary to metadata
            if results_data:
                metadata['results_summary'] = {
                    'total_answers': sum(len(opt.get('userresponses', [])) for opt in results_data.get('options', [])),
                    'published_to_students': results_data.get('publishinfo', False),
                }

            choice_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': choice.get('timemodified', 0),
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': choice_id,
                    'name': choice_name,
                    'files': choice_files,
                },
            )

        return result

    def _format_options(self, choice_name: str, options: List[Dict]) -> str:
        """Format choice options as readable Markdown"""
        lines = [
            f"# {choice_name} - Available Options",
            "",
            "## Options",
            "",
        ]

        for idx, option in enumerate(options, 1):
            option_text = option.get('text', 'Unknown option')
            max_answers = option.get('maxanswers', 0)
            count_answers = option.get('countanswers', 0)
            disabled = option.get('disabled', False)

            lines.append(f"### {idx}. {option_text}")
            lines.append("")

            if max_answers > 0:
                lines.append(f"- **Limit:** {max_answers} answers")
                lines.append(f"- **Current:** {count_answers} / {max_answers}")
            else:
                lines.append(f"- **Current answers:** {count_answers}")

            if disabled:
                lines.append("- **Status:** Disabled (limit reached)")

            lines.append("")

        return '\n'.join(lines)

    def _format_results(self, choice_name: str, results: List[Dict]) -> str:
        """Format choice results as readable Markdown"""
        lines = [
            f"# {choice_name} - Results",
            "",
            "## Voting Statistics",
            "",
        ]

        total_responses = sum(len(opt.get('userresponses', [])) for opt in results)
        lines.append(f"**Total responses:** {total_responses}")
        lines.append("")

        for idx, option in enumerate(results, 1):
            option_text = option.get('text', 'Unknown option')
            user_responses = option.get('userresponses', [])
            count = len(user_responses)

            # Calculate percentage
            percentage = (count / total_responses * 100) if total_responses > 0 else 0

            lines.append(f"### {idx}. {option_text}")
            lines.append("")
            lines.append(f"- **Votes:** {count} ({percentage:.1f}%)")

            if user_responses:
                lines.append("- **Users:**")
                for user in user_responses:
                    fullname = user.get('fullname', 'Unknown user')
                    lines.append(f"  - {fullname}")

            lines.append("")

        return '\n'.join(lines)
