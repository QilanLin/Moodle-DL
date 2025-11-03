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

            logging.info(f'📚 Processing book: "{book_name}" (module_id={module_id})')

            # Initialize book files list
            book_files = []

            # Try to fetch print book HTML
            print_book_html, print_book_url = await self._fetch_print_book_html(module_id, course_id)

            if print_book_html:
                # Success! We have the print book HTML
                # Extract Kaltura videos from the print book
                video_list = self._extract_kaltura_videos_from_print_book(print_book_html, book_name)

                # Create video file entries for download
                for video_info in video_list:
                    video_entry = {
                        'filename': video_info['video_filename'],
                        'filepath': f'/{book_name}_files/',  # Save videos in _files subfolder
                        'fileurl': video_info['lti_launch_url'],
                        'filesize': 0,
                        'timemodified': int(time.time()),
                        'type': 'kalvidres_embedded',
                        'mimetype': 'video/mp4',
                        'entry_id': video_info['entry_id'],
                    }
                    book_files.append(video_entry)

                # Replace iframes with video tags
                modified_html = self._replace_kaltura_iframes_with_video_tags(print_book_html, video_list)

                # Create the print book HTML file entry
                # 🔧 避免双重.html扩展名：如果book_name已经以.html结尾就不再添加
                html_filename = book_name if book_name.endswith('.html') else f"{book_name}.html"
                book_files.append({
                    'filename': html_filename,
                    'filepath': '/',
                    'timemodified': book.get('timemodified', int(time.time())),
                    'html': modified_html,
                    'type': 'html',
                    'no_search_for_urls': True,  # Don't try to download embedded resources automatically
                    'filesize': len(modified_html),
                })

                logging.info(f'✅ Created print book HTML: {html_filename} with {len(video_list)} embedded videos')

            else:
                # Fallback: Use old chapter-based approach if print book fails
                logging.warning(f'⚠️  Could not fetch print book HTML for "{book_name}", falling back to chapter-based download')

                # Keep the old implementation as fallback
                book_files = self.get_introfiles(book, 'book_file', copy=True)

                book_intro = book.get('intro', '')
                intro_file = self.create_intro_file(book_intro)
                if intro_file:
                    intro_file['filename'] = 'Book intro'
                    book_files.append(intro_file)

            # If we successfully fetched print book HTML, skip fallback chapter processing
            if not print_book_html:
                # Fallback: Download chapter files individually
                book_contents = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])
                if len(book_contents) > 1:
                    for chapter_content in book_contents[1:]:
                        book_files.append(chapter_content)

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

            # Create metadata file only for fallback mode
            if not print_book_html:
                book_contents_for_meta = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])
                book_toc = json.loads(book_contents_for_meta[0].get('content', '[]')) if len(book_contents_for_meta) > 0 else []

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

    # Note: _convert_netscape_cookies_to_playwright() has been removed.
    # Use the global function from cookie_manager instead:
    # from moodle_dl.cookie_manager import convert_netscape_cookies_to_playwright

    async def _handle_expired_cookies_and_retry(self, module_id: int, print_book_url: str) -> Tuple[str, str]:
        """
        Handle expired cookies by attempting to auto-refresh and retry.

        使用全局CookieManager来刷新cookies，遵循DRY原则。

        @param module_id: The course module ID of the book
        @param print_book_url: The print book URL that failed
        @return: Tuple of (HTML content, URL) if retry succeeds, or ('', '') if fails
        """
        from moodle_dl.utils import Log
        from moodle_dl.cookie_manager import create_cookie_manager_from_client, convert_netscape_cookies_to_playwright

        try:
            # 使用全局CookieManager刷新cookies
            cookie_manager = create_cookie_manager_from_client(self.client, self.config)

            if not cookie_manager.refresh_cookies(auto_get_token=False):
                # Refresh failed, instructions already shown by CookieManager
                return '', ''

            # Retry the download with fresh cookies
            from playwright.async_api import async_playwright

            playwright_cookies = convert_netscape_cookies_to_playwright(cookie_manager.cookies_path)

            if not playwright_cookies:
                Log.warning('⚠️  转换cookies失败')
                return '', ''

            async with async_playwright() as p:
                browser = await p.firefox.launch(headless=True)
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/115.0',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-GB',
                    timezone_id='Europe/London',
                    accept_downloads=False,
                    ignore_https_errors=False,
                )
                await context.add_cookies(playwright_cookies)
                page = await context.new_page()

                try:
                    response = await page.goto(print_book_url, wait_until='networkidle', timeout=60000)

                    if not response:
                        await browser.close()
                        return '', ''

                    current_url = page.url
                    html_content = await page.content()

                    # Check again if we're redirected (cookies still invalid)
                    from moodle_dl.cookie_manager import CookieManager
                    if CookieManager.is_cookie_expired_response(current_url, html_content):
                        Log.warning('⚠️  重试后仍被重定向，cookies可能需要手动刷新')
                        Log.info('   请确保你在浏览器中已登录Moodle，然后运行: moodle-dl --config')
                        await browser.close()
                        return '', ''

                    # Check if we got book content
                    if 'book_chapter' in html_content or 'book p-4' in html_content:
                        Log.success('✅ 重试成功！已获取print book内容')
                        await browser.close()
                        return html_content, print_book_url
                    else:
                        Log.warning('⚠️  重试后仍未获取到book内容')
                        await browser.close()
                        return '', ''

                except Exception as retry_error:
                    logging.error(f'❌ 重试时出错: {retry_error}')
                    await browser.close()
                    return '', ''

        except Exception as e:
            logging.error(f'❌ 自动刷新cookies时出错: {e}')
            Log.warning('⚠️  请手动重新导出cookies: moodle-dl --config （选择步骤7）')
            return '', ''

    async def _fetch_print_book_html(self, module_id: int, course_id: int) -> Tuple[str, str]:
        """
        Fetch the complete print book HTML from Moodle's print book tool using Playwright.

        This uses a headless browser to download the single-page HTML version of the entire book,
        which includes all chapters, TOC, and embedded content in one file.

        Uses Playwright instead of simple HTTP requests because print book tool requires
        full browser session with SSO cookies.

        @param module_id: The course module ID of the book
        @param course_id: The course ID (used to initialize session)
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
                # 用一个标志来只记录第一个keats请求的详细cookies
                first_request_logged = [False]

                async def log_request(request):
                    if 'keats.kcl.ac.uk' in request.url:
                        headers = await request.all_headers()
                        cookie_header = headers.get('cookie', '')
                        has_moodle_session = 'MoodleSession' in cookie_header

                        # 只详细记录第一个请求
                        if not first_request_logged[0]:
                            logging.debug(f'🔍 第一个HTTP请求: {request.url[:100]}')
                            logging.debug(f'🔍 Cookie header长度: {len(cookie_header)} 字符')
                            logging.debug(f'🔍 Cookie header有MoodleSession: {has_moodle_session}')
                            if cookie_header:
                                logging.debug(f'🔍 Cookie header完整内容: {cookie_header[:500]}')
                            else:
                                logging.debug(f'🔍 ❌ Cookie header为空！')
                            first_request_logged[0] = True

                        if has_moodle_session:
                            # 显示MoodleSession部分
                            for part in cookie_header.split('; '):
                                if 'MoodleSession' in part:
                                    logging.debug(f'🔍 Cookie值: {part}')

                page.on('request', log_request)

                try:
                    # 🔧 先访问课程主页来初始化session
                    # 这可以确保cookies被正确激活并且session状态正确
                    course_url = f"https://{self.client.moodle_url.domain}/course/view.php?id={course_id}"
                    logging.debug(f'🔧 首先访问课程主页来初始化session: {course_url}')
                    init_response = await page.goto(course_url, wait_until='networkidle', timeout=60000)
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
                    response = await page.goto(print_book_url, wait_until='networkidle', timeout=60000)

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

                    # 特殊处理：重定向到enrol页面通常意味着没有权限，而不是cookies过期
                    if 'enrol/index.php' in current_url.lower():
                        logging.warning(f'⚠️  无法访问Print Book：被重定向到课程注册页面')
                        logging.warning(f'⚠️  可能原因：')
                        logging.warning(f'     1. 课程已结束，Print Book功能被禁用')
                        logging.warning(f'     2. 你的账号没有访问Print Book的权限')
                        logging.warning(f'     3. Print Book工具在此课程中未启用')
                        logging.info(f'ℹ️  将使用章节下载模式作为替代方案')
                        await browser.close()
                        return '', ''

                    if CookieManager.is_cookie_expired_response(current_url, html_content):
                        logging.warning(f'⚠️  Cookies已过期 - 尝试自动刷新')
                        logging.warning(f'⚠️  触发原因: URL包含过期标记或内容包含过期关键词')
                        await browser.close()
                        # Try to auto-refresh cookies using global mechanism
                        return await self._handle_expired_cookies_and_retry(module_id, print_book_url)

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
                'relative_path': f"{book_name}_files/{video_filename}",  # Path relative to HTML file
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
