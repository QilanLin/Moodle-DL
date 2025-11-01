import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class SubsectionMod(MoodleMod):
    """
    Subsection module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/subsection/

    Subsection is a navigation module introduced in Moodle 4.3 for organizing
    course content into nested sections. It does not have its own WebService API
    or file content, but is accessed through core_course_get_contents.

    Supports:
    - Subsection metadata from core contents
    - Section reference information
    - Basic description and introduction
    """

    MOD_NAME = 'subsection'
    MOD_PLURAL_NAME = 'subsections'
    MOD_MIN_VERSION = 2023100900  # 4.3

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_subsections() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Subsection modules from courses

        Note: Subsection does not have a dedicated WebService API like other modules.
        It is a navigation/organization module that appears in core_course_get_contents
        but doesn't contain downloadable files. We extract its metadata from core_contents.

        Process:
        1. Iterate through core_contents to find subsection modules
        2. Extract metadata and section reference
        3. Create informational metadata file
        """

        result = {}

        if not self.config.get_download_subsections():
            return result

        # Subsection modules are found in core_contents, not via a separate API
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
                    subsection_name = module.get('name', 'Subsection')
                    subsection_description = module.get('description', '')

                    subsection_files = []

                    # Get subsection intro/description if available
                    if subsection_description:
                        subsection_files.append(
                            {
                                'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                                'filepath': '/',
                                'description': subsection_description,
                                'type': 'description',
                                'timemodified': 0,
                            }
                        )

                    # Get contents if any (though subsections typically don't have files)
                    subsection_contents = module.get('contents', [])
                    if subsection_contents:
                        subsection_files.extend(subsection_contents)

                    # Create metadata about the subsection
                    # Get target section if available (the section this subsection links to)
                    section_id = section.get('id', 0)
                    target_section_id = module.get('target', section_id)

                    metadata = {
                        'subsection_id': module_id,
                        'course_id': course_id,
                        'name': subsection_name,
                        'description': subsection_description,
                        'section_reference': {
                            'section_id': section_id,
                            'section_name': section.get('name', ''),
                            'target_section_id': target_section_id,
                        },
                        'module_info': {
                            'visible': module.get('visible', 1),
                            'visibleoncoursepage': module.get('visibleoncoursepage', 1),
                            'uservisible': module.get('uservisible', True),
                            'availabilityinfo': module.get('availabilityinfo', ''),
                        },
                        'navigation': {
                            'purpose': 'Course content organization and navigation',
                            'type': 'Nested section marker',
                            'moodle_version': '4.3+',
                        },
                        'note': 'Subsection is a navigation module that organizes course content into nested sections. '
                        + 'It does not contain files itself but serves as a structural element.',
                    }

                    subsection_files.append(
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
                            'name': subsection_name,
                            'files': subsection_files,
                        },
                    )

                    logging.debug(
                        f"Found subsection module: {subsection_name} (ID: {module_id}) in course {course_id}"
                    )

        return result
