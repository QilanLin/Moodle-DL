import logging
import sqlite3
from sqlite3 import Error
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.types import Course, File, MoodleDlOpts
from moodle_dl.utils import PathTools as PT


class StateRecorder:
    """
    Saves the state and provides utilities to detect changes in the current
    state against the previous.
    """

    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        """
        Initiates the database.
        If no database exists yet, a new one is created.
        @param opts: Moodle-dl options
        """
        self.opts = opts
        self.db_file = PT.make_path(config.get_misc_files_path(), 'moodle_state.db')

        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()

            # 检查数据库版本
            current_version = c.execute('pragma user_version').fetchone()[0]
            
            # 检查 files 表是否存在
            table_exists = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
            ).fetchone() is not None

            # 优化：对于全新数据库，直接创建最新版本的 schema（v8）
            # 这避免了 8 次增量升级，大幅提高性能
            if current_version == 0 and not table_exists:
                logging.info('🆕 创建全新数据库（直接使用 v8 schema，跳过所有升级过程）')
                self._create_fresh_database_v8(c)
                current_version = 8
                conn.commit()
                logging.info('✅ 全新数据库已准备就绪（版本 v8）')
            elif not table_exists and current_version > 0:
                # 对于已存在版本号但缺少表的异常情况（不应该发生），创建 v0 基础表开始升级
                logging.warning('⚠️  数据库异常：版本号存在但 files 表缺失，创建 v0 基础表')
                # 创建基础 files 表（v0 schema - 仅用于向后兼容）
                sql_create_index_table = """ CREATE TABLE IF NOT EXISTS files (
                course_id integer NOT NULL,
                course_fullname integer NOT NULL,
                module_id integer NOT NULL,
                section_name text NOT NULL,
                module_name text NOT NULL,
                content_filepath text NOT NULL,
                content_filename text NOT NULL,
                content_fileurl text NOT NULL,
                content_filesize integer NOT NULL,
                content_timemodified integer NOT NULL,
                module_modname text NOT NULL,
                content_type text NOT NULL,
                content_isexternalfile text NOT NULL,
                saved_to text NOT NULL,
                time_stamp integer NOT NULL,
                modified integer DEFAULT 0 NOT NULL,
                deleted integer DEFAULT 0 NOT NULL,
                notified integer DEFAULT 0 NOT NULL
                );
                """

                # Create two indices for a faster search.
                sql_create_index = """
                CREATE INDEX IF NOT EXISTS idx_module_id
                ON files (module_id);
                """

                sql_create_index2 = """
                CREATE INDEX IF NOT EXISTS idx_course_id
                ON files (course_id);
                """

                c.execute(sql_create_index_table)
                c.execute(sql_create_index)
                c.execute(sql_create_index2)
                conn.commit()

            # 执行增量升级（从当前版本升级到最新版本）
            if current_version == 0:
                # Add Hash Column
                sql_create_hash_column = """ALTER TABLE files
                ADD COLUMN hash text NULL;
                """
                c.execute(sql_create_hash_column)
                c.execute("PRAGMA user_version = 1;")
                current_version = 1
                conn.commit()

            if current_version == 1:
                # Add moved Column
                sql_create_moved_column = """ALTER TABLE files
                ADD COLUMN moved integer DEFAULT 0 NOT NULL;
                """
                c.execute(sql_create_moved_column)

                c.execute('PRAGMA user_version = 2;')
                current_version = 2
                conn.commit()

            if current_version == 2:
                # Modified gets a new meaning
                sql_remove_modified_entries = """UPDATE files
                    SET modified = 0
                    WHERE modified = 1;
                """
                c.execute(sql_remove_modified_entries)

                c.execute('PRAGMA user_version = 3;')
                current_version = 3

                conn.commit()

            if current_version == 3:
                # Add file_id Column
                sql_create_new_files_table_1 = """
                ALTER TABLE files
                RENAME TO old_files;
                """

                sql_create_new_files_table_2 = """
                CREATE TABLE IF NOT EXISTS files (
                file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id integer NOT NULL,
                course_fullname integer NOT NULL,
                module_id integer NOT NULL,
                section_name text NOT NULL,
                module_name text NOT NULL,
                content_filepath text NOT NULL,
                content_filename text NOT NULL,
                content_fileurl text NOT NULL,
                content_filesize integer NOT NULL,
                content_timemodified integer NOT NULL,
                module_modname text NOT NULL,
                content_type text NOT NULL,
                content_isexternalfile text NOT NULL,
                saved_to text NOT NULL,
                hash text NULL,
                time_stamp integer NOT NULL,
                old_file_id integer NULL,
                modified integer DEFAULT 0 NOT NULL,
                moved integer DEFAULT 0 NOT NULL,
                deleted integer DEFAULT 0 NOT NULL,
                notified integer DEFAULT 0 NOT NULL
                );"""

                sql_create_new_files_table_3 = """
                INSERT INTO files
                (course_id, course_fullname, module_id, section_name,
                 module_name, content_filepath, content_filename,
                 content_fileurl, content_filesize, content_timemodified,
                 module_modname, content_type, content_isexternalfile,
                 saved_to, time_stamp, modified, deleted, notified, hash,
                 moved)
                SELECT * FROM old_files
                """

                sql_create_new_files_table_4 = """
                DROP TABLE old_files;
                """
                c.execute(sql_create_new_files_table_1)
                c.execute(sql_create_new_files_table_2)
                c.execute(sql_create_new_files_table_3)
                c.execute(sql_create_new_files_table_4)

                c.execute('PRAGMA user_version = 4;')
                current_version = 4

                conn.commit()

            if current_version == 4:
                # Add section_id Column
                sql_create_section_id_column = """ALTER TABLE files
                ADD COLUMN section_id integer DEFAULT 0 NOT NULL;
                """
                c.execute(sql_create_section_id_column)

                c.execute('PRAGMA user_version = 5;')
                current_version = 5
                conn.commit()

            if current_version == 5:
                # v6: Add authentication sessions management tables
                # auth_sessions - 管理 tokens 和 cookies 的会话表
                sql_create_auth_sessions = """
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
                """

                # cookie_store - 详细的 cookies 存储表
                sql_create_cookie_store = """
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
                """

                # auth_audit_log - 认证操作审计日志表
                sql_create_audit_log = """
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
                """

                # 创建索引
                sql_create_auth_indexes = [
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

                c.execute(sql_create_auth_sessions)
                c.execute(sql_create_cookie_store)
                c.execute(sql_create_audit_log)

                for idx_sql in sql_create_auth_indexes:
                    c.execute(idx_sql)

                c.execute('PRAGMA user_version = 6;')
                current_version = 6
                conn.commit()
                logging.info('✓ Database upgraded to v6: Authentication tables created')

            if current_version == 6:
                # v7: Add download failure tracking fields to files table
                # 防御性编程：检查列是否已存在（处理数据库状态不一致的情况）
                columns = [col[1] for col in c.execute("PRAGMA table_info(files)").fetchall()]
                
                sql_add_download_tracking = [
                    ("download_status", """ALTER TABLE files ADD COLUMN download_status TEXT DEFAULT 'pending';"""),
                    ("download_attempts", """ALTER TABLE files ADD COLUMN download_attempts INTEGER DEFAULT 0;"""),
                    ("last_download_at", """ALTER TABLE files ADD COLUMN last_download_at INTEGER DEFAULT 0;"""),
                    ("last_failed_at", """ALTER TABLE files ADD COLUMN last_failed_at INTEGER DEFAULT 0;"""),
                    ("last_failed_reason", """ALTER TABLE files ADD COLUMN last_failed_reason TEXT;"""),
                    ("consecutive_failures", """ALTER TABLE files ADD COLUMN consecutive_failures INTEGER DEFAULT 0;"""),
                ]

                for col_name, sql in sql_add_download_tracking:
                    if col_name not in columns:
                        try:
                            c.execute(sql)
                        except Exception as e:
                            # 忽略"列已存在"的错误
                            if 'already exists' not in str(e).lower() and 'duplicate column' not in str(e).lower():
                                raise
                    else:
                        logging.debug(f'✓ Column {col_name} already exists, skipping')

                # 防御性编程：确保 moved 字段存在（修复升级路径不完整的问题）
                # 检查 moved 字段是否存在
                columns = [col[1] for col in c.execute("PRAGMA table_info(files)").fetchall()]
                if 'moved' not in columns:
                    # moved 字段缺失，补充添加（应该在 v1→v2 升级时添加）
                    logging.warning('⚠️  Database inconsistency detected: missing "moved" column, adding it now')
                    c.execute("""ALTER TABLE files ADD COLUMN moved INTEGER DEFAULT 0 NOT NULL;""")

                # 为现有记录设置默认状态：已下载的文件标记为 success
                c.execute("""
                    UPDATE files
                    SET download_status = 'success'
                    WHERE deleted = 0 AND modified = 0 AND moved = 0;
                """)

                # 创建索引加速失败文件查询
                c.execute("""
                    CREATE INDEX IF NOT EXISTS idx_download_status
                    ON files(download_status);
                """)
                c.execute("""
                    CREATE INDEX IF NOT EXISTS idx_consecutive_failures
                    ON files(consecutive_failures);
                """)

                c.execute('PRAGMA user_version = 7;')
                current_version = 7
                conn.commit()
                logging.info('✓ Database upgraded to v7: Download failure tracking added')

            if current_version == 7:
                # v8: Add position_in_section field for filename prefix indexing
                # 防御性编程：检查列是否已存在
                columns = [col[1] for col in c.execute("PRAGMA table_info(files)").fetchall()]
                
                if 'position_in_section' not in columns:
                    try:
                        c.execute("""ALTER TABLE files ADD COLUMN position_in_section INTEGER;""")
                    except Exception as e:
                        if 'already exists' not in str(e).lower() and 'duplicate column' not in str(e).lower():
                            raise
                        logging.warning('⚠️  Column position_in_section already exists, skipping')
                else:
                    logging.debug('✓ Column position_in_section already exists, skipping')

                # 为文件位置字段创建索引，加速按位置排序的查询
                c.execute("""
                    CREATE INDEX IF NOT EXISTS idx_position_in_section
                    ON files(course_id, section_id, position_in_section);
                """)

                c.execute('PRAGMA user_version = 8;')
                current_version = 8
                conn.commit()
                logging.info('✓ Database upgraded to v8: Position tracking added for filename indexing')

            conn.commit()
            logging.debug('Database Version: %s', str(current_version))

            conn.close()

        except Error as error:
            raise RuntimeError(f'Could not create database! Error: {error}')

    @staticmethod
    def _create_fresh_database_v8(cursor):
        """
        为全新数据库直接创建 v8 schema（最新版本）
        
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
                position_in_section INTEGER
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
        ]
        
        for idx_sql in files_indexes:
            cursor.execute(idx_sql)
        
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
            logging.debug(f'[files_are_different] cookie_mod comparison:')
            logging.debug(f'  file1: url={file1.content_fileurl[:80]}..., time={file1.content_timemodified}, size={file1.content_filesize}')
            logging.debug(f'  file2: url={file2.content_fileurl[:80]}..., time={file2.content_timemodified}, size={file2.content_filesize}')
            logging.debug(f'  url_diff={url_diff}, time_diff={time_diff}, size_diff={size_diff}')

        # Not sure if this would be a good idea
        #  or file1.module_name != file2.module_name)
        if file1.content_filesize != file2.content_filesize or (
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

    def get_stored_files(self) -> List[Course]:
        # get all stored files (that are not yet deleted)
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        stored_courses = []

        cursor.execute(
            """SELECT course_id, course_fullname
            FROM files WHERE deleted = 0 AND modified = 0 AND moved = 0
            GROUP BY course_id;"""
        )

        curse_rows = cursor.fetchall()

        for course_row in curse_rows:
            course = Course(course_row['course_id'], course_row['course_fullname'])

            cursor.execute(
                """SELECT *
                FROM files
                WHERE deleted = 0
                AND modified = 0
                AND moved = 0
                AND course_id = ?;""",
                (course.id,),
            )

            file_rows = cursor.fetchall()

            course.files = []

            for file_row in file_rows:
                notify_file = File.fromRow(file_row)
                course.files.append(notify_file)

            stored_courses.append(course)

        conn.close()
        return stored_courses

    def get_old_files(self) -> List[Course]:
        # get all stored files (that are not yet deleted)
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        stored_courses = []

        cursor.execute(
            """SELECT DISTINCT course_id, course_fullname
            FROM files WHERE old_file_id IS NOT NULL"""
        )

        course_rows = cursor.fetchall()
        for course_row in course_rows:
            course = Course(course_row['course_id'], course_row['course_fullname'])

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
        if file.deleted:
            self.delete_file(file, course_id, course_fullname)
        elif file.modified:
            self.modifie_file(file, course_id, course_fullname)
        elif file.moved:
            self.move_file(file, course_id, course_fullname)
        else:
            self.new_file(file, course_id, course_fullname)

    def new_file(self, file: File, course_id: int, course_fullname: str):
        # saves a file to index

        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        data = {'course_id': course_id, 'course_fullname': course_fullname}
        data.update(file.getMap())

        data.update({'modified': 0, 'deleted': 0, 'moved': 0, 'notified': 0})

        # 为下载追踪字段提供默认值（v7 引入）
        data.setdefault('download_status', 'pending')
        data.setdefault('download_attempts', 0)
        data.setdefault('last_download_at', 0)
        data.setdefault('last_failed_at', 0)
        data.setdefault('last_failed_reason', None)
        data.setdefault('consecutive_failures', 0)

        cursor.execute(File.INSERT, data)

        conn.commit()
        conn.close()

    def batch_delete_files(self, courses: List[Course]):
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

    def modifie_file(self, file: File, course_id: int, course_fullname: str):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        data_new = {'course_id': course_id, 'course_fullname': course_fullname}
        data_new.update(file.getMap())

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
            data = {
                'course_id': course_id,
                'course_fullname': course_fullname,
                'download_status': 'failed',
                'download_attempts': 1,
                'last_download_at': current_time,
                'last_failed_at': current_time,
                'last_failed_reason': error_message[:500] if error_message else None,
                'consecutive_failures': 1,
            }
            data.update(file.getMap())
            data.update({'modified': 0, 'deleted': 0, 'moved': 0, 'notified': 0})

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

        @param course_id: 可选，只查询特定课程的失败文件
        @param min_failures: 最小连续失败次数，默认1（所有失败文件）
        @return: 失败的文件列表
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        if course_id:
            cursor.execute(
                """SELECT * FROM files
                WHERE course_id = ?
                AND download_status = 'failed'
                AND consecutive_failures >= ?
                ORDER BY consecutive_failures DESC, last_failed_at DESC
                """,
                (course_id, min_failures)
            )
        else:
            cursor.execute(
                """SELECT * FROM files
                WHERE download_status = 'failed'
                AND consecutive_failures >= ?
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

        @param min_failures: 最小连续失败次数，默认1（所有失败文件）
        @return: 字典，键为 course_id，值为包含 course_fullname 和 files 列表的字典
        """
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """SELECT * FROM files
            WHERE download_status = 'failed'
            AND consecutive_failures >= ?
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
            WHERE download_status = 'failed'
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
        不重置 download_attempts（保留历史），但重置 consecutive_failures

        @param file: 要重试的文件
        @param course_id: 课程 ID
        """
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        cursor.execute(
            """UPDATE files
            SET download_status = 'pending',
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
