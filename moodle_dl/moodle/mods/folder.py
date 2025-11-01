import json
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class FolderMod(MoodleMod):
    MOD_NAME = 'folder'
    MOD_PLURAL_NAME = 'folders'
    MOD_MIN_VERSION = 2017051500  # 3.3

    # Display mode constants for folder
    DISPLAY_MODES = {
        0: {'name': 'INLINE', 'description': 'Display folder contents inline on course page'},
        1: {'name': 'SEPARATE', 'description': 'Display folder on separate page'},
    }

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        # TODO: Add download condition
        return True

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        folders = (
            await self.client.async_post(
                'mod_folder_get_folders_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('folders', [])

        result = {}
        for folder in folders:
            course_id = folder.get('course', 0)
            module_id = folder.get('coursemodule', 0)
            folder_id = folder.get('id', 0)
            folder_name = folder.get('name', 'unnamed folder')
            folder_intro = folder.get('intro', '')
            folder_time_modified = folder.get('timemodified', 0)

            folder_files = self.get_introfiles(folder, 'folder_file')

            intro_file = self.create_intro_file(folder_intro, folder_time_modified)
            if intro_file:
                intro_file['filter_urls_during_search_containing'] = ['/mod_folder/intro']
                folder_files.append(intro_file)

            # Get folder contents from core_contents
            folder_contents = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])
            folder_files += folder_contents

            # Parse display mode
            display_mode = folder.get('display', 0)
            display_mode_info = self.DISPLAY_MODES.get(display_mode, {
                'name': f'UNKNOWN_{display_mode}',
                'description': f'Unknown display mode {display_mode}'
            })

            # Create comprehensive metadata
            metadata = {
                'folder_id': folder_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': folder_name,
                'intro': folder_intro,
                'settings': {
                    'revision': folder.get('revision', 1),
                    'display': display_mode,
                    'display_mode_name': display_mode_info['name'],
                    'display_mode_description': display_mode_info['description'],
                    'showexpanded': folder.get('showexpanded', 1),
                    'showdownloadfolder': folder.get('showdownloadfolder', 1),
                    'forcedownload': folder.get('forcedownload', 1),
                },
                'file_count': len(folder_contents),
                'download_options': {
                    'can_download_folder': folder.get('showdownloadfolder', 1) == 1,
                    'folder_zip_available': folder.get('showdownloadfolder', 1) == 1,
                    'force_download_files': folder.get('forcedownload', 1) == 1,
                },
                'timestamps': {
                    'timemodified': folder_time_modified,
                },
                'features': self.get_features(purpose='content'),
                'note': 'Folder is a simple container for organizing files. '
                + 'This export includes all files, folder settings, and display mode documentation.',
            }

            folder_files.append(self.create_metadata_file(metadata))

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': folder_id,
                    'name': folder_name,
                    'timemodified': folder_time_modified,
                    'files': folder_files,
                },
            )

        return result
