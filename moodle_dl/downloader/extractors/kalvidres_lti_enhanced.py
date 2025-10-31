# coding: utf-8
"""
Enhanced Kalvidres LTI Extractor
提取视频的同时，保存页面文本内容（如 Errata）
"""
from __future__ import unicode_literals

import html
import re
import os

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.extractor.kaltura import KalturaIE
from yt_dlp.utils import ExtractorError, extract_attributes, urlencode_postdata


class KalvidresLtiEnhancedIE(InfoExtractor):
    """
    Enhanced Kalvidres LTI extractor that also extracts page text content
    """
    IE_NAME = 'kalvidresLtiEnhanced'
    _VALID_URL = r'(?P<scheme>https?://)(?P<host>[^/]+)(?P<path>.*)?/mod/kalvidres/view.php\?.*?id=(?P<id>\d+)'
    _LAUNCH_FORM = 'ltiLaunchForm'

    def _extract_page_text(self, webpage):
        """
        提取页面中的文本内容
        包括：标题、描述、Errata 等
        """
        text_content = {}

        # 1. 提取页面标题
        title_match = re.search(r'<h2[^>]*>([^<]+)</h2>', webpage)
        if title_match:
            text_content['title'] = html.unescape(title_match.group(1).strip())

        # 2. 提取 Errata 文本
        # 查找包含 "Errata" 的部分
        errata_pattern = r'Errata:(.*?)(?=<div class="activity-description|<iframe|<h[1-6]|$)'
        errata_match = re.search(errata_pattern, webpage, re.DOTALL | re.IGNORECASE)

        if errata_match:
            errata_html = errata_match.group(0)
            # 清理 HTML 标签但保留结构
            errata_text = re.sub(r'<br\s*/?>', '\n', errata_html)
            errata_text = re.sub(r'<li>', '\n- ', errata_text)
            errata_text = re.sub(r'<[^>]+>', '', errata_text)
            errata_text = html.unescape(errata_text)
            errata_text = re.sub(r'\n\s*\n', '\n\n', errata_text)  # 清理多余空行
            text_content['errata'] = errata_text.strip()

        # 3. 提取 activity-description（视频描述）
        desc_pattern = r'<div class="activity-description">(.*?)</div>'
        desc_match = re.search(desc_pattern, webpage, re.DOTALL)

        if desc_match:
            desc_html = desc_match.group(1)
            # 清理 HTML
            desc_text = re.sub(r'<br\s*/?>', '\n', desc_html)
            desc_text = re.sub(r'<[^>]+>', '', desc_text)
            desc_text = html.unescape(desc_text)
            text_content['description'] = desc_text.strip()

        # 4. 提取其他可能的文本内容
        # 查找所有段落
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', webpage, re.DOTALL)
        if paragraphs:
            clean_paragraphs = []
            for p in paragraphs:
                clean_p = re.sub(r'<[^>]+>', '', p)
                clean_p = html.unescape(clean_p).strip()
                if clean_p and len(clean_p) > 10:  # 过滤太短的段落
                    clean_paragraphs.append(clean_p)

            if clean_paragraphs:
                text_content['paragraphs'] = clean_paragraphs

        return text_content

    def _save_text_content(self, text_content, video_id, output_dir=None):
        """
        保存提取的文本内容到文件
        """
        if not text_content:
            return None

        # 构建文本文件内容
        lines = []

        if 'title' in text_content:
            lines.append(f"# {text_content['title']}")
            lines.append("")

        if 'description' in text_content:
            lines.append("## Description")
            lines.append("")
            lines.append(text_content['description'])
            lines.append("")

        if 'errata' in text_content:
            lines.append("## Errata")
            lines.append("")
            lines.append(text_content['errata'])
            lines.append("")

        if 'paragraphs' in text_content:
            lines.append("## Additional Content")
            lines.append("")
            for para in text_content['paragraphs']:
                lines.append(para)
                lines.append("")

        content = '\n'.join(lines)

        # 保存到文件
        if output_dir:
            filename = os.path.join(output_dir, f'kalvidres_{video_id}_notes.md')
        else:
            filename = f'kalvidres_{video_id}_notes.md'

        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)

            self.to_screen(f'Saved page text to: {filename}')
            return filename
        except Exception as e:
            self.report_warning(f'Failed to save text content: {str(e)}')
            return None

    def _real_extract(self, url):
        mobj = re.match(self._VALID_URL, url)
        video_id = mobj.group('id')

        # 1. Extract launch URL
        self.to_screen(f'Downloading kalvidres page {video_id}...')
        view_webpage = self._download_webpage(url, video_id, 'Downloading kalvidres video view webpage')

        # ✨ 新增：提取并保存页面文本内容
        self.to_screen('Extracting page text content...')
        text_content = self._extract_page_text(view_webpage)

        if text_content:
            self.to_screen(f'Found text content: {", ".join(text_content.keys())}')
            # 保存文本（可以通过参数配置输出目录）
            output_dir = self._downloader.params.get('paths', {}).get('home', None)
            self._save_text_content(text_content, video_id, output_dir)
        else:
            self.to_screen('No text content found on page')

        # 2. 继续原有的视频提取流程
        mobj = re.search(r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src=(["\'])(?P<url>[^"\']+)\1', view_webpage)
        if not mobj:
            raise ExtractorError('Unable to extract kalvidres launch url')

        launch_url = html.unescape(mobj.group('url'))

        # 3. Get launch parameters
        launch_webpage = self._download_webpage(launch_url, video_id, 'Downloading kalvidres launch webpage')
        launch_inputs = self._form_hidden_inputs(self._LAUNCH_FORM, launch_webpage)
        launch_form_str = self._search_regex(
            fr'(?P<form><form[^>]+?id=(["\']){self._LAUNCH_FORM}\2[^>]*>)', launch_webpage, 'login form', group='form'
        )

        action_url = extract_attributes(launch_form_str).get('action')

        # 4. Launch kalvidres video app
        submit_page, dummy = self._download_webpage_handle(
            action_url, video_id, 'Launch kalvidres app', data=urlencode_postdata(launch_inputs)
        )

        mobj = re.search(r'window.location.href = \'(?P<url>[^\']+)\'', submit_page)
        if not mobj:
            raise ExtractorError('Unable to extract kalvidres redirect url')

        # 5. Follow kalvidres video app redirect
        redirect_page, dummy = self._download_webpage_handle(
            html.unescape(mobj.group('url')), video_id, 'Follow kalvidres redirect'
        )

        kultura_url = KalturaIE._extract_url(redirect_page)
        if not kultura_url:
            raise ExtractorError('Unable to extract kaltura url')

        # 6. 返回视频信息
        return {
            '_type': 'url',
            'url': kultura_url,
            'ie_key': 'Kaltura',
        }
