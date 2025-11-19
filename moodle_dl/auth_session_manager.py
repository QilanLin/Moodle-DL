"""
认证会话管理器 - Authentication Session Manager

融合官方 Moodle 设计最佳实践的认证数据库管理系统：
- 双层密钥机制（token + privatetoken）- 官方标准
- 完整的时间戳记录（created_at, last_accessed_at, expires_at）
- 失败原因详细追踪
- 版本链管理
- 不可篡改审计日志
"""

import json
import logging
import sqlite3
import time
import uuid
from typing import List, Optional, Dict
from pathlib import Path


def normalize_playwright_cookie(cookie: dict) -> dict:
    """
    标准化 Playwright cookie 格式（防御性编程）
    
    处理不同来源的 cookies 字段名差异：
    - 数据库使用小写：httponly, samesite
    - Playwright 使用驼峰：httpOnly, sameSite
    - 确保 secure 和 httpOnly 是布尔值
    - 确保 expires 是有效值（-1 或正整数秒级时间戳）
    
    Args:
        cookie: 原始 cookie 字典
        
    Returns:
        标准化后的 cookie 字典（Playwright 格式）
    """
    normalized = cookie.copy()
    
    # 移除数据库专用字段
    normalized.pop('cookie_id', None)
    
    # 处理 expires 字段（Playwright 严格要求：-1 或正整数秒级时间戳）
    expires_value = normalized.get('expires')
    if expires_value is None or expires_value == '':
        # None 或空字符串 → session cookie
        normalized['expires'] = -1
    elif isinstance(expires_value, (int, float)):
        if expires_value <= 0 and expires_value != -1:
            # 负数（除了-1）→ session cookie
            normalized['expires'] = -1
        elif expires_value > 10000000000:
            # 毫秒级时间戳 → 转换为秒级
            normalized['expires'] = int(expires_value / 1000)
        else:
            # 正常的秒级时间戳
            normalized['expires'] = int(expires_value)
    else:
        # 其他类型 → session cookie
        normalized['expires'] = -1
    
    # 统一 secure 字段为布尔值
    if 'secure' in normalized:
        normalized['secure'] = bool(normalized['secure'])
    
    # 统一 httpOnly 字段（支持 httponly 和 httpOnly）
    if 'httponly' in normalized or 'httpOnly' in normalized:
        http_only_value = normalized.pop('httponly', normalized.get('httpOnly', False))
        normalized['httpOnly'] = bool(http_only_value)
    
    # 统一 sameSite 字段（支持 samesite 和 sameSite）
    if 'samesite' in normalized or 'sameSite' in normalized:
        same_site_value = normalized.pop('samesite', normalized.get('sameSite', 'Lax'))
        normalized['sameSite'] = same_site_value or 'Lax'
    
    # 确保必需字段存在且类型正确
    normalized.setdefault('path', '/')
    normalized.setdefault('secure', False)
    normalized.setdefault('httpOnly', False)
    normalized.setdefault('sameSite', 'Lax')
    
    return normalized


