# -*- coding: utf-8 -*-
"""
kalvidres_text_extractor_generic.py 单元测试

测试 Kaltura 视频页面文本提取功能：
- HTML 内容提取
- 文本清理
- Markdown 转换
- 文件保存
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch, mock_open

from moodle_dl.downloader.kalvidres_text_extractor_generic import KalvidresTextExtractor


class TestCleanHtml(unittest.TestCase):
    """_clean_html 方法测试"""

    def setUp(self):
        self.request_helper = Mock()
        self.extractor = KalvidresTextExtractor(self.request_helper, '/tmp/cookies.txt')

    def test_clean_html_simple_text(self):
        """测试简单文本清理"""
        html = 'Hello World'
        result = self.extractor._clean_html(html)
        self.assertEqual(result, 'Hello World')

    def test_clean_html_with_tags(self):
        """测试移除 HTML 标签"""
        html = '<p>Hello <strong>World</strong></p>'
        result = self.extractor._clean_html(html)
        self.assertEqual(result, 'Hello World')

    def test_clean_html_with_br(self):
        """测试 <br> 转换为换行"""
        html = 'Line 1<br>Line 2<br/>Line 3'
        result = self.extractor._clean_html(html)
        # _clean_html produces plain text with spaces (whitespace is normalized)
        self.assertEqual(result, 'Line 1 Line 2 Line 3')

    def test_clean_html_with_entities(self):
        """测试 HTML 实体解码"""
        html = '&lt;test&gt; &amp; &quot;quoted&quot;'
        result = self.extractor._clean_html(html)
        self.assertEqual(result, '<test> & "quoted"')

    def test_clean_html_unicode_entities(self):
        """测试 Unicode 实体解码"""
        html = '&#x4E2D;&#x6587;'  # 中文
        result = self.extractor._clean_html(html)
        self.assertEqual(result, '中文')

    def test_clean_html_whitespace(self):
        """测试空白字符清理"""
        html = '<p>Text  with    multiple   spaces</p>'
        result = self.extractor._clean_html(html)
        self.assertEqual(result, 'Text with multiple spaces')

    def test_clean_html_none_input(self):
        """测试 None 输入"""
        result = self.extractor._clean_html(None)
        self.assertIsNone(result)

    def test_clean_html_empty_string(self):
        """测试空字符串"""
        result = self.extractor._clean_html('')
        self.assertIsNone(result)

    def test_clean_html_only_tags(self):
        """测试只有标签的 HTML"""
        html = '<div><p><span></span></p></div>'
        result = self.extractor._clean_html(html)
        self.assertIsNone(result)


class TestCleanHtmlPreserveStructure(unittest.TestCase):
    """_clean_html_preserve_structure 方法测试"""

    def setUp(self):
        self.request_helper = Mock()
        self.extractor = KalvidresTextExtractor(self.request_helper, '/tmp/cookies.txt')

    def test_preserve_bold_to_markdown(self):
        """测试粗体转换为 Markdown"""
        html = '<b>Bold text</b> and <strong>more bold</strong>'
        result = self.extractor._clean_html_preserve_structure(html)
        self.assertEqual(result, '**Bold text** and **more bold**')

    def test_preserve_italic_to_markdown(self):
        """测试斜体转换为 Markdown"""
        html = '<i>Italic text</i> and <em>more italic</em>'
        result = self.extractor._clean_html_preserve_structure(html)
        self.assertEqual(result, '*Italic text* and *more italic*')

    def test_preserve_links_to_markdown(self):
        """测试链接转换为 Markdown"""
        html = '<a href="https://example.com">Link text</a>'
        result = self.extractor._clean_html_preserve_structure(html)
        self.assertEqual(result, '[Link text](https://example.com)')

    def test_preserve_paragraphs(self):
        """测试段落转换"""
        html = '<p>Para 1</p><p>Para 2</p>'
        result = self.extractor._clean_html_preserve_structure(html)
        self.assertEqual(result, 'Para 1\n\nPara 2')

    def test_preserve_lists(self):
        """测试列表转换"""
        html = '<ul><li>Item 1</li><li>Item 2</li></ul>'
        result = self.extractor._clean_html_preserve_structure(html)
        # The actual implementation produces: • Item 1\n• Item 2
        # (no leading or trailing newline)
        self.assertEqual(result, '• Item 1\n• Item 2')

    def test_preserve_ordered_list(self):
        """测试有序列表转换"""
        html = '<ol><li>First</li><li>Second</li></ol>'
        result = self.extractor._clean_html_preserve_structure(html)
        self.assertIn('• First', result)
        self.assertIn('• Second', result)

    def test_preserve_multiple_newlines(self):
        """测试多个换行符清理"""
        html = '<p>Text</p><br><br><p>More</p>'
        result = self.extractor._clean_html_preserve_structure(html)
        # Should not have excessive newlines
        self.assertNotIn('\n\n\n', result)

    def test_preserve_none_input(self):
        """测试 None 输入"""
        result = self.extractor._clean_html_preserve_structure(None)
        self.assertIsNone(result)


class TestIsNavigationText(unittest.TestCase):
    """_is_navigation_text 方法测试"""

    def setUp(self):
        self.request_helper = Mock()
        self.extractor = KalvidresTextExtractor(self.request_helper, '/tmp/cookies.txt')

    def test_navigation_jump_to(self):
        """测试 Jump to 导航文本"""
        result = self.extractor._is_navigation_text('Jump to content')
        self.assertTrue(result)

    def test_navigation_previous(self):
        """测试 Previous 导航文本"""
        result = self.extractor._is_navigation_text('Previous activity')
        self.assertTrue(result)

    def test_navigation_home(self):
        """测试 Home 导航文本"""
        result = self.extractor._is_navigation_text('Home')
        self.assertTrue(result)

    def test_navigation_case_insensitive(self):
        """测试大小写不敏感"""
        result = self.extractor._is_navigation_text('SKIP TO MAIN CONTENT')
        self.assertTrue(result)

    def test_non_navigation_text(self):
        """测试非导航文本"""
        result = self.extractor._is_navigation_text('This is course content about physics')
        self.assertFalse(result)

    def test_empty_text(self):
        """测试空文本"""
        result = self.extractor._is_navigation_text('')
        self.assertFalse(result)


class TestExtractTextContent(unittest.TestCase):
    """_extract_text_content 方法测试"""

    def setUp(self):
        self.request_helper = Mock()
        self.extractor = KalvidresTextExtractor(self.request_helper, '/tmp/cookies.txt')

    def test_extract_title(self):
        """测试提取页面标题"""
        html = '<html><head><title>Course Title</title></head><body></body></html>'
        result = self.extractor._extract_text_content(html)
        self.assertEqual(result['page_title'], 'Course Title')

    def test_extract_h1_module_name(self):
        """测试提取模块名称"""
        html = '<h1>Video Lecture 1</h1>'
        result = self.extractor._extract_text_content(html)
        self.assertEqual(result['module_name'], 'Video Lecture 1')

    def test_extract_h1_with_nested_tags(self):
        """测试带嵌套标签的 h1"""
        html = '<h1><span>Module</span> Name</h1>'
        result = self.extractor._extract_text_content(html)
        self.assertEqual(result['module_name'], 'Module Name')

    def test_extract_activity_description(self):
        """测试提取活动描述"""
        html = '''
        <div class="activity-description" id="yui_3_17_2_1_123">
            <div class="no-overflow">
                <p>This is the activity description.</p>
            </div>
        </div>
        '''
        result = self.extractor._extract_text_content(html)
        self.assertIn('activity_description', result)
        self.assertIn('activity description', result['activity_description'])

    def test_extract_additional_content(self):
        """测试提取额外内容"""
        html = '''
        <div id="region-main">
            <p>Additional paragraph 1 with enough content to pass filter.</p>
            <p>Additional paragraph 2 with enough content to pass filter.</p>
        </div>
        '''
        result = self.extractor._extract_text_content(html)
        self.assertIn('additional_content', result)

    def test_extract_complete_content(self):
        """测试完整内容提取"""
        html = '''
        <html>
        <head><title>Test Course</title></head>
        <body>
            <h1>Lecture 1</h1>
            <div class="activity-description" id="test">
                <div class="no-overflow">
                    <p>Main content here.</p>
                </div>
            </div>
        </body>
        </html>
        '''
        result = self.extractor._extract_text_content(html)
        self.assertEqual(result['page_title'], 'Test Course')
        self.assertEqual(result['module_name'], 'Lecture 1')
        self.assertIn('activity_description', result)


class TestExtractActivityDescription(unittest.TestCase):
    """_extract_activity_description 方法测试"""

    def setUp(self):
        self.request_helper = Mock()
        self.extractor = KalvidresTextExtractor(self.request_helper, '/tmp/cookies.txt')

    def test_extract_activity_description_simple(self):
        """测试简单活动描述提取"""
        html = '<div class="activity-description" id="test"><div class="no-overflow"><p>Content</p></div></div>'
        result = self.extractor._extract_activity_description(html)
        self.assertIn('Content', result)

    def test_extract_activity_description_with_attributes(self):
        """测试带属性的 activity-description"""
        html = '<div class="activity-description" data-id="123" id="abc"><div class="no-overflow">Text</div></div>'
        result = self.extractor._extract_activity_description(html)
        self.assertIn('Text', result)

    def test_extract_activity_description_none(self):
        """测试没有 activity-description"""
        html = '<div class="other-content">No description</div>'
        result = self.extractor._extract_activity_description(html)
        self.assertIsNone(result)


class TestExtractAdditionalContent(unittest.TestCase):
    """_extract_additional_content 方法测试"""

    def setUp(self):
        self.request_helper = Mock()
        self.extractor = KalvidresTextExtractor(self.request_helper, '/tmp/cookies.txt')

    def test_extract_region_main_content(self):
        """测试提取 region-main 内容"""
        html = '<div id="region-main"><p>Paragraph with sufficient content length to pass filter.</p></div>'
        result = self.extractor._extract_additional_content(html)
        self.assertIsNotNone(result)
        self.assertIn('Paragraph', result)

    def test_filter_short_paragraphs(self):
        """测试过滤短段落"""
        html = '<div id="region-main"><p>Short</p><p>Valid paragraph with enough content to pass.</p></div>'
        result = self.extractor._extract_additional_content(html)
        self.assertIn('Valid paragraph', result)
        # Short paragraph should be filtered out
        self.assertNotIn('Short', result)

    def test_filter_navigation_text(self):
        """测试过滤导航文本"""
        html = '<div id="region-main"><p>Jump to main content</p><p>Valid paragraph that passes filter.</p></div>'
        result = self.extractor._extract_additional_content(html)
        self.assertNotIn('Jump to', result)

    def test_no_region_main(self):
        """测试没有 region-main"""
        html = '<div class="content">No region main</div>'
        result = self.extractor._extract_additional_content(html)
        self.assertIsNone(result)


class TestExtractTextFromUrl(unittest.TestCase):
    """extract_text_from_url 方法测试"""

    def setUp(self):
        self.request_helper = Mock()
        self.extractor = KalvidresTextExtractor(self.request_helper, '/tmp/cookies.txt')

    def test_extract_success(self):
        """测试成功提取"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = 'https://example.com/kalvidres/view'
        mock_response.text = '<html><head><title>Test</title></head><body><p>Content</p></body></html>'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.extractor.extract_text_from_url('https://example.com/kalvidres')

        self.assertIsNotNone(result)
        self.assertEqual(result['page_title'], 'Test')

    def test_extract_status_not_200(self):
        """测试非 200 状态码"""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.url = 'https://example.com/kalvidres/view'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.extractor.extract_text_from_url('https://example.com/kalvidres')

        self.assertIsNone(result)

    def test_extract_redirected_to_login(self):
        """测试重定向到登录页"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = 'https://example.com/login/index.php'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.extractor.extract_text_from_url('https://example.com/kalvidres')

        self.assertIsNone(result)

    def test_extract_redirected_to_enrol(self):
        """测试重定向到注册页"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = 'https://example.com/enrol/index.php'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.extractor.extract_text_from_url('https://example.com/kalvidres')

        self.assertIsNone(result)

    def test_extract_with_save(self):
        """测试提取并保存到文件"""
        import tempfile
        import os

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = 'https://example.com/kalvidres/view'
        mock_response.text = '<html><head><title>Test Video</title></head></html>'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, 'video_content.md')
            result = self.extractor.extract_text_from_url('https://example.com/kalvidres', save_path)

            self.assertIsNotNone(result)
            self.assertTrue(os.path.exists(save_path))

            # Verify file contents
            with open(save_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.assertIn('# Test Video', content)

    @patch('moodle_dl.downloader.kalvidres_text_extractor_generic.logging')
    def test_extract_exception_handling(self, mock_logging):
        """测试异常处理"""
        self.request_helper.get_URL.side_effect = Exception('Network error')

        result = self.extractor.extract_text_from_url('https://example.com/kalvidres')

        self.assertIsNone(result)
        # Should log error
        self.assertTrue(mock_logging.error.called)


class TestSaveText(unittest.TestCase):
    """_save_text 方法测试"""

    def setUp(self):
        self.request_helper = Mock()
        self.extractor = KalvidresTextExtractor(self.request_helper, '/tmp/cookies.txt')

    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    @patch('moodle_dl.downloader.kalvidres_text_extractor_generic.logging')
    def test_save_text_success(self, mock_logging, mock_file, mock_makedirs):
        """测试成功保存文本"""
        text_data = {
            'page_title': 'Test Video',
            'module_name': 'Lecture 1',
            'activity_description': 'Main content here',
            'additional_content': 'Additional notes'
        }

        result = self.extractor._save_text(text_data, '/tmp/test/video.md')

        self.assertTrue(result)

        # Verify file was written
        mock_file.assert_called_once()
        handle = mock_file()
        written_content = ''.join(call[0][0] for call in handle.write.call_args_list)

        self.assertIn('# Test Video', written_content)
        self.assertIn('## Lecture 1', written_content)
        self.assertIn('Main content here', written_content)
        self.assertIn('## Additional Notes', written_content)

    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_save_text_minimal(self, mock_file, mock_makedirs):
        """测试保存最小数据"""
        text_data = {
            'page_title': 'Test'
        }

        self.extractor._save_text(text_data, '/tmp/test/minimal.md')

        handle = mock_file()
        written_content = ''.join(call[0][0] for call in handle.write.call_args_list)

        self.assertIn('# Test', written_content)

    @patch('os.makedirs')
    @patch('builtins.open')
    def test_save_text_io_error(self, mock_file, mock_makedirs):
        """测试文件写入错误"""
        mock_file.side_effect = IOError('Permission denied')

        text_data = {'page_title': 'Test'}

        result = self.extractor._save_text(text_data, '/tmp/test/error.md')

        self.assertFalse(result)

    @patch('os.makedirs')
    def test_save_text_mkdir_error(self, mock_makedirs):
        """测试目录创建错误"""
        mock_makedirs.side_effect = OSError('Directory creation failed')

        text_data = {'page_title': 'Test'}

        result = self.extractor._save_text(text_data, '/tmp/test/error.md')

        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
