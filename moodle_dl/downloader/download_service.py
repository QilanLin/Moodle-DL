import asyncio
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.task import Task
from moodle_dl.types import Course, DlEvent, DownloadStatus, MoodleDlOpts, TaskState
from moodle_dl.utils import calc_speed, format_bytes, format_speed, PathTools as PT


class DownloadService:
    "Manages jobs to download, delete or create files of courses"

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

    def _configure_task_settings(self) -> tuple:
        """Configure task settings and create thread pool.
        
        Returns:
            Tuple of (download_options, thread_pool)
        """
        Task.CHUNK_SIZE = self.opts.download_chunk_size
        dl_options = self.config.get_download_options(self.opts)
        thread_pool = ThreadPoolExecutor(max_workers=self.opts.max_parallel_yt_dlp)
        return dl_options, thread_pool

    def _load_incomplete_downloads_map(self) -> dict:
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

    def _create_task(self, course_file, course, dl_options, thread_pool) -> Task:
        """Create a single Task object.
        
        Args:
            course_file: File object from course
            course: Course object
            dl_options: Download options configuration
            thread_pool: ThreadPoolExecutor for yt-dlp
            
        Returns:
            Task object
        """
        return Task(
            task_id=self.status.files_to_download,
            file=course_file,
            course=course,
            options=dl_options,
            thread_pool=thread_pool,
            callback=self.status_callback,
        )

    def _is_incomplete_download(self, course_file, incomplete_files_map) -> bool:
        """Check if a file is an incomplete download that needs to be resumed.
        
        Args:
            course_file: File object to check
            incomplete_files_map: Map of incomplete downloads
            
        Returns:
            True if file is incomplete, False otherwise
        """
        return course_file.file_id and course_file.file_id in incomplete_files_map

    def _log_incomplete_download(self, course_file, incomplete_files_map):
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

    def _log_queue_summary(self, priority_task_count: int):
        """Log a summary of the download queue.
        
        Args:
            priority_task_count: Number of priority (incomplete) tasks
        """
        if self.status.files_to_download > 0:
            if priority_task_count > 0:
                logging.info(
                    '下载队列包含 %d 个任务 (%d 个未完成的下载需要续传)',
                    self.status.files_to_download,
                    priority_task_count
                )
            else:
                logging.info('下载队列包含 %d 个任务', self.status.files_to_download)
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
        5. Log queue summary
        6. Cleanup old incomplete download records
        
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
        
        for course in self.courses:
            for course_file in course.files:
                if course_file.deleted is False:
                    # Create task
                    task = self._create_task(course_file, course, dl_options, thread_pool)
                    
                    # Categorize by priority
                    if self._is_incomplete_download(course_file, incomplete_files_map):
                        priority_tasks.append(task)
                        self._log_incomplete_download(course_file, incomplete_files_map)
                    else:
                        normal_tasks.append(task)
                    
                    # Update statistics
                    self._update_download_statistics(course_file)
        
        # Combine tasks with priority ordering
        all_tasks = priority_tasks + normal_tasks
        
        # Step 5: Log summary
        self._log_queue_summary(len(priority_tasks))
        
        # Step 6: Cleanup old records
        self._cleanup_old_incomplete_downloads()
        
        return all_tasks

    def status_callback(self, event: DlEvent, task: Task, **extra_args):
        self.status.lock.acquire()
        if event == DlEvent.RECEIVED:
            self.status.bytes_downloaded += extra_args['bytes_received']
        elif event == DlEvent.FAILED:
            self.status.files_failed += 1
            # 记录失败的文件到数据库，包括目标路径和失败原因
            error_message = task.status.get_error_text() if task.status else '未知错误'
            self.database.save_failed_file(
                task.file,
                task.course.id,
                task.course.fullname,
                error_message
            )
        elif event == DlEvent.FINISHED:
            self.database.save_file(task.file, task.course.id, task.course.fullname)
            # 标记下载成功，重置失败计数器
            self.database.mark_download_success(task.file, task.course.id)
            self.status.files_downloaded += 1
        elif event == DlEvent.TOTAL_SIZE:
            self.status.bytes_to_download += extra_args['content_length']
        elif event == DlEvent.TOTAL_SIZE_UPDATE:
            self.status.bytes_to_download += extra_args['content_length_diff']
        self.status.lock.release()

    def run(self):
        asyncio.run(self.real_run())

    async def real_run(self):
        "Starts all tasks and issues status messages at regular intervals"

        # delete files, that should be deleted
        self.database.batch_delete_files(self.courses)

        if len(self.all_tasks) <= 0:
            return

        # run all other tasks
        status_logger_task = asyncio.create_task(self.log_download_status())

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

        # ========== 单线程顺序下载（已启用） ==========
        # 按顺序逐个下载，不使用并发
        for i, task in enumerate(self.all_tasks):
            await task.run()

            # 在每个任务之间添加随机延迟（0.7 到 1.3 秒）
            # 避免对服务器造成过大压力，模拟自然的下载行为
            if i < len(self.all_tasks) - 1:  # 最后一个任务后不需要等待
                delay = 0.7 + random.uniform(0, 0.6)
                logging.debug(f'等待 {delay:.2f} 秒后继续下一个任务...')
                await asyncio.sleep(delay)

        status_logger_task.cancel()

    async def log_download_status(self):
        last_bytes_downloaded = 0
        last_status_timestamp = time.time()
        while True:
            # Print every 2 sec the current status
            await asyncio.sleep(2)

            percentage = None
            if self.status.bytes_to_download != 0:
                percentage = int(self.status.bytes_downloaded * 100 / self.status.bytes_to_download)
                if percentage > 100 or percentage < 0:
                    percentage = None
            if percentage is None:
                percentage = ' NA%'
            else:
                percentage = f'{percentage:3}%'

            speed = calc_speed(last_status_timestamp, time.time(), self.status.bytes_downloaded - last_bytes_downloaded)
            last_status_timestamp = time.time()
            last_bytes_downloaded = self.status.bytes_downloaded

            message_line = (
                f'总计: {percentage}'
                + f' {format_bytes(self.status.bytes_downloaded):>5} / {format_bytes(self.status.bytes_to_download):<5}'
                + f' | 完成: {(self.status.files_downloaded + self.status.files_failed):>5}'
                + f' / {self.status.files_to_download:<5}'
                + f' | 速度: {format_speed(speed)}'
            )
            if self.status.files_failed > 0:
                message_line += f' | 失败: {self.status.files_failed}'

            logging.info(message_line)

    def get_failed_tasks(self) -> List[Task]:
        "Return a list of failed downloads."
        result = []
        for task in self.all_tasks:
            if task.status.state == TaskState.FAILED:
                result.append(task)
        return result
