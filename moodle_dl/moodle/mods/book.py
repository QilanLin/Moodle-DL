import html
import json
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
            book_files = list(book.get('introfiles', []))
            self.set_props_of_files(book_files, type='book_file')

            book_intro = book.get('intro', '')
            if book_intro != '':
                book_files.append(
                    {
                        'filename': 'Book intro',
                        'filepath': '/',
                        'description': book_intro,
                        'type': 'description',
                    }
                )

            book_contents = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])
            if len(book_contents) > 1:
                book_files += book_contents[1:]

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
