# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any, Dict, List, Optional, Tuple

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.html_localizer import build_local_resource_map, rewrite_html_links_to_local_paths
from moodle_dl.downloader.progress_tracker import ProgressTracker
from moodle_dl.downloader.task import Task
from moodle_dl.types import Course, DlEvent, DownloadOptions, DownloadStatus, File, MoodleDlOpts, TaskState
from moodle_dl.utils import calc_speed, format_bytes, format_speed, PathTools as PT


class DownloadPauseController:
    """Handle pause/resume hotkeys without interrupting the active file."""

    PAUSE_KEYS = {'p', 'P'}
    RESUME_KEYS = {'r', 'R'}

    def __init__(self, *, enabled: Optional[bool] = None, read_key=None, poll_interval: float = 0.2):
        self.enabled = sys.stdin.isatty() if enabled is None else enabled
        self.read_key = read_key
        self.poll_interval = poll_interval
        self._pause_requested = False
        self._paused = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen_for_hotkeys,
            name='moodle-dl-pause-hotkeys',
            daemon=True,
        )
        self._thread.start()
        logging.info(
            '快捷键 / Hotkeys: 按 P 在当前文件完成后暂停；暂停后按 R 继续。'
            ' / Press P to pause after the current file finishes; press R to resume.'
        )

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._pause_requested = False
            self._paused = False

    def handle_key(self, key: str) -> str:
        if key in self.PAUSE_KEYS:
            with self._lock:
                if self._paused or self._pause_requested:
                    return ''
                self._pause_requested = True
                return 'pause_requested'

        if key in self.RESUME_KEYS:
            with self._lock:
                if self._pause_requested or self._paused:
                    self._pause_requested = False
                    self._paused = False
                    return 'resume'

        return ''

    def _listen_for_hotkeys(self) -> None:
        read_key = self.read_key
        if read_key is None:
            try:
                import readchar
            except ImportError:
                logging.debug('readchar is not available; pause hotkeys are disabled')
                return
            read_key = readchar.readkey

        while not self._stop_event.is_set():
            try:
                action = self.handle_key(read_key())
            except (EOFError, KeyboardInterrupt):
                return
            except Exception as exc:
                logging.debug('Pause hotkey listener stopped: %s', exc)
                return

            if action == 'pause_requested':
                logging.info(
                    '已请求暂停：当前文件下载完成后会暂停。'
                    ' / Pause requested: download will pause after the current file finishes.'
                )
            elif action == 'resume':
                logging.info('继续下载。 / Resuming download.')

    def consume_pause_request(self) -> bool:
        with self._lock:
            if not self._pause_requested:
                return False
            self._paused = True
            return True

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    async def wait_if_requested(self) -> None:
        if not self.consume_pause_request():
            return

        logging.info('下载已暂停。按 R 继续。 / Download paused. Press R to resume.')
        while self.is_paused():
            await asyncio.sleep(self.poll_interval)
        logging.info('下载已恢复。 / Download resumed.')


