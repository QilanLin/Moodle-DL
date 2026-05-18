# -*- coding: utf-8 -*-
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

from moodle_dl.utils import PathTools as PT


class File:
    def __init__(
        self,
        module_id: int,
        section_name: str,
        section_id: int,
        module_name: str,
        content_filepath: str,
        content_filename: str,
        content_fileurl: str,
        content_filesize: int,
        content_timemodified: int,
        module_modname: str,
        content_type: str,
        content_isexternalfile: bool,
        saved_to: str = '',
        time_stamp: int = 0,
        modified: int = 0,
        moved: int = 0,
        deleted: int = 0,
        notified: int = 0,
        file_hash: str = None,
        file_id: int = None,
        old_file_id: int = None,
        position_in_section: int = None,
        # 🆕 扩展元数据字段（从 Moodle Mobile API 获取）
        visible: int = 1,  # 模块可见性 (1=可见, 0=隐藏)
        uservisible: int = 1,  # 用户可见性 (1=对用户可见, 0=不可见)
        availabilityinfo: str = None,  # 可用性信息（JSON 字符串）
        completion: int = 0,  # 完成状态 (0=未完成, 1=已完成, 2=已通过)
        timecreated: int = 0,  # 创建时间（Unix 时间戳）
        sortorder: int = 0,  # 在章节中的排序顺序
    ):
        self.file_id = file_id

        self.module_id = module_id
        self.section_name = section_name
        self.section_id = section_id
        self.module_name = module_name

        self.content_filepath = content_filepath
        self.content_filename = content_filename
        self.content_fileurl = content_fileurl if content_fileurl is not None else ''
        self.content_filesize = content_filesize
        self.content_timemodified = 0
        if content_timemodified is not None:
            self.content_timemodified = int(content_timemodified)

        self.module_modname = module_modname
        self.content_type = content_type

        if isinstance(content_isexternalfile, bool):
            self.content_isexternalfile = content_isexternalfile
        else:
            if content_isexternalfile == 1:
                self.content_isexternalfile = True
            else:
                self.content_isexternalfile = False

        self.saved_to = saved_to

        self.time_stamp = time_stamp

        if modified == 1:
            self.modified = True
        else:
            self.modified = False

        if moved == 1:
            self.moved = True
        else:
            self.moved = False

        if deleted == 1:
            self.deleted = True
        else:
            self.deleted = False

        if notified == 1:
            self.notified = True
        else:
            self.notified = False

        self.hash = file_hash

        # For text label
        self.text_content = None

        # For Created HTML-Files like Quizzes
        self.html_content = None

        # For JSON/text content files (like metadata.json)
        self.content = None

        # To manage the corresponding moved or changed files
        self.old_file = None
        self.new_file = None

        self.old_file_id = old_file_id
        self.position_in_section = position_in_section

        # 🆕 扩展元数据字段
        self.visible = visible if visible is not None else 1
        self.uservisible = uservisible if uservisible is not None else 1
        self.availabilityinfo = availabilityinfo
        self.completion = completion if completion is not None else 0
        self.timecreated = timecreated if timecreated is not None else 0
        self.sortorder = sortorder if sortorder is not None else 0

    def getMap(self) -> {str: str}:
        return {
            'file_id': self.file_id,
            'module_id': self.module_id,
            'section_name': self.section_name,
            'section_id': self.section_id,
            'module_name': self.module_name,
            'content_filepath': self.content_filepath,
            'content_filename': self.content_filename,
            'content_fileurl': self.content_fileurl,
            'content_filesize': self.content_filesize,
            'content_timemodified': self.content_timemodified,
            'module_modname': self.module_modname,
            'content_type': self.content_type,
            'content_isexternalfile': 1 if self.content_isexternalfile else 0,
            'content': self.content,
            'text_content': self.text_content,
            'html_content': self.html_content,
            'saved_to': self.saved_to,
            'time_stamp': self.time_stamp,
            'modified': 1 if self.modified else 0,
            'moved': 1 if self.moved else 0,
            'deleted': 1 if self.deleted else 0,
            'notified': 1 if self.notified else 0,
            'hash': self.hash,
            'old_file_id': self.old_file_id,
            'position_in_section': self.position_in_section,
            # 🆕 扩展元数据字段
            'visible': self.visible,
            'uservisible': self.uservisible,
            'availabilityinfo': self.availabilityinfo,
            'completion': self.completion,
            'timecreated': self.timecreated,
            'sortorder': self.sortorder,
        }

    @staticmethod
    def fromRow(row):
        # 尝试读取 position_in_section 字段（v8引入，可能不存在）
        try:
            position_in_section = row['position_in_section']
        except (KeyError, IndexError):
            position_in_section = None

        # 🆕 尝试读取扩展元数据字段（向后兼容，可能不存在）
        try:
            visible = row['visible']
        except (KeyError, IndexError):
            visible = 1

        try:
            uservisible = row['uservisible']
        except (KeyError, IndexError):
            uservisible = 1

        try:
            availabilityinfo = row['availabilityinfo']
        except (KeyError, IndexError):
            availabilityinfo = None

        try:
            completion = row['completion']
        except (KeyError, IndexError):
            completion = 0

        try:
            timecreated = row['timecreated']
        except (KeyError, IndexError):
            timecreated = 0

        try:
            sortorder = row['sortorder']
        except (KeyError, IndexError):
            sortorder = 0

        file = File(
            file_id=row['file_id'],
            module_id=row['module_id'],
            section_name=row['section_name'],
            section_id=row['section_id'],
            module_name=row['module_name'],
            content_filepath=row['content_filepath'],
            content_filename=row['content_filename'],
            content_fileurl=row['content_fileurl'],
            content_filesize=row['content_filesize'],
            content_timemodified=row['content_timemodified'],
            module_modname=row['module_modname'],
            content_type=row['content_type'],
            content_isexternalfile=row['content_isexternalfile'],
            saved_to=row['saved_to'],
            time_stamp=row['time_stamp'],
            modified=row['modified'],
            moved=row['moved'],
            deleted=row['deleted'],
            notified=row['notified'],
            file_hash=row['hash'],
            old_file_id=row['old_file_id'],
            position_in_section=position_in_section,
            # 🆕 扩展元数据字段（向后兼容）
            visible=visible,
            uservisible=uservisible,
            availabilityinfo=availabilityinfo,
            completion=completion,
            timecreated=timecreated,
            sortorder=sortorder,
        )

        for attribute in ('content', 'text_content', 'html_content'):
            try:
                setattr(file, attribute, row[attribute])
            except (KeyError, IndexError):
                pass

        return file

    INSERT = """INSERT INTO files
            (course_id, course_fullname, module_id, section_name, section_id,
            module_name, content_filepath, content_filename,
            content_fileurl, content_filesize, content_timemodified,
            module_modname, content_type, content_isexternalfile,
            content, text_content, html_content, saved_to,
            time_stamp, modified, moved, deleted, notified,
            hash, old_file_id, download_status, download_attempts,
            last_download_at, last_failed_at, last_failed_reason,
            consecutive_failures, position_in_section,
            visible, uservisible, availabilityinfo, completion, timecreated, sortorder)
            VALUES (:course_id, :course_fullname, :module_id,
            :section_name, :section_id, :module_name, :content_filepath,
            :content_filename, :content_fileurl, :content_filesize,
            :content_timemodified, :module_modname, :content_type,
            :content_isexternalfile, :content, :text_content, :html_content,
            :saved_to, :time_stamp,
            :modified, :moved, :deleted, :notified,  :hash,
            :old_file_id, :download_status, :download_attempts,
            :last_download_at, :last_failed_at, :last_failed_reason,
            :consecutive_failures, :position_in_section,
            :visible, :uservisible, :availabilityinfo, :completion, :timecreated, :sortorder);
            """

    def __str__(self):
        message = 'File ('

        message += f'module_id: {self.module_id}'
        message += f', section_name: "{PT.to_valid_name(self.section_name, is_file=False)}"'
        message += f', section_id: "{self.section_id}"'
        message += f', module_name: "{PT.to_valid_name(self.module_name, is_file=False)}"'
        message += f', content_filepath: {self.content_filepath}'
        valid_content_filename = PT.to_valid_name(self.content_filename, is_file=True)
        if len(valid_content_filename) > 256:
            message += (
                f', content_filename (longer than 256 chars): "{valid_content_filename[:200]}[...]'
                + f'{valid_content_filename[-50:]}"'
            )
        else:
            message += f', content_filename: "{valid_content_filename}"'
        if self.content_fileurl and len(self.content_fileurl) > 256:
            message += (
                f', content_fileurl (longer than 256 chars): "{self.content_fileurl[:200]}[...]'
                + f'{self.content_fileurl[-50:]}"'
            )
        else:
            message += f', content_fileurl: "{self.content_fileurl}"'
        message += f', content_filesize: {self.content_filesize}'
        message += f', content_timemodified: {self.content_timemodified}'
        message += f', module_modname: {self.module_modname}'
        message += f', content_type: {self.content_type}'
        message += f', content_isexternalfile: {self.content_isexternalfile}'

        message += f', saved_to: "{self.saved_to}"'
        message += f', time_stamp: {self.time_stamp}'
        message += f', modified: {self.modified}'
        message += f', moved: {self.moved}'
        message += f', deleted: {self.deleted}'
        message += f', notified: {self.notified}'
        message += f', hash: {self.hash}'
        message += f', file_id: {self.file_id}'
        message += f', old_file_id: {self.old_file_id}'

        message += ')'
        return message


