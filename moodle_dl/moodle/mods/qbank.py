import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class QbankMod(MoodleMod):
    """
    Question Bank (qbank) module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/qbank/

    Note: The official Moodle Mobile App does NOT support qbank modules
    (they are hidden with CAN_DISPLAY: false). However, qbank modules exist
    in Moodle as question bank containers that publish and use questions.

    This implementation extracts basic metadata from core_course_get_contents
    to provide informational exports about question banks.

    Supports:
    - Question bank metadata from core contents
    - Description and introduction
    - Question bank purpose and features documentation
    """

    MOD_NAME = 'qbank'
    MOD_PLURAL_NAME = 'qbanks'
    MOD_MIN_VERSION = 2023100900  # 4.3 - when qbank was introduced

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_qbanks() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Question Bank modules from courses

        Note: Qbank does not have a dedicated WebService API. It is a question
        bank container module that appears in core_course_get_contents.
        The official Moodle Mobile App doesn't support viewing qbanks.

        Process:
        1. Iterate through core_contents to find qbank modules
        2. Extract metadata and description
        3. Create informational metadata file about the question bank
        """

        result = {}

        if not self.config.get_download_qbanks():
            return result

        # Qbank modules are found in core_contents, not via a separate API
        for course in courses:
            course_id = course.id
            course_sections = core_contents.get(course_id, [])

            for section in course_sections:
                section_modules = section.get('modules', [])

                for module in section_modules:
                    module_modname = module.get('modname', '')

                    if module_modname != self.MOD_NAME:
                        continue

                    module_id = module.get('id', 0)
                    qbank_name = module.get('name', 'Question Bank')
                    qbank_description = module.get('description', '')

                    qbank_files = []

                    # Get qbank intro/description if available
                    if qbank_description:
                        qbank_files.append(
                            {
                                'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                                'filepath': '/',
                                'description': qbank_description,
                                'type': 'description',
                                'timemodified': 0,
                            }
                        )

                    # Get contents if any (though qbanks typically don't have downloadable files)
                    qbank_contents = module.get('contents', [])
                    if qbank_contents:
                        qbank_files.extend(qbank_contents)

                    # Create metadata about the question bank
                    metadata = {
                        'qbank_id': module_id,
                        'course_id': course_id,
                        'name': qbank_name,
                        'description': qbank_description,
                        'section_reference': {
                            'section_id': section.get('id', 0),
                            'section_name': section.get('name', ''),
                        },
                        'module_info': {
                            'visible': module.get('visible', 1),
                            'visibleoncoursepage': module.get('visibleoncoursepage', 1),
                            'uservisible': module.get('uservisible', True),
                            'availabilityinfo': module.get('availabilityinfo', ''),
                        },
                        'features': {
                            'publishes_questions': True,
                            'uses_questions': True,
                            'show_description': True,
                            'can_display_in_app': False,
                            'no_view_link': True,
                            'purpose': 'content',
                        },
                        'compatibility': {
                            'moodle_version': '4.3+',
                            'mobile_app_support': False,
                            'note': 'Official Moodle Mobile App does not support viewing qbank modules',
                        },
                        'note': 'Question Bank (qbank) is a module that serves as a container for questions. '
                        + 'It publishes and uses questions but does not have a dedicated viewing interface '
                        + 'in the Moodle Mobile App. This export provides metadata about the question bank.',
                    }

                    qbank_files.append(
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
                            'id': module_id,
                            'name': qbank_name,
                            'files': qbank_files,
                        },
                    )

                    logging.debug(f"Found qbank module: {qbank_name} (ID: {module_id}) in course {course_id}")

        return result
