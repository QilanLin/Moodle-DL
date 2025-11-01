import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class ImscpMod(MoodleMod):
    """
    IMS Content Package (IMSCP) module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/imscp/services/imscp.ts

    Supports:
    - IMS Content Package metadata
    - Table of Contents (TOC) parsing and export
    - Content files with proper filtering
    - Nested structure (items and subitems)
    - Special file handling (imsmanifest.xml)
    """

    MOD_NAME = 'imscp'
    MOD_PLURAL_NAME = 'imscps'
    MOD_MIN_VERSION = 2015111600  # 3.0

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_imscps() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    def _is_special_file(self, filename: str) -> bool:
        """
        Check if file should be excluded from download

        Special files that are part of the IMS CP structure but not needed:
        - imsmanifest.xml: IMS CP manifest file
        """
        return filename == 'imsmanifest.xml'

    def _parse_toc(self, toc_content: str) -> List[Dict]:
        """
        Parse TOC JSON structure from content

        TOC structure:
        [
            {
                "href": "page1.html",
                "title": "Introduction",
                "level": "0",
                "subitems": [
                    {
                        "href": "page1-1.html",
                        "title": "Section 1.1",
                        "level": "1"
                    }
                ]
            }
        ]
        """
        if not toc_content:
            return []

        try:
            return json.loads(toc_content)
        except json.JSONDecodeError:
            logging.warning("Failed to parse IMSCP TOC JSON")
            return []

    def _generate_toc_html(self, toc_items: List[Dict]) -> str:
        """
        Generate HTML Table of Contents from TOC structure

        Creates a hierarchical list with proper indentation and styling
        """
        html = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Table of Contents</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 900px;
            margin: 20px auto;
            padding: 20px;
        }
        h1 {
            color: #333;
            border-bottom: 2px solid #0066cc;
            padding-bottom: 10px;
        }
        .toc-list {
            list-style-type: none;
            padding-left: 0;
        }
        .toc-item {
            margin: 8px 0;
            padding: 6px 10px;
            border-left: 3px solid #0066cc;
            background: #f8f9fa;
        }
        .toc-item a {
            text-decoration: none;
            color: #0066cc;
            font-weight: 500;
        }
        .toc-item a:hover {
            text-decoration: underline;
        }
        .toc-sublist {
            list-style-type: none;
            padding-left: 20px;
            margin-top: 5px;
        }
        .toc-subitem {
            margin: 5px 0;
            padding: 4px 8px;
            border-left: 2px solid #66a3e0;
            background: #ffffff;
        }
        .toc-subitem a {
            color: #0052a3;
            font-size: 0.95em;
        }
        .level-info {
            display: inline-block;
            background: #e7f3ff;
            color: #0066cc;
            font-size: 0.8em;
            padding: 2px 6px;
            border-radius: 3px;
            margin-left: 8px;
        }
    </style>
</head>
<body>
    <h1>Table of Contents</h1>
    <ul class="toc-list">
'''

        for item in toc_items:
            href = item.get('href', '')
            title = item.get('title', 'Untitled')
            level = item.get('level', '0')
            subitems = item.get('subitems', [])

            html += f'        <li class="toc-item">\n'
            html += f'            <a href="{href}">{title}</a>\n'
            html += f'            <span class="level-info">Level {level}</span>\n'

            if subitems:
                html += '            <ul class="toc-sublist">\n'
                for subitem in subitems:
                    sub_href = subitem.get('href', '')
                    sub_title = subitem.get('title', 'Untitled')
                    sub_level = subitem.get('level', '1')

                    html += f'                <li class="toc-subitem">\n'
                    html += f'                    <a href="{sub_href}">{sub_title}</a>\n'
                    html += f'                    <span class="level-info">Level {sub_level}</span>\n'
                    html += '                </li>\n'
                html += '            </ul>\n'

            html += '        </li>\n'

        html += '''    </ul>