class Course:
    def __init__(self, _id: int, fullname: str, files: List[File] = None):
        self.id = _id
        self.fullname = PT.to_valid_name(fullname, is_file=False)
        if files is not None:
            self.files = files
        else:
            self.files = []

        self.overwrite_name_with = None
        self.create_directory_structure = True
        self.excluded_sections = []

    def __str__(self):
        message = 'Course ('

        message += f'id: {self.id}'
        message += f', fullname: "{self.fullname}"'
        message += f', overwrite_name_with: "{PT.to_valid_name(self.overwrite_name_with, is_file=False)}"'
        message += f', create_directory_structure: {self.create_directory_structure}'
        message += f', files: {len(self.files)}'
        message += ')'
        return message


@dataclass
class MoodleURL:
    use_http: bool
    domain: str
    path: str
    scheme: str = field(init=False)
    url_base: str = field(init=False)

    def __post_init__(self):
        if self.use_http:
            self.scheme = 'http://'
        else:
            self.scheme = 'https://'
        self.url_base = self.scheme + self.domain + self.path


@dataclass
class MoodleDlOpts:
    init: bool = False
    config: bool = False
    new_token: bool = False
    change_notification_mail: bool = False
    change_notification_telegram: bool = False
    change_notification_discord: bool = False
    change_notification_ntfy: bool = False
    change_notification_xmpp: bool = False
    manage_database: bool = False
    delete_old_files: bool = False
    reset_downloaded_files: bool = False
    reset_downloaded_files_cn: bool = False
    log_responses: bool = False
    add_all_visible_courses: bool = False
    retry_failed: bool = False
    resume: bool = False
    refresh_cookies: bool = False
    sso: bool = False
    username: str = ''
    password: str = ''
    token: str = ''
    path: str = '.'
    max_parallel_api_calls: int = 10
    max_parallel_downloads: int = 5
    max_parallel_yt_dlp: int = 5
    download_chunk_size: int = 102400
    ignore_ytdl_errors: bool = False
    without_downloading_files: bool = False
    max_path_length_workaround: bool = False
    allow_insecure_ssl: bool = False
    use_all_ciphers: bool = False
    skip_cert_verify: bool = False
    verbose: bool = False
    quiet: bool = False
    log_to_file: bool = False
    log_file_path: str = ''


