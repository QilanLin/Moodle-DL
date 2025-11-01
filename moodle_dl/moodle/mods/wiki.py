import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.moodle_constants import moodle_html_footer, moodle_html_header
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class WikiMod(MoodleMod):
    """
    Wiki module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/wiki/services/wiki.ts

    Supports:
    - Multiple subwikis (group wikis, individual wikis)
    - Wiki pages with content
    - File attachments
    - Tags
    """

    MOD_NAME = 'wiki'
    MOD_PLURAL_NAME = 'wikis'
    MOD_MIN_VERSION = 2015111600  # 3.0

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_wikis() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all wiki entries from courses

        Process:
        1. Get wikis by courses
        2. For each wiki, get subwikis
        3. For each subwiki, get pages and files
        4. Download page contents and attachments
        """

        result = {}
        if not self.config.get_download_wikis():
            return result

        # Get all wikis for the courses
        wikis = (
            await self.client.async_post(
                'mod_wiki_get_wikis_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('wikis', [])

        for wiki in wikis:
            course_id = wiki.get('course', 0)
            module_id = wiki.get('coursemodule', 0)
            wiki_id = wiki.get('id', 0)
            wiki_name = wiki.get('name', 'unnamed wiki')
            wiki_intro = wiki.get('intro', '')
            wiki_mode = wiki.get('wikimode', 'collaborative')
            first_page_title = wiki.get('firstpagetitle', 'Main Page')

            # Get wiki intro files
            wiki_files = wiki.get('introfiles', [])
            self.set_props_of_files(wiki_files, type='wiki_introfile')

            # Add wiki intro as description
            if wiki_intro != '':
                wiki_files.append(
                    {
                        'filename': 'Wiki intro',
                        'filepath': '/',
                        'description': wiki_intro,
                        'type': 'description',
                    }
                )

            # Create comprehensive wiki metadata
            metadata = {
                'wiki_id': wiki_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': wiki_name,
                'intro': wiki_intro,
                'settings': {
                    'wikimode': wiki_mode,  # collaborative, individual, or group
                    'firstpagetitle': first_page_title,
                    'defaultformat': wiki.get('defaultformat', 'html'),
                    'forceformat': wiki.get('forceformat', 1),
                    'editbegin': wiki.get('editbegin', 0),
                    'editend': wiki.get('editend', 0),
                },
                'capabilities': {
                    'cancreatepages': wiki.get('cancreatepages', True),
                    'caneditpages': wiki.get('caneditpages', True),
                    'canviewpage': wiki.get('canviewpage', True),
                },
                'timestamps': {
                    'timemodified': wiki.get('timemodified', 0),
                    'timecreated': wiki.get('timecreated', 0),
                },
                'features': {
                    'groups': True,
                    'groupings': True,
                    'intro_support': True,
                    'completion_tracks_views': True,
                    'backup_moodle2': True,
                    'show_description': True,
                    'purpose': 'collaboration',
                },
                'note': 'Wiki is a collaborative content creation module supporting multiple subwikis. '
                + f'Mode: {wiki_mode}. This export includes all pages, attachments, and tags.',
            }

            wiki_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': wiki.get('timemodified', 0),
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': wiki_id,
                    'name': wiki_name,
                    'files': wiki_files,
                    'firstpagetitle': first_page_title,
                    'wikimode': wiki_mode,
                },
            )

        # Load wiki contents (subwikis, pages, files)
        await self.add_wiki_contents(result)
        return result

    async def add_wiki_contents(self, wikis: Dict[int, Dict[int, Dict]]):
        """
        Fetch wiki subwikis, pages, and files
        @param wikis: Dictionary of all wikis, indexed by courses, then module id
        """
        if not self.config.get_download_wikis():
            return

        await self.run_async_load_function_on_mod_entries(wikis, self.load_wiki_contents)

    async def load_wiki_contents(self, wiki: Dict):
        """
        Load all contents for a wiki

        Process:
        1. Get subwikis
        2. For each subwiki:
           - Get pages
           - Get files
           - Download page contents
        """
        wiki_id = wiki.get('id', 0)
        wiki_name = wiki.get('name', 'wiki')

        try:
            # Get subwikis for this wiki
            subwikis_response = await self.client.async_post(
                'mod_wiki_get_subwikis',
                {'wikiid': wiki_id}
            )
            subwikis = subwikis_response.get('subwikis', [])

        except RequestRejectedError:
            logging.debug("No access rights for wiki %d", wiki_id)
            return
        except Exception as e:
            logging.debug("Error getting subwikis for wiki %d: %s", wiki_id, str(e))
            return

        # Process each subwiki
        for subwiki in subwikis:
            subwiki_id = subwiki.get('id', 0)
            subwiki_files = await self._get_subwiki_contents(subwiki, wiki_name)
            wiki['files'] += subwiki_files

    async def _get_subwiki_contents(self, subwiki: Dict, wiki_name: str) -> List[Dict]:
        """
        Get all contents for a subwiki

        @param subwiki: Subwiki data
        @param wiki_name: Parent wiki name
        @return: List of file dictionaries
        """
        result = []
        subwiki_id = subwiki.get('id', 0)
        wiki_id = subwiki.get('wikiid', 0)
        group_id = subwiki.get('groupid', 0)
        user_id = subwiki.get('userid', 0)

        # Determine subwiki folder name
        if group_id > 0:
            subwiki_folder = f'/group_{group_id}'
        elif user_id > 0:
            subwiki_folder = f'/user_{user_id}'
        else:
            subwiki_folder = '/collaborative'

        try:
            # Get pages for this subwiki
            pages_response = await self.client.async_post(
                'mod_wiki_get_subwiki_pages',
                {'wikiid': wiki_id, 'groupid': group_id, 'userid': user_id}
            )
            pages = pages_response.get('pages', [])

        except RequestRejectedError:
            logging.debug("No access to pages for subwiki %d", subwiki_id)
            return result
        except Exception as e:
            logging.debug("Error getting pages for subwiki %d: %s", subwiki_id, str(e))
            return result

        # Get subwiki files
        try:
            files_response = await self.client.async_post(
                'mod_wiki_get_subwiki_files',
                {'wikiid': wiki_id, 'groupid': group_id, 'userid': user_id}
            )
            subwiki_files = files_response.get('files', [])

            # Add files to result
            for file in subwiki_files:
                file['filepath'] = subwiki_folder + file.get('filepath', '/')
                self.set_props_of_file(file, type='wiki_file')
                result.append(file)

        except Exception as e:
            logging.debug("Error getting files for subwiki %d: %s", subwiki_id, str(e))

        # Download each page's content
        for page in pages:
            page_id = page.get('id', 0)
            page_files = await self._get_page_content(page, subwiki_folder, wiki_name)
            result += page_files

        return result

    async def _get_page_content(self, page: Dict, subwiki_folder: str, wiki_name: str) -> List[Dict]:
        """
        Get content for a wiki page

        @param page: Page data from subwiki_pages
        @param subwiki_folder: Folder path for this subwiki
        @param wiki_name: Parent wiki name
        @return: List of file dictionaries
        """
        result = []
        page_id = page.get('id', 0)
        page_title = page.get('title', 'Untitled')

        try:
            # Get page contents
            page_contents = await self.client.async_post(
                'mod_wiki_get_page_contents',
                {'pageid': page_id}
            )

            page_data = page_contents.get('page', {})
            cached_content = page_data.get('cachedcontent', '')

            if not cached_content:
                return result

            # Create HTML page
            page_html = moodle_html_header
            page_html += f'<h1>{page_title}</h1>\n'
            page_html += cached_content
            page_html += moodle_html_footer

            # Add page as HTML file
            safe_page_title = PT.to_valid_name(page_title, is_file=False)
            result.append({
                'filename': safe_page_title,
                'filepath': subwiki_folder + '/pages/',
                'timemodified': page_data.get('timemodified', 0),
                'html': page_html,
                'type': 'html',
                'filesize': len(page_html),
            })

            # Add tags if present
            tags = page_data.get('tags', [])
            if tags:
                tags_text = '# Tags\n\n'
                for tag in tags:
                    tag_name = tag.get('displayname', tag.get('rawname', 'Unknown'))
                    tags_text += f'- {tag_name}\n'

                result.append({
                    'filename': PT.to_valid_name(f'{page_title}_tags', is_file=True),
                    'filepath': subwiki_folder + '/pages/',
                    'timemodified': page_data.get('timemodified', 0),
                    'description': tags_text,
                    'type': 'description',
                })

        except RequestRejectedError:
            logging.debug("No access to page %d", page_id)
        except Exception as e:
            logging.debug("Error getting content for page %d: %s", page_id, str(e))

        return result
