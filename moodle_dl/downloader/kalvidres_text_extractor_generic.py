"""
Kalvidres 通用文本内容提取器
不硬编码特定关键词，提取所有页面文本内容
"""

import re
import html
import logging
import os


class KalvidresTextExtractor:
    """
    提取 kalvidres 页面的文本内容（通用版本）
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
        从 HTML 中提取文本内容（通用方法）
        """
        text_data = {}

        # 1. 提取页面标题（从 <title> 标签）
        title_match = re.search(r'<title>([^<]+)</title>', html_content)
        if title_match:
            text_data['page_title'] = html.unescape(title_match.group(1).strip())

        # 2. 提取模块名称（从 <h1> 标签）
        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL)
        if h1_match:
            h1_text = self._clean_html(h1_match.group(1))
            if h1_text:
                text_data['module_name'] = h1_text

        # 3. 提取 activity-description（核心内容）
        # 这里包含了页面的主要文本说明（可能是 Errata、描述、说明等）
        activity_desc = self._extract_activity_description(html_content)
        if activity_desc:
            text_data['activity_description'] = activity_desc

        # 4. 提取 region-main 中的其他文本内容
        # 作为补充，提取主要内容区域的其他段落
        additional_content = self._extract_additional_content(html_content)
        if additional_content:
            text_data['additional_content'] = additional_content

        return text_data

    def _extract_activity_description(self, html_content):
        """
        提取 activity-description 区域的内容
        这是 kalvidres 页面的核心文本内容
        """
        # 匹配整个 activity-description div
        # 结构: <div class="activity-description" id="...">
        #         <div class="no-overflow">内容</div>
        #       </div>
        pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
        match = re.search(pattern, html_content, re.DOTALL)

        if match:
            content_html = match.group(1)
            return self._clean_html_preserve_structure(content_html)

        return None

    def _extract_additional_content(self, html_content):
        """
        提取 region-main 中的其他内容区域
        作为 activity-description 的补充
        """
        # 查找 region-main
        region_pattern = r'<div[^>]*id="region-main"[^>]*>(.*?)</div>\s*(?=<div[^>]*class="mt-5|$)'
        region_match = re.search(region_pattern, html_content, re.DOTALL)

        if not region_match:
            return None

        region_content = region_match.group(1)

        # 提取所有有意义的段落（排除已在 activity-description 中的）
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', region_content, re.DOTALL)

        clean_paras = []
        for p in paragraphs:
            # 排除空段落和过短段落
            text = self._clean_html(p)
            if text and len(text) > 20 and not self._is_navigation_text(text):
                clean_paras.append(text)

        if clean_paras:
            return '\n\n'.join(clean_paras)

        return None

    def _is_navigation_text(self, text):
        """检查是否是导航文本（应该过滤掉）"""
        navigation_keywords = [
            'Jump to', 'Previous', 'Next', 'Skip to',
            'Mark as done', 'Activity completion', 'navbar',
            'Home', 'My courses', 'Dashboard'
        ]

        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in navigation_keywords)

    def _clean_html(self, html_text):
        """清理 HTML 标签，返回纯文本"""
        if not html_text:
            return None

        # 转换 <br> 为换行
        text = re.sub(r'<br\s*/?>', '\n', html_text)

        # 移除所有 HTML 标签
        text = re.sub(r'<[^>]+>', '', text)

        # 解码 HTML 实体
        text = html.unescape(text)

        # 清理空白
        text = re.sub(r'\s+', ' ', text)  # 多个空格变成单空格
        text = text.strip()

        return text if text else None

    def _clean_html_preserve_structure(self, html_text):
        """
        清理 HTML 但保留基本结构（列表、换行等）
        """
        if not html_text:
            return None

        # 转换 <br> 为换行
        text = re.sub(r'<br\s*/?>', '\n', html_text)

        # 转换段落
        text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text)
        text = re.sub(r'</?p[^>]*>', '\n', text)

        # 转换列表项
        text = re.sub(r'<li[^>]*>', '\n• ', text)
        text = re.sub(r'</li>', '', text)

        # 转换列表容器
        text = re.sub(r'</?ul[^>]*>', '\n', text)
        text = re.sub(r'</?ol[^>]*>', '\n', text)

        # 保留粗体标记（转换为 Markdown）
        text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text)
        text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text)

        # 保留斜体
        text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text)
        text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text)

        # 保留链接（转换为 Markdown）
        text = re.sub(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text)

        # 移除所有其他 HTML 标签
        text = re.sub(r'<[^>]+>', '', text)

        # 解码 HTML 实体
        text = html.unescape(text)

        # 清理空白
        text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)  # 多个空行变成双空行
        text = re.sub(r' +', ' ', text)  # 多个空格变成单空格
        text = text.strip()

        return text if text else None

    def _save_text(self, text_data, save_path):
        """保存文本内容为 Markdown 文件"""
        lines = []

        # 添加页面标题
        if text_data.get('page_title'):
            lines.append(f"# {text_data['page_title']}")
            lines.append("")

        # 添加模块名称
        if text_data.get('module_name'):
            lines.append(f"## {text_data['module_name']}")
            lines.append("")

        # 添加主要内容（activity-description）
        if text_data.get('activity_description'):
            lines.append(text_data['activity_description'])
            lines.append("")

        # 添加其他内容
        if text_data.get('additional_content'):
            lines.append("---")
            lines.append("")
            lines.append("## Additional Notes")
            lines.append("")
            lines.append(text_data['additional_content'])
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