class TaskState(Enum):
    INIT = 'INIT'
    STARTED = 'STARTED'
    FAILED = 'FAILED'
    FINISHED = 'FINISHED'


@dataclass
class TaskStatus:
    state: TaskState = field(init=False, default=TaskState.INIT)
    bytes_downloaded: int = field(init=False, default=0)
    external_total_size: int = field(init=False, default=0)
    error: Any = field(init=False, default=None)
    yt_dlp_failed_with_error: bool = field(init=False, default=False)
    yt_dlp_used_generic_extractor: bool = field(init=False, default=False)
    yt_dlp_current_file: str = field(init=False, default=None)
    yt_dlp_total_size_per_file: Dict[str, int] = field(init=False, default_factory=dict)
    yt_dlp_bytes_downloaded_per_file: Dict[str, int] = field(init=False, default_factory=dict)

    def get_error_text(self) -> str:
        str_error = str(self.error).strip()
        if str_error != '':
            return str_error
        return repr(self.error)

    def set_error(self, error: Any) -> None:
        self.error = error


@dataclass
class DownloadStatus:
    bytes_downloaded: int = field(init=False, default=0)
    bytes_to_download: int = field(init=False, default=0)

    files_downloaded: int = field(init=False, default=0)
    files_failed: int = field(init=False, default=0)
    files_to_download: int = field(init=False, default=0)

    lock: threading.Lock = field(init=False, default_factory=threading.Lock)


