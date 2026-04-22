# -*- coding: utf-8 -*-
import logging
import os
import sqlite3
import time
from sqlite3 import Error
from typing import Any, Dict, List, Optional

from moodle_dl.config import ConfigHelper
from moodle_dl.types import Course, File, MoodleDlOpts
from moodle_dl.utils import PathTools as PT


def _configure_sqlite_connection(conn: sqlite3.Connection) -> str:
    """
    配置 SQLite 连接的 journal 模式。

    某些卷上默认的 DELETE journal 会触发:
      sqlite3.OperationalError: attempt to write a readonly database

    策略：
    1. 优先 WAL（持久、正常模式）
    2. WAL 不可用时退回 MEMORY（只对当前连接生效，但可完成初始化）
    """
    try:
        row = conn.execute('PRAGMA journal_mode=WAL;').fetchone()
        mode = (row[0] if row else '').lower()
        if mode == 'wal':
            return 'wal'
    except sqlite3.OperationalError as exc:
        logging.debug(f'切换 SQLite WAL 模式失败: {exc}')

    row = conn.execute('PRAGMA journal_mode=MEMORY;').fetchone()
    mode = (row[0] if row else '').lower()
    if mode == 'memory':
        logging.warning('⚠️  SQLite WAL 模式不可用，已回退到 MEMORY journal 模式')
        return 'memory'

    return mode or 'unknown'


