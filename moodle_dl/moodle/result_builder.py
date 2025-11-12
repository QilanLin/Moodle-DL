import hashlib
import html
import logging
import mimetypes
import re
import urllib.parse as urlparse
from typing import Dict, List

from moodle_dl.types import Course, File, MoodleURL
from moodle_dl.utils import PathTools as PT


class ResultBuilder:
    """
    Combines all fetched mod files and core course files to one result based on File objects
    """

    def __init__(self, moodle_url: MoodleURL, version: int, mod_plurals: Dict):
        self.version = version
        self.moodle_url = moodle_url
        self.moodle_domain = moodle_url.domain
        self.mod_plurals = mod_plurals

    def get_files_in_sections(self, course_sections: List[Dict], fetched_mods: Dict[str, Dict]) -> List[File]:
        """
        Iterates over all sections of a course to find files (or modules).
        @param course_sections: Contains the sections of the course
        @param fetched_mods: Contains the fetched mods of the course
        @return: A list of files of the course.
        """
        files = []
        for section in course_sections:
            location = {
                'section_id': section.get('id', 0),
                'section_name': section.get('name', ''),
            }
            section_modules = section.get('modules', [])
            files += self._get_files_in_modules(section_modules, fetched_mods, **location)

            section_summary = section.get('summary', '')
            if section_summary is not None and section_summary != '':
                location.update(
                    {
                        'module_id': 0,
                        'module_name': 'Section summary',
                        'module_modname': 'section_summary',
                    }
                )
                files += self._handle_description(section_summary, **location)

        files += self._get_files_not_on_main_page(fetched_mods)

        import logging
        kalvidres_total = len([f for f in files if f.module_modname == 'cookie_mod-kalvidres'])
        if kalvidres_total > 0:
            logging.info(f'🌐 get_files_in_sections() returning {kalvidres_total} Kaltura videos total')

        return files

    def _get_files_in_modules(self, section_modules: List, fetched_mods: Dict[str, Dict], **location) -> List[File]:
        """
        Iterates over all modules to find files (or content) in them.
        @param section_modules: The modules of the section.
        @param fetched_mods: Contains the fetched mods of the course
        @param location: contains
            section_id: int,
            section_name: str,
        @return: A list of files of the section.
        """
        import logging
        files = []
        for module in section_modules:
            location['module_id'] = module.get('id', 0)
            location['module_name'] = module.get('name', '')
            location['module_modname'] = module.get('modname', '')

            module_url = module.get('url', '')
            module_contents = module.get('contents', [])
            module_description = module.get('description', None)

            # 🔍 DEBUG: Log all modules to see which branch books go through
            if 'week 1' in location.get('module_name', '').lower() and 'software' in location.get('module_name', '').lower():
                logging.info(f'🔵 [TRACE] Processing module: name="{location["module_name"]}", modname={location["module_modname"]}, id={location["module_id"]}')
                logging.info(f'🔵 [TRACE]   module_description={module_description is not None}, module_contents={len(module_contents)}, module_url={module_url[:50] if module_url else "None"}')

            # handle not supported modules that results in an index.html special
            if location['module_modname'] in ['moodecvideo']:
                location['module_modname'] = 'index_mod-' + location['module_modname']

            if location['module_modname'] in ['page'] and self.version < 2017051500:
                # legacy pages
                location['module_modname'] = 'index_mod-' + location['module_modname']

            if module_description is not None and location['module_modname'] not in fetched_mods:
                # Handle descriptions of Files, Labels and all mods that we do not handle in separately
                files += self._handle_description(module_description, **location)

            if location['module_modname'] in ['kalvidres', 'helixmedia']:
                # Special handling for specific LTI tools with dedicated extractors
                # Generic 'lti' modules are now handled by the LtiMod module
                location['module_modname'] = 'cookie_mod-' + location['module_modname']
                files += self._handle_cookie_mod(module_url, **location)

            elif location['module_modname'].startswith(('akarifolder', 'index_mod')):
                # Resource and URL modules now have dedicated module handlers
                files += self._handle_files(module_contents, **location)

            elif location['module_modname'] in fetched_mods:
                # find mod module with same module_id
                if location['module_modname'] == 'book':
                    logging.info(f'🟢 [DEBUG] BOOK BRANCH HIT! module_id={location["module_id"]}, module_name={location["module_name"]}')
                    logging.info(f'🟢 [DEBUG] fetched_mods.keys()={list(fetched_mods.keys())}')
                    logging.info(f'🟢 [DEBUG] fetched_mods["book"].keys()={list(fetched_mods.get("book", {}).keys())}')

                mod = fetched_mods.get(location['module_modname'], {}).get(location['module_id'], {})
                mod['on_main_page'] = True
                mod_files = mod.get('files', [])

                # 🔍 DEBUG: Log book module file usage
                if location['module_modname'] == 'book':
                    logging.info(f'🔍 [ResultBuilder] Using files from book module for "{location["module_name"]}" (module_id={location["module_id"]})')
                    logging.info(f'🔍 [ResultBuilder]   Found {len(mod_files)} files in fetched_mods')
                    if location['module_id'] not in fetched_mods.get('book', {}):
                        logging.warning(f'⚠️  Module ID {location["module_id"]} NOT in fetched_mods["book"]!')
                        logging.warning(f'⚠️  Available module IDs: {list(fetched_mods.get("book", {}).keys())}')

                files += self._handle_files(mod_files, **location)
            else:
                # 🔍 DEBUG: Log if book module is not in fetched_mods
                if location['module_modname'] == 'book':
                    logging.warning(f'⚠️  [ResultBuilder] Book module "{location["module_name"]}" NOT in fetched_mods!')
                    logging.warning(f'⚠️  [ResultBuilder]   module_id={location["module_id"]}, modname={location["module_modname"]}')
                    logging.warning(f'⚠️  [ResultBuilder]   Available mods: {list(fetched_mods.keys())}')
                    logging.warning(f'⚠️  [ResultBuilder]   module_contents count: {len(module_contents)}')

                if location['module_modname'] not in ['label']:
                    logging.debug(
                        'Got unhandled module: name=%s mod=%s url=%s',
                        location['module_name'],
                        location['module_modname'],
                        module_url,
                    )

        total_files_count = len(files)
        kalvidres_in_section = 0
        for f in files:
            if hasattr(f, 'module_modname') and f.module_modname == 'cookie_mod-kalvidres':
                kalvidres_in_section += 1
                logging.debug(f'Found kalvidres file: {f.content_filename}')

        if total_files_count > 0:
            logging.debug(f'_get_files_in_modules() returning {total_files_count} files total, {kalvidres_in_section} kalvidres')
        if kalvidres_in_section > 0:
            logging.info(f'🔄 _get_files_in_modules() returning {kalvidres_in_section} Kaltura videos in section')

        return files

    def get_mod_plural_name(self, mod_name: str) -> str:
        if mod_name in self.mod_plurals:
            return self.mod_plurals[mod_name].capitalize()
        return mod_name.capitalize()

    def _get_files_not_on_main_page(self, fetched_mods: Dict[str, Dict]) -> List[File]:
        """
        Iterates over all mods to find files (or content) that are not listed on the main page.
        @param fetched_mods: Contains the fetched_mods of the course
        @return: A list of files of mod modules not on the main page.
        """
        import logging

        # 🔍 DEBUG: Log books processing
        if 'book' in fetched_mods:
            logging.info(f'🟡 [NOT_ON_MAIN_PAGE] Found {len(fetched_mods["book"])} book(s) in fetched_mods')
            for module_id, module in list(fetched_mods["book"].items())[:3]:
                logging.info(f'🟡 [NOT_ON_MAIN_PAGE]   Book {module_id}: name={module.get("name", "?")}, on_main_page={("on_main_page" in module)}, files_count={len(module.get("files", []))}')

        files = []
        for mod_name, mod_modules in fetched_mods.items():
            location = {
                'section_id': -1,
                'section_name': f"{self.get_mod_plural_name(mod_name)} not on main page",
            }

            for _, module in mod_modules.items():
                if 'on_main_page' in module:
                    continue
                location.update(
                    {
                        'module_id': module.get('id', 0),
                        'module_name': module.get('name', ''),
                        'module_modname': mod_name,
                    }
                )

                # 🔍 DEBUG: Log when book is processed here
                if mod_name == 'book':
                    logging.info(f'🟡 [NOT_ON_MAIN_PAGE] Processing book: "{location["module_name"]}" (id={location["module_id"]}), files_count={len(module.get("files", []))}')

                # Handle not supported modules that results in an index.html special
                if location['module_modname'] in ['page'] and self.version < 2017051500:
                    location['module_modname'] = 'index_mod-' + location['module_modname']

                files += self._handle_files(module.get('files', []), **location)

        return files

    @staticmethod
    def filter_changing_attributes(description: str) -> str:
        """
        Tries to filter ids and stuff,
        that is knowing to change over time in descriptions.
        """
        if description is None:
            return ''

        if not isinstance(description, str):
            return description

        # to avoid changing encodings (see issue #96) we unencode and unquote everything
        description = html.unescape(description)
        description = urlparse.unquote(description)

        # ids can change very quickly
        description = re.sub(r'id="[^"]*"', "", description)
        description = re.sub(r"id='[^']*'", "", description)

        # Embedded images from Moodle can change their timestemp (is such a theme feature)
        # We change every timestemp to -1 the default.
        description = re.sub(
            r"\/theme\/image.php\/(\w+)\/(\w+)\/\d+\/",
            r"/theme/image.php/\g<1>/\g<2>/-1/",
            description,
        )

        # some folder downloads inside a description file may have some session key inside which will always be
        # different. We remove it, to prevent always tagging this file as "modified".
        description = re.sub(r'<input type="hidden" name="sesskey" value="[0-9a-zA-Z]*" \/>', "", description)
        description = re.sub(r"<input type='hidden' name='sesskey' value='[0-9a-zA-Z]*' \/>", "", description)

        return description

    def _find_all_urls(
        self,
        content_html: str,
        no_search_for_moodle_urls: bool,
        filter_urls_containing: List[str],
        **location,
    ) -> List[File]:
        """Parses a html string to find all urls in it. Then it creates for every url a file entry.

        @param location: contains
            section_id: int,
            section_name: str,
            module_id: str,
            module_name: str,
            module_modname: str,
            content_filepath: str,
        """

        # TODO: Also parse name or alt of an link to get a better name for URLs
        urls = list(set(re.findall(r'href=[\'"]?([^\'" >]+)', content_html)))
        urls += list(set(re.findall(r'<a[^>]*>(http[^<]*)<\/a>', content_html)))
        urls += list(set(re.findall(r'src=[\'"]?([^\'" >]+)', content_html)))
        urls += list(set(re.findall(r'data=[\'"]?([^\'" >]+)', content_html)))
        urls = list(set(urls))

        logging.debug(f'   🔎 _find_all_urls() found {len(urls)} raw URLs in HTML (length={len(content_html)})')
        if 'kaltura' in content_html.lower():
            logging.debug(f'   🎬 HTML contains "kaltura" - checking if Kaltura URLs are in extracted URLs')
            kaltura_urls = [u for u in urls if 'kaltura' in u.lower()]
            logging.debug(f'   🎬 Found {len(kaltura_urls)} Kaltura URLs: {kaltura_urls[:2] if kaltura_urls else "None"}')

        result = []
        original_module_modname = location['module_modname']

        for url in urls:
            if url == '':
                continue

            # To avoid different encodings and quotes and so that yt-dlp downloads correctly
            # (See issues #96 and #103), we remove all encodings.
            url = html.unescape(url)
            url = urlparse.unquote(url)

            url_parts = urlparse.urlparse(url)
            if (
                url_parts.hostname == self.moodle_domain
                or url_parts.netloc == self.moodle_domain
                and no_search_for_moodle_urls
            ):
                # Skip if no moodle urls should be found
                continue

            for filter_str in filter_urls_containing:
                # Skip url if a filter matches
                if url.find(filter_str) >= 0:
                    continue

            if url_parts.hostname == self.moodle_domain and url_parts.path.find('/theme/image.php/') >= 0:
                url = re.sub(
                    r"\/theme\/image.php\/(\w+)\/(\w+)\/\d+\/",
                    r"/theme/image.php/\g<1>/\g<2>/-1/",
                    url,
                )

            location['module_modname'] = 'url-description-' + original_module_modname

            if url_parts.hostname == self.moodle_domain and url_parts.path.find('/webservice/') >= 0:
                location['module_modname'] = 'index_mod-description-' + original_module_modname

            elif url_parts.hostname == self.moodle_domain:
                location['module_modname'] = 'cookie_mod-description-' + original_module_modname

            # Special handling for Kaltura LTI launch URLs
            # These are embedded Kaltura videos accessed via LTI (Learning Tools Interoperability)
            # Format: /filter/kaltura/lti_launch.php?...source=https://...entryid/1_xxxxx/...
            kaltura_converted = False
            if url_parts.hostname == self.moodle_domain and '/filter/kaltura/lti_launch.php' in url_parts.path:
                # Extract entry_id from the source parameter
                # URL format: ...source=https%3A%2F%2Fkaf.keats.kcl.ac.uk%2F...%2Fentryid%2F1_uwhesokp%2F...
                entry_id_match = re.search(r'entryid[/%]([^/%&]+)', url)
                if entry_id_match:
                    entry_id = entry_id_match.group(1)
                    # Convert to kalvidres URL format (same as standalone kalvidres modules)
                    # This allows the video to be downloaded using the existing kalvidres handler
                    url = f'https://{self.moodle_domain}/browseandembed/index/media/entryid/{entry_id}'
                    location['module_modname'] = 'cookie_mod-kalvidres'
                    kaltura_converted = True
                    logging.info(f'🎬 Converted Kaltura LTI URL to kalvidres format: entry_id={entry_id}')

            # Determine filename based on URL type
            if url.startswith('data:'):
                # Schema: data:[<mime type>][;charset=<Charset>][;base64],<Data>
                embedded_data = url.split(',', 1)[1]
                mime_type = url.split(':', 1)[1].split(',', 1)[0].split(';')[0]
                media_type = mime_type.split('/', 1)[0]
                file_extension_guess = mimetypes.guess_extension(mime_type, strict=False)
                if file_extension_guess is None:
                    file_extension_guess = f'.{media_type}'
                m = hashlib.sha1()
                if len(embedded_data) > 100000:
                    # To improve speed hash only first 100kb if file is bigger
                    m.update(embedded_data[:100000].encode(encoding='utf-8'))
                else:
                    m.update(embedded_data.encode(encoding='utf-8'))
                short_data_hash = m.hexdigest()

                fist_guess_filename = f'embedded_{media_type} ({short_data_hash}){file_extension_guess}'
            elif kaltura_converted:
                # Use entry_id for Kaltura videos
                fist_guess_filename = f'Kaltura Video {entry_id}'
            else:
                fist_guess_filename = url
                if len(fist_guess_filename) > 254:
                    fist_guess_filename = fist_guess_filename[:254]

            result.append(
                File(
                    **location,
                    content_filename=fist_guess_filename,
                    content_fileurl=url,
                    content_filesize=0,
                    content_timemodified=0,
                    content_type='description-url',
                    content_isexternalfile=True,
                )
            )
        return result

    def _handle_cookie_mod(self, module_url: str, **location) -> List[File]:
        """
        Create a list of files out of a cookie module
        @param module_url: The url to the cookie module.
        @param location: contains
            section_id: int,
            section_name: str,
            module_id: str,
            module_name: str,
            module_modname: str,
            content_timemodified: int (optional, defaults to 0)
        """
        import logging
        # Extract timemodified from location if provided, otherwise use 0
        content_timemodified = location.pop('content_timemodified', 0)

        file_obj = File(
            **location,
            content_filepath='/',
            content_filename=location['module_name'],
            content_fileurl=module_url,
            content_filesize=0,
            content_timemodified=content_timemodified,
            content_type='cookie_mod',
            content_isexternalfile=True,
        )
        logging.debug(f'Created cookie_mod file: modname={file_obj.module_modname}, filename={file_obj.content_filename}, time={content_timemodified}, url={module_url[:80]}...')
        return [file_obj]

    def _handle_files(self, module_contents: List, **location) -> List[File]:
        """
        Create a list of all files in a module
        @param module_contents: The list of content of the module
        @param location: contains
            section_id: int,
            section_name: str,
            module_id: str,
            module_name: str,
            module_modname: str,
        """
        import logging

        # Debug: Log what we're processing
        if location.get('module_modname') == 'book':
            logging.debug(f'📚 _handle_files() for book: module_name={location.get("module_name")}, contents_count={len(module_contents)}')
            for i, content in enumerate(module_contents):
                logging.debug(f'   Content[{i}]: type={content.get("type")}, filename={content.get("filename", "?")},'
                             f' has_html={("html" in content)}, html_len={len(content.get("html", ""))}')

        files = []
        kalvidres_count = 0
        for content in module_contents:
            content_type = content.get('type', '')
            content_filename = content.get('filename', '')
            content_filepath = content.get('filepath', '/') or '/'
            content_fileurl = content.get('fileurl', '')

            content_description = content.get('description', '')
            content_html = content.get('html', '')

            # Handle embedded Kaltura videos from book chapters
            # Keep module_modname as 'book' so videos are saved inside the book folder
            if content_type == 'kalvidres_embedded':
                logging.info(f'🎥 Processing embedded Kaltura video: {content_filename}')
                # Create File entry, override module_modname to trigger yt-dlp in task.py
                # Path will be: section_name/module_name/content_filepath/content_filename
                # 需要创建一个修改后的 location 副本，覆盖 module_modname
                video_location = {**location, 'module_modname': 'cookie_mod-kalvidres'}
                file_obj = File(
                    **video_location,
                    content_filepath=content_filepath,  # Use filepath from content (e.g., '/691947/')
                    content_filename=content_filename,  # Video name
                    content_fileurl=content_fileurl,  # Kalvidres URL
                    content_filesize=0,
                    content_timemodified=content.get('timemodified', 0),
                    content_type='cookie_mod',  # Mark as cookie_mod for download handling
                    content_isexternalfile=True,
                )
                files.append(file_obj)
                kalvidres_count += 1
                logging.info(f'   Created book-embedded kalvidres file: {content_filename} (path: {content_filepath})')
                continue  # Skip normal file processing for this

            if content_fileurl == '' and location['module_modname'].startswith(('url', 'index_mod', 'cookie_mod')):
                continue

            # Add the extention condition to avoid renaming pdf files or other downloaded content from moodle pages.
            if location['module_modname'].startswith('index_mod') and content_filename.endswith('.html'):
                content_filename = location['module_name']

            file_hash = None
            if content_type in ('description', 'html') and not content.get('no_hash', False):
                hashable_description = self.filter_changing_attributes(content_description)
                m = hashlib.sha1()
                m.update(hashable_description.encode('utf-8'))
                file_hash = m.hexdigest()

            new_file = File(
                **location,
                content_filepath=content_filepath,
                content_filename=content_filename,
                content_fileurl=content_fileurl,
                content_filesize=content.get('filesize', 0),
                content_timemodified=content.get('timemodified', 0),
                content_type=content_type,
                content_isexternalfile=content.get('isexternalfile', False),
                file_hash=file_hash,
            )

            if content_type == 'description':
                new_file.text_content = content_description
                content_html = content_description
            if content_type == 'html':
                new_file.html_content = content_html
            if content_type == 'content':
                new_file.content = content.get('content', '')

            if content_type in ['description', 'html'] and not content.get('no_search_for_urls', False):
                logging.debug(
                    f'🔍 URL extraction for {content_type}: filename={content_filename}, '
                    f'html_length={len(content_html)}, module={location.get("module_modname", "?")}'
                )
                extracted_files = self._find_all_urls(
                    content_html,
                    no_search_for_moodle_urls=content.get('no_search_for_moodle_urls', False),
                    filter_urls_containing=content.get('filter_urls_during_search_containing', []),
                    **location,
                    content_filepath=content_filepath,
                )
                if extracted_files:
                    logging.debug(f'   ✅ Extracted {len(extracted_files)} URLs from {content_filename}')
                    for extracted_file in extracted_files[:3]:  # Log first 3 URLs
                        logging.debug(f'      - {extracted_file.content_fileurl[:80]}...')
                files += extracted_files

            files.append(new_file)

            # 🆕 处理嵌套的 contents 数组（支持层级化的文件结构）
            # 这使得 feature/print-book 分支中 chapter_content['contents'] 中的视频能被正确处理
            nested_contents = content.get('contents', [])
            if nested_contents:
                logging.debug(f'🔄 Processing nested contents in "{content_filename}": {len(nested_contents)} items')

                # 递归处理嵌套内容，保持相同的 location 上下文（module_id, section_id 等）
                nested_files = self._handle_files(nested_contents, **location)
                files += nested_files

                # 统计嵌套内容中的 Kaltura 视频
                # 注意：nested_file.module_modname 仍然是 'book'，不是 'kalvidres'
                # 所以我们检查 content_type 和 fileurl 来识别视频
                for nested_file in nested_files:
                    if nested_file.content_type == 'cookie_mod' and \
                       ('kalvidres' in nested_file.content_fileurl or 'helixmedia' in nested_file.content_fileurl):
                        kalvidres_count += 1

                logging.debug(f'   ✅ Added {len(nested_files)} nested files from "{content_filename}"')

        if kalvidres_count > 0:
            logging.info(f'📤 _handle_files() returning {kalvidres_count} Kaltura videos for module "{location.get("module_name", "?")}"')

        return files

    def _handle_description(
        self,
        module_description: str,
        **location,
    ) -> List[File]:
        """
        Creates a description file
        @param module_description: The description of the module
        @param location: contains
            section_id: int,
            section_name: str,
            module_id: str,
            module_name: str,
            module_modname: str,
        @return: A list of files containing that description and URLs in that description.
        """
        files = []
        content_filepath = '/'

        m = hashlib.sha1()
        hashable_description = self.filter_changing_attributes(module_description)
        m.update(hashable_description.encode('utf-8'))
        hash_description = m.hexdigest()

        if location['module_modname'].startswith(('url', 'index_mod')):
            location['module_modname'] = 'url_description'

        description = File(
            **location,
            content_filepath=content_filepath,
            content_filename=location['module_name'],
            content_fileurl='',
            content_filesize=len(module_description),
            content_timemodified=0,
            content_type='description',
            content_isexternalfile=False,
            file_hash=hash_description,
        )
        description.text_content = module_description
        files.append(description)

        files += self._find_all_urls(
            module_description,
            no_search_for_moodle_urls=False,
            filter_urls_containing=[],
            **location,
            content_filepath=content_filepath,
        )

        return files

    def add_files_to_courses(
        self,
        courses: List[Course],
        core_contents: Dict[int, List[Dict]],
        fetched_mods_files: Dict[str, Dict],
    ):
        """
        @param fetched_mods_files:
            Dictionary of all fetched mod modules files, indexed by mod name, then by courses, then module id
        """
        for course in courses:
            course_sections = core_contents.get(course.id, [])

            fetched_mods = {}
            for mod_name, mod_courses in fetched_mods_files.items():
                fetched_mods[mod_name] = mod_courses.get(course.id, {})

            course.files = self.get_files_in_sections(course_sections, fetched_mods)

    def get_files_from_blocks(self, course_blocks: List[Dict], course_id: int) -> List[File]:
        """
        Processes course blocks and creates File objects for HTML blocks that contain content.
        These blocks typically appear in the course sidebar and can contain important information
        like Key Contacts, announcements, etc.

        @param course_blocks: List of block dictionaries from core_block_get_course_blocks API
        @param course_id: The course ID
        @return: A list of File objects representing the blocks
        """
        files = []

        for block in course_blocks:
            block_name = block.get('name', '')
            block_instance_id = block.get('instanceid', 0)
            block_visible = block.get('visible', True)

            # Only process visible blocks that have contents
            if not block_visible or 'contents' not in block:
                continue

            contents = block['contents']
            title = contents.get('title', '')
            content = contents.get('content', '')

            # Skip blocks without meaningful content
            if not title or not content:
                continue

            # Filter to only include useful blocks (HTML blocks and similar)
            # Skip pure navigation blocks like calendar, search, etc.
            if block_name in ['calendar_month', 'calendar_upcoming', 'search_forums', 'activity_modules']:
                continue

            # Create a safe filename from the block title
            safe_title = PT.to_valid_name(title, is_file=False)
            filename = safe_title

            # Create location info for the block
            location = {
                'module_id': block_instance_id,
                'section_name': '_course_info',  # Special section for course-level info
                'section_id': 0,
                'module_name': title,
                'module_modname': f'block_{block_name}',
            }

            # Calculate hash for change detection
            hash_content = hashlib.md5((content).encode('utf-8')).hexdigest()

            # Create File object for HTML version
            # We save blocks as HTML files so they can be easily converted to Markdown by the downloader
            block_file = File(
                **location,
                content_filepath='/',
                content_filename=filename,
                content_fileurl='',
                content_filesize=len(content),
                content_timemodified=0,
                content_type='html',  # Use 'html' type so downloader saves as .html
                content_isexternalfile=False,
                file_hash=hash_content,
            )
            block_file.html_content = content
            files.append(block_file)

        return files

    def add_blocks_to_course(self, course: Course, course_blocks: List[Dict]):
        """
        Adds block files to a course's file list.

        @param course: The course object to add blocks to
        @param course_blocks: List of block dictionaries for this course
        """
        block_files = self.get_files_from_blocks(course_blocks, course.id)
        course.files.extend(block_files)