</body>
</html>
'''
        return html

    def _create_flat_toc_list(self, toc_items: List[Dict]) -> List[Dict]:
        """
        Create a flat list of TOC items for metadata export

        Flattens the nested structure into a simple list
        """
        flat_list = []

        for item in toc_items:
            flat_list.append({
                'href': item.get('href', ''),
                'title': item.get('title', ''),
                'level': item.get('level', '0'),
                'has_subitems': len(item.get('subitems', [])) > 0,
            })

            for subitem in item.get('subitems', []):
                flat_list.append({
                    'href': subitem.get('href', ''),
                    'title': subitem.get('title', ''),
                    'level': subitem.get('level', '1'),
                    'has_subitems': False,
                })

        return flat_list

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all IMSCP modules from courses

        Process:
        1. Get IMSCP packages by courses
        2. Parse TOC from module contents
        3. Export metadata and TOC
        4. Filter content files (exclude imsmanifest.xml)
        """

        result = {}

        if not self.config.get_download_imscps():
            return result

        # Get all IMSCP packages for the courses
        try:
            response = await self.client.async_post(
                'mod_imscp_get_imscps_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            imscps = response.get('imscps', [])
        except RequestRejectedError:
            logging.debug("No access to IMSCP modules or WS not available")
            return result
        except Exception as e:
            logging.debug("Error getting IMSCP modules: %s", str(e))
            return result

        for imscp in imscps:
            course_id = imscp.get('course', 0)
            module_id = imscp.get('coursemodule', 0)
            imscp_id = imscp.get('id', 0)
            imscp_name = imscp.get('name', 'IMS Content Package')

            imscp_files = []

            # Copy introfiles to avoid modifying the original dict
            intro_files = list(imscp.get('introfiles', []))
            self.set_props_of_files(intro_files, type='imscp_file')
            imscp_files.extend(intro_files)

            # Get IMSCP intro/description
            imscp_intro = imscp.get('intro', '')
            if imscp_intro:
                imscp_files.append(
                    {
                        'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                        'filepath': '/',
                        'description': imscp_intro,
                        'type': 'description',
                        'timemodified': imscp.get('timemodified', 0),
                    }
                )

            # Get module contents from core_contents
            imscp_contents = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])

            # Parse TOC if available
            toc_items = []
            if len(imscp_contents) > 0:
                toc_content = imscp_contents[0].get('content', '')
                toc_items = self._parse_toc(toc_content)

                # Generate TOC HTML
                if toc_items:
                    toc_html = self._generate_toc_html(toc_items)
                    imscp_files.append(
                        {
                            'filename': PT.to_valid_name('Table of Contents', is_file=True) + '.html',
                            'filepath': '/',
                            'html': toc_html,
                            'type': 'html',
                            'timemodified': imscp.get('timemodified', 0),
                            'filesize': len(toc_html),
                        }
                    )

            # Add content files (exclude special files)
            if len(imscp_contents) > 1:
                content_files = []
                for content_file in imscp_contents[1:]:
                    filename = content_file.get('filename', '')

                    # Skip special files
                    if self._is_special_file(filename):
                        logging.debug(f"Skipping special file: {filename}")
                        continue

                    content_files.append(content_file)

                imscp_files.extend(content_files)

            # Create comprehensive metadata
            metadata = {
                'imscp_id': imscp_id,
                'course_id': course_id,
                'name': imscp_name,
                'intro': imscp_intro,
                'package_info': {
                    'revision': imscp.get('revision', 0),
                    'keepold': imscp.get('keepold', 0),
                    'structure': imscp.get('structure', ''),
                },
                'table_of_contents': {
                    'total_items': len(toc_items),
                    'items': self._create_flat_toc_list(toc_items),
                },
                'timestamps': {
                    'time_modified': imscp.get('timemodified', 0),
                },
                'content_summary': {
                    'total_files': len([f for f in imscp_files if f.get('type') not in ['description', 'html', 'content']]),
                    'has_toc': len(toc_items) > 0,
                },
            }

            imscp_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': imscp.get('timemodified', 0),
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': imscp_id,
                    'name': imscp_name,
                    'files': imscp_files,
                },
            )

        return result
