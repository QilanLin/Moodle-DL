import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class ResourceMod(MoodleMod):
    """
    Resource module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/resource/

    Resource is one of the most commonly used modules in Moodle for uploading
    files (documents, PDFs, videos, images, etc.). It supports various display
    modes and provides detailed file information.

    Supports:
    - File resources via mod_resource_get_resources_by_courses API
    - Complete metadata export (display mode, options, revision)
    - File details (size, type, dates, MIME type)
    - Display options parsing (showsize, showtype, showdate, printintro)
    - Multiple display modes (embed, frame, download, etc.)
    """

    MOD_NAME = 'resource'
    MOD_PLURAL_NAME = 'resources'
    MOD_MIN_VERSION = 2015111600  # 3.0 - mod_resource_get_resources_by_courses introduced

    # Display mode constants (RESOURCELIB_DISPLAY_*)
    DISPLAY_INVALID = -1  # Invalid (added by app)
    DISPLAY_AUTO = 0  # Automatic - try the best way
    DISPLAY_EMBED = 1  # Display using object tag
    DISPLAY_FRAME = 2  # Display inside frame
    DISPLAY_NEW = 3  # Display normal link in new window
    DISPLAY_DOWNLOAD = 4  # Force download of file
    DISPLAY_OPEN = 5  # Open directly
    DISPLAY_POPUP = 6  # Open in "emulated" pop-up without navigation

    DISPLAY_MODE_NAMES = {
        DISPLAY_INVALID: 'Invalid',
        DISPLAY_AUTO: 'Auto',
        DISPLAY_EMBED: 'Embed',
        DISPLAY_FRAME: 'Frame',
        DISPLAY_NEW: 'New Window',
        DISPLAY_DOWNLOAD: 'Download',
        DISPLAY_OPEN: 'Open',
        DISPLAY_POPUP: 'Popup',
    }

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_resources() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Resource modules from courses

        Uses mod_resource_get_resources_by_courses API to get complete metadata
        including display modes, options, and file details.

        Process:
        1. Call API to get all resources for courses
        2. Parse display options (serialized string)
        3. Extract file details from module contents
        4. Generate comprehensive metadata export
        """

        result = {}

        if not self.config.get_download_resources():
            return result

        # Get all resources via API
        response = await self.client.async_post(
            'mod_resource_get_resources_by_courses',
            self.get_data_for_mod_entries_endpoint(courses),
        )

        resources = response.get('resources', [])

        for resource in resources:
            course_id = resource.get('course', 0)
            module_id = resource.get('coursemodule', 0)
            resource_id = resource.get('id', 0)
            resource_name = resource.get('name', 'Resource')

            resource_files = []

            # Get intro/description if available
            intro = resource.get('intro', '')
            intro_file = self.create_intro_file(intro)
            if intro_file:
                resource_files.append(intro_file)

            # Get content files (the actual resource files)
            content_files = resource.get('contentfiles', [])
            for content_file in content_files:
                resource_files.append(
                    {
                        'filename': content_file.get('filename', 'file'),
                        'filepath': content_file.get('filepath', '/'),
                        'filesize': content_file.get('filesize', 0),
                        'fileurl': content_file.get('fileurl', ''),
                        'timemodified': content_file.get('timemodified', 0),
                        'mimetype': content_file.get('mimetype', ''),
                        'isexternalfile': content_file.get('isexternalfile', False),
                        'repositorytype': content_file.get('repositorytype', ''),
                        'type': 'resource_file',
                    }
                )

            # Get module from core_contents to access full contents info
            module_contents = self.get_module_in_core_contents(course_id, module_id, core_contents)
            if module_contents:
                # Add any additional contents not in contentfiles
                for content in module_contents:
                    # Check if already added
                    filename = content.get('filename', '')
                    if not any(f.get('filename') == filename for f in resource_files if f.get('type') == 'resource_file'):
                        resource_files.append(content)

            # Parse display options
            display_options_str = resource.get('displayoptions', '')
            display_options = self._parse_display_options(display_options_str)

            # Get file details
            file_details = self._get_file_details(content_files, display_options)

            # Create comprehensive metadata
            display_mode = resource.get('display', self.DISPLAY_AUTO)
            metadata = {
                'resource_id': resource_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': resource_name,
                'intro': intro,
                'display': {
                    'mode': display_mode,
                    'mode_name': self.DISPLAY_MODE_NAMES.get(display_mode, 'Unknown'),
                    'options': display_options,
                },
                'file_info': {
                    'total_files': len(content_files),
                    'total_size': sum(f.get('filesize', 0) for f in content_files),
                    'details': file_details,
                },
                'settings': {
                    'tobemigrated': resource.get('tobemigrated', 0),
                    'legacyfiles': resource.get('legacyfiles', 0),
                    'legacyfileslast': resource.get('legacyfileslast', 0),
                    'filterfiles': resource.get('filterfiles', 0),
                    'revision': resource.get('revision', 0),
                },
                'timestamps': {
                    'timemodified': resource.get('timemodified', 0),
                },
                'features': {
                    'archetype': 'resource',
                    'intro_support': True,
                    'completion_tracks_views': True,
                    'show_description': True,
                    'purpose': 'content',
                },
                'note': 'Resource is one of the most commonly used modules for file uploads. '
                + 'It supports various display modes (embed, frame, download, etc.) and provides '
                + 'detailed file information.',
            }

            resource_files.append(self.create_metadata_file(metadata))

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': module_id,
                    'name': resource_name,
                    'files': resource_files,
                },
            )

            logging.debug(
                f"Found resource module: {resource_name} (ID: {module_id}, "
                f"Display: {self.DISPLAY_MODE_NAMES.get(display_mode)}, "
                f"Files: {len(content_files)}) in course {course_id}"
            )

        return result

    def _parse_display_options(self, options_str: str) -> Dict:
        """
        Parse serialized display options string

        Format: a:4:{s:12:"printintro";i:1;s:10:"printlastmodified";i:1;...}

        Returns parsed options with defaults
        """
        if not options_str:
            return {}

        # This is PHP serialized data - try basic parsing
        options = {}

        try:
            # Simple parsing for common boolean options
            if 'printintro' in options_str:
                options['printintro'] = 'i:1' in options_str.split('printintro')[1][:20]
            if 'showsize' in options_str:
                options['showsize'] = 'i:1' in options_str.split('showsize')[1][:20]
            if 'showtype' in options_str:
                options['showtype'] = 'i:1' in options_str.split('showtype')[1][:20]
            if 'showdate' in options_str:
                options['showdate'] = 'i:1' in options_str.split('showdate')[1][:20]
            if 'printlastmodified' in options_str:
                options['printlastmodified'] = 'i:1' in options_str.split('printlastmodified')[1][:20]

            # Store raw string for reference
            options['_raw'] = options_str

        except Exception as e:
            logging.debug(f"Error parsing display options: {e}")
            options['_raw'] = options_str
            options['_parse_error'] = str(e)

        return options

    def _get_file_details(self, content_files: List[Dict], display_options: Dict) -> Dict:
        """
        Generate file details based on display options and content files

        Includes: size, type, mimetype, extension, dates
        """
        if not content_files:
            return {}

        main_file = content_files[0]
        details = {}

        # File size (if showsize option is enabled or by default)
        if display_options.get('showsize', True):
            total_size = sum(f.get('filesize', 0) for f in content_files)
            details['size_bytes'] = total_size
            details['size_human'] = self._bytes_to_human(total_size)

        # File type/mimetype (if showtype option is enabled or by default)
        if display_options.get('showtype', True):
            mimetype = main_file.get('mimetype', '')
            filename = main_file.get('filename', '')

            details['mimetype'] = mimetype
            details['extension'] = filename.split('.')[-1] if '.' in filename else ''

            # Get mimetype description
            if mimetype:
                details['type_description'] = self._get_mimetype_description(mimetype)

        # Dates (if showdate option is enabled)
        if display_options.get('showdate', False):
            timemodified = main_file.get('timemodified', 0)
            timecreated = main_file.get('timecreated', 0)

            # If modified significantly after creation (more than 5 minutes), show modified date
            if timemodified > timecreated + 300:
                details['modified_date'] = timemodified
            elif timecreated:
                details['uploaded_date'] = timecreated

        # Repository info
        repository_type = main_file.get('repositorytype', '')
        if repository_type:
            details['repository_type'] = repository_type
            details['is_external'] = main_file.get('isexternalfile', False)

        # File reference info
        if main_file.get('isexternalfile', False):
            details['is_reference'] = True
            details['note'] = 'External file - size and date may not be cached'

        return details

    def _bytes_to_human(self, bytes_size: int) -> str:
        """Convert bytes to human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.1f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.1f} PB"

    def _get_mimetype_description(self, mimetype: str) -> str:
        """Get human-readable description for MIME type"""
        descriptions = {
            'application/pdf': 'PDF document',
            'application/msword': 'Word document',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word document (DOCX)',
            'application/vnd.ms-excel': 'Excel spreadsheet',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'Excel spreadsheet (XLSX)',
            'application/vnd.ms-powerpoint': 'PowerPoint presentation',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PowerPoint presentation (PPTX)',
            'application/zip': 'ZIP archive',
            'text/html': 'HTML document',
            'text/plain': 'Text file',
            'image/jpeg': 'JPEG image',
            'image/png': 'PNG image',
            'image/gif': 'GIF image',
            'video/mp4': 'MP4 video',
            'audio/mpeg': 'MP3 audio',
        }
        return descriptions.get(mimetype, mimetype)