class AuthSessionManager:
    """
    管理认证会话（tokens 和 cookies）的生命周期

    设计原则：
    1. 统一存储 - 所有认证数据存储在数据库中（无文本文件）
    2. 版本链 - 每个新版本链接到前一个版本，形成完整的历史链
    3. 审计追踪 - 每个操作都被记录，包含失败原因和操作者信息
    4. 官方标准 - 与 Moodle 官方设计一致（双层密钥、IP限制等）
    """

    # Token 状态常量
    STATUS_VALID = 'valid'
    STATUS_EXPIRED = 'expired'
    STATUS_REVOKED = 'revoked'
    STATUS_REPLACED = 'replaced'

    # 会话类型常量
    TYPE_TOKEN = 'token'
    TYPE_COOKIE_BATCH = 'cookie_batch'
    TYPE_SSO_KEY = 'sso_key'

    # 来源常量
    SOURCE_API_LOGIN = 'api_login'
    SOURCE_BROWSER_EXPORT = 'browser_export'
    SOURCE_AUTOLOGIN = 'autologin'
    SOURCE_SSO = 'sso'

    # 操作常量
    ACTION_CREATE = 'create'
    ACTION_VERIFY = 'verify'
    ACTION_REFRESH = 'refresh'
    ACTION_REVOKE = 'revoke'
    ACTION_DELETE = 'delete'

    # 官方 Moodle 性能优化：最少间隔60秒更新一次 last_accessed_at
    LASTACCESS_UPDATE_INTERVAL = 60

    def __init__(self, db_file: str):
        """
        初始化管理器

        Args:
            db_file: SQLite 数据库文件路径
        """
        self.db_file = db_file
        self.logger = logging.getLogger(__name__)
        
        # 注意：数据库表初始化已移至 StateRecorder 中统一管理
        # 这避免了重复的表创建逻辑
        # 详见 moodle_dl/database.py 中的 v6 升级流程

    def create_session(
        self,
        session_type: str,
        source: str,
        token: str = None,
        private_token: str = None,
        cookies: List[dict] = None,
        creator_id: str = None,
        owner_id: str = None,
        expires_in_seconds: int = None,
        ip_restriction: str = None,
        ip_address: str = None,
        context_id: str = None,
        metadata: dict = None
    ) -> str:
        """
        创建新的认证会话

        Args:
            session_type: 会话类型（token/cookie_batch/sso_key）
            source: 来源（api_login/browser_export/autologin/sso）
            token: 公开 token（128字符）
            private_token: 私有 token（64字符，可选）
            cookies: cookies 列表
            creator_id: 创建者ID（谁创建了这个会话）
            owner_id: 所有者ID（Moodle 用户ID）
            expires_in_seconds: 多少秒后过期
            ip_restriction: IP 限制规则（CIDR 格式）
            ip_address: 当前 IP 地址
            context_id: Moodle context ID
            metadata: 额外的元数据

        Returns:
            session_id: 新创建的会话ID
        """
        session_id = self._generate_session_id()
        now = int(time.time())
        expires_at = now + expires_in_seconds if expires_in_seconds else None

        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        try:
            # 1. 插入会话记录
            c.execute("""
                INSERT INTO auth_sessions (
                    session_id, session_type, source,
                    token_value, private_token_value,
                    status, created_at, last_accessed_at, expires_at,
                    creator_id, owner_id, ip_restriction, ip_address,
                    context_id, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, session_type, source,
                token, private_token,
                self.STATUS_VALID, now, now, expires_at,
                creator_id, owner_id, ip_restriction, ip_address,
                context_id,
                json.dumps(metadata) if metadata else None
            ))

            # 2. 添加 cookies（如果有）
            if cookies:
                for cookie in cookies:
                    self._add_cookie_to_session(c, session_id, cookie)

            # 3. 记录审计日志
            self._log_action(
                c,
                action=self.ACTION_CREATE,
                session_id=session_id,
                status='success',
                triggered_by='user_action',
                ip_address=ip_address,
                user_id=creator_id,
                details={
                    'session_type': session_type,
                    'source': source,
                    'cookies_count': len(cookies) if cookies else 0
                }
            )

            conn.commit()
            self.logger.info(f'✓ Created session {session_id} (type={session_type}, source={source})')
            return session_id

        except Exception as e:
            conn.rollback()
            self._log_action(
                c,
                action=self.ACTION_CREATE,
                session_id=None,
                status='failed',
                reason=str(e),
                triggered_by='system',
                ip_address=ip_address
            )
            self.logger.error(f'✗ Failed to create session: {e}')
            raise
        finally:
            conn.close()

    def get_valid_session(self, session_type: str = None) -> Optional[Dict]:
        """
        获取最新的有效会话

        官方验证流程：检查过期、IP限制，更新 last_accessed_at

        Args:
            session_type: 可选的会话类型过滤

        Returns:
            会话信息字典，或 None 如果不存在
        """
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        now = int(time.time())
        query = """
            SELECT * FROM auth_sessions
            WHERE status = ?
            AND (expires_at IS NULL OR expires_at > ?)
        """
        params = [self.STATUS_VALID, now]

        if session_type:
            query += " AND session_type = ?"
            params.append(session_type)

        query += " ORDER BY created_at DESC LIMIT 1"

        c.execute(query, params)
        result = c.fetchone()
        conn.close()

        if not result:
            return None

        # 转换为字典
        session = {
            'session_id': result[0],
            'session_type': result[1],
            'owner_id': result[2],
            'creator_id': result[3],
            'token_value': result[4],
            'private_token_value': result[5],
            'status': result[6],
            'created_at': result[7],
            'last_accessed_at': result[8],
            'expires_at': result[9],
            'source': result[10],
            'ip_restriction': result[11],
            'ip_address': result[12],
            'previous_session_id': result[13],
            'replaced_by_session_id': result[14],
            'context_id': result[15],
            'metadata': result[16]
        }

        # 更新 last_accessed_at（官方 60 秒优化）
        self._update_last_accessed(session['session_id'])

        return session

    def verify_session(self, session_id: str) -> bool:
        """
        验证会话有效性

        检查：
        1. 会话是否存在
        2. 是否已过期
        3. IP 限制是否满足
        4. 更新 last_accessed_at（官方优化）

        Args:
            session_id: 要验证的会话ID

        Returns:
            True 如果有效，False 如果无效
        """
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        # 1. 检查是否存在
        c.execute("SELECT * FROM auth_sessions WHERE session_id = ?", (session_id,))
        result = c.fetchone()

        if not result:
            self._log_action(
                c, self.ACTION_VERIFY, session_id, 'failed',
                'Session not found', 'system'
            )
            conn.commit()
            conn.close()
            return False

        session = {
            'session_id': result[0],
            'expires_at': result[9],
            'ip_restriction': result[11],
            'status': result[6]
        }

        # 2. 检查是否已过期
        if session['expires_at'] and session['expires_at'] < time.time():
            c.execute(
                "UPDATE auth_sessions SET status = ? WHERE session_id = ?",
                (self.STATUS_EXPIRED, session_id)
            )
            self._log_action(
                c, self.ACTION_VERIFY, session_id, 'failed',
                'Session expired', 'system'
            )
            conn.commit()
            conn.close()
            return False

        # 3. 检查 IP 限制
        if session['ip_restriction']:
            if not self._verify_ip_restriction(session['ip_restriction']):
                self._log_action(
                    c, self.ACTION_VERIFY, session_id, 'failed',
                    'IP not allowed', 'system'
                )
                conn.commit()
                conn.close()
                return False

        # 4. 更新 last_accessed_at（官方优化：60秒最多一次）
        self._update_last_accessed(session_id)

        self._log_action(
            c, self.ACTION_VERIFY, session_id, 'success',
            triggered_by='system'
        )
        conn.commit()
        conn.close()
        return True

    def refresh_session(
        self,
        old_session_id: str,
        new_token: str = None,
        new_private_token: str = None,
        new_cookies: List[dict] = None,
        creator_id: str = None
    ) -> str:
        """
        刷新认证会话

        创建新版本，标记旧版本为已替换，形成版本链

        Args:
            old_session_id: 旧会话ID
            new_token: 新的 token
            new_private_token: 新的 private token
            new_cookies: 新的 cookies
            creator_id: 刷新操作的执行者

        Returns:
            新会话的 session_id
        """
        new_session_id = self._generate_session_id()
        now = int(time.time())

        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        try:
            # 1. 获取旧会话信息
            c.execute(
                "SELECT * FROM auth_sessions WHERE session_id = ?",
                (old_session_id,)
            )
            old_result = c.fetchone()

            if not old_result:
                raise ValueError(f"Session {old_session_id} not found")

            # 2. 创建新会话（继承旧会话的属性）
            c.execute("""
                INSERT INTO auth_sessions (
                    session_id, session_type, previous_session_id,
                    token_value, private_token_value,
                    source, owner_id, creator_id,
                    status, created_at, last_accessed_at,
                    ip_restriction, context_id, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_session_id,
                old_result[1],  # session_type
                old_session_id,  # previous_session_id
                new_token or old_result[4],  # token_value
                new_private_token or old_result[5],  # private_token_value
                old_result[10],  # source
                old_result[2],  # owner_id
                creator_id or old_result[3],  # creator_id
                self.STATUS_VALID,
                now, now,
                old_result[11],  # ip_restriction
                old_result[15],  # context_id
                old_result[16]  # metadata
            ))

            # 3. 添加新 cookies
            if new_cookies:
                for cookie in new_cookies:
                    self._add_cookie_to_session(c, new_session_id, cookie)

            # 4. 标记旧会话为已替换
            c.execute(
                """UPDATE auth_sessions
                   SET status = ?, replaced_by_session_id = ?
                   WHERE session_id = ?""",
                (self.STATUS_REPLACED, new_session_id, old_session_id)
            )

            # 5. 记录审计日志
            self._log_action(
                c,
                action=self.ACTION_REFRESH,
                session_id=new_session_id,
                status='success',
                triggered_by='auto_refresh',
                user_id=creator_id,
                details={
                    'previous_session': old_session_id,
                    'new_cookies_count': len(new_cookies) if new_cookies else 0,
                    'has_new_token': new_token is not None,
                    'has_new_private_token': new_private_token is not None
                }
            )

            conn.commit()
            self.logger.info(
                f'✓ Refreshed session: {old_session_id} → {new_session_id}'
            )
            return new_session_id

        except Exception as e:
            conn.rollback()
            self._log_action(
                c,
                action=self.ACTION_REFRESH,
                session_id=new_session_id,
                status='failed',
                reason=str(e),
                triggered_by='system',
                user_id=creator_id
            )
            self.logger.error(f'✗ Failed to refresh session: {e}')
            raise
        finally:
            conn.close()

    def revoke_session(self, session_id: str, reason: str = None):
        """
        撤销会话（逻辑删除，保留审计记录）

        Args:
            session_id: 要撤销的会话ID
            reason: 撤销原因
        """
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        try:
            c.execute(
                "UPDATE auth_sessions SET status = ? WHERE session_id = ?",
                (self.STATUS_REVOKED, session_id)
            )

            self._log_action(
                c,
                action=self.ACTION_REVOKE,
                session_id=session_id,
                status='success',
                reason=reason,
                triggered_by='user_action'
            )

            conn.commit()
            self.logger.info(f'✓ Revoked session {session_id}')

        except Exception as e:
            conn.rollback()
            self.logger.error(f'✗ Failed to revoke session: {e}')
            raise
        finally:
            conn.close()

    def get_session_cookies(self, session_id: str) -> List[Dict]:
        """
        获取会话的所有 cookies（返回 Playwright 标准格式）

        Args:
            session_id: 会话ID

        Returns:
            cookies 列表（Playwright 格式：httpOnly, sameSite 等驼峰命名）
        """
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        c.execute(
            "SELECT * FROM cookie_store WHERE session_id = ? AND valid = 1",
            (session_id,)
        )
        results = c.fetchall()
        conn.close()

        cookies = []
        for row in results:
            # 从数据库读取（小写字段名）
            raw_cookie = {
                'cookie_id': row[0],
                'name': row[2],
                'value': row[3],
                'domain': row[4],
                'path': row[5],
                'expires': row[6],
                'secure': row[8],  # 数据库存储为 0/1
                'httponly': row[9],  # 数据库存储为 0/1
                'samesite': row[10]
            }
            # 标准化为 Playwright 格式（驼峰命名 + 布尔值）
            cookies.append(normalize_playwright_cookie(raw_cookie))

        return cookies

    def save_sso_cookies(self, cookies: List[Dict], creator_id: str = None) -> Optional[str]:
        """
        保存 SSO 登录获取的 cookies 到数据库

        这是为 SSO 登录流程设计的便利方法，
        自动创建会话并保存 cookies。

        Args:
            cookies: Playwright 格式的 cookies 列表
            creator_id: 创建者ID（通常是触发SSO的用户）

        Returns:
            session_id: 创建的会话ID，失败返回None
        """
        try:
            session_id = self.create_session(
                session_type=self.TYPE_COOKIE_BATCH,
                source=self.SOURCE_SSO,
                cookies=cookies,
                creator_id=creator_id or 'sso_login'
            )
            logging.info(f'✓ SSO cookies 已保存到数据库: {session_id}')
            return session_id
        except Exception as e:
            logging.error(f'✗ 保存 SSO cookies 失败: {e}')
            return None

    def get_audit_log(
        self,
        session_id: str = None,
        action: str = None,
        status: str = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        获取审计日志

        Args:
            session_id: 可选的会话ID过滤
            action: 可选的操作类型过滤
            status: 可选的状态过滤
            limit: 返回的最大记录数

        Returns:
            审计日志列表
        """
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        query = "SELECT * FROM auth_audit_log WHERE 1=1"
        params = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if action:
            query += " AND action = ?"
            params.append(action)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        c.execute(query, params)
        results = c.fetchall()
        conn.close()

        logs = []
        for row in results:
            logs.append({
                'log_id': row[0],
                'session_id': row[1],
                'action': row[2],
                'status': row[3],
                'reason': row[4],
                'triggered_by': row[5],
                'user_id': row[6],
                'ip_address': row[7],
                'user_agent': row[8],
                'timestamp': row[9],
                'details': json.loads(row[10]) if row[10] else None
            })

        return logs

    # ===== 辅助方法 =====

    def _add_cookie_to_session(
        self,
        cursor,
        session_id: str,
        cookie: dict
    ):
        """向会话添加 cookie"""
        cookie_id = str(uuid.uuid4())
        now = int(time.time())

        cursor.execute("""
            INSERT INTO cookie_store (
                cookie_id, session_id,
                name, value, domain, path,
                expires, max_age, secure, httponly, samesite,
                created_at, updated_at, valid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cookie_id, session_id,
            cookie.get('name'), cookie.get('value'),
            cookie.get('domain'), cookie.get('path', '/'),
            cookie.get('expires'), cookie.get('max_age'),
            # 防御性编程：支持 secure 和 httpOnly/httponly（Playwright 使用驼峰命名）
            int(bool(cookie.get('secure', cookie.get('secure', 0)))),
            int(bool(cookie.get('httpOnly', cookie.get('httponly', 0)))),
            cookie.get('sameSite', cookie.get('samesite', 'Lax')),
            now, now, 1
        ))

    def _log_action(
        self,
        cursor,
        action: str,
        session_id: str,
        status: str,
        reason: str = None,
        triggered_by: str = None,
        user_id: str = None,
        ip_address: str = None,
        user_agent: str = None,
        context_id: str = None,
        details: dict = None
    ):
        """记录审计日志"""
        cursor.execute("""
            INSERT INTO auth_audit_log (
                session_id, action, status, reason,
                triggered_by, user_id, ip_address,
                user_agent, context_id,
                timestamp, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, action, status, reason,
            triggered_by, user_id, ip_address,
            user_agent, context_id,
            int(time.time()),
            json.dumps(details) if details else None
        ))

    def _update_last_accessed(self, session_id: str):
        """
        更新 last_accessed_at

        官方优化：最多每 60 秒更新一次，以避免频繁数据库写入
        """
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        c.execute(
            "SELECT last_accessed_at FROM auth_sessions WHERE session_id = ?",
            (session_id,)
        )
        result = c.fetchone()

        if result:
            last_accessed = result[0] or 0
            now = int(time.time())

            if now - last_accessed > self.LASTACCESS_UPDATE_INTERVAL:
                c.execute(
                    "UPDATE auth_sessions SET last_accessed_at = ? WHERE session_id = ?",
                    (now, session_id)
                )
                conn.commit()

        conn.close()

    def _verify_ip_restriction(self, ip_restriction: str) -> bool:
        """
        验证当前 IP 是否满足 IP 限制规则

        Args:
            ip_restriction: CIDR 格式的 IP 限制规则（逗号分隔）

        Returns:
            True 如果允许，False 如果不允许
        """
        # 这里需要实现 IP 检查逻辑
        # 简化版本：直接返回 True（完整实现需要 ipaddress 库）
        return True

    # 注意：_initialize_tables() 方法已被移除
    # 所有数据库表的初始化现在由 StateRecorder 统一管理（moodle_dl/database.py）
    # 这样做的好处：
    # 1. 避免重复的表创建逻辑
    # 2. 确保版本号管理的一致性
    # 3. 简化维护和避免同步问题

    def _generate_session_id(self) -> str:
        """生成唯一的 session ID"""
        return str(uuid.uuid4())
