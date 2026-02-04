# -*- coding: utf-8 -*-
"""
IP 地址验证工具

用于检测和诊断 Moodle 服务器 IP 白名单相关问题。
"""

import logging
import re
from typing import Optional, Tuple

import requests
from requests.exceptions import RequestException


class IPValidator:
    """
    IP 地址验证工具类

    功能：
    - 检测当前公网 IP 地址
    - 诊断 403 错误是否由 IP 白名单引起
    - 生成管理员友好的白名单配置消息
    """

    # IP 白名单相关错误关键词
    IP_RESTRICTION_KEYWORDS = [
        'ip',
        'address',
        'restricted',
        'whitelist',
        'allowed',
        'unauthorized',
        'access denied',
        'forbidden',
    ]

    # IP 检测服务（按优先级排序）
    IP_DETECTION_SERVICES = [
        # 格式: (URL, 提取函数)
        ('https://api.ipify.org?format=json', lambda r: r.json().get('ip')),
        ('https://ifconfig.me/ip', lambda r: r.text.strip()),
        ('https://icanhazip.com', lambda r: r.text.strip()),
        ('https://checkip.amazonaws.com', lambda r: r.text.strip()),
    ]

    @staticmethod
    def get_public_ip(timeout: int = 5) -> Optional[str]:
        """
        检测当前公网 IP 地址

        尝试多个 IP 检测服务，直到成功或全部失败。

        Args:
            timeout: 每个服务的超时时间（秒）

        Returns:
            当前公网 IP 地址，如果检测失败则返回 None
        """
        for service_url, extract_func in IPValidator.IP_DETECTION_SERVICES:
            try:
                response = requests.get(service_url, timeout=timeout)
                response.raise_for_status()
                ip = extract_func(response)

                # 验证 IP 格式
                if IPValidator._is_valid_ip(ip):
                    logging.debug(f'检测到公网 IP: {ip} (通过 {service_url})')
                    return ip

            except RequestException as e:
                logging.debug(f'IP 检测服务 {service_url} 失败: {e}')
                continue
            except Exception as e:
                logging.debug(f'IP 提取失败 ({service_url}): {e}')
                continue

        logging.warning('无法检测公网 IP 地址（所有服务均失败）')
        return None

    @staticmethod
    def _is_valid_ip(ip: str) -> bool:
        """
        验证字符串是否为有效的 IPv4 或 IPv6 地址

        Args:
            ip: 待验证的 IP 字符串

        Returns:
            是否为有效 IP 地址
        """
        if not ip or not isinstance(ip, str):
            return False

        # IPv4 正则表达式
        ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        # IPv6 正则表达式（简化版）
        ipv6_pattern = r'^([0-9a-fA-F]{0,4}:){7}[0-9a-fA-F]{0,4}$'

        ip = ip.strip()

        # 检查 IPv4
        if re.match(ipv4_pattern, ip):
            # 验证每个八位组在 0-255 范围内
            octets = ip.split('.')
            for octet in octets:
                if not 0 <= int(octet) <= 255:
                    return False
            return True

        # 检查 IPv6
        if re.match(ipv6_pattern, ip):
            return True

        return False

    @staticmethod
    def diagnose_403_error(
        error_message: str,
        response_text: Optional[str] = None,
        moodle_domain: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        诊断 403 错误是否可能由 IP 白名单限制引起

        Args:
            error_message: HTTP 错误消息
            response_text: 响应体文本（可选）
            moodle_domain: Moodle 域名（可选）

        Returns:
            (是否可能是 IP 限制, 诊断消息)
        """
        # 组合所有可用的文本进行关键词检查
        all_text = error_message.lower()
        if response_text:
            all_text += ' ' + response_text.lower()

        # 检查是否包含 IP 限制相关的关键词
        ip_restriction_indicators = [
            'ip not allowed',
            'ip address restricted',
            'ip whitelist',
            'ip restriction',
            'unauthorized ip',
            'access from your ip',
            'your ip is not',
            'ip address is not allowed',
        ]

        is_ip_restriction = any(
            indicator in all_text
            for indicator in ip_restriction_indicators
        )

        if is_ip_restriction:
            # 服务器明确返回了 IP 限制消息
            return True, IPValidator._generate_ip_restriction_message(moodle_domain)

        # 如果没有明确消息，仍然提示可能是 IP 问题
        # 因为 Moodle 的 IP 白名单经常返回通用的 403
        return True, IPValidator._generate_generic_403_message(moodle_domain)

    @staticmethod
    def _generate_ip_restriction_message(moodle_domain: Optional[str]) -> str:
        """
        生成 IP 限制的详细错误消息

        Args:
            moodle_domain: Moodle 域名

        Returns:
            格式化的错误消息
        """
        current_ip = IPValidator.get_public_ip()

        domain_info = f'\n Moodle 域名: {moodle_domain}' if moodle_domain else ''
        ip_info = f'\n\n当前 IP 地址: {current_ip}' if current_ip else '\n\n无法检测当前 IP 地址'

        message = f'''
╔═══════════════════════════════════════════════════════════════════╗
║                    ⚠️  IP 访问被拒绝                                ║
╚═══════════════════════════════════════════════════════════════════╝

您的 IP 地址不在 Moodle 服务器的 Web Service API 白名单中。

解决方案：
────────────────────────────────────────────────────────────────────

1. 联系 Moodle 管理员，请求将您的 IP 地址添加到白名单

2. 配置路径（管理员）：
   网站管理 → 安全 → HTTP 安全 → Web Service IP 限制

3. 需要添加的信息：{domain_info}{ip_info}

4. 如果您的 IP 地址经常变化，可以请求管理员：
   • 添加整个 IP 段（如 123.45.0.0/16）
   • 为您创建专用的 API Token
   • 暂时禁用 IP 限制（不推荐）

═════════════════════════════════════════════════════════════════════
'''
        return message

    @staticmethod
    def _generate_generic_403_message(moodle_domain: Optional[str]) -> str:
        """
        生成通用 403 错误消息（包含 IP 限制的可能性）

        Args:
            moodle_domain: Moodle 域名

        Returns:
            格式化的错误消息
        """
        current_ip = IPValidator.get_public_ip()

        domain_info = f'\n Moodle 域名: {moodle_domain}' if moodle_domain else ''
        ip_info = f'\n当前 IP: {current_ip}' if current_ip else '\n无法检测当前 IP'

        message = f'''
╔═══════════════════════════════════════════════════════════════════╗
║                    ⚠️  访问被拒绝 (HTTP 403)                        ║
╚═══════════════════════════════════════════════════════════════════╝

可能的原因：
────────────────────────────────────────────────────────────────────

1. 🔐 IP 地址不在白名单中（最常见）
   → Moodle 服务器可能启用了 Web Service IP 限制
   → 您的 IP 需要被管理员添加到允许列表中
   → {ip_info}{domain_info}

2. 🔑 Token 无效或已过期
   → 请尝试重新获取 Token
   → 使用: moodle-dl --token <your-token>

3. 🚫 权限不足
   → 您的账户可能没有访问此 API 的权限
   → 请联系管理员确认权限设置

═════════════════════════════════════════════════════════════════════
'''
        return message

    @staticmethod
    def generate_whitelist_request_message(moodle_domain: str, admin_email: Optional[str] = None) -> str:
        """
        生成发给管理员的白名单请求消息

        Args:
            moodle_domain: Moodle 域名
            admin_email: 管理员邮箱（可选）

        Returns:
            格式化的请求消息
        """
        current_ip = IPValidator.get_public_ip()

        if not current_ip:
            current_ip = '[无法自动检测，请手动查看]'

        email_line = f'Email: {admin_email}\n' if admin_email else ''

        message = f'''
主题：请求将 IP 地址添加到 Moodle Web Service API 白名单

尊敬的 Moodle 管理员：

我正在使用 Moodle-DL 工具访问 Moodle 系统的课程内容，但遇到了访问被拒绝的错误（HTTP 403）。

经过诊断，这很可能是因为我的 IP 地址不在 Web Service API 的白名单中。

系统信息：
────────────────────────────────────────────────────────────────────
Moodle 域名: {moodle_domain}
{email_line}需要添加的 IP: {current_ip}
（如果 IP 地址经常变化，请考虑添加整个 IP 段）

配置路径：网站管理 → 安全 → HTTP 安全 → Web Service IP 限制
═════════════════════════════════════════════════════════════════════

参考文档：https://docs.moodle.org/en/Web_Service_IP_restrictions

此致
敬礼
'''
        return message