class DownloadService:
    "Manages jobs to download, delete or create files of courses"

    PROGRESS_LOG_MIN_BYTES = 16 * 1024 * 1024

    def __init__(self, courses: List[Course], config: ConfigHelper, opts: MoodleDlOpts, database: StateRecorder):
        self.courses = courses
        self.config = config
        self.opts = opts
        self.database = database

        # 设置文件名限制（解决全局状态问题）
        # 注意：这仍然使用类变量，但至少集中在一个地方设置
        PT.restricted_filenames = config.get_restricted_filenames()

        self.status = DownloadStatus()
        self.all_tasks = self.gen_all_tasks()
        
        # 🆕 增强的进度追踪器
        self.progress_tracker = ProgressTracker()
        self.pause_controller = DownloadPauseController()
        self._status_log_event = None
        self._status_log_loop = None
        self._last_logged_status_snapshot = None
        self._bytes_downloaded_at_last_status_log_signal = 0

    def _configure_task_settings(self) -> tuple:
        """Configure task settings and create thread pool.
        
        Returns:
            Tuple of (download_options, thread_pool)
        """
        Task.CHUNK_SIZE = self.opts.download_chunk_size
        dl_options = self.config.get_download_options(self.opts)
        thread_pool = ThreadPoolExecutor(max_workers=self.opts.max_parallel_yt_dlp)
        return dl_options, thread_pool

    def _load_incomplete_downloads_map(self) -> Dict[int, Dict[str, Any]]:
        """Load incomplete downloads from database and create a lookup map.
        
        Returns:
            Dictionary mapping file_id to incomplete download info
        """
        incomplete_downloads = self.database.get_incomplete_downloads_for_retry()
        incomplete_files_map = {}
        for incomplete in incomplete_downloads:
            file_id = incomplete['file_id']
            incomplete_files_map[file_id] = incomplete
        return incomplete_files_map

    def _create_task(
        self, course_file: File, course: Course, dl_options: DownloadOptions, thread_pool: ThreadPoolExecutor, api_source: str = 'mobile'
    ) -> Task:
        """Create a single Task object.
        
        Args:
            course_file: File object from course
            course: Course object
            dl_options: Download options configuration
            thread_pool: ThreadPoolExecutor for yt-dlp
            api_source: API source ('mobile' or 'web'), defaults to 'mobile'
            
        Returns:
            Task object
        """
        task = Task(
            task_id=self.status.files_to_download,
            file=course_file,
            course=course,
            options=dl_options,
            thread_pool=thread_pool,
            callback=self.status_callback,
        )
        # 标记任务使用的 API 来源
        task.api_source = api_source
        return task

    def _is_incomplete_download(self, course_file: File, incomplete_files_map: Dict[int, Dict[str, Any]]) -> bool:
        """Check if a file is an incomplete download that needs to be resumed.
        
        Args:
            course_file: File object to check
            incomplete_files_map: Map of incomplete downloads
            
        Returns:
            True if file is incomplete, False otherwise
        """
        return course_file.file_id and course_file.file_id in incomplete_files_map

    def _log_incomplete_download(self, course_file: File, incomplete_files_map: Dict[int, Dict[str, Any]]) -> None:
        """Log information about detected incomplete download.
        
        Args:
            course_file: File object
            incomplete_files_map: Map of incomplete downloads
        """
        incomplete_info = incomplete_files_map[course_file.file_id]
        logging.info(
            '✅ 检测到未完成的下载（%s/%s 字节）：%s',
            format_bytes(incomplete_info['downloaded_bytes']),
            format_bytes(incomplete_info['total_bytes']),
            course_file.content_filename
        )

    def _update_download_statistics(self, course_file):
        """Update download statistics when processing a file.
        
        Args:
            course_file: File object being processed
        """
        self.status.bytes_to_download += course_file.content_filesize
        self.status.files_to_download += 1

    def _log_queue_summary(self, priority_task_count: int, web_api_task_count: int = 0):
        """Log a summary of the download queue.
        
        Args:
            priority_task_count: Number of priority (incomplete) tasks
            web_api_task_count: Number of tasks from web API (manually specified courses)
        """
        if self.status.files_to_download > 0:
            msg_parts = [f'下载队列包含 {self.status.files_to_download} 个任务']
            if priority_task_count > 0:
                msg_parts.append(f'{priority_task_count} 个未完成的下载需要续传')
            if web_api_task_count > 0:
                msg_parts.append(f'{web_api_task_count} 个来自手动指定课程 (Web API)')
            
            # 构建最终消息
            final_msg = msg_parts[0] + (' (' + ', '.join(msg_parts[1:]) + ')' if len(msg_parts) > 1 else '')
            logging.info(final_msg)
        else:
            logging.debug('下载队列为空')

    def _cleanup_old_incomplete_downloads(self):
        """Clean up incomplete download records older than 7 days."""
        try:
            cleaned = self.database.cleanup_old_incomplete_downloads(days_old=7)
            if cleaned > 0:
                logging.debug('清理了 %d 个超期的未完成下载记录', cleaned)
        except Exception as cleanup_err:
            logging.debug('清理未完成下载记录时出错: %s', cleanup_err)

    def gen_all_tasks(self) -> List:
        """Generate all download tasks from courses.
        
        Process pipeline:
        1. Configure task settings (chunk size, thread pool)
        2. Load incomplete downloads map
        3. Iterate through courses and files
        4. Create tasks with proper prioritization (incomplete first)
        5. Add manually specified courses (via web API)
        6. Log queue summary
        7. Cleanup old incomplete download records
        
        Returns:
            List of Task objects, with incomplete downloads prioritized
        """
        # Step 1: Configure settings
        dl_options, thread_pool = self._configure_task_settings()
        
        # Step 2: Load incomplete downloads map
        incomplete_files_map = self._load_incomplete_downloads_map()
        
        # Step 3-4: Build task queue with priority ordering
        priority_tasks = []  # Incomplete downloads (high priority)
        normal_tasks = []    # Regular downloads (normal priority)
        
        # Process enrolled courses (via Mobile API)
        for course in self.courses:
            for course_file in course.files:
                if course_file.deleted is False:
                    # Create task with 'mobile' API source
                    task = self._create_task(
                        course_file, course, dl_options, thread_pool, 
                        api_source='mobile'
                    )
                    
                    # Categorize by priority
                    if self._is_incomplete_download(course_file, incomplete_files_map):
                        priority_tasks.append(task)
                        self._log_incomplete_download(course_file, incomplete_files_map)
                    else:
                        normal_tasks.append(task)
                    
                    # Update statistics
                    self._update_download_statistics(course_file)
        
        # Step 5: Process manually specified courses (via Web API)
        manually_specified_ids = self.config.get_manually_specified_course_ids()
        web_priority_tasks, web_api_tasks = self._create_tasks_for_manually_specified_courses(
            manually_specified_ids, dl_options, thread_pool, incomplete_files_map
        )
        
        # Combine tasks with priority ordering
        all_tasks = priority_tasks + web_priority_tasks + normal_tasks + web_api_tasks
        
        # Step 6: Log summary
        total_priority = len(priority_tasks) + len(web_priority_tasks)
        web_api_task_count = len(web_priority_tasks) + len(web_api_tasks)
        self._log_queue_summary(total_priority, web_api_task_count)
        
        # Step 7: Cleanup old records
        self._cleanup_old_incomplete_downloads()
        
        return all_tasks

    def status_callback(self, event: DlEvent, task: Task, **extra_args):
        """
        处理下载事件的回调函数
        
        使用 with 语句确保锁在任何情况下都会被释放，
        避免数据库操作抛出异常时造成死锁。
        """
        should_log_status = False
        with self.status.lock:
            if event == DlEvent.RECEIVED:
                self.status.bytes_downloaded += extra_args['bytes_received']
            elif event == DlEvent.FAILED:
                self.status.files_failed += 1
                # 记录失败的文件到数据库，包括目标路径和失败原因
                try:
                    error_message = task.status.get_error_text() if task.status else '未知错误'
                    self.database.save_failed_file(
                        task.file,
                        task.course.id,
                        task.course.fullname,
                        error_message
                    )
                except Exception as e:
                    logging.error(f'保存失败文件记录时出错: {e}')
            elif event == DlEvent.FINISHED:
                try:
                    self.database.save_file(task.file, task.course.id, task.course.fullname)
                    # 标记下载成功，重置失败计数器
                    self.database.mark_download_success(task.file, task.course.id)
                    self.status.files_downloaded += 1
                except Exception as e:
                    logging.error(f'保存成功文件记录时出错: {e}')
                    self.status.files_failed += 1
            elif event == DlEvent.TOTAL_SIZE:
                self.status.bytes_to_download += extra_args['content_length']
            elif event == DlEvent.TOTAL_SIZE_UPDATE:
                self.status.bytes_to_download += extra_args['content_length_diff']

            should_log_status = self._should_signal_status_log_locked(event)

        if should_log_status:
            self._signal_status_log()

    def _should_signal_status_log_locked(self, event: DlEvent) -> bool:
        if event in (DlEvent.FINISHED, DlEvent.FAILED, DlEvent.TOTAL_SIZE, DlEvent.TOTAL_SIZE_UPDATE):
            return True

        if event == DlEvent.RECEIVED:
            last_signal_bytes = getattr(self, '_bytes_downloaded_at_last_status_log_signal', 0)
            if self.status.bytes_downloaded - last_signal_bytes >= self.PROGRESS_LOG_MIN_BYTES:
                self._bytes_downloaded_at_last_status_log_signal = self.status.bytes_downloaded
                return True

        return False

    def _signal_status_log(self) -> None:
        event = getattr(self, '_status_log_event', None)
        loop = getattr(self, '_status_log_loop', None)
        if event is None or loop is None or loop.is_closed():
            return

        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass

    @staticmethod
    def _download_delay_seconds() -> float:
        return 2 + random.uniform(0, 3)

    async def _wait_before_network_task(self) -> None:
        delay = self._download_delay_seconds()
        logging.debug(f'等待 {delay:.2f} 秒后继续下一个网络任务...')
        await asyncio.sleep(delay)
        await self.pause_controller.wait_if_requested()

    @staticmethod
    def _task_may_perform_network_io(task) -> bool:
        checker = getattr(task, 'may_perform_network_io', None)
        if callable(checker):
            return bool(checker())
        return True

    def run(self):
        asyncio.run(self.real_run())

    async def real_run(self):
        "Starts all tasks and issues status messages when download state changes"

        # delete files, that should be deleted
        self.database.batch_delete_files(self.courses)

        if len(self.all_tasks) <= 0:
            return

        # run all other tasks
        self._status_log_event = asyncio.Event()
        self._status_log_loop = asyncio.get_running_loop()
        self._last_logged_status_snapshot = None
        self._bytes_downloaded_at_last_status_log_signal = self.status.bytes_downloaded
        status_logger_task = asyncio.create_task(self.log_download_status())
        self.pause_controller.start()

        # ========== 并发下载（已禁用） ==========
        # dl_tasks = set()
        # for task in self.all_tasks:
        #     if len(dl_tasks) >= self.opts.max_parallel_downloads:
        #         # Wait for some download to finish before adding a new one
        #         _done, dl_tasks = await asyncio.wait(dl_tasks, return_when=asyncio.FIRST_COMPLETED)
        #     dl_tasks.add(asyncio.create_task(task.run()))
        #
        # # Wait for the remaining downloads to finish
        # await asyncio.wait(dl_tasks)

        try:
            # ========== 单线程顺序下载（已启用） ==========
            # 按顺序逐个下载，不使用并发
            has_seen_network_task = False
            for task in self.all_tasks:
                task_may_use_network = self._task_may_perform_network_io(task)
                if task_may_use_network and has_seen_network_task:
                    await self._wait_before_network_task()

                await task.run()
                await self.pause_controller.wait_if_requested()

                if task_may_use_network:
                    has_seen_network_task = True
        finally:
            self.pause_controller.stop()
            status_logger_task.cancel()
            with suppress(asyncio.CancelledError):
                await status_logger_task

        self._rewrite_downloaded_html_resource_links()

        # 🆕 显示下载总结
        if self.status.files_to_download > 0:
            self._display_download_summary()

    def _iter_known_files(self):
        seen = set()
        for course in self.courses:
            for file in getattr(course, 'files', []) or []:
                file_identity = id(file)
                if file_identity not in seen:
                    seen.add(file_identity)
                    yield file

        for task in self.all_tasks:
            file = getattr(task, 'file', None)
            if file is None:
                continue
            file_identity = id(file)
            if file_identity not in seen:
                seen.add(file_identity)
                yield file

    @staticmethod
    def _is_saved_html_file(file: File) -> bool:
        saved_to = getattr(file, 'saved_to', '') or ''
        content_type = (getattr(file, 'content_type', '') or '').lower()
        return content_type == 'html' or saved_to.lower().endswith(('.html', '.htm'))

    def _rewrite_downloaded_html_resource_links(self) -> int:
        """Make saved HTML files reference downloaded embedded resources locally."""
        files = list(self._iter_known_files())
        local_resources = build_local_resource_map(files)
        if not local_resources:
            return 0

        replacement_count = 0
        html_file_count = 0
        for file in files:
            if not self._is_saved_html_file(file):
                continue

            saved_to = getattr(file, 'saved_to', '') or ''
            if not saved_to or not os.path.isfile(saved_to):
                continue

            try:
                with open(saved_to, 'r', encoding='utf-8') as html_file:
                    html_content = html_file.read()

                rewritten_html, file_replacements = rewrite_html_links_to_local_paths(
                    html_content,
                    saved_to,
                    local_resources,
                )

                if file_replacements <= 0:
                    continue

                with open(saved_to, 'w', encoding='utf-8') as html_file:
                    html_file.write(rewritten_html)

                html_file_count += 1
                replacement_count += file_replacements
                logging.debug(
                    '已将 %d 个 HTML 内嵌资源链接改为本地路径: %s',
                    file_replacements,
                    saved_to,
                )
            except OSError as error:
                logging.debug('重写 HTML 本地资源链接失败: %s (%s)', saved_to, error)

        if replacement_count:
            logging.info(
                '已将 %d 个 HTML 内嵌资源链接改为本地路径（%d 个 HTML 文件）。 / '
                'Rewrote %d embedded HTML resource link(s) to local paths across %d HTML file(s).',
                replacement_count,
                html_file_count,
                replacement_count,
                html_file_count,
            )

        return replacement_count

    async def log_download_status(self):
        """
        记录下载进度状态（使用增强的进度追踪器）
        """
        while True:
            event = getattr(self, '_status_log_event', None)
            if event is None:
                event = asyncio.Event()
                self._status_log_event = event
                self._status_log_loop = asyncio.get_running_loop()

            await event.wait()
            event.clear()

            if self.pause_controller.is_paused():
                continue

            self._log_download_status_once()

    def _log_download_status_once(self):
        status_snapshot = (
            self.status.bytes_downloaded,
            self.status.bytes_to_download,
            self.status.files_downloaded,
            self.status.files_failed,
            self.status.files_to_download,
        )
        if status_snapshot == getattr(self, '_last_logged_status_snapshot', None):
            return

        self._last_logged_status_snapshot = status_snapshot

        # 更新进度追踪器
        self.progress_tracker.update(
            downloaded_bytes=self.status.bytes_downloaded,
            total_bytes=self.status.bytes_to_download,
            completed=self.status.files_downloaded,
            failed=self.status.files_failed,
            total=self.status.files_to_download,
            skipped=0  # 目前 DownloadStatus 没有跳过计数，可以后续添加
        )

        # 获取进度信息
        progress_line = self.progress_tracker.get_progress_line()
        statistics_line = self.progress_tracker.get_statistics_line()

        # 显示进度
        logging.info(progress_line)

        # 如果有统计信息，也显示
        if statistics_line:
            logging.info(f"   {statistics_line}")
    
    def _display_download_summary(self):
        """
        显示下载完成的总结信息
        """
        summary = self.progress_tracker.get_summary()
        
        # 使用 info 级别输出，确保用户能看到
        for line in summary.split('\n'):
            logging.info(line)

    def _create_tasks_for_manually_specified_courses(
        self,
        course_ids: List[int],
        dl_options,
        thread_pool,
        incomplete_files_map: dict
    ) -> Tuple[List[Task], List[Task]]:
        """Create tasks for manually specified courses using Web API.
        
        完整实现包括：
        1. 从 core_course_get_contents 获取 sections
        2. 从 sections 的 modules 生成 File 对象
        3. 应用课程选项（自定义名称、目录结构、排除章节）
        4. 创建下载任务
        
        Args:
            course_ids: List of manually specified course IDs
            dl_options: Download options configuration
            thread_pool: ThreadPoolExecutor for yt-dlp
            incomplete_files_map: Map of incomplete downloads
            
        Returns:
            Tuple[List[Task], List[Task]]: (priority_tasks, normal_tasks)
        """
        web_api_priority_tasks: List[Task] = []
        web_api_tasks: List[Task] = []
        
        if not course_ids:
            return web_api_priority_tasks, web_api_tasks
        
        try:
            from moodle_dl.moodle.course_validator import CourseValidator
            from moodle_dl.moodle.request_helper import RequestHelper
            
            # 创建 RequestHelper 并传递给 CourseValidator
            request_helper = RequestHelper(
                self.config, 
                self.opts,
                self.config.get_moodle_URL(),
                self.config.get_token()
            )
            validator = CourseValidator(self.config, self.opts, request_helper)
            
            for course_id in course_ids:
                try:
                    # Get course info via web API
                    course_info = validator.validate_course_exists_and_accessible(course_id)
                    if not course_info:
                        logging.warning(f'无法访问手动指定的课程 {course_id}，跳过')
                        continue
                    
                    logging.info(f'✓ 课程 {course_id} ({course_info.get("fullname")}) 已验证')
                    
                    # Get course content via web API
                    course_data = self._fetch_course_data_from_web_api(course_id)
                    if not course_data:
                        logging.warning(f'  → 无法从网页版 API 获取课程 {course_id} 的内容')
                        continue
                    
                    # Create pseudo-Course object from web API data
                    course = self._build_course_from_web_api_data(
                        course_id, course_info, course_data
                    )
                    
                    # Apply course options (overwrite_name_with, create_directory_structure, excluded_sections)
                    # 这确保用户在配置向导中设置的选项会被正确应用
                    self._apply_course_options_to_manually_specified_course(course)
                    
                    # Create tasks for course files
                    for course_file in course.files:
                        if course_file.deleted is False:
                            task = self._create_task(
                                course_file, course, dl_options, thread_pool,
                                api_source='web'
                            )
                            
                            # Categorize by priority
                            if self._is_incomplete_download(course_file, incomplete_files_map):
                                web_api_priority_tasks.append(task)
                                self._log_incomplete_download(course_file, incomplete_files_map)
                            else:
                                web_api_tasks.append(task)
                            self._update_download_statistics(course_file)
                    
                    if not course.files:
                        logging.info(f'  → 课程 {course_id} 没有可下载的文件')
                
                except Exception as e:
                    logging.warning(f'处理手动课程 {course_id} 时出错: {str(e)}')
                    continue
        
        except ImportError:
            logging.error('无法导入 CourseValidator，跳过手动课程处理')
        
        return web_api_priority_tasks, web_api_tasks
    
    def _fetch_course_data_from_web_api(self, course_id: int) -> Dict[str, Any]:
        """Fetch course data from web API (core_course_get_contents).
        
        获取课程内容（sections 和 modules）。
        
        Args:
            course_id: Course ID
            
        Returns:
            Dictionary with sections, or empty dict if failed
        """
        try:
            from moodle_dl.moodle.request_helper import RequestHelper
            request_helper = RequestHelper(
                self.config,
                self.opts,
                self.config.get_moodle_URL(),
                self.config.get_token()
            )
            
            # 使用 RequestHelper.post() 方法调用 Moodle Web Service API
            # post() 方法会自动处理 wstoken、wsfunction 和 moodlewsrestformat
            data = {'courseid': course_id}
            response = request_helper.post('core_course_get_contents', data)
            
            # post() 方法返回的是解析后的 JSON 字典
            # core_course_get_contents 返回的是 sections 列表
            if response and isinstance(response, list):
                return {
                    'id': course_id,
                    'sections': response
                }
            
            return {}
        
        except Exception as e:
            logging.debug(f'从 Web API 获取课程内容 {course_id} 失败: {str(e)}')
            return {}
    
    def _build_course_from_web_api_data(self, course_id: int, course_info: dict, course_data: dict) -> Course:
        """Build a Course object from web API data.
        
        从 sections 中提取文件并构建 Course 对象。
        这个实现基于 ResultBuilder.get_files_in_sections() 的逻辑。
        
        Args:
            course_id: Course ID
            course_info: Course metadata from core_course_get_courses
            course_data: Course content from core_course_get_contents
            
        Returns:
            Course object with files extracted from sections
        """
        from moodle_dl.types import Course, File
        
        # 创建 Course 对象
        # Course 构造函数签名: __init__(self, _id: int, fullname: str, files: List[File] = None)
        course = Course(
            _id=course_id,
            fullname=course_info.get('fullname', f'Course {course_id}'),
        )
        
        # 从 sections 中提取文件
        files = []
        sections = course_data.get('sections', [])
        
        for section in sections:
            section_id = section.get('id', 0)
            section_name = section.get('name', '')
            section_modules = section.get('modules', [])
            
            position_in_section = 0
            
            # 处理 section 中的每个 module
            for module in section_modules:
                module_id = module.get('id', 0)
                module_name = module.get('name', '')
                module_modname = module.get('modname', '')
                module_contents = module.get('contents', [])
                
                # 从 module contents 中提取文件
                for content in module_contents:
                    # 跳过不是文件的内容
                    if 'fileurl' not in content and 'content' not in content:
                        continue
                    
                    # 确定是否是外部链接或嵌入内容
                    if 'fileurl' in content:
                        # 这是一个可下载文件
                        file_url = content['fileurl']
                        filename = content.get('filename', module_name)
                        filesize = content.get('filesize', 0)
                        timemodified = content.get('timemodified', 0)
                        raw_file_id = content.get('fileid')
                        file_id = None
                        if raw_file_id is not None:
                            try:
                                file_id = int(raw_file_id)
                            except (ValueError, TypeError):
                                file_id = None
                        
                        # 创建 File 对象
                        file_obj = File(
                            module_id=module_id,
                            module_name=module_name,
                            module_modname=module_modname,
                            section_id=section_id,
                            section_name=section_name,
                            content_filename=filename,
                            content_filepath='/',
                            content_fileurl=file_url,
                            content_filesize=filesize,
                            content_timemodified=timemodified,
                            content_type='file',
                            content_isexternalfile=False,
                            file_id=file_id,
                        )
                        
                        # 不分配位置索引给系统文件
                        if not self._is_system_file_from_web_api(filename):
                            file_obj.position_in_section = position_in_section
                            position_in_section += 1
                        else:
                            file_obj.position_in_section = None
                        
                        files.append(file_obj)
        
        course.files = files
        logging.debug(
            f'✓ 从网页版 API 为课程 {course_id} 提取了 {len(files)} 个文件'
        )
        
        return course
    
    @staticmethod
    def _is_system_file_from_web_api(filename: str) -> bool:
        """判断文件是否为系统文件（不应编号）。
        
        基于 ResultBuilder._is_system_file() 的逻辑。
        """
        filename_lower = filename.lower()
        
        # 隐藏文件
        if filename_lower.startswith('.'):
            return True
        
        # 元数据文件
        if filename_lower.endswith('_metadata.json') or filename_lower == 'metadata.json':
            return True
        
        # 笔记和信息文件
        if filename_lower.endswith(('_info', '_notes.md')):
            return True
        
        # JSON 文件（来自 Resource 模块）
        if filename_lower.endswith('.json'):
            return True
        
        # 特定模块文件
        if filename_lower in ('questions.json', 'analysis.json', 'grade', 'entry_metadata.json'):
            return True
        
        # Session 文件
        if filename_lower.startswith('session_') and filename_lower.endswith('.json'):
            return True
        
        return False
    
    @staticmethod
    def _filter_files_by_excluded_sections(files: List[File], excluded_sections: List[int]) -> List[File]:
        """
        根据排除的章节过滤文件列表。
        
        只负责过滤逻辑，不修改原始文件列表。
        
        Args:
            files: 原始文件列表
            excluded_sections: 要排除的章节 ID 列表
            
        Returns:
            List[File]: 过滤后的文件列表
        """
        if not excluded_sections:
            return files
        return [file for file in files if file.section_id not in excluded_sections]
    
    def _apply_course_options_to_manually_specified_course(self, course: Course):
        """Apply course options to a manually specified course.
        
        从配置中读取课程选项并应用到 Course 对象。
        这确保用户在配置向导中设置的选项（如自定义名称、目录结构、排除章节）会被正确应用。
        
        Args:
            course: Course object to apply options to
        """
        options_of_courses = self.config.get_options_of_courses()
        options = options_of_courses.get(str(course.id), None)
        
        if options is not None:
            # 应用自定义名称
            course.overwrite_name_with = options.get('overwrite_name_with', None)
            
            # 应用目录结构设置
            course.create_directory_structure = options.get('create_directory_structure', True)
            
            # 应用排除的章节
            excluded_sections_raw = options.get('excluded_sections', [])
            excluded_sections = ConfigHelper.normalize_id_list(excluded_sections_raw)
            course.excluded_sections = excluded_sections
            
            # 如果设置了排除章节，过滤文件列表
            if excluded_sections:
                course.files = self._filter_files_by_excluded_sections(course.files, excluded_sections)
                logging.debug(
                    f'课程 {course.id}: 已排除 {len(excluded_sections)} 个章节，'
                    f'剩余 {len(course.files)} 个文件'
                )

    def get_failed_tasks(self) -> List[Task]:
        "Return a list of failed downloads."
        result = []
        for task in self.all_tasks:
            if task.status.state == TaskState.FAILED:
                result.append(task)
        return result
