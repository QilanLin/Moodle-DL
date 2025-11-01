import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class LabelMod(MoodleMod):
    """
    Label module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/label/services/label.ts

    Supports:
    - Label HTML content export
    - Embedded media and attachments
    - Text and image display
    """

    MOD_NAME = 'label'
    MOD_PLURAL_NAME = 'labels'
    MOD_MIN_VERSION = 2015111600  # 3.0

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_labels() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Label modules from courses

        Process:
        1. Get labels by courses
        2. Export label HTML content
        3. Download embedded media and attachments
        """

        result = {}
        if not self.config.get_download_labels():
            return result

        # Get all labels for the courses
        try:
            response = await self.client.async_post(
                'mod_label_get_labels_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            labels = response.get('labels', [])
        except RequestRejectedError:
            logging.debug("No access to Label modules or WS not available")
            return result
        except Exception as e:
            logging.debug("Error getting Label modules: %s", str(e))
            return result

        for label in labels:
            course_id = label.get('course', 0)
            module_id = label.get('coursemodule', 0)
            label_name = label.get('name', 'Label')

            # Get intro files (embedded media and attachments)
            # Copy the list to avoid modifying the original label dict
            label_files = self.get_introfiles(label, 'label_file', copy=True)

            # Get the HTML content
            label_intro = label.get('intro', '')

            # Create HTML/Markdown content file
            if label_intro:
                # Clean up the label name for filename
                safe_name = PT.to_valid_name(label_name if label_name != 'Label' else 'Content', is_file=True)

                label_files.append(
                    {
                        'filename': safe_name,
                        'filepath': '/',
                        'description': label_intro,
                        'type': 'description',
                        'timemodified': label.get('timemodified', 0),
                    }
                )

            # Create metadata file
            metadata = {
                'label_id': label.get('id', 0),
                'course_id': course_id,
                'name': label_name,
                'timestamps': {
                    'time_modified': label.get('timemodified', 0),
                },
                'content_length': len(label_intro),
                'has_files': len(label.get('introfiles', [])) > 0,
            }

            label_files.append(
                self.create_metadata_file(metadata, timemodified=label.get('timemodified', 0))
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': label.get('id', 0),
                    'name': label_name,
                    'files': label_files,
                },
            )

        return result
