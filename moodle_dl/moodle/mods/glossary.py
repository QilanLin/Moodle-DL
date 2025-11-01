import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class GlossaryMod(MoodleMod):
    """
    Glossary module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/glossary/services/glossary.ts

    Supports:
    - Downloading all glossary entries (terms and definitions)
    - Entry attachments (images, documents, etc.)
    - Categorized entries
    - Author information
    - Tags
    """

    MOD_NAME = 'glossary'
    MOD_PLURAL_NAME = 'glossaries'
    MOD_MIN_VERSION = 2015111600  # 3.0

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_glossaries() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all glossary entries from courses

        Process:
        1. Get glossaries by courses
        2. For each glossary, get all entries
        3. Download entry definitions and attachments
        """

        result = {}
        if not self.config.get_download_glossaries():
            return result

        # Get all glossaries for the courses
        glossaries = (
            await self.client.async_post(
                'mod_glossary_get_glossaries_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('glossaries', [])

        for glossary in glossaries:
            course_id = glossary.get('course', 0)
            module_id = glossary.get('coursemodule', 0)
            glossary_id = glossary.get('id', 0)
            glossary_name = glossary.get('name', 'unnamed glossary')
            glossary_intro = glossary.get('intro', '')

            # Get glossary intro files
            glossary_files = glossary.get('introfiles', [])
            self.set_props_of_files(glossary_files, type='glossary_introfile')

            # Add glossary intro as description
            if glossary_intro != '':
                glossary_files.append(
                    {
                        'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                        'filepath': '/',
                        'description': glossary_intro,
                        'type': 'description',
                        'timemodified': 0,
                    }
                )

            # Get categories for this glossary
            categories_data = await self._get_glossary_categories(glossary_id)

            # Create comprehensive glossary metadata
            metadata = {
                'glossary_id': glossary_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': glossary_name,
                'intro': glossary_intro,
                'settings': {
                    # Display settings
                    'allowduplicatedentries': glossary.get('allowduplicatedentries', 0),
                    'displayformat': glossary.get('displayformat', 'dictionary'),
                    'mainglossary': glossary.get('mainglossary', 0),
                    'showspecial': glossary.get('showspecial', 1),
                    'showalphabet': glossary.get('showalphabet', 1),
                    'showall': glossary.get('showall', 1),
                    # Permissions
                    'allowcomments': glossary.get('allowcomments', 0),
                    'allowprintview': glossary.get('allowprintview', 1),
                    'usedynalink': glossary.get('usedynalink', 1),
                    'defaultapproval': glossary.get('defaultapproval', 1),
                    'globalglossary': glossary.get('globalglossary', 0),
                    'editalways': glossary.get('editalways', 0),
                    # Display options
                    'entbypage': glossary.get('entbypage', 10),
                    'approvaldisplayformat': glossary.get('approvaldisplayformat', 'default'),
                    # Browse modes
                    'browsemodes': glossary.get('browsemodes', []),
                    'canaddentry': glossary.get('canaddentry', 0),
                },
                'rss': {
                    'rsstype': glossary.get('rsstype', 0),
                    'rssarticles': glossary.get('rssarticles', 0),
                },
                'assessment': {
                    'assessed': glossary.get('assessed', 0),
                    'assesstimestart': glossary.get('assesstimestart', 0),
                    'assesstimefinish': glossary.get('assesstimefinish', 0),
                    'scale': glossary.get('scale', 0),
                },
                'completion': {
                    'completionentries': glossary.get('completionentries', 0),
                },
                'entries_count': glossary.get('entries', 0),
                'categories': categories_data,
                'timestamps': {
                    'timemodified': glossary.get('timemodified', 0),
                },
                'features': {
                    'groups': True,
                    'groupings': True,
                    'intro_support': True,
                    'completion_tracks_views': True,
                    'grade_has_grade': True,
                    'grade_outcomes': True,
                    'backup_moodle2': True,
                    'show_description': True,
                    'purpose': 'collaboration',
                },
                'note': 'Glossary is a collaborative knowledge building module. '
                + 'This export includes comprehensive settings, categories, and all entries with their metadata.',
            }

            glossary_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': 0,
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            # Export categories as separate file if available
            if categories_data:
                glossary_files.append(
                    {
                        'filename': 'categories.json',
                        'filepath': '/',
                        'timemodified': 0,
                        'content': json.dumps(categories_data, indent=2, ensure_ascii=False),
                        'type': 'content',
                    }
                )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': glossary_id,
                    'name': glossary_name,
                    'files': glossary_files,
                    'entries': glossary.get('entries', 0),  # Number of entries
                },
            )

        # Load glossary entries
        await self.add_glossary_entries(result)
        return result

    async def add_glossary_entries(self, glossaries: Dict[int, Dict[int, Dict]]):
        """
        Fetch glossary entries
        @param glossaries: Dictionary of all glossaries, indexed by courses, then module id
        """
        if not self.config.get_download_glossaries():
            return

        await self.run_async_load_function_on_mod_entries(glossaries, self.load_glossary_entries)

    async def load_glossary_entries(self, glossary: Dict):
        """
        Load all entries for a glossary

        Process:
        1. Get all entries (using 'ALL' letter to get everything)
        2. For each entry:
           - Create definition file
           - Download attachments
           - Save metadata
        """
        glossary_id = glossary.get('id', 0)
        glossary_name = glossary.get('name', 'glossary')

        try:
            # Get all entries using 'ALL' letter (gets all entries regardless of first letter)
            # We can also iterate through alphabet letters, but 'ALL' is more efficient
            entries_response = await self.client.async_post(
                'mod_glossary_get_entries_by_letter',
                {
                    'id': glossary_id,
                    'letter': 'ALL',
                    'from': 0,
                    'limit': 0,  # 0 means no limit
                    'options': {
                        'includenotapproved': False,
                    },
                },
            )

            entries = entries_response.get('entries', [])

        except RequestRejectedError:
            logging.debug("No access rights for glossary %d", glossary_id)
            return
        except Exception as e:
            logging.debug("Error getting entries for glossary %d: %s", glossary_id, str(e))
            return

        # Process each entry
        for entry in entries:
            entry_files = self._create_entry_files(entry, glossary_name)
            glossary['files'] += entry_files

    def _create_entry_files(self, entry: Dict, glossary_name: str) -> List[Dict]:
        """
        Create files for a glossary entry

        @param entry: Entry data
        @param glossary_name: Parent glossary name
        @return: List of file dictionaries
        """
        result = []
        entry_id = entry.get('id', 0)
        concept = entry.get('concept', 'Untitled')
        definition = entry.get('definition', '')
        author = entry.get('userfullname', 'Unknown')
        time_created = entry.get('timecreated', 0)
        time_modified = entry.get('timemodified', 0)

        # Create safe filename for the entry
        safe_concept = PT.to_valid_name(concept, is_file=False)

        # Build entry content in Markdown format
        entry_content = f"# {concept}\n\n"

        # Add author and timestamps
        entry_content += f"**Author:** {author}\n\n"
        if time_modified > 0:
            from datetime import datetime

            modified_readable = datetime.fromtimestamp(time_modified).strftime('%Y-%m-%d %H:%M:%S')
            entry_content += f"**Last modified:** {modified_readable}\n\n"

        # Add definition
        entry_content += f"## Definition\n\n{definition}\n\n"

        # Add aliases if present
        aliases = entry.get('aliases', [])
        if aliases:
            entry_content += f"## Aliases\n\n"
            for alias in aliases:
                alias_name = alias if isinstance(alias, str) else alias.get('alias', '')
                if alias_name:
                    entry_content += f"- {alias_name}\n"
            entry_content += "\n"

        # Add category if present
        category_name = entry.get('categoryname', '')
        if category_name:
            entry_content += f"**Category:** {category_name}\n\n"

        # Add tags if present
        tags = entry.get('tags', [])
        if tags:
            entry_content += f"## Tags\n\n"
            for tag in tags:
                tag_name = tag.get('displayname', tag.get('rawname', 'Unknown'))
                entry_content += f"- {tag_name}\n"
            entry_content += "\n"

        # Create the entry file
        result.append(
            {
                'filename': safe_concept,
                'filepath': '/entries/',
                'timemodified': time_modified,
                'description': entry_content,
                'type': 'description',
            }
        )

        # Create comprehensive entry metadata
        entry_metadata = {
            'entry_id': entry_id,
            'glossary_id': entry.get('glossaryid', 0),
            'concept': concept,
            'definition': definition,
            'user': {
                'userid': entry.get('userid', 0),
                'userfullname': author,
                'userpictureurl': entry.get('userpictureurl', ''),
            },
            'formatting': {
                'definitionformat': entry.get('definitionformat', 1),
                'definitiontrust': entry.get('definitiontrust', 0),
            },
            'flags': {
                'teacherentry': entry.get('teacherentry', 0),
                'approved': entry.get('approved', 1),
            },
            'linking': {
                'sourceglossaryid': entry.get('sourceglossaryid', 0),
                'usedynalink': entry.get('usedynalink', 1),
                'casesensitive': entry.get('casesensitive', 0),
                'fullmatch': entry.get('fullmatch', 1),
            },
            'category': {
                'categoryid': entry.get('categoryid', 0),
                'categoryname': category_name,
            },
            'aliases': [alias if isinstance(alias, str) else alias.get('alias', '') for alias in aliases],
            'tags': [{'name': tag.get('displayname', tag.get('rawname', '')), 'id': tag.get('id', 0)} for tag in tags],
            'timestamps': {
                'timecreated': time_created,
                'timemodified': time_modified,
            },
            'attachments_count': len(entry.get('attachments', [])),
            'inline_files_count': len(entry.get('definitioninlinefiles', [])),
        }

        # Add entry metadata as JSON file
        result.append(
            {
                'filename': PT.to_valid_name(f'{safe_concept}_metadata', is_file=True) + '.json',
                'filepath': '/entries/',
                'timemodified': 0,
                'content': json.dumps(entry_metadata, indent=2, ensure_ascii=False),
                'type': 'content',
            }
        )

        # Add attachments
        attachments = entry.get('attachments', [])
        if attachments:
            for attachment in attachments:
                # Place attachments in entry-specific subdirectory
                attachment['filepath'] = f'/entries/{safe_concept}_files/'
                self.set_props_of_file(attachment, type='glossary_file')
                result.append(attachment)

        # Add inline files (images embedded in definition)
        inline_files = entry.get('definitioninlinefiles', [])
        if inline_files:
            for inline_file in inline_files:
                inline_file['filepath'] = f'/entries/{safe_concept}_files/'
                self.set_props_of_file(inline_file, type='glossary_file')
                result.append(inline_file)

        return result

    async def _get_glossary_categories(self, glossary_id: int) -> List[Dict]:
        """
        Get all categories for a glossary

        Returns list of category data
        """
        try:
            response = await self.client.async_post(
                'mod_glossary_get_categories',
                {'id': glossary_id, 'from': 0, 'limit': 0}
            )
            categories = response.get('categories', [])
            return [
                {
                    'id': cat.get('id', 0),
                    'glossaryid': cat.get('glossaryid', 0),
                    'name': cat.get('name', ''),
                    'usedynalink': cat.get('usedynalink', 1),
                }
                for cat in categories
            ]
        except Exception as e:
            logging.debug(f"Could not fetch categories for glossary {glossary_id}: {e}")
            return []

    async def _get_authors_list(self, glossary_id: int) -> List[str]:
        """
        Get list of authors who have contributed to glossary
        Uses mod_glossary_get_entries_by_author API

        Returns list of author identifiers
        """
        try:
            # Get first page to see structure
            response = await self.client.async_post(
                'mod_glossary_get_entries_by_author',
                {
                    'id': glossary_id,
                    'letter': 'ALL',  # Get all authors
                    'field': 'LASTNAME',  # Sort by lastname
                    'sort': 'ASC',
                    'from': 0,
                    'limit': 10  # Just get sample to verify API availability
                }
            )
            # Extract unique authors from the response
            entries = response.get('entries', [])
            authors = list({entry.get('userfullname', ''): entry.get('userid', 0) for entry in entries}.items())
            return [{'name': name, 'id': uid} for name, uid in authors if name]
        except Exception as e:
            logging.debug(f"Could not fetch authors for glossary {glossary_id}: {e}")
            return []

    async def _get_entries_by_date_info(self, glossary_id: int) -> Dict:
        """
        Get information about entries organized by date
        Uses mod_glossary_get_entries_by_date API

        Returns metadata about date-based organization
        """
        try:
            # Get recent entries (newest first)
            response = await self.client.async_post(
                'mod_glossary_get_entries_by_date',
                {
                    'id': glossary_id,
                    'order': 'CREATE',  # or 'UPDATE' for modification date
                    'sort': 'DESC',  # Newest first
                    'from': 0,
                    'limit': 10  # Sample to verify API
                }
            )
            entries = response.get('entries', [])
            if entries:
                newest_entry = entries[0].get('timecreated', 0) if entries else 0
                oldest_entry = entries[-1].get('timecreated', 0) if len(entries) > 1 else newest_entry
                return {
                    'api_available': True,
                    'sample_count': len(entries),
                    'newest_entry_time': newest_entry,
                    'oldest_entry_time': oldest_entry,
                    'sort_orders_supported': ['CREATE', 'UPDATE'],
                    'note': 'Entries can be browsed by creation or modification date'
                }
            return {'api_available': True, 'sample_count': 0}
        except Exception as e:
            logging.debug(f"Could not fetch date info for glossary {glossary_id}: {e}")
            return {'api_available': False, 'error': str(e)}