class StateRecorder:
    """
    Saves the state and provides utilities to detect changes in the current
    state against the previous.
    
    📊 DATABASE SCHEMA & STATE TRACKING:
    
    Key Tables:
    1. files: Main file repository with download status tracking
       - saved_to: File path (empty = not downloaded)
       - download_status: 'pending', 'success', or 'failed'
       - consecutive_failures: Failed retry count
       - last_failed_reason: Detailed error message
       
    2. incomplete_downloads: Resumable download state (v9+)
       - Stores partial download information
       - Supports HTTP Range-based resume
       - Includes ETag/Last-Modified for integrity
    
    3. auth_sessions: Session management (v9+)
       - Token storage
       - Session expiration tracking
       - Multiple auth method support
    
    4. cookie_store: Cookie management (v9+)
       - Secure cookie storage in database (not files)
       - Session-based organization
       - Automatic cleanup support
    
    📈 OPTIMIZATION FEATURES:
    - Query caching with TTL (5 minutes)
    - Database indexing for common queries
    - Foreign key constraints for data integrity
    - Transaction support for consistency
    
    🔄 STATE TRANSITIONS:
    File State Flow:
      pending → (download starts) → success (✅ file saved)
            ↓
            └→ failed (❌ error occurred)
               ├→ retry < 5: eligible for auto-retry
               ├→ retry ≥ 5: needs manual intervention
               └→ success (✅ eventually succeeds)
    
    Database Version: 9 (with incomplete_downloads support)
    """
    
    # 🆕 查询缓存配置
    CACHE_TTL_SECONDS = 300  # 缓存 5 分钟

    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        """
        Initiates the database.
        If no database exists yet, a new one is created.
        @param opts: Moodle-dl options
        """
        self.opts = opts
        self.db_file = PT.make_path(config.get_misc_files_path(), 'moodle_state.db')
        
        # 🆕 查询缓存存储
        self._query_cache: Dict[str, tuple] = {}  # {cache_key: (data, timestamp)}
        self._cache_locks: Dict[str, bool] = {}  # 防止缓存击穿

        try:
            conn = sqlite3.connect(self.db_file)
            journal_mode = _configure_sqlite_connection(conn)
            c = conn.cursor()
            if getattr(opts, 'init', False):
                logging.info('🗃️ SQLite journal_mode: %s', journal_mode.upper())
            else:
                logging.debug('SQLite journal_mode: %s', journal_mode)

            # 检查数据库版本
            current_version = c.execute('pragma user_version').fetchone()[0]
            
            # 检查所有必要的表是否存在
            required_tables = [
                'files',
                'auth_sessions',
                'cookie_store',
                'auth_audit_log',
                'incomplete_downloads',
            ]
            existing_tables = set([
                row[0] for row in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
                ).fetchall()
            ])
            missing_tables = set(required_tables) - existing_tables
            
            # 新策略：始终使用 v9 schema，弃用增量升级
            should_rebuild = False
            rebuild_reason = ""
            
            if current_version == 0 and not existing_tables:
                # 情况 1：全新数据库
                rebuild_reason = "全新数据库"
                should_rebuild = True
                logging.info('🆕 创建全新数据库（v9 schema）')
            elif current_version < 9:
                # 情况 2：旧版本数据库 → 直接升级到 v9
                rebuild_reason = f"旧版本数据库 (v{current_version})"
                should_rebuild = True
                logging.warning(f'⚠️  检测到旧版本数据库 (v{current_version})，将升级到 v9')
            elif missing_tables:
                # 情况 3：版本正确但缺少表 → 数据库损坏，重建
                rebuild_reason = f"数据库不完整，缺少表: {', '.join(missing_tables)}"
                should_rebuild = True
                logging.warning(f'⚠️  数据库结构不完整：缺少表 {missing_tables}')
            
            if should_rebuild:
                logging.info(f'📋 重建数据库原因: {rebuild_reason}')

                # 删除所有现有的表和索引
                if existing_tables:
                    logging.info('  正在清理旧数据...')

                    # 🔒 安全修复：使用白名单验证表名，防止 SQL 注入
                    ALLOWED_TABLES = {
                        'files', 'auth_sessions', 'cookie_store',
                        'auth_audit_log', 'incomplete_downloads'
                    }

                    for table_name in existing_tables:
                        # 只删除白名单中的表
                        if table_name in ALLOWED_TABLES:
                            c.execute(f'DROP TABLE IF EXISTS {table_name};')
                        else:
                            logging.warning(f'  ⚠️  跳过未知表: {table_name}')

                    # 删除所有索引
                    all_indexes = c.execute(
                        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%';"
                    ).fetchall()

                    # 🔒 安全修复：验证索引名称格式（只允许字母、数字、下划线）
                    import re
                    VALID_INDEX_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

                    for index in all_indexes:
                        index_name = index[0]
                        # 验证索引名称格式
                        if VALID_INDEX_PATTERN.match(index_name):
                            c.execute(f'DROP INDEX IF EXISTS {index_name};')
                        else:
                            logging.warning(f'  ⚠️  跳过无效索引名: {index_name}')

                conn.commit()

                # 使用 v9 schema 创建所有表
                self._create_fresh_database_v9(c)
                
                # 设置版本号为 9
                c.execute('PRAGMA user_version = 9;')
                current_version = 9
                conn.commit()
                logging.info('✅ 数据库已准备就绪（v9 schema）')
            else:
                logging.debug(f'✓ 数据库已是最新版本 (v{current_version})，所有表完整')

            # ============================================================
            # 增量升级已弃用：统一使用 v9 schema
            # 旧的增量升级代码已移至文件末尾的 _deprecated_incremental_upgrade()
            # ============================================================

                conn.commit()
            logging.debug('Database Version: %s', str(current_version))
            conn.close()

        except Error as error:
            raise RuntimeError(f'Could not create database! Error: {error}')

    # ============================================================
    # 已删除：旧的增量升级代码（v0→v1→v2→...→v8）
    # 现在统一使用 v9 schema，大幅简化数据库初始化逻辑
    # 如需参考旧代码，请查看 Git 历史记录
    # ============================================================

    @staticmethod
    def _create_fresh_database_v8(cursor):
        """
        为全新数据库直接创建 v8 schema（v9 的基础部分）
        
        这比执行 8 次增量升级高效得多！
        
        @param cursor: SQLite cursor
        """
        # 创建 files 表（包含所有 v8 的字段）
        cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                course_fullname TEXT NOT NULL,
                module_id INTEGER NOT NULL,
                section_name TEXT NOT NULL,
                section_id INTEGER DEFAULT 0 NOT NULL,
                module_name TEXT NOT NULL,
                content_filepath TEXT NOT NULL,
                content_filename TEXT NOT NULL,
                content_fileurl TEXT NOT NULL,
                content_filesize INTEGER DEFAULT 0 NOT NULL,
                content_timemodified INTEGER DEFAULT 0 NOT NULL,
                module_modname TEXT NOT NULL,
                content_type TEXT NOT NULL,
                content_isexternalfile INTEGER DEFAULT 0 NOT NULL,
                content TEXT,
                text_content TEXT,
                html_content TEXT,
                saved_to TEXT NOT NULL,
                time_stamp INTEGER DEFAULT 0 NOT NULL,
                modified INTEGER DEFAULT 0 NOT NULL,
                deleted INTEGER DEFAULT 0 NOT NULL,
                moved INTEGER DEFAULT 0 NOT NULL,
                notified INTEGER DEFAULT 0 NOT NULL,
                hash TEXT,
                old_file_id INTEGER DEFAULT 0,
                download_status TEXT DEFAULT 'pending',
                download_attempts INTEGER DEFAULT 0,
                last_download_at INTEGER DEFAULT 0,
                last_failed_at INTEGER DEFAULT 0,
                last_failed_reason TEXT,
                consecutive_failures INTEGER DEFAULT 0,
                position_in_section INTEGER,
                -- 🆕 扩展元数据字段（v10 schema）
                visible INTEGER DEFAULT 1 NOT NULL,
                uservisible INTEGER DEFAULT 1 NOT NULL,
                availabilityinfo TEXT,
                completion INTEGER DEFAULT 0 NOT NULL,
                timecreated INTEGER DEFAULT 0 NOT NULL,
                sortorder INTEGER DEFAULT 0 NOT NULL
            );
        """)
        
        # 创建 files 表的所有索引
        files_indexes = [
            "CREATE INDEX IF NOT EXISTS idx_module_id ON files(module_id);",
            "CREATE INDEX IF NOT EXISTS idx_course_id ON files(course_id);",
            "CREATE INDEX IF NOT EXISTS idx_files_saved_to ON files(saved_to);",
            "CREATE INDEX IF NOT EXISTS idx_files_time_stamp ON files(time_stamp);",
            "CREATE INDEX IF NOT EXISTS idx_files_modified ON files(modified);",
            "CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(deleted);",
            "CREATE INDEX IF NOT EXISTS idx_files_notified ON files(notified);",
            "CREATE INDEX IF NOT EXISTS idx_download_status ON files(download_status);",
            "CREATE INDEX IF NOT EXISTS idx_consecutive_failures ON files(consecutive_failures);",
            "CREATE INDEX IF NOT EXISTS idx_position_in_section ON files(course_id, section_id, position_in_section);",
            # 幂等性增强：添加唯一索引，防止重复文件
            # 只对未删除的文件应用约束（deleted = 0），允许已删除文件的 URL 被重用
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_file_url ON files(course_id, module_id, content_fileurl) WHERE deleted = 0;",
            # 🆕 扩展元数据字段的索引
            "CREATE INDEX IF NOT EXISTS idx_visible ON files(visible);",
            "CREATE INDEX IF NOT EXISTS idx_uservisible ON files(uservisible);",
            "CREATE INDEX IF NOT EXISTS idx_completion ON files(completion);",
            "CREATE INDEX IF NOT EXISTS idx_sortorder ON files(sortorder);",
        ]

        for idx_sql in files_indexes:
            cursor.execute(idx_sql)

        # 🆕 向后兼容：为已存在的数据库添加新列（v10 schema migration）
        # 使用 ALTER TABLE ... ADD COLUMN 并在列已存在时捕获异常
        migration_columns = [
            ('visible', 'INTEGER DEFAULT 1 NOT NULL'),
            ('uservisible', 'INTEGER DEFAULT 1 NOT NULL'),
            ('availabilityinfo', 'TEXT'),
            ('completion', 'INTEGER DEFAULT 0 NOT NULL'),
            ('timecreated', 'INTEGER DEFAULT 0 NOT NULL'),
            ('sortorder', 'INTEGER DEFAULT 0 NOT NULL'),
        ]

        for column_name, column_def in migration_columns:
            try:
                cursor.execute(f"ALTER TABLE files ADD COLUMN {column_name} {column_def};")
                logging.info(f"Added migration column: {column_name}")
            except Exception as e:
                # 列已存在或其他错误，忽略
                if "duplicate column name" not in str(e).lower():
                    logging.debug(f"Migration column {column_name}: {e}")
        
        # 创建 auth_sessions 表
        cursor.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_id TEXT PRIMARY KEY,
                    session_type TEXT NOT NULL,
                    owner_id TEXT,
                    creator_id TEXT,
                    token_value TEXT,
                    private_token_value TEXT,
                    status TEXT DEFAULT 'valid',
                    created_at INTEGER NOT NULL,
                    last_accessed_at INTEGER DEFAULT 0,
                    expires_at INTEGER,
                    source TEXT NOT NULL,
                    ip_restriction TEXT,
                    ip_address TEXT,
                    previous_session_id TEXT,
                    replaced_by_session_id TEXT,
                    context_id TEXT,
                    metadata TEXT,
                    FOREIGN KEY (previous_session_id) REFERENCES auth_sessions(session_id),
                    FOREIGN KEY (replaced_by_session_id) REFERENCES auth_sessions(session_id)
                );
        """)

        # 创建 cookie_store 表
        cursor.execute("""
                CREATE TABLE IF NOT EXISTS cookie_store (
                    cookie_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    path TEXT DEFAULT '/',
                    expires INTEGER,
                    max_age INTEGER,
                    secure INTEGER DEFAULT 0,
                    httponly INTEGER DEFAULT 0,
                    samesite TEXT DEFAULT 'Lax',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER DEFAULT 0,
                    valid INTEGER DEFAULT 1,
                    FOREIGN KEY (session_id) REFERENCES auth_sessions(session_id)
                );
        """)

        # 创建 auth_audit_log 表
        cursor.execute("""
                CREATE TABLE IF NOT EXISTS auth_audit_log (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    triggered_by TEXT NOT NULL,
                    user_id TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    context_id TEXT,
                    timestamp INTEGER NOT NULL,
                    details TEXT,
                    FOREIGN KEY (session_id) REFERENCES auth_sessions(session_id)
                );
        """)

        # 创建认证表的所有索引
        auth_indexes = [
                    "CREATE INDEX IF NOT EXISTS idx_auth_token ON auth_sessions(token_value);",
                    "CREATE INDEX IF NOT EXISTS idx_auth_owner ON auth_sessions(owner_id, created_at);",
                    "CREATE INDEX IF NOT EXISTS idx_auth_creator ON auth_sessions(creator_id);",
                    "CREATE INDEX IF NOT EXISTS idx_auth_status ON auth_sessions(status);",
                    "CREATE INDEX IF NOT EXISTS idx_auth_expires ON auth_sessions(expires_at);",
                    "CREATE INDEX IF NOT EXISTS idx_cookie_session ON cookie_store(session_id);",
                    "CREATE INDEX IF NOT EXISTS idx_cookie_name ON cookie_store(name);",
                    "CREATE INDEX IF NOT EXISTS idx_audit_session ON auth_audit_log(session_id);",
                    "CREATE INDEX IF NOT EXISTS idx_audit_action ON auth_audit_log(action);",
                    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON auth_audit_log(timestamp);",
                    "CREATE INDEX IF NOT EXISTS idx_audit_user ON auth_audit_log(user_id);",
                ]

        for idx_sql in auth_indexes:
            cursor.execute(idx_sql)

        # 设置数据库版本为 8
        cursor.execute('PRAGMA user_version = 8;')
        
        logging.info('✅ 全新数据库创建完成（v8 schema）')

    @staticmethod
    def _create_fresh_database_v9(cursor):
        """
        为全新数据库直接创建 v9 schema（最新版本）
        
        v9 相比 v8 的改进：
        - 添加 incomplete_downloads 表用于支持断点续传
        
        @param cursor: SQLite cursor
        """
        # 首先创建 v8 的所有表
        StateRecorder._create_fresh_database_v8(cursor)
        
        # 创建 incomplete_downloads 表（用于断点续传）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incomplete_downloads (
                download_id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                file_url TEXT NOT NULL,
                file_path TEXT NOT NULL,
                total_bytes INTEGER DEFAULT 0,
                downloaded_bytes INTEGER DEFAULT 0,
                start_time INTEGER NOT NULL,
                last_update_time INTEGER NOT NULL,
                server_supports_range INTEGER DEFAULT 0,
                etag TEXT,
                last_modified TEXT,
                attempts INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                error_reason TEXT,
                FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE,
                UNIQUE(file_id, file_path)
            );
        """)
        
        # 创建 incomplete_downloads 表的索引
        incomplete_dl_indexes = [
            "CREATE INDEX IF NOT EXISTS idx_incomplete_file_id ON incomplete_downloads(file_id);",
            "CREATE INDEX IF NOT EXISTS idx_incomplete_status ON incomplete_downloads(status);",
            "CREATE INDEX IF NOT EXISTS idx_incomplete_last_update ON incomplete_downloads(last_update_time);",
            "CREATE INDEX IF NOT EXISTS idx_incomplete_attempts ON incomplete_downloads(attempts);",
        ]
        
        for idx_sql in incomplete_dl_indexes:
            cursor.execute(idx_sql)
        
        # 设置数据库版本为 9
        cursor.execute('PRAGMA user_version = 9;')
        
        logging.info('✅ 全新数据库创建完成（v9 schema - 支持断点续传）')

    @staticmethod
    def files_have_same_type(file1: File, file2: File) -> bool:
        # Returns True if the files have the same type attributes

        if file1.content_type == file2.content_type and file1.module_modname == file2.module_modname:
            return True

        elif (
            file1.content_type == 'description-url'
            and file1.content_type == file2.content_type
            and (
                file1.module_modname.startswith(file2.module_modname)
                or file2.module_modname.startswith(file1.module_modname)
            )
        ):
            # stop redownloading old description urls. Sorry the  module_modname structure has changed
            return True

        return False

    @classmethod
    def files_have_same_path(cls, file1: File, file2: File) -> bool:
        # Returns True if the files have the same path attributes

        if (
            file1.module_id == file2.module_id
            and file1.section_name == file2.section_name
            and file1.content_filepath == file2.content_filepath
            and file1.content_filename == file2.content_filename
            and cls.files_have_same_type(file1, file2)
            and (file1.content_type != 'description' or file1.module_name == file2.module_name)
        ):
            return True
        return False

    @staticmethod
    def files_are_diffrent(file1: File, file2: File) -> bool:
        # Returns True if these files differ from each other

        # Debug cookie_mod files
        if file1.content_type == 'cookie_mod' or file2.content_type == 'cookie_mod':
            url_diff = file1.content_fileurl != file2.content_fileurl
            time_diff = file1.content_timemodified != file2.content_timemodified
            size_diff = file1.content_filesize != file2.content_filesize
            module_id_diff = file1.module_id != file2.module_id
            logging.debug(f'[files_are_different] cookie_mod comparison:')
            logging.debug(f'  file1: url={file1.content_fileurl[:80]}..., time={file1.content_timemodified}, size={file1.content_filesize}, module_id={file1.module_id}')
            logging.debug(f'  file2: url={file2.content_fileurl[:80]}..., time={file2.content_timemodified}, size={file2.content_filesize}, module_id={file2.module_id}')
            logging.debug(f'  url_diff={url_diff}, time_diff={time_diff}, size_diff={size_diff}, module_id_diff={module_id_diff}')

        # For cookie_mod files (especially kalvidres), URL or module_id difference means different files
        # This is important because Kaltura videos may have same time=0, size=0 but different URLs/module_ids
        if file1.content_type == 'cookie_mod' or file2.content_type == 'cookie_mod':
            if file1.content_fileurl != file2.content_fileurl or file1.module_id != file2.module_id:
                result = True
            elif file1.content_filesize != file2.content_filesize:
                result = True
            else:
                result = False
        # Not sure if this would be a good idea
        #  or file1.module_name != file2.module_name)
        elif file1.content_filesize != file2.content_filesize or (
            file1.content_fileurl != file2.content_fileurl and file1.content_timemodified != file2.content_timemodified
        ):
            result = True
        elif (
            file1.content_type in ('description', 'html')
            and file1.content_type == file2.content_type
            and (file1.hash != file2.hash or file1.content_timemodified != file2.content_timemodified)
        ):
            result = True
        elif (
            file1.content_type == 'description-url'
            and file1.content_type == file2.content_type
            and file1.content_fileurl != file2.content_fileurl
            # One consideration: or file1.section_name != file2.section_name)
            # But useless if description-links in the course must be unique anyway
        ):
            result = True
        else:
            result = False

        if file1.content_type == 'cookie_mod' or file2.content_type == 'cookie_mod':
            logging.debug(f'  Result: files_are_different={result}')

        return result

    @staticmethod
    def files_are_moveable(file1: File, file2: File) -> bool:
        # Descriptions are not not movable at all
        if file1.content_type == 'description' or file2.content_type == 'description':
            return False
        # HTMLs with no hash are not moveable
        if (file1.content_type == 'html' and file1.hash is None) or (
            file2.content_type == 'html' and file2.hash is None
        ):
            return False
        return True

    @classmethod
    def file_was_moved(cls, file1: File, file2: File) -> bool:
        # Returns True if the file was moved to an other path

        if (
            not cls.files_are_diffrent(file1, file2)
            and cls.files_have_same_type(file1, file2)
            and not cls.files_have_same_path(file1, file2)
            and cls.files_are_moveable(file1, file2)
        ):
            return True
        return False

    @staticmethod
    def ignore_deleted(file: File):
        # Returns true if the deleted file should be ignored.
        if file.module_modname.endswith(('forum', 'calendar')):
            return True

        return False

    @staticmethod
    def _file_exists_on_disk(file: File) -> bool:
        """
        检查文件是否实际存在于磁盘上
        
        用于处理以下场景：
        - 用户手动删除了下载的文件（如 .webloc 快捷方式）
        - 用户希望重新下载但数据库中仍有记录
        
        如果文件在数据库中有记录但磁盘上不存在，应该重新下载
        
        @param file: 数据库中存储的文件记录
        @return: True 如果文件存在于磁盘上
        """
        if not file.saved_to:
            # 没有保存路径，认为文件不存在
            return False
        
        return os.path.exists(file.saved_to)

    def get_stored_files(self) -> List[Course]:
        """
        获取所有存储的文件（未删除、未修改、未移动的文件）
        
        ✅ 优化：使用缓存和优化查询替代 N+1 查询
        之前: 2 个查询 (1 个获取课程 + N 个获取每个课程的文件)
        现在: 1 个查询 + 内存分组
        """
        # 生成缓存键
        cache_key = self._get_cache_key('get_stored_files')
        
        # 使用缓存或执行优化查询
        def query_func():
            where_clause = "deleted = 0 AND modified = 0 AND moved = 0"
            return self._query_files_optimized(where_clause)
        
        return self._get_cached(cache_key, query_func)

    def get_old_files(self) -> List[Course]:
        """
        获取所有有旧版本的文件
        
        ✅ 优化：使用缓存和优化查询替代 N+1 查询
        """
        # 生成缓存键
        cache_key = self._get_cache_key('get_old_files')
        
        # 使用缓存或执行优化查询
        def query_func():
            where_clause = "old_file_id IS NOT NULL"
            return self._query_files_optimized(where_clause)
        
        result = self._get_cached(cache_key, query_func)
        
        # 保留原始方法的返回值处理
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        stored_courses = result
        
        for course in stored_courses:
            cursor.execute(
                """SELECT *
                FROM files
                WHERE course_id = ?
                AND old_file_id IS NOT NULL""",
                (course.id,),
            )

            updated_files = cursor.fetchall()

            course.files = []

            for updated_file in updated_files:
                cursor.execute(
                    """SELECT *
                    FROM files
                    WHERE file_id = ?""",
                    (updated_file['old_file_id'],),
                )

                old_file = cursor.fetchone()

                notify_file = File.fromRow(old_file)
                course.files.append(notify_file)

            stored_courses.append(course)

        conn.close()
        return stored_courses

    def get_modified_files(self, stored_courses: List[Course], current_courses: List[Course]) -> List[Course]:
        # returns courses with modified and deleted files
        changed_courses = []

        for stored_course in stored_courses:
            same_course_in_current = None

            for current_course in current_courses:
                if current_course.id == stored_course.id:
                    same_course_in_current = current_course
                    break

            if same_course_in_current is None:
                # stroed_course does not exist anymore!

                # maybe it would be better
                # to not notify about this changes?
                for stored_file in stored_course.files:
                    stored_file.deleted = True
                    stored_file.notified = False
                changed_courses.append(stored_course)
                # skip the next checks!
                continue

            # there is the same course in the current set
            # so try to find removed files, that are still exist in storage
            # also find modified files
            changed_course = Course(stored_course.id, stored_course.fullname)
            for stored_file in stored_course.files:
                matching_file = None

                for current_file in same_course_in_current.files:
                    # Try to find a matching file with same path
                    if self.files_have_same_path(current_file, stored_file):
                        matching_file = current_file
                        # file does still exist
                        break

                if matching_file is not None:
                    # An matching file was found
                    # Test for modification
                    if self.files_are_diffrent(matching_file, stored_file):
                        # file is modified
                        matching_file.modified = True
                        matching_file.old_file = stored_file
                        changed_course.files.append(matching_file)

                    continue

                # No matching file was found --> file was deleted or moved
                # check for moved files

                for current_file in same_course_in_current.files:
                    # Try to find a matching file that was moved
                    if self.file_was_moved(current_file, stored_file):
                        matching_file = current_file
                        # file does still exist
                        break

                if matching_file is None and not self.ignore_deleted(stored_file):
                    # No matching file was found --> file was deleted
                    stored_file.deleted = True
                    stored_file.notified = False
                    changed_course.files.append(stored_file)

                elif matching_file is not None:
                    matching_file.moved = True
                    matching_file.old_file = stored_file
                    changed_course.files.append(matching_file)

            if len(changed_course.files) > 0:
                changed_courses.append(changed_course)

        return changed_courses

    def get_new_files(
        self, changed_courses: List[Course], stored_courses: List[Course], current_courses: List[Course]
    ) -> List[Course]:
        # check for new files
        for current_course in current_courses:
            # check if that file does not exist in stored

            same_course_in_stored = None

            for stored_course in stored_courses:
                if stored_course.id == current_course.id:
                    same_course_in_stored = stored_course
                    break

            if same_course_in_stored is None:
                # current_course is not saved yet

                changed_courses.append(current_course)
                # skip the next checks!
                continue

            # Debug: Count kalvidres in current course
            current_kalvidres = [f for f in current_course.files if f.module_modname == 'cookie_mod-kalvidres']
            if len(current_kalvidres) > 0:
                logging.info(f'🔍 [get_new_files] Course "{current_course.fullname}" has {len(current_kalvidres)} kalvidres in current_course.files')
                stored_kalvidres = [f for f in same_course_in_stored.files if f.module_modname == 'cookie_mod-kalvidres']
                logging.info(f'🔍 [get_new_files] Same course has {len(stored_kalvidres)} kalvidres in stored files')

            changed_course = Course(current_course.id, current_course.fullname)
            kalvidres_matched_count = 0
            kalvidres_new_count = 0

            for current_file in current_course.files:
                matching_file = None

                for stored_file in same_course_in_stored.files:
                    # Try to find a matching file
                    has_same_path = self.files_have_same_path(current_file, stored_file)
                    was_moved = self.file_was_moved(current_file, stored_file)
                    if has_same_path or was_moved:
                        # 🆕 检查文件是否实际存在于磁盘上
                        # 如果用户手动删除了文件，应该重新下载
                        if not self._file_exists_on_disk(stored_file):
                            logging.info(
                                f'📁 [get_new_files] 文件在数据库中存在但磁盘上不存在，将重新下载: '
                                f'{stored_file.saved_to}'
                            )
                            # 不设置 matching_file，让文件被加入下载队列
                            break
                        
                        matching_file = current_file
                        # Debug: Log if kalvidres file matched
                        if current_file.module_modname == 'cookie_mod-kalvidres':
                            kalvidres_matched_count += 1
                            logging.debug(f'❌ [get_new_files] Kalvidres "{current_file.content_filename}" matched with stored file')
                            logging.debug(f'   Current: module_id={current_file.module_id}, filename={current_file.content_filename}, filepath={current_file.content_filepath}')
                            logging.debug(f'   Stored:  module_id={stored_file.module_id}, filename={stored_file.content_filename}, filepath={stored_file.content_filepath}, modname={stored_file.module_modname}')
                        break

                if matching_file is None:
                    # current_file is a new file
                    if current_file.module_modname == 'cookie_mod-kalvidres':
                        kalvidres_new_count += 1
                    changed_course.files.append(current_file)

            if kalvidres_matched_count > 0 or kalvidres_new_count > 0:
                logging.info(f'📊 [get_new_files] Kalvidres results: {kalvidres_new_count} new, {kalvidres_matched_count} matched (not new)')

            if len(changed_course.files) > 0:
                matched_changed_course = None
                for ch_course in changed_courses:
                    if ch_course.id == changed_course.id:
                        matched_changed_course = ch_course
                        break
                if matched_changed_course is None:
                    changed_courses.append(changed_course)
                else:
                    matched_changed_course.files += changed_course.files
        return changed_courses

    def changes_of_new_version(self, current_courses: List[Course]) -> List[Course]:
        # all changes are stored inside changed_courses,
        # as a list of changed courses
        changed_courses = []

        # this is kind of bad code ... maybe someone can fix it

        # we need to check if there are files stored that
        # are no longer exists on Moodle => deleted
        # And if there are files that are already existing
        # check if they are modified => modified

        # later check for new files

        # first get all stored files (that are not yet deleted)
        stored_courses = self.get_stored_files()

        changed_courses = self.get_modified_files(stored_courses, current_courses)
        # ----------------------------------------------------------

        # check for new files
        changed_courses = self.get_new_files(changed_courses, stored_courses, current_courses)

        return changed_courses

    def get_last_timestamp_per_mod_module(self) -> Dict[str, Dict[int, int]]:
        """
        Returns a dict per mod of timestamps per course module id
        Like:
        {
            "forum": {
                345: 12345623466,
                346: 12345623531,
            }
        }
        """

        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        mod_forum_dict = {}
        mod_calendar_dict = {}

        cursor.execute(
            """SELECT module_id, max(content_timemodified) as content_timemodified
            FROM files WHERE module_modname = 'forum' AND content_type = 'description'
            GROUP BY module_id;"""
        )

        curse_rows = cursor.fetchall()

        for course_row in curse_rows:
            mod_forum_dict[course_row['module_id']] = course_row['content_timemodified']

        cursor.execute(
            """SELECT module_id, max(content_timemodified) as content_timemodified
            FROM files WHERE module_modname = 'calendar' AND content_type = 'html'
            GROUP BY module_id;"""
        )

        course_row = cursor.fetchone()
        if course_row is not None:
            mod_calendar_dict[course_row['module_id']] = course_row['content_timemodified']

        conn.close()

        return {'forum': mod_forum_dict, 'calendar': mod_calendar_dict}

    def changes_to_notify(self) -> List[Course]:
        changed_courses = []

        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """SELECT course_id, course_fullname
            FROM files WHERE notified = 0 GROUP BY course_id;"""
        )

        curse_rows = cursor.fetchall()

        for course_row in curse_rows:
            course = Course(course_row['course_id'], course_row['course_fullname'])

            cursor.execute(
                """SELECT *
                FROM files WHERE notified = 0 AND course_id = ?;""",
                (course.id,),
            )

            file_rows = cursor.fetchall()

            course.files = []

            for file_row in file_rows:
                notify_file = File.fromRow(file_row)
                if notify_file.modified or notify_file.moved:
                    # add reference to new file

                    cursor.execute(
                        """SELECT *
                        FROM files
                        WHERE old_file_id = ?;""",
                        (notify_file.file_id,),
                    )

                    file_row = cursor.fetchone()
                    if file_row is not None:
                        notify_file.new_file = File.fromRow(file_row)

                course.files.append(notify_file)

            changed_courses.append(course)

        conn.close()
        return changed_courses

    def notified(self, courses: List[Course]):
        # saves that a notification with the changes where send

        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        for course in courses:
            course_id = course.id

            for file in course.files:
                data = {'course_id': course_id}
                data.update(file.getMap())

                cursor.execute(
                    """UPDATE files
                    SET notified = 1
                    WHERE file_id = :file_id;
                    """,
                    data,
                )

        conn.commit()
        conn.close()

    def save_file(self, file: File, course_id: int, course_fullname: str):
        # 🆕 清除相关缓存（数据有变化）
        self._clear_cache('get_stored_files')
        self._clear_cache('get_old_files')
        
        if file.deleted:
            self.delete_file(file, course_id, course_fullname)
        elif file.modified:
            self.modify_file(file, course_id, course_fullname)
        elif file.moved:
            self.move_file(file, course_id, course_fullname)
        else:
            self.new_file(file, course_id, course_fullname)

    @staticmethod
    def _set_insert_defaults(data: dict) -> dict:
        """
        为 File.INSERT SQL 语句设置所有必需的默认值
        
        这个方法确保所有 INSERT 操作都有完整的字段值，
        避免 sqlite3.ProgrammingError: You did not supply a value for binding parameter
        
        遵循 DRY 原则：默认值只在此处定义一次
        """
        # 状态标志字段
        data.setdefault('modified', 0)
        data.setdefault('deleted', 0)
        data.setdefault('moved', 0)
        data.setdefault('notified', 0)
        data.setdefault('old_file_id', 0)

        # 下载追踪字段（v7 引入）
        data.setdefault('download_status', 'pending')
        data.setdefault('download_attempts', 0)
        data.setdefault('last_download_at', 0)
        data.setdefault('last_failed_at', 0)
        data.setdefault('last_failed_reason', None)
        data.setdefault('consecutive_failures', 0)
        data.setdefault('position_in_section', 0)
        
        return data

    def new_file(self, file: File, course_id: int, course_fullname: str):
        """
        保存新文件到数据库索引
        
        幂等性保证：如果文件已存在（基于 course_id, module_id, content_fileurl），
        则跳过插入，避免创建重复记录。
        
        @param file: 文件对象
        @param course_id: 课程 ID
        @param course_fullname: 课程全名
        @return: file_id（新插入的或已存在的）
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 幂等性检查：查询文件是否已存在
        cursor.execute(
            """SELECT file_id FROM files 
               WHERE course_id = ? AND module_id = ? AND content_fileurl = ?""",
            (course_id, file.module_id, file.content_fileurl)
        )
        existing = cursor.fetchone()
        
        if existing:
            # 文件已存在，跳过插入
            file_id = existing[0]
            logging.debug(
                f'文件已存在于数据库中，跳过插入: {file.content_filename} (file_id={file_id})'
            )
            conn.close()
            return file_id

        # 文件不存在，执行插入
        data = {'course_id': course_id, 'course_fullname': course_fullname}
        data.update(file.getMap())
        self._set_insert_defaults(data)

        cursor.execute(File.INSERT, data)
        file_id = cursor.lastrowid
        logging.debug(f'插入新文件记录: {file.content_filename} (file_id={file_id})')

        conn.commit()
        conn.close()
        
        return file_id

    def batch_delete_files(self, courses: List[Course]):
        # 🆕 清除相关缓存（数据有变化）
        self._clear_cache('get_stored_files')
        self._clear_cache('get_old_files')
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        for course in courses:
            for file in course.files:
                if file.deleted:
                    data = {'course_id': course.id, 'course_fullname': course.fullname}
                    data.update(file.getMap())

                    cursor.execute(
                        """UPDATE files
                        SET notified = 0, deleted = 1, time_stamp = :time_stamp
                        WHERE file_id = :file_id;
                        """,
                        data,
                    )

        conn.commit()
        conn.close()

    def batch_delete_files_from_db(self, files: List[File]):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        for file in files:
            cursor.execute(
                """UPDATE files
                SET old_file_id = NULL
                WHERE old_file_id = ?
                """,
                (file.file_id,),
            )

            data = {}
            data.update(file.getMap())

            cursor.execute(
                """DELETE FROM files
                WHERE file_id = :file_id
                """,
                data,
            )

        conn.commit()
        conn.close()

    def delete_file(self, file: File, course_id: int, course_fullname: str):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        data = {'course_id': course_id, 'course_fullname': course_fullname}
        data.update(file.getMap())

        cursor.execute(
            """UPDATE files
            SET notified = 0, deleted = 1, time_stamp = :time_stamp
            WHERE file_id = :file_id;
            """,
            data,
        )

        conn.commit()
        conn.close()

    def move_file(self, file: File, course_id: int, course_fullname: str):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        data_new = {'course_id': course_id, 'course_fullname': course_fullname}
        data_new.update(file.getMap())
        self._set_insert_defaults(data_new)

        if file.old_file is not None:
            # insert a new file, but it is already notified because the same file already exists as moved
            data_new.update(
                {'old_file_id': file.old_file.file_id, 'modified': 0, 'moved': 0, 'deleted': 0, 'notified': 1}
            )
            cursor.execute(File.INSERT, data_new)

            data_old = {'course_id': course_id, 'course_fullname': course_fullname}
            data_old.update(file.old_file.getMap())

            cursor.execute(
                """UPDATE files
            SET notified = 0, moved = 1
            WHERE file_id = :file_id;
            """,
                data_old,
            )
        else:
            # this should never happen, but the old file is not saved in the
            # file descriptor, so we need to inform about the new file notified = 0
            data_new.update({'modified': 0, 'deleted': 0, 'moved': 0, 'notified': 0})
            cursor.execute(File.INSERT, data_new)

        conn.commit()
        conn.close()

    def modify_file(self, file: File, course_id: int, course_fullname: str):
        """
        处理已修改的文件：更新旧文件状态并插入新文件记录
        
        @param file: 修改后的文件对象
        @param course_id: 课程 ID
        @param course_fullname: 课程全名
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        data_new = {'course_id': course_id, 'course_fullname': course_fullname}
        data_new.update(file.getMap())
        self._set_insert_defaults(data_new)

        if file.old_file is not None:
            # insert a new file,
            # but it is already notified because the same file already exists
            # as modified
            data_new.update(
                {'old_file_id': file.old_file.file_id, 'modified': 0, 'moved': 0, 'deleted': 0, 'notified': 1}
            )
            cursor.execute(File.INSERT, data_new)

            data_old = {'course_id': course_id, 'course_fullname': course_fullname}
            data_old.update(file.old_file.getMap())

            cursor.execute(
                """UPDATE files
            SET notified = 0, modified = 1,
            saved_to = :saved_to
            WHERE file_id = :file_id;
            """,
                data_old,
            )
        else:
            # this should never happen, but the old file is not saved in the
            # file descriptor, so we need to inform about the new file
            # notified = 0

            data_new.update({'modified': 0, 'deleted': 0, 'moved': 0, 'notified': 0})
            cursor.execute(File.INSERT, data_new)

        conn.commit()
        conn.close()

    def save_failed_file(self, file: File, course_id: int, course_fullname: str, error_message: str):
        """
        记录下载失败的文件，包括目标路径和失败原因

        @param file: 失败的文件对象
        @param course_id: 课程 ID
        @param course_fullname: 课程全名
        @param error_message: 失败原因
        """
        import time

        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        current_time = int(time.time())

        # 检查文件是否已存在
        cursor.execute(
            """SELECT file_id, download_attempts, consecutive_failures
               FROM files
               WHERE course_id = ?
               AND module_id = ?
               AND content_fileurl = ?
            """,
            (course_id, file.module_id, file.content_fileurl)
        )
        existing = cursor.fetchone()

        if existing:
            # 文件已存在，更新失败记录
            file_id, attempts, consecutive = existing
            cursor.execute(
                """UPDATE files
                SET download_status = 'failed',
                    download_attempts = ?,
                    last_download_at = ?,
                    last_failed_at = ?,
                    last_failed_reason = ?,
                    consecutive_failures = ?,
                    saved_to = ?,
                    notified = 0
                WHERE file_id = ?
                """,
                (
                    attempts + 1,
                    current_time,
                    current_time,
                    error_message[:500] if error_message else None,  # 限制长度
                    consecutive + 1,
                    file.saved_to,
                    file_id
                )
            )
            logging.debug(f'更新失败文件记录: {file.content_filename} (尝试次数: {attempts + 1})')
        else:
            # 新文件，插入失败记录
            data = {'course_id': course_id, 'course_fullname': course_fullname}
            data.update(file.getMap())
            self._set_insert_defaults(data)
            # 覆盖失败相关的字段
            data.update({
                'download_status': 'failed',
                'download_attempts': 1,
                'last_download_at': current_time,
                'last_failed_at': current_time,
                'last_failed_reason': error_message[:500] if error_message else None,
                'consecutive_failures': 1,
            })

            cursor.execute(File.INSERT, data)
            logging.debug(f'插入失败文件记录: {file.content_filename}')

        conn.commit()
        conn.close()

    def mark_download_success(self, file: File, course_id: int):
        """
        标记文件下载成功，重置失败计数器

        @param file: 成功下载的文件对象
        @param course_id: 课程 ID
        """
        import time

        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        current_time = int(time.time())

        cursor.execute(
            """UPDATE files
            SET download_status = 'success',
                last_download_at = ?,
                consecutive_failures = 0,
                last_failed_reason = NULL
            WHERE course_id = ?
            AND module_id = ?
            AND content_fileurl = ?
            """,
            (current_time, course_id, file.module_id, file.content_fileurl)
        )

        conn.commit()
        conn.close()

    def get_failed_files(self, course_id: int = None, min_failures: int = 1) -> List[File]:
        """
        查询下载失败的文件列表
        
        包含 'failed' 和 'retrying' 状态的文件：
        - 'failed': 上次下载失败的文件
        - 'retrying': 正在重试但被中断的文件（上次 --retry-failed 中途中断）

        @param course_id: 可选，只查询特定课程的失败文件
        @param min_failures: 最小连续失败次数，默认1（所有失败文件）
                            注意：'retrying' 状态的文件 consecutive_failures=0，
                            但它们仍需要被重试，所以也会被包含
        @return: 失败的文件列表
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        if course_id:
            cursor.execute(
                """SELECT * FROM files
                WHERE course_id = ?
                AND (
                    (download_status = 'failed' AND consecutive_failures >= ?)
                    OR download_status = 'retrying'
                )
                ORDER BY consecutive_failures DESC, last_failed_at DESC
                """,
                (course_id, min_failures)
            )
        else:
            cursor.execute(
                """SELECT * FROM files
                WHERE (
                    (download_status = 'failed' AND consecutive_failures >= ?)
                    OR download_status = 'retrying'
                )
                ORDER BY consecutive_failures DESC, last_failed_at DESC
                """,
                (min_failures,)
            )

        results = cursor.fetchall()
        conn.close()

        # 转换为 File 对象列表
        failed_files = []
        for row in results:
            file_dict = dict(zip([d[0] for d in cursor.description], row))
            file = File(
                module_id=file_dict['module_id'],
                section_name=file_dict['section_name'],
                section_id=file_dict.get('section_id', 0),
                module_name=file_dict['module_name'],
                content_filepath=file_dict['content_filepath'],
                content_filename=file_dict['content_filename'],
                content_fileurl=file_dict['content_fileurl'],
                content_filesize=file_dict['content_filesize'],
                content_timemodified=file_dict['content_timemodified'],
                module_modname=file_dict['module_modname'],
                content_type=file_dict['content_type'],
                content_isexternalfile=file_dict['content_isexternalfile'],
                saved_to=file_dict['saved_to'],
                time_stamp=file_dict['time_stamp'],
                modified=file_dict['modified'],
                moved=file_dict.get('moved', 0),
                deleted=file_dict['deleted'],
                notified=file_dict['notified'],
                file_hash=file_dict.get('hash'),
                file_id=file_dict.get('file_id'),
                old_file_id=file_dict.get('old_file_id'),
                position_in_section=file_dict.get('position_in_section')
            )
            failed_files.append(file)

        return failed_files

    def get_failed_files_with_course_info(self, min_failures: int = 1) -> Dict[int, Dict]:
        """
        查询下载失败的文件列表，并按课程分组
        
        包含 'failed' 和 'retrying' 状态的文件：
        - 'failed': 上次下载失败的文件
        - 'retrying': 正在重试但被中断的文件（上次 --retry-failed 中途中断）

        @param min_failures: 最小连续失败次数，默认1（所有失败文件）
                            注意：'retrying' 状态的文件 consecutive_failures=0，
                            但它们仍需要被重试，所以也会被包含
        @return: 字典，键为 course_id，值为包含 course_fullname 和 files 列表的字典
        """
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """SELECT * FROM files
            WHERE (
                (download_status = 'failed' AND consecutive_failures >= ?)
                OR download_status = 'retrying'
            )
            ORDER BY course_id, consecutive_failures DESC, last_failed_at DESC
            """,
            (min_failures,)
        )

        results = cursor.fetchall()
        conn.close()

        # 按课程分组
        courses_dict = {}
        for row in results:
            course_id = row['course_id']
            course_fullname = row['course_fullname']

            if course_id not in courses_dict:
                courses_dict[course_id] = {
                    'course_fullname': course_fullname,
                    'files': []
                }

            # 构造 File 对象
            file = File(
                module_id=row['module_id'],
                section_name=row['section_name'],
                section_id=row['section_id'] if row['section_id'] is not None else 0,
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
                moved=row['moved'] if row['moved'] is not None else 0,
                deleted=row['deleted'],
                notified=row['notified'],
                file_hash=row['hash'],
                file_id=row['file_id'],
                old_file_id=row['old_file_id'] if row['old_file_id'] is not None else 0,
                position_in_section=row['position_in_section'] if row['position_in_section'] is not None else None
            )

            courses_dict[course_id]['files'].append(file)

        return courses_dict

    def get_failed_files_summary(self) -> Dict[int, Dict]:
        """
        获取失败文件的统计摘要（按课程分组）
        
        包含 'failed' 和 'retrying' 状态的文件：
        - 'failed': 上次下载失败的文件
        - 'retrying': 正在重试但被中断的文件（上次 --retry-failed 中途中断）

        @return: 字典，键为 course_id，值为统计信息
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute(
            """SELECT
                course_id,
                course_fullname,
                COUNT(*) as failed_count,
                SUM(consecutive_failures) as total_failures,
                MAX(consecutive_failures) as max_consecutive,
                MIN(last_failed_at) as earliest_failure,
                MAX(last_failed_at) as latest_failure
            FROM files
            WHERE download_status IN ('failed', 'retrying')
            GROUP BY course_id
            ORDER BY failed_count DESC
            """
        )

        results = cursor.fetchall()
        conn.close()

        summary = {}
        for row in results:
            course_id = row[0]
            summary[course_id] = {
                'course_fullname': row[1],
                'failed_count': row[2],
                'total_failures': row[3],
                'max_consecutive': row[4],
                'earliest_failure': row[5],
                'latest_failure': row[6]
            }

        return summary

    def reset_failed_file_for_retry(self, file: File, course_id: int):
        """
        重置失败文件状态，准备重试
        使用 'retrying' 状态而非 'pending'，这样如果中断后重新运行，
        这些文件仍然会被包含在重试列表中。
        
        不重置 download_attempts（保留历史），但重置 consecutive_failures

        @param file: 要重试的文件
        @param course_id: 课程 ID
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute(
            """UPDATE files
            SET download_status = 'retrying',
                consecutive_failures = 0,
                last_failed_reason = NULL
            WHERE course_id = ?
            AND module_id = ?
            AND content_fileurl = ?
            """,
            (course_id, file.module_id, file.content_fileurl)
        )

        conn.commit()
        conn.close()
        logging.debug(f'重置失败文件状态用于重试: {file.content_filename}')

    def save_incomplete_download(self, file_id: int, file_url: str, file_path: str, 
                                  total_bytes: int, downloaded_bytes: int, 
                                  server_supports_range: bool = False, 
                                  etag: str = None, last_modified: str = None):
        """
        保存未完成的下载信息，用于断点续传
        
        @param file_id: 文件 ID
        @param file_url: 下载 URL
        @param file_path: 文件保存路径
        @param total_bytes: 文件总字节数
        @param downloaded_bytes: 已下载的字节数
        @param server_supports_range: 服务器是否支持 Range 请求
        @param etag: ETag（用于验证文件完整性）
        @param last_modified: Last-Modified 时间戳
        """
        import time
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        current_time = int(time.time())
        
        try:
            # 检查是否已存在该下载记录
            cursor.execute(
                "SELECT download_id FROM incomplete_downloads WHERE file_id = ? AND file_path = ?",
                (file_id, file_path)
            )
            existing = cursor.fetchone()
            
            if existing:
                # 更新现有记录
                cursor.execute(
                    """UPDATE incomplete_downloads
                    SET file_url = ?,
                        total_bytes = ?,
                        downloaded_bytes = ?,
                        server_supports_range = ?,
                        etag = ?,
                        last_modified = ?,
                        last_update_time = ?,
                        status = 'pending'
                    WHERE download_id = ?
                    """,
                    (file_url, total_bytes, downloaded_bytes, int(server_supports_range),
                     etag, last_modified, current_time, existing[0])
                )
                logging.debug(f'更新未完成下载记录 [file_id={file_id}]: {downloaded_bytes}/{total_bytes} 字节')
            else:
                # 插入新记录
                cursor.execute(
                    """INSERT INTO incomplete_downloads
                    (file_id, file_url, file_path, total_bytes, downloaded_bytes, 
                     server_supports_range, etag, last_modified, start_time, last_update_time, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (file_id, file_url, file_path, total_bytes, downloaded_bytes,
                     int(server_supports_range), etag, last_modified, current_time, current_time)
                )
                logging.debug(f'保存未完成下载记录 [file_id={file_id}]: {downloaded_bytes}/{total_bytes} 字节')
            
            conn.commit()
        finally:
            conn.close()

    def get_incomplete_download(self, file_id: int, file_path: str) -> Optional[Dict[str, Any]]:
        """
        获取未完成的下载信息
        
        @param file_id: 文件 ID
        @param file_path: 文件路径
        @return: 下载信息字典或 None
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                """SELECT download_id, file_url, total_bytes, downloaded_bytes, 
                   server_supports_range, etag, last_modified, attempts, error_reason
                FROM incomplete_downloads
                WHERE file_id = ? AND file_path = ? AND status = 'pending'
                """,
                (file_id, file_path)
            )
            result = cursor.fetchone()
            
            if result:
                return {
                    'download_id': result[0],
                    'file_url': result[1],
                    'total_bytes': result[2],
                    'downloaded_bytes': result[3],
                    'server_supports_range': bool(result[4]),
                    'etag': result[5],
                    'last_modified': result[6],
                    'attempts': result[7],
                    'error_reason': result[8],
                }
            return None
        finally:
            conn.close()

    def mark_download_complete(self, file_id: int, file_path: str):
        """
        标记下载为完成状态（删除不完整下载记录）
        
        @param file_id: 文件 ID
        @param file_path: 文件路径
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "DELETE FROM incomplete_downloads WHERE file_id = ? AND file_path = ?",
                (file_id, file_path)
            )
            conn.commit()
            logging.debug(f'删除完成的下载记录 [file_id={file_id}]')
        finally:
            conn.close()

    def increment_incomplete_download_attempt(self, download_id: int, error_reason: str = None):
        """
        增加未完成下载的尝试次数
        
        @param download_id: 下载 ID
        @param error_reason: 错误原因
        """
        import time
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        current_time = int(time.time())
        
        try:
            cursor.execute(
                """UPDATE incomplete_downloads
                SET attempts = attempts + 1,
                    last_update_time = ?,
                    error_reason = ?
                WHERE download_id = ?
                """,
                (current_time, error_reason[:500] if error_reason else None, download_id)
            )
            conn.commit()
        finally:
            conn.close()

    def get_incomplete_downloads_for_retry(self, max_attempts: int = 5) -> List[Dict[str, Any]]:
        """
        获取可以重试的不完整下载列表（按最后更新时间排序）
        
        @param max_attempts: 最大尝试次数
        @return: 未完成下载列表
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                """SELECT download_id, file_id, file_url, file_path, total_bytes, 
                   downloaded_bytes, server_supports_range, etag, last_modified, attempts
                FROM incomplete_downloads
                WHERE status = 'pending' AND attempts < ?
                ORDER BY last_update_time DESC
                """,
                (max_attempts,)
            )
            
            results = cursor.fetchall()
            downloads = []
            for row in results:
                downloads.append({
                    'download_id': row[0],
                    'file_id': row[1],
                    'file_url': row[2],
                    'file_path': row[3],
                    'total_bytes': row[4],
                    'downloaded_bytes': row[5],
                    'server_supports_range': bool(row[6]),
                    'etag': row[7],
                    'last_modified': row[8],
                    'attempts': row[9],
                })
            
            return downloads
        finally:
            conn.close()

    def cleanup_old_incomplete_downloads(self, days_old: int = 7):
        """
        清理超过指定天数的未完成下载记录
        
        @param days_old: 超过多少天的记录会被删除
        @return: 删除的记录数
        """
        import time
        
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            cutoff_time = int(time.time()) - (days_old * 24 * 60 * 60)
            
            cursor.execute(
                "DELETE FROM incomplete_downloads WHERE last_update_time < ?",
                (cutoff_time,)
            )
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            if deleted_count > 0:
                logging.debug(f'清理了 {deleted_count} 个超过 {days_old} 天的未完成下载记录')
            
            return deleted_count
        finally:
            conn.close()

    # ========================================================================
    # 🆕 查询缓存和性能优化方法
    # ========================================================================
    
    def _get_cache_key(self, method_name: str, *args, **kwargs) -> str:
        """
        生成缓存键
        
        @param method_name: 方法名称
        @param args: 位置参数
        @param kwargs: 关键字参数
        @return: 缓存键
        """
        import hashlib
        key_parts = [method_name] + [str(arg) for arg in args] + [f"{k}={v}" for k, v in kwargs.items()]
        key_str = "|".join(key_parts)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _get_cached(self, cache_key: str, query_func, *args, **kwargs):
        """
        获取缓存数据，如果缓存不存在或过期，则执行查询
        
        @param cache_key: 缓存键
        @param query_func: 查询函数
        @param args: 查询函数的位置参数
        @param kwargs: 查询函数的关键字参数
        @return: 查询结果
        """
        current_time = time.time()
        
        # 检查缓存是否有效
        if cache_key in self._query_cache:
            data, timestamp = self._query_cache[cache_key]
            if current_time - timestamp < self.CACHE_TTL_SECONDS:
                logging.debug(f'📦 使用缓存: {cache_key[:8]}...')
                return data
        
        # 缓存不存在或过期，执行查询
        logging.debug(f'🔍 执行数据库查询: {cache_key[:8]}...')
        data = query_func(*args, **kwargs)
        
        # 保存到缓存
        self._query_cache[cache_key] = (data, current_time)
        return data
    
    def _clear_cache(self, pattern: Optional[str] = None):
        """
        清除缓存
        
        @param pattern: 模式（如果指定，只清除匹配的缓存）
        """
        if pattern is None:
            # 清除所有缓存
            self._query_cache.clear()
            logging.debug('🧹 清除所有缓存')
        else:
            # 清除匹配的缓存
            keys_to_delete = [k for k in self._query_cache.keys() if pattern in k]
            for key in keys_to_delete:
                del self._query_cache[key]
            if keys_to_delete:
                logging.debug(f'🧹 清除 {len(keys_to_delete)} 个匹配缓存')
    
    def _query_files_optimized(self, where_clause: str = "", where_params: tuple = ()) -> List[Course]:
        """
        优化的文件查询方法，使用 GROUP BY 和 JOIN 而不是 N+1 查询
        
        这个方法替代了原始的两步查询（先查询课程，再查询文件）
        使用单个优化的查询获取所有数据。
        
        @param where_clause: WHERE 子句
        @param where_params: WHERE 子句参数
        @return: Course 对象列表
        """
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        stored_courses = []
        
        try:
            # 🔍 优化查询：一次获取所有文件数据
            query = """
                SELECT course_id, course_fullname, *
                FROM files
                WHERE 1=1
            """
            
            if where_clause:
                query += f" AND {where_clause}"
            
            query += " ORDER BY course_id"
            
            cursor.execute(query, where_params)
            file_rows = cursor.fetchall()
            
            if not file_rows:
                conn.close()
                return []
            
            # 🔄 内存中分组，避免多次数据库查询
            current_course_id = None
            current_course = None
            
            for file_row in file_rows:
                course_id = file_row['course_id']
                
                # 检测课程变更
                if course_id != current_course_id:
                    if current_course is not None:
                        stored_courses.append(current_course)
                    
                    current_course = Course(course_id, file_row['course_fullname'])
                    current_course_id = course_id
                
                # 添加文件到课程
                notify_file = File.fromRow(file_row)
                current_course.files.append(notify_file)
            
            # 添加最后一个课程
            if current_course is not None:
                stored_courses.append(current_course)
        
        finally:
            conn.close()
        
        return stored_courses
