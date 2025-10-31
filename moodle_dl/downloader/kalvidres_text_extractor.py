"""
Kalvidres 文本内容提取器
在下载视频前，先提取页面的文本内容（Errata 等）
"""

import re
import html
import logging
import os


class KalvidresTextExtractor:
    """
    提取 kalvidres 页面的文本内容
    """

    def __init__(self, request_helper, cookies_path):
        """
        初始化
        :param request_helper: moodle-dl 的 RequestHelper 实例
        :param cookies_path: Cookies 文件路径
        """
        self.request_helper = request_helper
        self.cookies_path = cookies_path

    def extract_text_from_url(self, url, save_path=None):
        """
        从 kalvidres URL 提取文本内容

        :param url: kalvidres 页面 URL
        :param save_path: 保存文本的路径（可选）
        :return: 提取的文本内容字典
        """
        try:
            # 使用 moodle-dl 的 request_helper 获取页面
            response, session = self.request_helper.get_URL(url, self.cookies_path)

            if response.status_code != 200:
                logging.warning(f'Failed to fetch kalvidres page: {response.status_code}')
                return None

            # 检查是否被重定向到登录页
            if 'login' in response.url.lower() or 'enrol' in response.url.lower():
                logging.warning(f'Redirected to login page, cookies may be invalid')
                return None

            html_content = response.text

            # 提取文本内容
            text_content = self._extract_text_content(html_content)

            # 如果指定了保存路径，保存文本
            if save_path and text_content:
                self._save_text(text_content, save_path)

            return text_content

        except Exception as e:
            logging.error(f'Error extracting kalvidres text: {e}')
            return None

    def _extract_text_content(self, html_content):
        """
        从 HTML 中提取文本内容
        """
        text_data = {}

        # 1. 提取页面标题
        title_match = re.search(r'<h2[^>]*class="[^"]*page-header[^"]*"[^>]*>([^<]+)</h2>', html_content)
        if not title_match:
            title_match = re.search(r'<h2[^>]*>([^<]+)</h2>', html_content)

        if title_match:
            text_data['title'] = html.unescape(title_match.group(1).strip())

        # 2. 提取 Errata 文本
        text_data['errata'] = self._extract_errata(html_content)

        # 3. 提取视频描述
        text_data['description'] = self._extract_description(html_content)

        # 4. 提取其他内容区域
        text_data['content'] = self._extract_content_area(html_content)

        return text_data

    def _extract_errata(self, html_content):
        """提取 Errata 勘误文本"""
        # 多种可能的 Errata 格式
        patterns = [
            # Pattern 1: Errata: 后跟内容
            r'Errata:\s*(.*?)(?=<div class="activity-description|<iframe|<h[1-6]|<footer|$)',
            # Pattern 2: <p>Errata...</p>
            r'<p[^>]*>Errata[^<]*</p>(.*?)(?=<div|<iframe|<h[1-6]|$)',
            # Pattern 3: 包含 errata class 的 div
            r'<div[^>]*class="[^"]*errata[^"]*"[^>]*>(.*?)</div>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                errata_html = match.group(0) if match.lastindex is None else match.group(1)
                return self._clean_html(errata_html)

        return None

    def _extract_description(self, html_content):
        """提取视频描述"""
        desc_pattern = r'<div class="activity-description">(.*?)</div>'
        match = re.search(desc_pattern, html_content, re.DOTALL)

        if match:
            return self._clean_html(match.group(1))

        return None

    def _extract_content_area(self, html_content):
        """提取主要内容区域"""
        # 查找主要内容区域
        content_patterns = [
            r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="region-main"[^>]*>(.*?)</div>',
        ]

        for pattern in content_patterns:
            match = re.search(pattern, html_content, re.DOTALL)
            if match:
                content_html = match.group(1)
                # 提取所有段落
                paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', content_html, re.DOTALL)

                clean_paras = []
                for p in paragraphs:
                    clean_p = self._clean_html(p)
                    if clean_p and len(clean_p) > 10:
                        clean_paras.append(clean_p)

                if clean_paras:
                    return '\n\n'.join(clean_paras)

        return None

    def _clean_html(self, html_text):
        """清理 HTML 标签，保留文本"""
        if not html_text:
            return None

        # 转换 <br> 为换行
        text = re.sub(r'<br\s*/?>', '\n', html_text)

        # 转换列表项
        text = re.sub(r'<li[^>]*>', '\n• ', text)

        # 移除所有其他 HTML 标签
        text = re.sub(r'<[^>]+>', '', text)

        # 解码 HTML 实体
        text = html.unescape(text)

        # 清理空白
        text = re.sub(r'\n\s*\n', '\n\n', text)  # 多个空行变成双空行
        text = re.sub(r' +', ' ', text)  # 多个空格变成单空格
        text = text.strip()

        return text if text else None

    def _save_text(self, text_data, save_path):
        """保存文本内容为 Markdown 文件"""
        lines = []

        # 添加标题
        if text_data.get('title'):
            lines.append(f"# {text_data['title']}")
            lines.append("")

        # 添加描述
        if text_data.get('description'):
            lines.append("## Description")
            lines.append("")
            lines.append(text_data['description'])
            lines.append("")

        # 添加 Errata
        if text_data.get('errata'):
            lines.append("## Errata")
            lines.append("")
            lines.append(text_data['errata'])
            lines.append("")

        # 添加其他内容
        if text_data.get('content'):
            lines.append("## Additional Notes")
            lines.append("")
            lines.append(text_data['content'])
            lines.append("")

        # 保存文件
        content = '\n'.join(lines)

        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logging.info(f'Saved kalvidres text to: {save_path}')
            return True

        except Exception as e:
            logging.error(f'Failed to save kalvidres text: {e}')
            return False
