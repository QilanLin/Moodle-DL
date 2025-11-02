import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class UrlMod(MoodleMod):
    """
    URL module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/url/services/url.ts

    Supports:
    - URL metadata export (display options, parameters)
    - Link shortcuts creation
    - External URL tracking
    """

    MOD_NAME = 'url'
    MOD_PLURAL_NAME = 'urls'
    MOD_MIN_VERSION = 2015111600  # 3.0

    # Display types based on Moodle constants
    DISPLAY_OPEN = 0  # Open in same window
    DISPLAY_POPUP = 1  # Open in popup
    DISPLAY_EMBED = 2  # Embed in page
    DISPLAY_DOWNLOAD = 3  # Force download
    DISPLAY_AUTO = 5  # Automatic

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_urls() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all URL modules from courses

        Process:
        1. Get URLs by courses
        2. Export URL metadata
        3. Create link shortcuts (handled by downloader)
        """

        result = {}
        if not self.config.get_download_urls():
            return result

        # Get all URLs for the courses
        try:
            response = await self.client.async_post(
                'mod_url_get_urls_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            urls = response.get('urls', [])
        except RequestRejectedError:
            logging.debug("No access to URL modules or WS not available")
            return result
        except Exception as e:
            logging.debug("Error getting URL modules: %s", str(e))
            return result

        for url_mod in urls:
            course_id = url_mod.get('course', 0)
            module_id = url_mod.get('coursemodule', 0)
            url_name = url_mod.get('name', 'unnamed url')

            # Get intro files
            url_files = self.get_introfiles(url_mod, 'url_introfile')

            # Add intro description
            url_intro = url_mod.get('intro', '')
            intro_file = self.create_intro_file(url_intro)
            if intro_file:
                url_files.append(intro_file)

            # Get the external URL
            external_url = url_mod.get('externalurl', '')

            # Create metadata file
            display_type = url_mod.get('display', self.DISPLAY_AUTO)
            display_options = url_mod.get('displayoptions', '')
            parameters = url_mod.get('parameters', '')

            metadata = {
                'url_id': url_mod.get('id', 0),
                'course_id': course_id,
                'name': url_name,
                'external_url': external_url,
                'display': {
                    'type': display_type,
                    'type_name': self._get_display_type_name(display_type),
                    'options': self._parse_display_options(display_options),
                },
                'parameters': self._parse_parameters(parameters),
                'timestamps': {
                    'time_modified': url_mod.get('timemodified', 0),
                },
                'features': self.get_features(purpose='content'),
                'note': 'URL module provides links to external resources. '
                + 'This export includes URL metadata, display settings, and parameters.',
            }

            url_files.append(
                self.create_metadata_file(metadata, timemodified=url_mod.get('timemodified', 0))
            )

            # Get module from core_contents to access URL files
            # URL modules in core_course_get_contents contain the actual file URL in contents
            module_contents = self.get_module_in_core_contents(course_id, module_id, core_contents)
            if module_contents:
                # Add URL file contents (the actual external files to download)
                for content in module_contents.get('contents', []):
                    # URL modules have type='url' in their contents
                    # These should be downloaded if download_urls is enabled
                    filename = content.get('filename', '')
                    if filename and content.get('type') == 'url':
                        # Add the URL file for download
                        url_files.append(content)

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': url_mod.get('id', 0),
                    'name': url_name,
                    'files': url_files,
                },
            )

        return result

    def _get_display_type_name(self, display_type: int) -> str:
        """
        Get human-readable name for display type

        @param display_type: Display type constant
        @return: Display type name
        """
        display_names = {
            self.DISPLAY_OPEN: 'Open',
            self.DISPLAY_POPUP: 'Popup',
            self.DISPLAY_EMBED: 'Embed',
            self.DISPLAY_DOWNLOAD: 'Download',
            self.DISPLAY_AUTO: 'Automatic',
        }
        return display_names.get(display_type, 'Unknown')

    def _parse_display_options(self, display_options: str) -> Dict:
        """
        Parse display options string into dictionary

        Display options are stored as URL parameters like: "width=620&height=450&printintro=1"

        @param display_options: Display options string
        @return: Parsed options dictionary
        """
        if not display_options:
            return {}

        options = {}
        try:
            # Parse URL-encoded parameters
            pairs = display_options.split('&')
            for pair in pairs:
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    # Try to convert to appropriate type
                    if value.isdigit():
                        options[key] = int(value)
                    elif value.lower() in ('true', 'false'):
                        options[key] = value.lower() == 'true'
                    else:
                        options[key] = value
        except Exception as e:
            logging.debug("Error parsing display options '%s': %s", display_options, str(e))

        return options

    def _parse_parameters(self, parameters: str) -> Dict:
        """
        Parse URL parameters string into dictionary

        Parameters are stored in serialized format. In Moodle, this is typically:
        - PHP serialized array (e.g., "a:1:{s:4:\"name\";s:5:\"value\";}")
        - URL parameter format (e.g., "param1=value1&param2=value2")
        - Empty string if no parameters

        @param parameters: Parameters string
        @return: Parsed parameters dictionary or structured representation
        """
        if not parameters:
            return {}

        # Try to parse as URL-encoded parameters first
        if '=' in parameters and not parameters.startswith('a:'):
            parsed = {}
            try:
                pairs = parameters.split('&')
                for pair in pairs:
                    if '=' in pair:
                        key, value = pair.split('=', 1)
                        # URL decode and type conversion
                        if value.isdigit():
                            parsed[key] = int(value)
                        elif value.lower() in ('true', 'false'):
                            parsed[key] = value.lower() == 'true'
                        else:
                            parsed[key] = value
                return parsed
            except Exception as e:
                logging.debug("Error parsing URL parameters '%s': %s", parameters, str(e))

        # If it's PHP serialized or other format, return as structured metadata
        if parameters.startswith('a:'):
            return {
                'format': 'php_serialized',
                'raw': parameters,
                'note': 'PHP serialized data - requires PHP unserialize for parsing'
            }

        # Return raw string in structured format for unknown formats
        return {
            'format': 'unknown',
            'raw': parameters,
        }
