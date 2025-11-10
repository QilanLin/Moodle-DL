import copy
import html
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Dict, List, Tuple

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class BookMod(MoodleMod):
    """
    Moodle Book Module Handler

    This module downloads Moodle Book content with improved chapter organization.

    Compatibility:
    - ✅ Fully compatible with official Moodle API (Web Service)
    - ✅ Follows official Moodle Mobile App implementation patterns
    - ✅ Supports Moodle 3.8+ (stable Web Service API)
    - ✅ Handles all official API endpoints: mod_book_get_books_by_courses, core_course_get_contents

    Key Features:
    - Chapters organized by title (e.g., "01 - Chapter 1 - Introduction") instead of IDs
    - Unified Kaltura video handling with result_builder consistency
    - Print Book HTML with relative path links to local chapter videos
    - Full compatibility with official pluginfile.php URL format

    Reference Sources (Verified Compatibility):
    - Official: /public/mod/book/classes/external.php
    - Official: /public/mod/book/db/services.php
    - Official: /public/mod/book/lib.php (book_export_contents)
    - Mobile App: /src/addons/mod/book/services/book.ts

    See: COMPATIBILITY_VERIFICATION_REPORT.md for detailed verification
    """
    MOD_NAME = 'book'
    MOD_PLURAL_NAME = 'books'
    MOD_MIN_VERSION = 2015111600  # 3.0 (Moodle 3.8+ recommended)

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_books() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:

        logging.info('🔍 [DEBUG] BookMod.real_fetch_mod_entries() CALLED')

        result = {}
        if not self.config.get_download_books():
            logging.info('🔍 [DEBUG] download_books is FALSE, returning empty result')
            return result

        logging.info('🔍 [DEBUG] Calling mod_book_get_books_by_courses API...')
        books = (
            await self.client.async_post(
                'mod_book_get_books_by_courses', self.get_data_for_mod_entries_endpoint(courses)
            )
        ).get('books', [])

        logging.info(f'🔍 [DEBUG] API returned {len(books)} books')

        for book in books:
            course_id = book.get('course', 0)
            module_id = book.get('coursemodule', 0)
            book_name = book.get('name', 'unnamed book')

            logging.info(f'🔍 [DEBUG] Processing book: "{book_name}" (course_id={course_id}, module_id={module_id})')
            logging.info(f'📚 Processing book: "{book_name}" (module_id={module_id})')

            # Initialize book files list
            book_files = []

            # 🎯 方案A：先使用 Mobile API 获取章节分离数据
            logging.info('📖 Step 1: Processing chapters from Mobile API (core_contents)')
            book_contents = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])

            if len(book_contents) > 0:
                # First content is TOC
                book_toc = json.loads(book_contents[0].get('content', '[]'))

                # Generate Table of Contents
                toc_html = '''<!DOCTYPE html>
<html>
    <head>
        <style>
            ol { counter-reset: item }
            li { display: block }
            li:before { content: counters(item, ".")" "; counter-increment: item }
            .hidden { color: #999; font-style: italic; }
            .level-0 { font-weight: bold; }
        </style>
    </head>
    <body>
        '''
                toc_html += self.create_ordered_index(book_toc)
                toc_html += '''
    </body>
</html>'''

                book_files.append({
                    'filename': 'Table of Contents',
                    'filepath': '/',
                    'timemodified': book.get('timemodified', 0),
                    'html': toc_html,
                    'type': 'html',
                    'no_search_for_urls': True,
                    'filesize': len(toc_html),
                })

                # Track chapters with their info for later linking in Print Book
                # 🆕 改进：使用章节标题而不是数字ID
                chapters_by_id = {}  # {chapter_id: {title, folder_name, content, videos, ...}}

                # 🆕 Step 1: Group all content by chapter_id
                # Mobile API returns separate content objects for HTML + attachments
                contents_by_chapter = {}  # {chapter_id: [content1, content2, ...]}
                for content in book_contents[1:]:
                    # Extract chapter ID from filename or fileurl
                    filename = content.get('filename', '')
                    fileurl = content.get('fileurl', '')

                    if '/' in filename:
                        chapter_id = filename.split('/')[0]
                    elif fileurl:
                        match = re.search(r'/chapter/(\d+)/', fileurl)
                        chapter_id = match.group(1) if match else None
                    else:
                        chapter_id = None

                    if chapter_id:
                        if chapter_id not in contents_by_chapter:
                            contents_by_chapter[chapter_id] = []
                        contents_by_chapter[chapter_id].append(content)

                # 🆕 Step 2: Process each chapter (sorted by ID for consistent ordering)
                chapter_count = 0
                for chapter_id in sorted(contents_by_chapter.keys()):
                    chapter_contents_list = contents_by_chapter[chapter_id]
                    chapter_count += 1

                    logging.debug(f'   📁 Processing chapter {chapter_id}: {len(chapter_contents_list)} file(s)')

                    # Find the HTML file (index.html) - this is the main chapter content
                    chapter_html_content = None
                    chapter_attachments = []

                    for content in chapter_contents_list:
                        filename = content.get('filename', '')
                        if filename.endswith('index.html') or filename == 'index.html':
                            chapter_html_content = content
                            logging.debug(f'      Found HTML: {filename}')
                        else:
                            chapter_attachments.append(content)
                            logging.debug(f'      Found attachment: {filename}')

                    if not chapter_html_content:
                        logging.warning(f'   ⚠️ Chapter {chapter_id} has no index.html, skipping')
                        continue

                    # 🆕 从TOC获取章节标题，用于创建文件夹名
                    chapter_title = self._get_chapter_title_from_toc(chapter_id, book_toc)
                    # 格式化文件夹名：添加序号并清理路径 (is_file=False 表示这是文件夹)
                    chapter_folder_name = PT.to_valid_name(f'{chapter_count:02d} - {chapter_title}', is_file=False)
                    logging.info(f'   📁 Chapter {chapter_count}: {chapter_folder_name} ({len(chapter_attachments)} attachment(s))')

                    # Copy chapter_html_content to modify it
                    chapter_content = copy.deepcopy(chapter_html_content)

                    # 修改type为'html'，这样result_builder会自动提取URL
                    chapter_content['type'] = 'html'
                    # 设置filepath为章节文件夹
                    chapter_content['filepath'] = f'/{chapter_folder_name}/'

                    # ⚠️ CRITICAL: 下载完整的HTML内容（包含视频）
                    chapter_fileurl = chapter_content.get('fileurl', '')
                    if chapter_fileurl:
                        logging.debug(f'      🔽 Fetching HTML from: {chapter_fileurl[:80]}...')
                        fetched_html = await self._fetch_chapter_html(chapter_fileurl)
                        if fetched_html:
                            chapter_content['html'] = fetched_html
                            logging.debug(f'      ✅ Fetched {len(fetched_html)} chars')
                        else:
                            chapter_content['html'] = chapter_content.get('content', '')
                            logging.warning(f'      ⚠️ Failed to fetch HTML')
                    else:
                        chapter_content['html'] = chapter_content.get('content', '')

                    # Initialize 'contents' array for additional files (videos + attachments)
                    if 'contents' not in chapter_content:
                        chapter_content['contents'] = []

                    # 🆕 Add attachments (PPT, PDF, etc.) to contents array
                    for attachment in chapter_attachments:
                        # Copy attachment and update filepath to chapter folder
                        attachment_copy = copy.deepcopy(attachment)
                        attachment_copy['filepath'] = f'/{chapter_folder_name}/'
                        # Keep the original type from Mobile API (usually 'file')
                        chapter_content['contents'].append(attachment_copy)
                        logging.debug(f'      📎 Added attachment: {attachment.get("filename", "unknown")}')

                    # 🆕 提取该章节中的Kaltura视频并转换URL
                    chapter_html_content = chapter_content.get('html', '')
                    kaltura_videos = []
                    if chapter_html_content:
                        # 查找章节HTML中的Kaltura iframe
                        kaltura_pattern = r'<iframe[^>]+src="([^"]*filter/kaltura/lti_launch\.php[^"]*)"'
                        matches = re.findall(kaltura_pattern, chapter_html_content, re.IGNORECASE)
                        for idx, iframe_src in enumerate(matches, 1):
                            iframe_src = html.unescape(iframe_src)
                            # 转换URL到标准格式
                            converted_url, entry_id = self._convert_kaltura_url_to_kalvidres(iframe_src)
                            if entry_id:
                                # 为视频生成文件名（包含章节名和 entry_id）
                                # 格式：{章节名} - Video ({entry_id}).mp4
                                # 如果有多个视频：{章节名} - Video 01 ({entry_id}).mp4
                                if len(matches) == 1:
                                    video_name = f'{chapter_title} - Video'
                                else:
                                    video_name = f'{chapter_title} - Video {idx:02d}'
                                video_filename = f'{video_name} ({entry_id}).mp4'

                                # 添加到contents数组，这样result_builder会处理
                                chapter_content['contents'].append({
                                    'filename': video_filename,
                                    'filepath': f'/{chapter_folder_name}/',
                                    'fileurl': converted_url,
                                    'filesize': 0,
                                    'timemodified': int(time.time()),
                                    'type': 'kalvidres_embedded',
                                    'mimetype': 'video/mp4',
                                })

                                kaltura_videos.append({
                                    'entry_id': entry_id,
                                    'filename': video_filename,
                                    'original_url': iframe_src,
                                    'converted_url': converted_url,
                                })

                                logging.debug(f'   🎬 Extracted Kaltura video {idx}: entry_id={entry_id}, filename={video_filename}')

                    # Save chapter reference with metadata
                    chapters_by_id[chapter_id] = {
                        'title': chapter_title,
                        'folder_name': chapter_folder_name,
                        'index': chapter_count,
                        'content': chapter_content,
                        'videos': kaltura_videos,
                    }

                    logging.debug(f'   ✅ Chapter {chapter_id} processed with {len(kaltura_videos)} video(s)')

                logging.info(f'✅ Processed {chapter_count} chapters from Mobile API')
            else:
                # No Mobile API contents
                chapters_by_id = {}

            # 🎯 方案B：然后使用 Playwright 获取完整 Print Book
            logging.info('📖 Step 2: Fetching complete Print Book HTML with Playwright')
            print_book_html, print_book_url = await self._fetch_print_book_html(module_id, course_id)

            if print_book_html:
                # 🆕 改进：使用章节映射链接Print Book中的视频到本地文件
                # 而不是嵌入完整的iframe或重新下载视频

                # 为新helper方法准备章节映射格式
                chapter_mapping_for_print_book = {}
                for chapter_id, chapter_info in chapters_by_id.items():
                    chapter_mapping_for_print_book[chapter_id] = {
                        'folder_name': chapter_info['folder_name'],
                        'title': chapter_info['title'],
                        'videos': chapter_info['videos'],
                    }

                # 使用新helper方法替换Print Book中的iframe为相对路径video标签
                modified_print_book_html = self._create_linked_print_book_html(
                    print_book_html,
                    chapter_mapping_for_print_book
                )

                # Create the print book HTML file entry
                html_filename = book_name if book_name.endswith('.html') else f"{book_name}.html"
                book_files.append({
                    'filename': html_filename,
                    'filepath': '/',
                    'timemodified': book.get('timemodified', int(time.time())),
                    'html': modified_print_book_html,
                    'type': 'html',
                    'no_search_for_urls': True,
                    'filesize': len(modified_print_book_html),
                })

                logging.info(f'✅ Created complete print book HTML with linked videos: {html_filename}')
            else:
                logging.warning('⚠️  Could not fetch print book HTML, only chapter-based files available')

            # Add all chapters to book_files (after Print Book processing is complete)
            for chapter_id, chapter_info in chapters_by_id.items():
                book_files.append(chapter_info['content'])
                logging.debug(f'   Added chapter {chapter_id} with folder: {chapter_info["folder_name"]}')

            logging.info(f'📚 Book "{book_name}" has {len(book_files)} files total')

            module_data = {
                'id': book.get('id', 0),
                'name': book_name,
                'files': book_files,
            }

            logging.info(f'🔍 [DEBUG] Adding book to result: course_id={course_id}, module_id={module_id}, files_count={len(book_files)}')

            self.add_module(
                result,
                course_id,
                module_id,
                module_data,
            )

        logging.info(f'🔍 [DEBUG] Returning result with {len(result)} courses')
        for cid, modules in result.items():
            logging.info(f'🔍 [DEBUG]   Course {cid}: {len(modules)} book modules')
            for mid in modules.keys():
                logging.info(f'🔍 [DEBUG]     Module ID: {mid}')

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

    # Note: Cookies auto-refresh logic is now integrated directly into _fetch_print_book_html()
    # using the retry_count parameter. This follows DRY principle by reusing CookieManager.

    async def _fetch_print_book_html(
        self, module_id: int, course_id: int, retry_count: int = 0
    ) -> Tuple[str, str]:
        """
        Fetch the complete print book HTML from Moodle's print book tool using Playwright.

        This uses a headless browser to download the single-page HTML version of the entire book,
        which includes all chapters, TOC, and embedded content in one file.

        Uses Playwright instead of simple HTTP requests because print book tool requires
        full browser session with SSO cookies.

        自动刷新机制：
        - 如果检测到 cookies 过期（timeout 或重定向到登录页），会自动刷新 cookies
        - 然后重试一次（最多重试1次）

        @param module_id: The course module ID of the book
        @param course_id: The course ID (used to initialize session)
        @param retry_count: Internal parameter for retry logic (0 = first attempt, 1 = retry)
        @return: Tuple of (HTML content as string, base URL for resolving relative links)
        """
        try:
            from playwright.async_api import async_playwright

            # Construct print book URL
            # Format: https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id={module_id}
            url_base = self.client.moodle_url.url_base.rstrip('/')
            print_book_url = f"{url_base}/mod/book/tool/print/index.php?id={module_id}"

            logging.info(f'📖 Fetching print book HTML using headless browser from: {print_book_url}')

            # Get cookies path
            cookies_path = PT.get_cookies_path(self.config.get_misc_files_path())

            if not os.path.exists(cookies_path):
                logging.warning(f'⚠️  Cookies file not found at {cookies_path}, print book download may fail')
                return '', ''

            # Convert cookies from Netscape format to Playwright format (use global function)
            from moodle_dl.cookie_manager import convert_netscape_cookies_to_playwright
            playwright_cookies = convert_netscape_cookies_to_playwright(cookies_path)

            if not playwright_cookies:
                logging.warning(f'⚠️  No cookies loaded, print book download may fail')
                return '', ''

            # Get Moodle domain for request filtering
            moodle_domain = self.client.moodle_url.domain

            # Use Playwright to fetch the page with cookies
            async with async_playwright() as p:
                # Launch headless browser
                browser = await p.firefox.launch(headless=True)

                # Create context with cookies and realistic browser settings
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/115.0',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-GB',
                    timezone_id='Europe/London',
                    accept_downloads=False,
                    ignore_https_errors=False,
                )

                # 🔍 DEBUG: 查看要添加的cookies
                moodle_sessions = [c for c in playwright_cookies if c['name'] == 'MoodleSession']
                logging.debug(f'🔍 准备添加 {len(playwright_cookies)} 个cookies')
                logging.debug(f'🔍 其中MoodleSession cookies: {len(moodle_sessions)} 个')
                for ms_cookie in moodle_sessions:
                    logging.debug(f'🔍 MoodleSession完整信息:')
                    logging.debug(f'   name={ms_cookie["name"]}')
                    logging.debug(f'   value={ms_cookie["value"][:20]}...')
                    logging.debug(f'   domain={ms_cookie["domain"]}')
                    logging.debug(f'   path={ms_cookie["path"]}')
                    logging.debug(f'   httpOnly={ms_cookie["httpOnly"]}')
                    logging.debug(f'   secure={ms_cookie["secure"]}')
                    logging.debug(f'   sameSite={ms_cookie["sameSite"]}')
                    logging.debug(f'   expires={ms_cookie["expires"]}')

                await context.add_cookies(playwright_cookies)

                # 🔍 DEBUG: 验证cookies是否被正确添加
                added_cookies = await context.cookies()
                added_sessions = [c for c in added_cookies if c['name'] == 'MoodleSession']
                logging.debug(f'🔍 实际添加了 {len(added_cookies)} 个cookies')
                logging.debug(f'🔍 其中MoodleSession cookies: {len(added_sessions)} 个')

                # Create page and navigate
                page = await context.new_page()

                # 🔍 DEBUG: 监听所有HTTP请求，查看实际发送的cookies
                # 用一个标志来只记录第一个Moodle请求的详细cookies
                first_request_logged = [False]

                async def log_request(request):
                    if moodle_domain in request.url:
                        headers = await request.all_headers()
                        cookie_header = headers.get('cookie', '')
                        has_moodle_session = 'MoodleSession' in cookie_header

                        # 只详细记录第一个请求（包括 MoodleSession 值）
                        if not first_request_logged[0]:
                            logging.debug(f'🔍 第一个HTTP请求: {request.url[:100]}')
                            logging.debug(f'🔍 Cookie header长度: {len(cookie_header)} 字符')
                            logging.debug(f'🔍 Cookie header有MoodleSession: {has_moodle_session}')
                            if cookie_header:
                                logging.debug(f'🔍 Cookie header完整内容: {cookie_header[:500]}')

                                # 显示MoodleSession的值
                                if has_moodle_session:
                                    for part in cookie_header.split('; '):
                                        if 'MoodleSession' in part:
                                            logging.debug(f'🔍 Cookie值: {part}')
                            else:
                                logging.debug(f'🔍 ❌ Cookie header为空！')
                            first_request_logged[0] = True

                page.on('request', log_request)

                try:
                    # 🔧 先访问课程主页来初始化session
                    # 这可以确保cookies被正确激活并且session状态正确
                    course_url = f"https://{self.client.moodle_url.domain}/course/view.php?id={course_id}"
                    logging.debug(f'🔧 首先访问课程主页来初始化session: {course_url}')
                    # 使用 domcontentloaded 而不是 load - 只等DOM加载，不等所有资源
                    # 这样可以避免被第三方tracking scripts阻塞
                    init_response = await page.goto(course_url, wait_until='domcontentloaded', timeout=60000)
                    if init_response:
                        logging.debug(f'✅ 课程主页访问成功: {page.url}')

                        # 🔍 DEBUG: 保存HTML用于调试
                        init_html = await page.content()
                        debug_path = f'/tmp/playwright_course_page_{course_id}.html'
                        with open(debug_path, 'w', encoding='utf-8') as f:
                            f.write(init_html)
                        logging.debug(f'📝 已保存课程页面HTML到: {debug_path}')
                        logging.debug(f'📝 HTML长度: {len(init_html)} 字符')
                        logging.debug(f'📝 标题: {await page.title()}')

                        await page.wait_for_timeout(1000)  # 等待1秒让session稳定

                    # Navigate to print book page
                    logging.debug(f'🔧 现在访问Print Book页面: {print_book_url}')
                    # 使用 domcontentloaded - 只等DOM加载，避免第三方资源阻塞
                    response = await page.goto(print_book_url, wait_until='domcontentloaded', timeout=60000)

                    if not response:
                        logging.error(f'❌ No response from print book URL')
                        await browser.close()
                        return '', ''

                    # Check if we got redirected to login page
                    current_url = page.url
                    if 'login' in current_url.lower() or 'microsoft' in current_url.lower():
                        logging.warning(f'⚠️  Redirected to login page: {current_url}')
                        logging.warning(f'⚠️  Cookies may have expired, please run: moodle-dl --init --sso')
                        await browser.close()
                        return '', ''

                    # Get the HTML content
                    html_content = await page.content()

                    # Check if we got actual book content or login page
                    is_login_page = 'Sign in to your account' in html_content or 'Microsoft' in html_content[:500]
                    if is_login_page:
                        logging.warning(f'⚠️  Received login page instead of print book content')
                        logging.warning(f'⚠️  Cookies may have expired, please run: moodle-dl --init --sso')
                        await browser.close()
                        return '', ''

                    # Check if cookies expired (use global CookieManager detection)
                    from moodle_dl.cookie_manager import CookieManager

                    # 🔍 DEBUG: 记录详细信息用于调试cookie过期检测
                    logging.debug(f'🔍 Cookie检测 - 当前URL: {current_url}')
                    logging.debug(f'🔍 Cookie检测 - HTML长度: {len(html_content)} 字符')
                    logging.debug(f'🔍 Cookie检测 - HTML开头500字符: {html_content[:500]}')

                    # 检查URL中的过期特征
                    url_has_enrol = 'enrol/index.php' in current_url.lower()
                    url_has_login = '/login/' in current_url.lower()
                    url_has_auth = '/auth/' in current_url.lower()
                    logging.debug(f'🔍 URL检测 - enrol: {url_has_enrol}, login: {url_has_login}, auth: {url_has_auth}')

                    # 检查内容中的过期特征
                    content_lower = html_content.lower()
                    has_guest_user = 'guest user' in content_lower
                    has_not_logged_in = 'not logged in' in content_lower
                    has_login_required = 'login required' in content_lower
                    has_auth_required = 'authentication required' in content_lower
                    has_session_expired = 'session expired' in content_lower
                    logging.debug(f'🔍 内容检测 - guest user: {has_guest_user}, not logged in: {has_not_logged_in}')
                    logging.debug(f'🔍 内容检测 - login required: {has_login_required}, auth required: {has_auth_required}, session expired: {has_session_expired}')

                    # 检测是否被重定向（cookies过期或权限问题）
                    if CookieManager.is_cookie_expired_response(current_url, html_content):
                        is_enrol_page = 'enrol/index.php' in current_url.lower()

                        logging.warning(f'⚠️  检测到重定向到：{current_url}')
                        await browser.close()

                        # 🔄 自动刷新 cookies 并重试（仅第一次失败时）
                        if retry_count == 0:
                            if is_enrol_page:
                                logging.info('🔍 检测到重定向到enrol页面 - 可能是cookies过期或权限问题')
                            else:
                                logging.info('🔍 检测到重定向到登录页面 - cookies已过期')

                            logging.info('🔄 尝试自动刷新cookies并重试...')

                            # 使用 CookieManager 刷新 cookies（复用现有机制，符合DRY原则）
                            from moodle_dl.cookie_manager import create_cookie_manager_from_client
                            cookie_manager = create_cookie_manager_from_client(self.client, self.config)

                            if cookie_manager.refresh_cookies(auto_get_token=False):
                                logging.info('✅ Cookies刷新成功，正在重试Print Book下载...')
                                # 递归调用自己，retry_count = 1 确保只重试一次
                                return await self._fetch_print_book_html(module_id, course_id, retry_count=1)
                            else:
                                logging.warning('⚠️  自动刷新cookies失败，请手动操作')
                                return '', ''
                        else:
                            # 重试后仍然失败 - 区分是权限问题还是cookies问题
                            if is_enrol_page:
                                logging.warning('⚠️  刷新cookies后仍被重定向到enrol页面')
                                logging.warning('⚠️  这可能是真正的权限/课程访问问题：')
                                logging.warning('     1. 课程已结束，Print Book功能被禁用')
                                logging.warning('     2. 你的账号没有访问此课程的权限')
                                logging.warning('     3. Print Book工具在此课程中未启用')
                                logging.info('ℹ️  将使用章节下载模式作为替代方案')
                            else:
                                logging.error('❌ 刷新cookies后仍被重定向到登录页面')
                                logging.info('💡 请确保在浏览器中已登录Moodle，然后重新导出cookies')
                            return '', ''

                    # Check if we got the actual book content
                    if 'book_chapter' not in html_content and 'book p-4' not in html_content:
                        logging.warning(f'⚠️  Page content does not appear to be a book (no book_chapter class found)')
                        logging.debug(f'HTML start: {html_content[:500]}...')
                        logging.debug(f'Current URL after load: {current_url}')
                        # Save HTML for debugging
                        debug_path = f'/tmp/playwright_debug_{module_id}.html'
                        try:
                            with open(debug_path, 'w', encoding='utf-8') as f:
                                f.write(html_content)
                            logging.debug(f'Saved debug HTML to: {debug_path}')
                        except Exception as e:
                            logging.debug(f'Could not save debug HTML: {e}')
                        await browser.close()
                        return '', ''

                    logging.info(f'✅ Successfully fetched print book HTML ({len(html_content)} bytes)')
                    await browser.close()
                    return html_content, print_book_url

                except Exception as page_error:
                    logging.error(f'❌ Error while loading page: {page_error}')
                    await browser.close()

                    # Check if this might be a timeout/expired cookies issue
                    error_str = str(page_error).lower()
                    is_timeout_error = 'timeout' in error_str

                    # 🔄 自动刷新 cookies 并重试（仅第一次失败时）
                    if is_timeout_error and retry_count == 0:
                        logging.warning(f'⚠️  检测到超时 - cookies可能已过期')
                        logging.info('🔄 尝试自动刷新cookies并重试...')

                        # 使用 CookieManager 刷新 cookies（复用现有机制，符合DRY原则）
                        from moodle_dl.cookie_manager import create_cookie_manager_from_client
                        cookie_manager = create_cookie_manager_from_client(self.client, self.config)

                        if cookie_manager.refresh_cookies(auto_get_token=False):
                            logging.info('✅ Cookies刷新成功，正在重试Print Book下载...')

                            # 递归调用自己，retry_count = 1 确保只重试一次
                            return await self._fetch_print_book_html(module_id, course_id, retry_count=1)
                        else:
                            logging.warning('⚠️  自动刷新cookies失败')
                            logging.info('')
                            logging.info('🔧 请手动刷新cookies：')
                            logging.info('   方法1: moodle-dl --init --sso')
                            logging.info('   方法2: 在config.json中添加 "preferred_browser": "firefox"')
                            logging.info('')
                            return '', ''
                    elif is_timeout_error and retry_count > 0:
                        # 重试后仍然失败
                        logging.error('❌ 刷新cookies后仍然超时，Print Book下载失败')
                        logging.info('💡 可能的原因：')
                        logging.info('   1. 浏览器cookies本身已过期（请重新登录Moodle）')
                        logging.info('   2. Print Book页面加载确实很慢')
                        logging.info('   3. 网络连接问题')
                        return '', ''
                    else:
                        # 非超时错误，直接返回
                        return '', ''

        except Exception as e:
            logging.error(f'❌ Exception while fetching print book HTML with Playwright: {e}')
            import traceback
            logging.debug(f'Traceback: {traceback.format_exc()}')
            return '', ''

    def _extract_kaltura_videos_from_print_book(self, html_content: str, book_name: str) -> List[Dict]:
        """
        Extract all Kaltura video iframes from the print book HTML.

        @param html_content: The complete print book HTML
        @param book_name: Name of the book (for video naming)
        @return: List of video info dictionaries containing iframe_src, entry_id, video_name, etc.
        """
        video_list = []

        # Pattern to match Kaltura iframe with lti_launch.php
        kaltura_pattern = r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src="([^"]*filter/kaltura/lti_launch\.php[^"]*)"[^>]*>'

        matches = re.findall(kaltura_pattern, html_content, re.IGNORECASE | re.DOTALL)

        logging.info(f'🎬 Found {len(matches)} Kaltura video(s) in print book')

        for idx, iframe_src in enumerate(matches, 1):
            # Unescape HTML entities
            iframe_src_unescaped = html.unescape(iframe_src)

            # Extract the source parameter which contains the actual Kaltura URL
            source_match = re.search(r'[?&]source=([^&]+)', iframe_src_unescaped)
            if not source_match:
                logging.warning(f'⚠️  Could not extract source parameter from iframe {idx}')
                continue

            # Decode the URL-encoded source parameter
            kaltura_source = urllib.parse.unquote(source_match.group(1))

            # Extract entry ID from the Kaltura URL
            entry_id_match = re.search(r'/entryid/([^/]+)', kaltura_source)
            if not entry_id_match:
                logging.warning(f'⚠️  Could not extract entry ID from Kaltura source {idx}')
                continue

            entry_id = entry_id_match.group(1)

            # Generate video filename
            video_name = f"{book_name} - Video {idx:02d}" if len(matches) > 1 else f"{book_name} - Video"
            video_filename = f"{video_name} ({entry_id}).mp4"

            video_info = {
                'iframe_src': iframe_src,  # Original iframe src (may contain HTML entities)
                'iframe_src_unescaped': iframe_src_unescaped,  # Unescaped version
                'entry_id': entry_id,
                'video_name': video_name,
                'video_filename': video_filename,
                'lti_launch_url': iframe_src_unescaped,  # URL for yt-dlp
                'relative_path': video_filename,  # Video in same directory as HTML file
            }

            video_list.append(video_info)
            logging.debug(f'   Video {idx}: {video_name} (entry_id: {entry_id})')

        return video_list

    def _replace_kaltura_iframes_with_video_tags(self, html_content: str, video_list: List[Dict]) -> str:
        """
        Replace Kaltura iframe tags with HTML5 video tags pointing to local video files.

        @param html_content: The print book HTML content
        @param video_list: List of video info dictionaries from _extract_kaltura_videos_from_print_book
        @return: Modified HTML content with video tags instead of iframes
        """
        modified_html = html_content

        for video_info in video_list:
            iframe_src = video_info['iframe_src']
            relative_path = video_info['relative_path']
            video_name = video_info['video_name']

            # Create HTML5 video tag to replace the iframe
            # Keep width and height for consistency
            video_tag = f'''<div class="kaltura-video-container" style="max-width: 608px; margin: 20px auto;">
    <video controls style="width: 100%; max-width: 608px; height: auto;" preload="metadata">
        <source src="{relative_path}" type="video/mp4">
        <p>Your browser does not support HTML5 video. <a href="{relative_path}">Download the video</a> instead.</p>
    </video>
    <p style="text-align: center; font-size: 0.9em; color: #666; margin-top: 10px;">{video_name}</p>
</div>'''

            # Replace the entire iframe tag with the video tag
            # We need to match the complete iframe tag, not just the src
            iframe_pattern = r'<iframe[^>]*src="' + re.escape(iframe_src) + r'"[^>]*>.*?</iframe>'

            # Try with closing tag
            if re.search(iframe_pattern, modified_html, re.DOTALL):
                modified_html = re.sub(iframe_pattern, video_tag, modified_html, flags=re.DOTALL)
                logging.debug(f'✅ Replaced iframe with video tag for: {video_name}')
            else:
                # Try self-closing tag
                iframe_pattern_selfclose = r'<iframe[^>]*src="' + re.escape(iframe_src) + r'"[^>]*/>'
                if re.search(iframe_pattern_selfclose, modified_html):
                    modified_html = re.sub(iframe_pattern_selfclose, video_tag, modified_html)
                    logging.debug(f'✅ Replaced self-closing iframe with video tag for: {video_name}')
                else:
                    logging.warning(f'⚠️  Could not find iframe tag to replace for: {video_name}')

        logging.info(f'✅ Replaced {len(video_list)} Kaltura iframe(s) with HTML5 video tags')
        return modified_html

    def _extract_kaltura_videos_from_chapter(
        self, chapter_html: str, chapter_folder: str, chapter_num: int
    ) -> List[Dict]:
        """
        Extract Kaltura videos from a single chapter HTML (from Mobile API).

        @param chapter_html: Chapter HTML content
        @param chapter_folder: Chapter folder name for file organization
        @param chapter_num: Chapter number for naming
        @return: List of video info dictionaries
        """
        video_list = []

        # Pattern to match Kaltura iframe
        kaltura_pattern = r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src="([^"]*filter/kaltura/lti_launch\.php[^"]*)"[^>]*>'
        matches = re.findall(kaltura_pattern, chapter_html, re.IGNORECASE | re.DOTALL)

        for idx, iframe_src in enumerate(matches, 1):
            iframe_src_unescaped = html.unescape(iframe_src)

            # Extract source parameter
            source_match = re.search(r'[?&]source=([^&]+)', iframe_src_unescaped)
            if not source_match:
                continue

            kaltura_source = urllib.parse.unquote(source_match.group(1))

            # Extract entry ID
            entry_id_match = re.search(r'/entryid/([^/]+)', kaltura_source)
            if not entry_id_match:
                continue

            entry_id = entry_id_match.group(1)

            # Generate video filename (简洁格式，不包含章节ID)
            if len(matches) > 1:
                video_name = f"Video {idx:02d}"
            else:
                video_name = "Video"
            video_filename = f"{video_name} ({entry_id}).mp4"

            video_info = {
                'iframe_src': iframe_src,
                'iframe_src_unescaped': iframe_src_unescaped,
                'entry_id': entry_id,
                'video_name': video_name,
                'video_filename': video_filename,
                'lti_launch_url': iframe_src_unescaped,
                'relative_path': video_filename,  # Relative to chapter folder
            }

            video_list.append(video_info)
            logging.debug(f'   Chapter {chapter_num} Video {idx}: {video_name} (entry_id: {entry_id})')

        return video_list

    def _replace_print_book_videos_with_chapter_links(
        self, print_book_html: str, print_book_videos: List[Dict], downloaded_videos: Dict[str, str],
        video_to_chapter: Dict[str, str] = None
    ) -> str:
        """
        Replace video iframes in print book with links to chapter videos.

        @param print_book_html: Complete print book HTML
        @param print_book_videos: Videos extracted from print book
        @param downloaded_videos: Dict mapping entry_id to relative path of downloaded video
        @param video_to_chapter: Dict mapping entry_id to chapter_id (optional)
        @return: Modified print book HTML with video links to chapter files
        """
        modified_html = print_book_html
        video_to_chapter = video_to_chapter or {}

        for video_info in print_book_videos:
            entry_id = video_info['entry_id']
            iframe_src = video_info['iframe_src']

            # 优先使用已下载的视频路径
            if entry_id in downloaded_videos:
                chapter_video_path = downloaded_videos[entry_id]
                video_name = video_info['video_name']
                logging.debug(f'   Using downloaded video: {chapter_video_path}')

            elif entry_id in video_to_chapter:
                # 虽然没下载，但知道属于哪个章节，生成路径
                chapter_id = video_to_chapter[entry_id]

                # 计算这是该章节的第几个视频
                chapter_videos_list = [eid for eid, cid in video_to_chapter.items() if cid == chapter_id]
                video_idx = chapter_videos_list.index(entry_id) + 1

                # 生成文件名（与Mobile API逻辑一致）
                if len(chapter_videos_list) > 1:
                    video_filename = f"Video {video_idx:02d} ({entry_id}).mp4"
                    video_name = f"Video {video_idx:02d}"
                else:
                    video_filename = f"Video ({entry_id}).mp4"
                    video_name = "Video"

                chapter_video_path = f"{chapter_id}/{video_filename}"
                logging.debug(f'   Generated path for unmapped video: {chapter_video_path}')
            else:
                # 无法确定章节，使用根目录路径
                video_filename = f"Video ({entry_id}).mp4"
                chapter_video_path = video_filename
                video_name = "Video"
                logging.warning(f'⚠️  Cannot determine chapter for video {entry_id}, using root path')

            # 创建HTML5 video标签
            video_tag = f'''<div class="kaltura-video-container" style="max-width: 608px; margin: 20px auto;">
    <video controls style="width: 100%; max-width: 608px; height: auto;" preload="metadata">
        <source src="{chapter_video_path}" type="video/mp4">
        <p>Your browser does not support HTML5 video. <a href="{chapter_video_path}">Download the video</a> instead.</p>
    </video>
    <p style="text-align: center; font-size: 0.9em; color: #666; margin-top: 10px;">{video_name}</p>
</div>'''

            # 替换iframe
            iframe_pattern = r'<iframe[^>]*src="' + re.escape(iframe_src) + r'"[^>]*>.*?</iframe>'
            if re.search(iframe_pattern, modified_html, re.DOTALL):
                modified_html = re.sub(iframe_pattern, video_tag, modified_html, flags=re.DOTALL)
                logging.debug(f'✅ Replaced iframe with video: {chapter_video_path}')
            else:
                # Try self-closing iframe
                iframe_pattern_selfclose = r'<iframe[^>]*src="' + re.escape(iframe_src) + r'"[^>]*/>'
                if re.search(iframe_pattern_selfclose, modified_html):
                    modified_html = re.sub(iframe_pattern_selfclose, video_tag, modified_html)
                    logging.debug(f'✅ Replaced iframe with video: {chapter_video_path}')

        logging.info(f'✅ Replaced {len(print_book_videos)} video iframe(s) in print book')
        return modified_html

    def _extract_chapter_video_mapping_from_print_book(self, html_content: str) -> Dict[str, List[str]]:
        """
        从Print Book HTML提取章节ID与视频entry_id的映射

        Print Book HTML结构示例：
        <div class="book_chapter" id="ch691947">
            <h2>2. Week Overview</h2>
            <iframe src="...entryid/1_er5gtb0g..."></iframe>
        </div>

        @param html_content: Print Book完整HTML
        @return: {chapter_id: [entry_id1, entry_id2, ...]}
        """
        chapter_video_mapping = {}

        # 🔍 DEBUG: 保存Print Book HTML用于调试
        import os
        import tempfile
        debug_file = os.path.join(tempfile.gettempdir(), 'print_book_debug.html')
        try:
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logging.debug(f'🔍 Saved Print Book HTML to: {debug_file}')
        except:
            pass

        # 匹配每个章节div及其内容
        # 注意：class可能是 "book_chapter pt-3" 等，需要匹配包含 book_chapter 的class
        # 使用更宽松的匹配：找到包含 book_chapter 的div，提取ID，然后匹配到下一个 book_chapter div 或者文档结束
        chapter_pattern = r'<div[^>]*class="[^"]*book_chapter[^"]*"[^>]*id="ch(\d+)"[^>]*>(.*?)(?=<div[^>]*class="[^"]*book_chapter|$)'

        matches = list(re.finditer(chapter_pattern, html_content, re.DOTALL))
        logging.debug(f'🔍 Found {len(matches)} chapter divs in Print Book HTML (pattern: class contains "book_chapter")')

        for match in matches:
            chapter_id = match.group(1)  # "691947"
            chapter_html = match.group(2)

            # 在这个章节的HTML中查找所有Kaltura视频entry_id
            # 注意：URL可能被编码了（%2Fentryid%2F），需要先解码
            import urllib.parse
            chapter_html_decoded = urllib.parse.unquote(chapter_html)

            video_pattern = r'/entryid/([^/"\s]+)'
            video_entry_ids = re.findall(video_pattern, chapter_html_decoded)

            if video_entry_ids:
                chapter_video_mapping[chapter_id] = video_entry_ids
                logging.debug(f'   Chapter {chapter_id}: found {len(video_entry_ids)} video(s)')
            else:
                logging.debug(f'   Chapter {chapter_id}: no videos found')

        logging.info(f'📊 Extracted video mapping for {len(chapter_video_mapping)} chapters from Print Book')
        return chapter_video_mapping

    def _build_video_to_chapter_mapping(self, chapter_video_mapping: Dict[str, List[str]]) -> Dict[str, str]:
        """
        建立视频entry_id到章节ID的反向映射

        @param chapter_video_mapping: {chapter_id: [entry_id1, entry_id2]}
        @return: {entry_id: chapter_id}
        """
        video_to_chapter = {}

        for chapter_id, entry_ids in chapter_video_mapping.items():
            for entry_id in entry_ids:
                video_to_chapter[entry_id] = chapter_id

        logging.debug(f'   Built reverse mapping for {len(video_to_chapter)} videos')
        return video_to_chapter

    def _get_chapter_title_from_toc(self, chapter_id: str, toc: List[Dict]) -> str:
        """
        从TOC（目录）中查找指定章节ID对应的标题

        @param chapter_id: 章节ID（数字字符串）
        @param toc: TOC数据结构（嵌套列表）
        @return: 章节标题或 "Chapter {chapter_id}" 作为备选
        """
        def search_toc(items: List[Dict], target_id: str) -> str:
            for item in items:
                # TOC中的href格式如 "691946/index.html" 或 "691946/"
                href = item.get('href', '')
                if href.startswith(target_id + '/'):
                    title = item.get('title', f'Chapter {target_id}')
                    return title
                # 递归搜索子项
                subitems = item.get('subitems', [])
                if subitems:
                    result = search_toc(subitems, target_id)
                    if result:
                        return result
            return None

        title = search_toc(toc, chapter_id)
        return title if title else f'Chapter {chapter_id}'

    def _convert_kaltura_url_to_kalvidres(self, url: str) -> Tuple[str, str]:
        """
        从 Kaltura LTI launch URL 中提取 entry_id。
        不转换 URL，让 task.py 的 extract_kalvidres_video_url 处理完整流程。

        task.py 的 extract_kalvidres_video_url 需要原始的 LTI launch URL 或包含
        lti_launch.php iframe 的页面 URL，因为它需要通过完整的 LTI launch 流程来
        获取正确的认证和参数。

        @param url: 原始 LTI launch URL
        @return: Tuple of (原始 URL, entry_id) 或 (原始url, '') 如果提取失败
        """
        # 检测Kaltura URL
        if '/filter/kaltura/lti_launch.php' not in url:
            return url, ''

        # URL 解码并提取 entry_id（仅用于文件命名）
        # URL 可能包含 %2F (/) 等编码字符
        decoded_url = urllib.parse.unquote(url)
        entry_id_match = re.search(r'entryid[/%]([^/%&]+)', decoded_url)
        entry_id = entry_id_match.group(1) if entry_id_match else ''

        if entry_id:
            logging.debug(f'✅ Extracted entry_id from Kaltura URL: {entry_id}')
        else:
            logging.warning(f'⚠️  Cannot extract entry_id from URL: {decoded_url[:100]}')

        # ✅ 返回原始 LTI launch URL，不转换
        # 这与 book6 分支的方法一致，让 task.py 处理完整的下载流程
        return url, entry_id

    def _create_linked_print_book_html(
        self, print_book_html: str, chapter_mapping: Dict[str, Dict]
    ) -> str:
        """
        替换Print Book HTML中的Kaltura iframe为相对路径的video标签。

        Print Book中的视频应该链接到对应章节文件夹中的视频文件。

        @param print_book_html: 完整的Print Book HTML
        @param chapter_mapping: 章节映射 {chapter_id: {title, folder_name, videos: [{entry_id, filename}]}}
        @return: 修改后的Print Book HTML
        """
        modified_html = print_book_html

        # 首先建立entry_id到相对路径的映射
        entry_id_to_path = {}
        for chapter_id, chapter_info in chapter_mapping.items():
            folder_name = chapter_info.get('folder_name', '')
            for video_info in chapter_info.get('videos', []):
                entry_id = video_info.get('entry_id', '')
                filename = video_info.get('filename', '')
                if entry_id and filename:
                    entry_id_to_path[entry_id] = f'{folder_name}/{filename}'

        # 提取Print Book中的所有Kaltura iframe
        kaltura_pattern = r'<iframe[^>]*class="kaltura-player-iframe"[^>]*src="([^"]*filter/kaltura/lti_launch\.php[^"]*)"[^>]*>'
        matches = list(re.finditer(kaltura_pattern, modified_html, re.IGNORECASE | re.DOTALL))

        logging.info(f'🎬 Found {len(matches)} Kaltura iframe(s) in print book to link')

        for match in matches:
            iframe_src = match.group(1)

            # URL 解码后再提取 entry_id（修复 %2F 编码问题）
            # 例如：entryid%2F1_xxx → entryid/1_xxx
            decoded_src = urllib.parse.unquote(iframe_src)
            entry_id_match = re.search(r'entryid[/%]([^/%&]+)', decoded_src)
            if not entry_id_match:
                continue

            entry_id = entry_id_match.group(1)

            # 查找相对路径
            if entry_id not in entry_id_to_path:
                logging.warning(f'⚠️  Cannot find chapter folder for video {entry_id}, skipping')
                continue

            relative_path = entry_id_to_path[entry_id]

            # 创建HTML5 video标签
            video_tag = f'''<div class="kaltura-video-container" style="max-width: 608px; margin: 20px auto;">
    <video controls style="width: 100%; max-width: 608px; height: auto;" preload="metadata">
        <source src="{relative_path}" type="video/mp4">
        <p>Your browser does not support HTML5 video. <a href="{relative_path}">Download the video</a> instead.</p>
    </video>
</div>'''

            # 替换iframe为video标签
            iframe_pattern = re.escape(iframe_src)
            full_iframe_pattern = r'<iframe[^>]*class="kaltura-player-iframe"[^>]*src="' + iframe_pattern + r'"[^>]*>'

            if re.search(full_iframe_pattern, modified_html):
                modified_html = re.sub(full_iframe_pattern, video_tag, modified_html)
                logging.debug(f'✅ Replaced iframe with linked video: {relative_path}')

        logging.info(f'✅ Converted {len(matches)} Kaltura iframe(s) to linked video tags in print book')
        return modified_html