class DlEvent(Enum):
    FINISHED = 'FINISHED'
    FAILED = 'FAILED'
    RECEIVED = 'RECEIVED'
    TOTAL_SIZE = 'TOTAL_SIZE'
    TOTAL_SIZE_UPDATE = 'TOTAL_SIZE_UPDATE'


@dataclass
class DownloadOptions:
    token: str
    moodle_url: str
    download_linked_files: bool
    download_domains_whitelist: List
    download_domains_blacklist: List
    cookies_text: str
    yt_dlp_options: Dict
    video_passwords: Dict
    external_file_downloaders: Dict
    restricted_filenames: bool
    write_links: Dict
    download_path: str
    download_metadata_files: bool
    global_opts: MoodleDlOpts


@dataclass
class HeadInfo:
    content_type: str
    is_html: bool = field(init=False, default=False)
    content_length: int
    last_modified: str
    final_url: str
    guessed_file_name: str
    host: str

    def __post_init__(self):
        """
        判断响应是否为 HTML 页面
        
        判断逻辑：
        1. 基于服务器返回的 Content-Type 头
        2. 如果 Content-Type 是 HTML/text，但 URL 明确以文件后缀结尾（如 .pdf, .mp4），
           则认为是服务器配置错误，强制判断为非 HTML
        
        这解决了以下问题：
        - 服务器临时错误返回错误页面（Content-Type: text/html）
        - 服务器配置不当，对 PDF 等文件返回 text/plain
        """
        if self.content_type in ('text/html', 'text/plain'):
            # 检查 URL 是否明确以已知文件后缀结尾
            # 如果是，则认为服务器返回了错误的 Content-Type，不应视为 HTML
            if self._url_has_non_html_extension():
                self.is_html = False
            else:
                self.is_html = True
    
    def _url_has_non_html_extension(self) -> bool:
        """
        检查 URL 是否以已知的非 HTML 文件后缀结尾
        
        例如：
        - https://example.com/slides07.pdf → True（.pdf 是非 HTML 后缀）
        - https://example.com/page.html → False（.html 是 HTML 后缀）
        - https://example.com/video.mp4?token=xxx → True（.mp4 是非 HTML 后缀）
        """
        import os
        from urllib.parse import urlparse
        from moodle_dl.utils import NON_HTML_FILE_EXTENSIONS
        
        if not self.final_url:
            return False
        
        # 解析 URL 路径（去除查询参数）
        parsed = urlparse(self.final_url)
        path = parsed.path
        
        # 获取文件后缀（不含点号，小写）
        _, ext = os.path.splitext(path)
        if ext:
            ext = ext[1:].lower()  # 去掉点号，转小写
            return ext in NON_HTML_FILE_EXTENSIONS
        
        return False
