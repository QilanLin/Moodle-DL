import json
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class PageMod(MoodleMod):
    MOD_NAME = 'page'
    MOD_PLURAL_NAME = 'pages'
    MOD_MIN_VERSION = 2017051500  # 3.3

    # Display mode constants (RESOURCELIB_DISPLAY_*)
    DISPLAY_MODES = {
        0: {'name': 'AUTOMATIC', 'description': 'Automatic - best option for file type'},
        1: {'name': 'EMBED', 'description': 'Embed - display in page'},
        2: {'name': 'FRAME', 'description': 'Open in frame'},
        3: {'name': 'NEW', 'description': 'Open in new window'},
        4: {'name': 'DOWNLOAD', 'description': 'Force download'},
        5: {'name': 'OPEN', 'description': 'Open directly (default for page)'},
        6: {'name': 'POPUP', 'description': 'Open in popup window'},
    }

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        # TODO: Add download condition
        return True

    @staticmethod
    def _parse_display_options(displayoptions_str: str) -> Dict:
        """
        Parse display options string into structured dictionary

        Format: "printintro=1,printlastmodified=1,popupwidth=620,popupheight=450"
        """
        if not displayoptions_str:
            return {}

        options = {}
        for pair in displayoptions_str.split(','):
            if '=' in pair:
                key, value = pair.split('=', 1)
                key = key.strip()
                value = value.strip()

                # Try to convert to int if possible
                try:
                    options[key] = int(value)
                except ValueError:
                    options[key] = value

        return options

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        pages = (
            await self.client.async_post(
                'mod_page_get_pages_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('pages', [])

        result = {}
        for page in pages:
            course_id = page.get('course', 0)
            module_id = page.get('coursemodule', 0)
            page_id = page.get('id', 0)
            page_name = page.get('name', 'unnamed page')
            page_content = page.get('content', '')
            page_intro = page.get('intro', '')

            page_files = page.get('introfiles', [])
            page_files += page.get('contentfiles', [])
            self.set_props_of_files(page_files, type='page_file')

            if page_intro != '':
                page_files.append(
                    {
                        'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                        'filepath': '/',
                        'description': page_intro,
                        'type': 'description',
                        'timemodified': 0,
                    }
                )

            if page_content != '':
                page_files.append(
                    {
                        'filename': page_name,
                        'filepath': '/',
                        'html': page_content,
                        'filter_urls_during_search_containing': ['/mod_page/content/'],
                        'no_hash': True,
                        'type': 'html',
                        'timemodified': page.get('timemodified', 0),
                        'filesize': len(page_content),
                    }
                )

            # Parse display mode and options
            display_mode = page.get('display', 5)
            display_mode_info = self.DISPLAY_MODES.get(display_mode, {
                'name': f'UNKNOWN_{display_mode}',
                'description': f'Unknown display mode {display_mode}'
            })

            displayoptions_str = page.get('displayoptions', '')
            displayoptions_parsed = self._parse_display_options(displayoptions_str)

            # Create comprehensive metadata
            metadata = {
                'page_id': page_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': page_name,
                'intro': page_intro,
                'content': page_content,
                'settings': {
                    'contentformat': page.get('contentformat', 1),
                    'legacyfiles': page.get('legacyfiles', 0),
                    'legacyfileslast': page.get('legacyfileslast'),
                    'display': display_mode,
                    'display_mode_name': display_mode_info['name'],
                    'display_mode_description': display_mode_info['description'],
                    'displayoptions_raw': displayoptions_str,
                    'displayoptions_parsed': displayoptions_parsed,
                    'revision': page.get('revision', 1),
                    'printheading': page.get('printheading', 1),
                    'printlastmodified': page.get('printlastmodified', 1),
                },
                'timestamps': {
                    'timemodified': page.get('timemodified', 0),
                },
                'features': {
                    'groups': True,
                    'groupings': True,
                    'intro_support': True,
                    'completion_tracks_views': True,
                    'grade_has_grade': False,
                    'grade_outcomes': True,
                    'backup_moodle2': True,
                    'show_description': True,
                    'purpose': 'content',
                },
                'note': 'Page is a simple content module for displaying HTML content. '
                + 'This export includes the full HTML content, settings, and parsed display options.',
            }

            page_files.append(
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
                    'id': page_id,
                    'name': page_name,
                    'files': page_files,
                },
            )

        return result
