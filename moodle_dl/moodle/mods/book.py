import html
import json
import re
import time
import urllib.parse
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File


class BookMod(MoodleMod):
    MOD_NAME = 'book'
    MOD_PLURAL_NAME = 'books'
    MOD_MIN_VERSION = 2015111600  # 3.0

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_books() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:

        result = {}
        if not self.config.get_download_books():
            return result

        books = (
            await self.client.async_post(
                'mod_book_get_books_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('books', [])

        for book in books:
            course_id = book.get('course', 0)
            module_id = book.get('coursemodule', 0)
            book_name = book.get('name', 'unnamed book')

            # Create a copy to avoid modifying the original dict
            book_files = self.get_introfiles(book, 'book_file', copy=True)

            book_intro = book.get('intro', '')
            intro_file = self.create_intro_file(book_intro)
            if intro_file:
                intro_file['filename'] = 'Book intro'
                book_files.append(intro_file)

            book_contents = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])
            if len(book_contents) > 1:
                # Process book chapters: extract embedded videos and add chapters
                for chapter_content in book_contents[1:]:
                    book_files.append(chapter_content)

                    # Extract Kaltura videos from chapter HTML
                    # Note: The 'content' field only contains the chapter title, not the HTML
                    # We need to download the actual HTML from the fileurl
                    chapter_fileurl = chapter_content.get('fileurl', '')
                    chapter_title = chapter_content.get('content', 'chapter')

                    if chapter_fileurl:
                        try:
                            import logging
                            logging.debug(f'📖 Attempting to fetch chapter HTML from: {chapter_fileurl[:100]}...')

                            # Download the chapter HTML content
                            chapter_html = await self._fetch_chapter_html(chapter_fileurl)

                            if chapter_html:
                                logging.debug(f'✅ Fetched chapter HTML ({len(chapter_html)} bytes)')

                                # Extract Kaltura videos from the HTML
                                kaltura_videos = self._extract_kaltura_videos_from_html(
                                    chapter_html,
                                    chapter_title,
                                    course_id,
                                    module_id
                                )

                                if kaltura_videos:
                                    logging.info(f'🎬 Extracted {len(kaltura_videos)} Kaltura video(s) from chapter: {chapter_title}')
                                    logging.debug(f'Video entries: {[v.get("filename") for v in kaltura_videos]}')
                                    book_files.extend(kaltura_videos)
                                    logging.debug(f'Total book_files now: {len(book_files)}')
                                else:
                                    logging.debug(f'No Kaltura videos found in chapter: {chapter_title}')
                            else:
                                logging.debug(f'❌ Failed to fetch chapter HTML (empty response)')
                        except Exception as e:
                            # If fetching fails, just skip video extraction for this chapter
                            # The chapter HTML file itself will still be downloaded normally
                            import logging
                            logging.warning(f'⚠️  Failed to fetch chapter HTML for video extraction: {e}')
                            import traceback
                            logging.debug(f'Traceback: {traceback.format_exc()}')

            if len(book_contents) > 0:
                # Generate Table of Contents
                book_toc = json.loads(book_contents[0].get('content', ''))

                toc_html = '''
<!DOCTYPE html>
<html>
    <head>
        <style>
            ol {
                counter-reset: item
            }
            li {
                display: block
            }
            li:before {
                content: counters(item, ".")" ";
                counter-increment: item
            }
            .hidden {
                color: #999;
                font-style: italic;
            }
            .level-0 {
                font-weight: bold;
            }
        </style>
    </head>
    <body>
        '''
                toc_html += self.create_ordered_index(book_toc)
                toc_html += '''
    </body>
</html>
                '''

                book_files.append(
                    {
                        'filename': 'Table of Contents',
                        'filepath': '/',
                        'timemodified': book.get('timemodified', 0),
                        'html': toc_html,
                        'type': 'html',
                        'no_search_for_urls': True,
                        'filesize': len(toc_html),
                    }
                )

            # Create metadata file with book configuration
            book_toc = json.loads(book_contents[0].get('content', '[]')) if len(book_contents) > 0 else []

            metadata = {
                'book_id': book.get('id', 0),
                'course_id': course_id,
                'name': book_name,
                'intro': book.get('intro', ''),
                'configuration': {
                    'numbering': self._get_numbering_name(book.get('numbering', 0)),
                    'numbering_value': book.get('numbering', 0),
                    'navstyle': self._get_navstyle_name(book.get('navstyle', 0)),
                    'navstyle_value': book.get('navstyle', 0),
                    'customtitles': book.get('customtitles', 0),
                    'revision': book.get('revision', 0),
                },
                'timestamps': {
                    'time_created': book.get('timecreated', 0),
                    'time_modified': book.get('timemodified', 0),
                },
                'statistics': {
                    'total_chapters': len(self._get_flat_toc_list(book_toc)) if book_toc else 0,
                    'has_intro_files': len(book.get('introfiles', [])) > 0,
                },
            }

            book_files.append(
                {
                    'filename': 'metadata.json',
                    'filepath': '/',
                    'timemodified': book.get('timemodified', 0),
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            import logging
            logging.info(f'📚 Book "{book_name}" has {len(book_files)} files total')
            video_count = len([f for f in book_files if f.get('type') == 'kalvidres_embedded'])
            if video_count > 0:
                logging.info(f'   Including {video_count} embedded Kaltura videos')

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': book.get('id', 0),
                    'name': book_name,
                    'files': book_files,
                },
            )

        return result

    @staticmethod
    def create_ordered_index(items: List[Dict]) -> str:
        result = '<ol>\n'
        for entry in items:
            chapter_title = html.escape(entry.get("title", "untitled"))
            chapter_href = urllib.parse.quote(entry.get("href", "#failed"))
            chapter_level = entry.get("level", 0)
            chapter_hidden = entry.get("hidden", "0") == "1"

            # Add CSS classes based on chapter properties
            css_classes = [f'level-{chapter_level}']
            if chapter_hidden:
                css_classes.append('hidden')

            class_attr = f' class="{" ".join(css_classes)}"' if css_classes else ''
            hidden_marker = ' [Hidden]' if chapter_hidden else ''

            result += f'<li{class_attr}><a title="{chapter_title}" href="{chapter_href}">{chapter_title}{hidden_marker}</a></li>\n'
            subitems = entry.get('subitems', [])
            if len(subitems) > 0:
                result += BookMod.create_ordered_index(subitems)

        result += '</ol>'
        return result

    def _get_numbering_name(self, numbering: int) -> str:
        """Get human-readable name for book numbering configuration"""
        names = {
            0: 'None',
            1: 'Numbers',
            2: 'Bullets',
            3: 'Indented',
        }
        return names.get(numbering, 'Unknown')

    def _get_navstyle_name(self, navstyle: int) -> str:
        """Get human-readable name for book navigation style"""
        names = {
            0: 'Image',
            1: 'Text',
        }
        return names.get(navstyle, 'Unknown')

    def _get_flat_toc_list(self, toc: List[Dict]) -> List[Dict]:
        """Flatten nested TOC structure into a list of all chapters"""
        chapters = []
        for chapter in toc:
            chapters.append(chapter)
            subitems = chapter.get('subitems', [])
            if subitems:
                chapters.extend(self._get_flat_toc_list(subitems))
        return chapters

    async def _fetch_chapter_html(self, fileurl: str) -> str:
        """
        Fetch the HTML content of a book chapter from its fileurl.

        The fileurl is a Moodle webservice URL that requires authentication (token).
        This method downloads the HTML content so we can extract embedded Kaltura videos.

        @param fileurl: The webservice URL to the chapter HTML file
        @return: The HTML content as a string, or empty string if fetch fails
        """
        try:
            # The fileurl already contains the full URL to the file
            # We need to add the token parameter for authentication
            import aiohttp

            # Add token to URL
            separator = '&' if '?' in fileurl else '?'
            authenticated_url = f"{fileurl}{separator}token={self.client.token}"

            async with aiohttp.ClientSession() as session:
                async with session.get(authenticated_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        # Read as text with proper encoding
                        html_content = await response.text(encoding='utf-8')
                        return html_content
                    else:
                        return ''
        except Exception:
            return ''

    def _extract_kaltura_videos_from_html(
        self, chapter_html: str, chapter_name: str, course_id: int, module_id: int
    ) -> List[Dict]:
        """
        Extract Kaltura video iframes from book chapter HTML and create file entries for them.

        This allows embedded Kaltura videos (internal videos) to be downloaded.
        YouTube and other external public videos are preserved as-is in the HTML.

        @param chapter_html: The HTML content of the book chapter
        @param chapter_name: The name of the chapter (for naming extracted videos)
        @param course_id: The course ID
        @param module_id: The module ID
        @return: List of file dictionaries for Kaltura videos
        """
        video_files = []

        # Pattern to match Kaltura iframe with lti_launch.php
        # Example: src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?...&source=https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_er5gtb0g%2F..."
        kaltura_pattern = r'<iframe[^>]+src="([^"]*filter/kaltura/lti_launch\.php[^"]*)"'

        matches = re.findall(kaltura_pattern, chapter_html, re.IGNORECASE)

        for idx, iframe_src in enumerate(matches, 1):
            # Unescape HTML entities
            iframe_src = html.unescape(iframe_src)

            # Extract the source parameter which contains the actual Kaltura URL
            source_match = re.search(r'[?&]source=([^&]+)', iframe_src)
            if not source_match:
                continue

            # Decode the URL-encoded source parameter
            kaltura_source = urllib.parse.unquote(source_match.group(1))

            # Extract entry ID from the Kaltura URL
            # Example: https://kaf.keats.kcl.ac.uk/browseandembed/index/media/entryid/1_er5gtb0g/...
            entry_id_match = re.search(r'/entryid/([^/]+)', kaltura_source)
            if not entry_id_match:
                continue

            entry_id = entry_id_match.group(1)

            # Construct the Kaltura video module URL (similar to standalone kalvidres modules)
            # We use the lti_launch.php URL as the module URL for cookie_mod processing
            video_url = iframe_src

            # Generate a descriptive filename
            if len(matches) == 1:
                video_name = f"{chapter_name} - Video"
            else:
                video_name = f"{chapter_name} - Video {idx}"

            # Create a file entry for this Kaltura video
            # Mark it as 'kalvidres_embedded' so it can be processed by the kalvidres downloader
            # Use current timestamp to ensure proper change detection (different videos will have different URLs and timestamps)
            video_files.append({
                'filename': video_name,
                'filepath': '/',
                'fileurl': video_url,
                'filesize': 0,
                'timemodified': int(time.time()),
                'type': 'kalvidres_embedded',  # Special type for embedded Kaltura videos
                'mimetype': 'video/mp4',
                'entry_id': entry_id,  # Store entry ID for reference
            })

        return video_files
