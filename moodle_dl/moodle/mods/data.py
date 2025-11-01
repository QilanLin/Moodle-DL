import json
import logging
from datetime import datetime
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class DataMod(MoodleMod):
    MOD_NAME = 'data'
    MOD_PLURAL_NAME = 'databases'
    MOD_MIN_VERSION = 2015051100  # 2.9

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_databases() or file.content_type != 'database_file'

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        databases = (
            await self.client.async_post(
                'mod_data_get_databases_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('databases', [])

        result = {}
        for database in databases:
            course_id = database.get('course', 0)
            module_id = database.get('coursemodule', 0)
            database_id = database.get('id', 0)
            database_name = database.get('name', 'db')
            database_intro = database.get('intro', '')

            database_files = self.get_introfiles(database, 'database_introfile')

            intro_file = self.create_intro_file(database_intro)
            if intro_file:
                database_files.append(intro_file)

            # Get field definitions (schema) for this database
            fields_data = await self._get_database_fields(database_id)

            # Create comprehensive database metadata
            metadata = {
                'database_id': database_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': database_name,
                'intro': database_intro,
                'schema': {
                    'fields': fields_data,
                    'field_count': len(fields_data),
                },
                'settings': {
                    # Entries settings
                    'requiredentries': database.get('requiredentries', 0),
                    'requiredentriestoview': database.get('requiredentriestoview', 0),
                    'maxentries': database.get('maxentries', 0),
                    # Approval and comments
                    'approval': database.get('approval', 0),
                    'comments': database.get('comments', 0),
                    'manageapproved': database.get('manageapproved', 0),
                    # Timing
                    'timeavailablefrom': database.get('timeavailablefrom', 0),
                    'timeavailableto': database.get('timeavailableto', 0),
                    'timeviewfrom': database.get('timeviewfrom', 0),
                    'timeviewto': database.get('timeviewto', 0),
                    # Display settings
                    'defaultsort': database.get('defaultsort', 0),
                    'defaultsortdir': database.get('defaultsortdir', 0),
                    # Rating settings
                    'assessed': database.get('assessed', 0),
                    'assesstimestart': database.get('assesstimestart', 0),
                    'assesstimefinish': database.get('assesstimefinish', 0),
                    'scale': database.get('scale', 1),
                    # Notification settings
                    'notification': database.get('notification', 0),
                },
                'timestamps': {
                    'timemodified': database.get('timemodified', 0),
                },
                'features': self.get_features(
                    purpose='collaboration',
                    completion_tracks_views=False,
                    grade_has_grade=True
                ),
                'note': 'Database is a structured data collection module. '
                + 'This export includes schema definition (field types), all settings, and all entries with their metadata.',
            }

            database_files.append(self.create_metadata_file(metadata))

            # Export schema as separate file if available
            if fields_data:
                database_files.append(
                    {
                        'filename': 'schema.json',
                        'filepath': '/',
                        'timemodified': 0,
                        'content': json.dumps(fields_data, indent=2, ensure_ascii=False),
                        'type': 'content',
                    }
                )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': database_id,
                    'name': database_name,
                    'files': database_files,
                },
            )

        await self.add_database_files(result)
        return result

    async def add_database_files(self, databases: Dict[int, Dict[int, Dict]]):
        """
        Fetches for the databases list the database file entries.
        @param databases: Dictionary of all databases, indexed by courses, then module id
        """
        if not self.config.get_download_databases():
            return

        if self.version < 2017051500:  # 3.3
            return

        await self.run_async_load_function_on_mod_entries(databases, self.load_database_files)

    async def load_database_files(self, database: Dict):
        """
        Fetches for a given database the database files and metadata

        Improvement based on official Moodle Mobile App:
        - Downloads entry files
        - Exports entry metadata (author, timestamps, approval status, ratings, tags)
        - Creates JSON metadata files for each entry
        """
        data = {'databaseid': database.get('id', 0)}
        access = await self.client.async_post('mod_data_get_data_access_information', data)
        if not access.get('timeavailable', False):
            logging.debug("No access rights for database %d", database.get('id', 0))
            return

        data.update({'returncontents': 1})
        entries = await self.client.async_post('mod_data_get_entries', data)
        database['files'] += self._get_files_of_db_entries(entries)

    @classmethod
    def _get_files_of_db_entries(cls, entries: Dict) -> List:
        """
        Extract files and metadata from database entries

        Based on official Moodle Mobile App implementation.
        Creates:
        - Entry files (images, documents, etc.)
        - Metadata JSON files with entry information
        - Human-readable metadata descriptions
        """
        result = []
        entry_list = entries.get('entries', [])

        for entry in entry_list:
            entry_id = entry.get('id', 0)
            entry_name = f"Entry {entry_id}"

            # Extract metadata
            metadata = {
                'entry_id': entry_id,
                'database_id': entry.get('dataid', 0),
                'author': {
                    'user_id': entry.get('userid', 0),
                    'full_name': entry.get('fullname', 'Unknown'),
                },
                'group_id': entry.get('groupid', 0),
                'timestamps': {
                    'created': entry.get('timecreated', 0),
                    'created_readable': datetime.fromtimestamp(entry.get('timecreated', 0)).strftime('%Y-%m-%d %H:%M:%S')
                    if entry.get('timecreated', 0) > 0
                    else 'N/A',
                    'modified': entry.get('timemodified', 0),
                    'modified_readable': datetime.fromtimestamp(entry.get('timemodified', 0)).strftime('%Y-%m-%d %H:%M:%S')
                    if entry.get('timemodified', 0) > 0
                    else 'N/A',
                },
                'status': {
                    'approved': entry.get('approved', False),
                    'can_manage': entry.get('canmanageentry', False),
                },
                'tags': entry.get('tags', []),
            }

            # Add rating info if available
            if 'ratinginfo' in entries:
                metadata['rating'] = entries.get('ratinginfo', {})

            # Create JSON metadata file
            result.append({
                'filename': PT.to_valid_name(f"{entry_name}_metadata", is_file=True) + '.json',
                'filepath': f'/entry_{entry_id}/',
                'timemodified': entry.get('timemodified', 0),
                'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                'type': 'content',
            })

            # Create human-readable metadata description
            metadata_text = f"# Database Entry {entry_id}\n\n"
            metadata_text += f"## Author\n"
            metadata_text += f"- Name: {metadata['author']['full_name']}\n"
            metadata_text += f"- User ID: {metadata['author']['user_id']}\n\n"
            metadata_text += f"## Timestamps\n"
            metadata_text += f"- Created: {metadata['timestamps']['created_readable']}\n"
            metadata_text += f"- Modified: {metadata['timestamps']['modified_readable']}\n\n"
            metadata_text += f"## Status\n"
            metadata_text += f"- Approved: {'Yes' if metadata['status']['approved'] else 'No'}\n"
            metadata_text += f"- Group ID: {metadata['group_id']}\n\n"

            if metadata['tags']:
                metadata_text += f"## Tags\n"
                for tag in metadata['tags']:
                    tag_name = tag.get('displayname', tag.get('rawname', 'Unknown'))
                    metadata_text += f"- {tag_name}\n"
                metadata_text += "\n"

            result.append({
                'filename': PT.to_valid_name(f"{entry_name}_info", is_file=True),
                'filepath': f'/entry_{entry_id}/',
                'timemodified': entry.get('timemodified', 0),
                'description': metadata_text,
                'type': 'description',
            })

            # Extract entry files
            for entry_content in entry.get('contents', []):
                for entry_file in entry_content.get('files', []):
                    filename = entry_file.get('filename', '')
                    if filename.startswith('thumb_'):
                        continue

                    # Place files in entry-specific subdirectory
                    entry_file['filepath'] = f'/entry_{entry_id}' + entry_file.get('filepath', '/')
                    cls.set_props_of_file(entry_file, type='database_file')
                    result.append(entry_file)

        return result

    async def _get_database_fields(self, database_id: int) -> List[Dict]:
        """
        Get field definitions (schema) for a database

        Returns list of field data including type, name, description, and parameters
        """
        try:
            response = await self.client.async_post(
                'mod_data_get_fields',
                {'databaseid': database_id}
            )
            fields = response.get('fields', [])
            return [
                {
                    'id': field.get('id', 0),
                    'dataid': field.get('dataid', 0),
                    'type': field.get('type', ''),
                    'name': field.get('name', ''),
                    'description': field.get('description', ''),
                    'required': field.get('required', 0),
                    'param1': field.get('param1', ''),
                    'param2': field.get('param2', ''),
                    'param3': field.get('param3', ''),
                }
                for field in fields
            ]
        except Exception as e:
            logging.debug(f"Could not fetch fields for database {database_id}: {e}")
            return []
