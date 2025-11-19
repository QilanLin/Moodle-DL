#!/usr/bin/env python3
"""
认证迁移脚本：将现有 cookies.txt 和 token 数据迁移到数据库

功能：
1. 从 config.json 读取 token 和 privatetoken
2. 从 Cookies.txt 读取现有的 cookies（Netscape格式）
3. 在数据库中创建 token session
4. 在数据库中创建 cookie batch session
5. 完整的迁移验证和报告

使用：
  python moodle_dl/migrate_auth_to_db.py ~/.moodle-dl
"""

import json
import os
import sys
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class AuthMigrator:
    """认证数据迁移器"""

    def __init__(self, moodle_dl_path: str):
        """初始化迁移器"""
        self.moodle_dl_path = Path(moodle_dl_path).expanduser()
        self.config_file = self.moodle_dl_path / 'config.json'
        self.cookies_file = self.moodle_dl_path / 'Cookies.txt'
        self.db_file = self.moodle_dl_path / 'moodle_state.db'

        self.config = {}
        self.existing_cookies = []
        self.migration_log = []

    def log(self, level: str, message: str):
        """记录迁移日志"""
        timestamp = datetime.now().isoformat()
        log_entry = f"[{timestamp}] {level}: {message}"
        self.migration_log.append(log_entry)
        print(log_entry)

    def validate_paths(self) -> bool:
        """验证所需的文件和目录是否存在"""
        self.log("INFO", f"检查路径: {self.moodle_dl_path}")

        if not self.moodle_dl_path.exists():
            self.log("ERROR", f"目录不存在: {self.moodle_dl_path}")
            return False

        if not self.config_file.exists():
            self.log("ERROR", f"config.json 不存在: {self.config_file}")
            return False

        if not self.db_file.exists():
            self.log("ERROR", f"数据库文件不存在: {self.db_file}")
            self.log("INFO", "提示: 请先运行 moodle-dl 初始化数据库")
            return False

        self.log("INFO", f"✓ config.json 存在")
        if self.cookies_file.exists():
            self.log("INFO", f"✓ Cookies.txt 存在")
        else:
            self.log("WARNING", f"⚠️ Cookies.txt 不存在（可选）")

        return True

    def load_config(self) -> bool:
        """从 config.json 加载配置"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            self.log("INFO", f"✓ 已加载 config.json")
            return True
        except Exception as e:
            self.log("ERROR", f"加载 config.json 失败: {e}")
            return False

    def load_cookies_from_file(self) -> bool:
        """从 Cookies.txt 加载 cookies（Netscape格式）"""
        if not self.cookies_file.exists():
            self.log("INFO", "Cookies.txt 不存在，跳过")
            return True

        try:
            with open(self.cookies_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 跳过注释和空行
                    if not line or line.startswith('#'):
                        continue

                    # 解析 Netscape 格式
                    # domain flag path secure expiration name value
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        # 处理 TRUE/FALSE 字符串或数字格式
                        def parse_bool(s: str) -> int:
                            if s.upper() == 'TRUE':
                                return 1
                            elif s.upper() == 'FALSE':
                                return 0
                            else:
                                return int(s)

                        try:
                            expires = int(parts[4]) if parts[4] and parts[4] != '0' else None
                        except ValueError:
                            expires = None

                        cookie = {
                            'domain': parts[0],
                            'path': parts[2],
                            'secure': parse_bool(parts[3]),
                            'expires': expires,
                            'name': parts[5],
                            'value': parts[6],
                            'httponly': 1,  # Netscape 格式不包含，默认为 1
                            'samesite': 'Lax'  # 默认值
                        }
                        self.existing_cookies.append(cookie)

            self.log("INFO", f"✓ 从 Cookies.txt 加载了 {len(self.existing_cookies)} 个 cookies")
            return True

        except Exception as e:
            self.log("ERROR", f"加载 Cookies.txt 失败: {e}")
            return False

    def verify_token_exists(self) -> bool:
        """验证 token 是否存在于 config.json"""
        token = self.config.get('token')
        if not token:
            self.log("ERROR", "config.json 中未找到 token")
            return False

        self.log("INFO", f"✓ 找到 token（长度: {len(token)} 字符）")
        return True

    def create_token_session(self, conn: sqlite3.Connection) -> Optional[str]:
        """在数据库中创建 token session"""
        try:
            token = self.config.get('token')
            privatetoken = self.config.get('privatetoken')

            if not token:
                self.log("ERROR", "Token 为空，无法创建 session")
                return None

            # 生成 session_id
            session_id = str(uuid.uuid4())

            now = int(datetime.now(timezone.utc).timestamp())

            c = conn.cursor()

            # 插入 token session
            c.execute('''
                INSERT INTO auth_sessions (
                    session_id, session_type, token_value, private_token_value,
                    status, created_at, last_accessed_at, source, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                'token',
                token,
                privatetoken,
                'valid',
                now,
                now,
                'migration',
                json.dumps({'migrated_from': 'config.json'})
            ))

            conn.commit()
            self.log("INFO", f"✓ 创建 token session: {session_id}")
            return session_id

        except Exception as e:
            self.log("ERROR", f"创建 token session 失败: {e}")
            conn.rollback()
            return None

    def create_cookie_session(self, conn: sqlite3.Connection) -> Optional[str]:
        """在数据库中创建 cookie batch session"""
        if not self.existing_cookies:
            self.log("INFO", "无 cookies 数据，跳过创建 cookie session")
            return None

        try:
            # 生成 session_id
            session_id = str(uuid.uuid4())

            now = int(datetime.now(timezone.utc).timestamp())

            c = conn.cursor()

            # 插入 cookie session
            c.execute('''
                INSERT INTO auth_sessions (
                    session_id, session_type, status, created_at,
                    last_accessed_at, source, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                'cookie_batch',
                'valid',
                now,
                now,
                'migration',
                json.dumps({'migrated_from': 'Cookies.txt', 'count': len(self.existing_cookies)})
            ))

            # 插入 cookies
            for i, cookie in enumerate(self.existing_cookies):
                cookie_id = f"{session_id}:cookie:{i}"
                c.execute('''
                    INSERT INTO cookie_store (
                        cookie_id, session_id, name, value, domain, path,
                        expires, secure, httponly, samesite, created_at, valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    cookie_id,
                    session_id,
                    cookie['name'],
                    cookie['value'],
                    cookie['domain'],
                    cookie['path'],
                    cookie['expires'],
                    cookie['secure'],
                    cookie['httponly'],
                    cookie['samesite'],
                    now,
                    1  # valid
                ))

            conn.commit()
            self.log("INFO", f"✓ 创建 cookie session: {session_id} (包含 {len(self.existing_cookies)} 个 cookies)")
            return session_id

        except Exception as e:
            self.log("ERROR", f"创建 cookie session 失败: {e}")
            conn.rollback()
            return None

    def log_migration_action(self, conn: sqlite3.Connection, session_id: str, action: str, status: str):
        """记录迁移操作到审计日志"""
        try:
            now = int(datetime.now(timezone.utc).timestamp())
            c = conn.cursor()

            c.execute('''
                INSERT INTO auth_audit_log (
                    session_id, action, status, triggered_by, timestamp, details
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                action,
                status,
                'migration_script',
                now,
                json.dumps({'reason': 'automatic migration'})
            ))

            conn.commit()

        except Exception as e:
            self.log("WARNING", f"记录审计日志失败: {e}")

    def _verify_database_tables(self, conn: sqlite3.Connection) -> bool:
        """
        防御性检查：验证必需的数据库表是否存在
        
        这确保数据库已由 StateRecorder 正确初始化
        """
        try:
            c = conn.cursor()
            
            required_tables = ['auth_sessions', 'cookie_store', 'auth_audit_log']
            
            for table_name in required_tables:
                c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                )
                if c.fetchone() is None:
                    self.log("ERROR", f"缺少必需的表: {table_name}")
                    return False
            
            self.log("INFO", "✓ 所有必需的数据库表都存在")
            return True
            
        except Exception as e:
            self.log("ERROR", f"验证数据库表失败: {e}")
            return False

    def verify_migration(self, conn: sqlite3.Connection) -> bool:
        """验证迁移是否成功"""
        try:
            c = conn.cursor()

            # 检查 token session
            c.execute('SELECT COUNT(*) FROM auth_sessions WHERE session_type = ?', ('token',))
            token_sessions = c.fetchone()[0]

            c.execute('SELECT COUNT(*) FROM auth_sessions WHERE session_type = ?', ('cookie_batch',))
            cookie_sessions = c.fetchone()[0]

            c.execute('SELECT COUNT(*) FROM cookie_store')
            total_cookies = c.fetchone()[0]

            self.log("INFO", "")
            self.log("INFO", "=== 迁移结果 ===")
            self.log("INFO", f"Token sessions: {token_sessions}")
            self.log("INFO", f"Cookie batch sessions: {cookie_sessions}")
            self.log("INFO", f"总 cookies: {total_cookies}")

            return token_sessions > 0 and (cookie_sessions > 0 or len(self.existing_cookies) == 0)

        except Exception as e:
            self.log("ERROR", f"验证迁移失败: {e}")
            return False

    def run(self) -> bool:
        """执行完整的迁移流程"""
        self.log("INFO", "========================================")
        self.log("INFO", "开始认证数据迁移")
        self.log("INFO", "========================================")

        # 1. 验证路径
        if not self.validate_paths():
            return False

        # 2. 加载配置
        if not self.load_config():
            return False

        # 3. 验证 token 存在
        if not self.verify_token_exists():
            return False

        # 4. 加载 cookies
        if not self.load_cookies_from_file():
            return False

        # 5. 连接数据库并执行迁移
        try:
            conn = sqlite3.connect(str(self.db_file))
            conn.row_factory = sqlite3.Row
            
            # 防御性检查：验证必需的表存在
            if not self._verify_database_tables(conn):
                self.log("ERROR", "❌ 数据库缺少必需的表。请先运行 moodle-dl 以初始化数据库。")
                conn.close()
                return False

            # 创建 token session
            token_session_id = self.create_token_session(conn)
            if token_session_id:
                self.log_migration_action(conn, token_session_id, 'create', 'success')

            # 创建 cookie session
            cookie_session_id = self.create_cookie_session(conn)
            if cookie_session_id:
                self.log_migration_action(conn, cookie_session_id, 'create', 'success')

            # 验证迁移
            success = self.verify_migration(conn)

            conn.close()

            if success:
                self.log("INFO", "✅ 迁移成功完成！")
                self.log("INFO", "")
                self.log("INFO", "后续步骤：")
                self.log("INFO", "1. 验证应用正常启动: moodle-dl --verbose")
                self.log("INFO", "2. 如需，可备份 Cookies.txt 后删除")
                self.log("INFO", "3. token 数据同时保存在 config.json 和数据库中")
                self.log("INFO", "")
                return True
            else:
                self.log("ERROR", "❌ 迁移验证失败")
                return False

        except Exception as e:
            self.log("ERROR", f"数据库操作失败: {e}")
            return False

    def save_migration_log(self):
        """保存迁移日志到文件"""
        log_file = self.moodle_dl_path / 'migration.log'
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.migration_log))
            print(f"\n📝 迁移日志已保存到: {log_file}")
        except Exception as e:
            print(f"⚠️ 保存迁移日志失败: {e}")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("使用方法: python moodle_dl/migrate_auth_to_db.py <moodle_dl_path>")
        print("示例: python moodle_dl/migrate_auth_to_db.py ~/.moodle-dl")
        sys.exit(1)

    moodle_dl_path = sys.argv[1]
    migrator = AuthMigrator(moodle_dl_path)

    success = migrator.run()
    migrator.save_migration_log()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
