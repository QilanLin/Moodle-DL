# -*- coding: utf-8 -*-
"""
downloader/task.py 单元测试

测试 Task 类的静态方法和辅助函数：
- HTML 转换方法
- DRM 错误检测
- Kaltura URL 提取
- 文件名生成
- 元数据文件检测
"""

import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from concurrent.futures import ThreadPoolExecutor
from moodle_dl.downloader.task import Task, KalturaExtractionError
from moodle_dl.downloader.task_file_ops import TaskFileOps
from moodle_dl.downloader.task_url_ops import TaskUrlOps
from moodle_dl.types import Course, File, MoodleDlOpts, MoodleURL, DownloadOptions
from moodle_dl.config import ConfigHelper


class TestHTMLConversionMethods(unittest.TestCase):
    """HTML 转换静态方法测试"""

    def test_convert_line_breaks(self):
        """测试 <br> 标签转换"""
        html = "<p>Hello<br>World</p>"
        result = TaskFileOps.convert_line_breaks(html)
        self.assertEqual(result, "<p>Hello\nWorld</p>")

    def test_convert_line_breaks_with_slash(self):
        """测试 <br/> 标签转换"""
        html = "Line 1<br/>Line 2<br />Line 3"
        result = TaskFileOps.convert_line_breaks(html)
        self.assertEqual(result, "Line 1\nLine 2\nLine 3")

    def test_convert_paragraphs(self):
        """测试 <p> 标签转换"""
        html = "<p>Para 1</p><p>Para 2</p>"
        result = TaskFileOps.convert_paragraphs(html)
        self.assertIn("\n", result)

    def test_convert_lists_ul(self):
        """测试 <ul> 列表转换"""
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        result = TaskFileOps.convert_lists(html)
        self.assertIn("•", result)

    def test_convert_lists_ol(self):
        """测试 <ol> 列表转换"""
        html = "<ol><li>First</li><li>Second</li></ol>"
        result = TaskFileOps.convert_lists(html)
        self.assertIn("•", result)

    def test_convert_formatting_bold(self):
        """测试 <b> 和 <strong> 标签转换"""
        html = "<b>Bold text</b> and <strong>strong text</strong>"
        result = TaskFileOps.convert_formatting(html)
        self.assertIn("**Bold text**", result)
        self.assertIn("**strong text**", result)

    def test_convert_formatting_italic(self):
        """测试 <i> 和 <em> 标签转换"""
        html = "<i>Italic text</i> and <em>emphasized text</em>"
        result = TaskFileOps.convert_formatting(html)
        self.assertIn("*Italic text*", result)
        self.assertIn("*emphasized text*", result)

    def test_remove_html_tags(self):
        """测试移除 HTML 标签"""
        html = "<p>Hello <b>World</b></p>"
        result = TaskFileOps.remove_html_tags(html)
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)

    def test_decode_html_entities(self):
        """测试 HTML 实体解码"""
        html = "Hello &amp; World &lt;3&gt;"
        result = TaskFileOps.decode_html_entities(html)
        self.assertIn("Hello & World <3>", result)

    def test_clean_whitespace(self):
        """测试空白清理"""
        text = "Hello    World   Test"
        result = TaskFileOps.clean_whitespace(text)
        self.assertNotIn("  ", result)

    def test_clean_html_simple(self):
        """测试简单 HTML 清理（实例方法）"""
        html = "<p>Hello<br>World</p>"
        # 创建一个 Task 实例来测试实例方法
        opts = MoodleDlOpts()
        course = Course(1, "Test Course")
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="test.pdf",
            content_fileurl=f"https://example.com/test.pdf",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )
        options = DownloadOptions(
            token="test_token",
            moodle_url="https://example.com",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/test",
            download_metadata_files=True,
            global_opts=opts
        )

        from concurrent.futures import ThreadPoolExecutor
        thread_pool = ThreadPoolExecutor(max_workers=1)
        task = Task(1, file, course, options, thread_pool, lambda: None)
        thread_pool.shutdown(wait=False)

        result = task._file_ops.clean_html_simple(html)
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertIn("Hello", result)


class TestDRMErrorDetection(unittest.TestCase):
    """DRM 错误检测测试"""

    def setUp(self):
        self.opts = MoodleDlOpts()

        # 创建实际的 Course 对象
        self.course = Course(1, "Test Course")
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        self.file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="test.pdf",
            content_fileurl="https://example.com/test.pdf",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )

        # 创建 DownloadOptions
        self.options = DownloadOptions(
            token="test_token",
            moodle_url="https://example.com",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/test",
            download_metadata_files=True,
            global_opts=self.opts
        )

        self.thread_pool = ThreadPoolExecutor(max_workers=1)
        self.callback = lambda: None

        self.task = Task(1, self.file, self.course, self.options, self.thread_pool, self.callback)

    def tearDown(self):
        self.thread_pool.shutdown(wait=False)

    def test_drm_error_detection_drm_keyword(self):
        """测试检测 DRM 关键词"""
        error_msg = "This content is DRM protected"
        result = TaskUrlOps().is_drm_error(error_msg)
        self.assertTrue(result)

    def test_drm_error_detection_widevine(self):
        """测试检测 Widevine 关键词"""
        error_msg = "WidevineDecryptor failed"
        result = TaskUrlOps().is_drm_error(error_msg)
        self.assertTrue(result)

    def test_drm_error_detection_encrypted(self):
        """测试检测 encrypted 关键词"""
        error_msg = "Content is encrypted"
        result = TaskUrlOps().is_drm_error(error_msg)
        self.assertTrue(result)

    def test_drm_error_detection_case_insensitive(self):
        """测试大小写不敏感"""
        error_msg = "THIS IS DRM PROTECTED CONTENT"
        result = TaskUrlOps().is_drm_error(error_msg)
        self.assertTrue(result)

    def test_drm_error_detection_negative(self):
        """测试非 DRM 错误"""
        error_msg = "Network connection failed"
        result = TaskUrlOps().is_drm_error(error_msg)
        self.assertFalse(result)

    def test_drm_error_detection_empty(self):
        """测试空错误消息"""
        error_msg = ""
        result = TaskUrlOps().is_drm_error(error_msg)
        self.assertFalse(result)


class TestKalturaExtraction(unittest.TestCase):
    """Kaltura URL 提取测试"""

    def setUp(self):
        self.opts = MoodleDlOpts()

        # 创建实际的 Course 对象
        self.course = Course(1, "Test Course")
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        self.file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="test.pdf",
            content_fileurl="https://example.com/test.pdf",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )

        # 创建 DownloadOptions
        self.options = DownloadOptions(
            token="test_token",
            moodle_url="https://example.com",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/test",
            download_metadata_files=True,
            global_opts=self.opts
        )

        self.thread_pool = ThreadPoolExecutor(max_workers=1)
        self.callback = lambda: None

        self.task = Task(1, self.file, self.course, self.options, self.thread_pool, self.callback)

    def tearDown(self):
        self.thread_pool.shutdown(wait=False)

    def test_extract_entry_id_valid(self):
        """测试提取有效的 entry ID"""
        url = "https://example.com/browseandembed/index/entryid/abc123def/"
        result = self.task._extract_entry_id(url)
        self.assertEqual(result, "abc123def")

    def test_extract_entry_id_invalid(self):
        """测试无效 URL 提取 entry ID"""
        url = "https://example.com/invalid/url"
        with self.assertRaises(KalturaExtractionError):
            self.task._extract_entry_id(url)

    def test_extract_uiconf_id_valid(self):
        """测试提取有效的 UI 配置 ID"""
        url = "https://example.com/browseandembed/playerSkin/12345/"
        result = self.task._extract_uiconf_id(url)
        self.assertEqual(result, "12345")

    def test_extract_uiconf_id_invalid(self):
        """测试无效 URL 提取 UI 配置 ID"""
        url = "https://example.com/invalid/url"
        with self.assertRaises(KalturaExtractionError):
            self.task._extract_uiconf_id(url)

    def test_extract_partner_id_valid(self):
        """测试提取有效的 partner ID"""
        html = '<script>var partnerId=123456</script>'
        result = self.task._extract_partner_id(html)
        self.assertEqual(result, "123456")

    def test_extract_partner_id_with_equals(self):
        """测试使用 = 符号的 partner ID"""
        html = 'partnerId=987654'
        result = self.task._extract_partner_id(html)
        self.assertEqual(result, "987654")

    def test_extract_partner_id_with_json_spacing_and_partner_path(self):
        """测试 JSON 和 Kaltura URL 格式的 partner ID"""
        self.assertEqual(
            self.task._extract_partner_id('{"partnerId": 123456}'),
            "123456",
        )
        self.assertEqual(
            self.task._extract_partner_id('https://cdn.kaltura.com/p/654321/embed'),
            "654321",
        )
        self.assertEqual(
            self.task._extract_partner_id('/embedIframeJs/uiconf_id/1/partner_id/111222?x=1'),
            "111222",
        )

    def test_infer_partner_id_for_known_kcl_kaf_host(self):
        """测试 KCL KAF host 的 partner ID 兜底"""
        result = self.task._infer_partner_id_from_browse_url(
            "https://kaf.kcl.ac.uk/browseandembed/index/media/entryid/1_abcd/view/playerSkin/123456"
        )
        self.assertEqual(result, "2368101")

        keats_result = self.task._infer_partner_id_from_browse_url(
            "http://kaf.keats.kcl.ac.uk/browseandembed/index/media/entryid/1_abcd/view/playerSkin/123456"
        )
        self.assertEqual(keats_result, "2368101")

        self.assertIsNone(
            self.task._infer_partner_id_from_browse_url(
                "https://kaf.example.com/browseandembed/index/media/entryid/1_abcd/view/playerSkin/123456"
            )
        )

    def test_source_url_from_kaltura_lti_launch_without_source(self):
        """测试没有 source 参数的 Moodle Kaltura wrapper"""
        self.assertIsNone(
            self.task._source_url_from_kaltura_lti_launch(
                "https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?courseid=1"
            )
        )

    def test_known_embed_url_requires_entry_id_and_uiconf(self):
        """测试已知 Kaltura embed URL 缺少必要参数时不构建 URL"""
        self.assertIsNone(
            self.task._build_kaltura_url_from_known_embed_url(
                "https://keats.kcl.ac.uk/browseandembed/index/media/playerSkin/42864872/"
            )
        )
        self.assertIsNone(
            self.task._build_kaltura_url_from_known_embed_url(
                "https://kaf.kcl.ac.uk/browseandembed/index/media/entryid/1_abcd/"
            )
        )

    def test_extract_partner_id_invalid(self):
        """测试无效 HTML 提取 partner ID"""
        html = "<html><body>No partner ID here</body></html>"
        with self.assertRaises(KalturaExtractionError):
            self.task._extract_partner_id(html)

    def test_detect_kaltura_cdn(self):
        """测试检测 Kaltura CDN"""
        html = '<iframe src="https://cdn.kaltura.com/p/123456/embed"></iframe>'
        result = self.task._detect_kaltura_cdn(html)
        self.assertEqual(result, "cdn.kaltura.com")

    def test_detect_kaltura_cdn_not_found(self):
        """测试未找到 Kaltura CDN"""
        html = '<iframe src="https://example.com/video"></iframe>'
        result = self.task._detect_kaltura_cdn(html)
        self.assertIsNone(result)


class TestFilenameGeneration(unittest.TestCase):
    """文件名生成测试"""

    def setUp(self):
        self.opts = MoodleDlOpts()

        # 创建实际的 Course 对象
        self.course = Course(1, "Test Course")

        # 创建 DownloadOptions
        self.options = DownloadOptions(
            token="test_token",
            moodle_url="https://example.com",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/test",
            download_metadata_files=True,
            global_opts=self.opts
        )

        self.thread_pool = ThreadPoolExecutor(max_workers=1)
        self.callback = lambda: None

    def tearDown(self):
        self.thread_pool.shutdown(wait=False)

    def test_generate_filename_with_index_single_digit(self):
        """测试生成单位数索引的文件名"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="lecture.pdf",
            content_fileurl=f"https://example.com/lecture.pdf",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
            position_in_section=0,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._file_ops.generate_filename_with_index(file)
        self.assertEqual(result, "*01* lecture.pdf")

    def test_generate_filename_with_index_double_digit(self):
        """测试生成两位数索引的文件名"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="video.mp4",
            content_fileurl=f"https://example.com/video.mp4",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
            position_in_section=9,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._file_ops.generate_filename_with_index(file)
        self.assertEqual(result, "*10* video.mp4")

    def test_generate_filename_with_index_preserves_original(self):
        """测试保留原始文件名中的数字"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="01-introduction.pdf",
            content_fileurl=f"https://example.com/01-introduction.pdf",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
            position_in_section=4,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._file_ops.generate_filename_with_index(file)
        self.assertEqual(result, "*05* 01-introduction.pdf")

    def test_generate_filename_without_index(self):
        """测试没有索引的文件名生成"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="readme.txt",
            content_fileurl=f"https://example.com/readme.txt",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._file_ops.generate_filename_with_index(file)
        self.assertEqual(result, "readme.txt")


class TestMetadataFileDetection(unittest.TestCase):
    """元数据文件检测测试"""

    def setUp(self):
        self.opts = MoodleDlOpts()

        # 创建实际的 Course 对象
        self.course = Course(1, "Test Course")

        # 创建 DownloadOptions
        self.options = DownloadOptions(
            token="test_token",
            moodle_url="https://example.com",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/test",
            download_metadata_files=True,
            global_opts=self.opts
        )

        self.thread_pool = ThreadPoolExecutor(max_workers=1)
        self.callback = lambda: None

    def tearDown(self):
        self.thread_pool.shutdown(wait=False)

    def test_is_metadata_file_json(self):
        """测试 JSON 文件检测"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="metadata.json",
            content_fileurl=f"https://example.com/metadata.json",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._is_metadata_file()
        self.assertTrue(result)

    def test_is_metadata_file_info(self):
        """测试 _info 文件检测"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="homework_info",
            content_fileurl=f"https://example.com/homework_info",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._is_metadata_file()
        self.assertTrue(result)

    def test_is_metadata_file_notes(self):
        """测试 _notes.md 文件检测"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="lecture_notes.md",
            content_fileurl=f"https://example.com/lecture_notes.md",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._is_metadata_file()
        self.assertTrue(result)

    def test_is_metadata_file_case_insensitive(self):
        """测试大小写不敏感"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="METADATA.JSON",
            content_fileurl=f"https://example.com/METADATA.JSON",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._is_metadata_file()
        self.assertTrue(result)

    def test_is_metadata_file_negative(self):
        """测试非元数据文件"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="lecture.pdf",
            content_fileurl=f"https://example.com/lecture.pdf",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )
        task = Task(1, file, self.course, self.options, self.thread_pool, self.callback)
        result = task._is_metadata_file()
        self.assertFalse(result)


class TestPathGeneration(unittest.TestCase):
    """路径生成测试"""

    def test_gen_path_flat(self):
        """测试平铺路径生成"""
        course = MagicMock()
        course.fullname = "Test Course"
        course.overwrite_name_with = None
        course.create_directory_structure = False
        course.excluded_sections = []

        file = MagicMock()
        file.content_filepath = "/files"
        file.section_name = "Section 1"

        storage_path = "/storage"

        result = TaskFileOps(MagicMock()).gen_path(storage_path, course, file)
        self.assertIsNotNone(result)

    def test_gen_path_with_subdirectory(self):
        """测试带子目录的路径生成"""
        course = MagicMock()
        course.fullname = "Test Course"
        course.overwrite_name_with = "Renamed Course"
        course.create_directory_structure = True
        course.excluded_sections = []

        file = MagicMock()
        file.content_filepath = "/files"
        file.module_modname = "assign"
        file.module_name = "Assignment 1"
        file.section_name = "Section 1"

        storage_path = "/storage"

        result = TaskFileOps(MagicMock()).gen_path(storage_path, course, file)
        self.assertIsNotNone(result)

    def test_gen_path_overwrite_name(self):
        """测试使用覆盖名称"""
        course = MagicMock()
        course.fullname = "Original Name"
        course.overwrite_name_with = "Custom Name"
        course.create_directory_structure = False
        course.excluded_sections = []

        file = MagicMock()
        file.content_filepath = "/files"
        file.section_name = "Section 1"

        storage_path = "/storage"

        result = TaskFileOps(MagicMock()).gen_path(storage_path, course, file)
        self.assertIsNotNone(result)


class TestBuildKalturaUrl(unittest.TestCase):
    """Kaltura URL 构建测试"""

    def setUp(self):
        self.opts = MoodleDlOpts()

        # 创建实际的 Course 对象
        self.course = Course(1, "Test Course")
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        self.file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="test.pdf",
            content_fileurl="https://example.com/test.pdf",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )

        # 创建 DownloadOptions
        self.options = DownloadOptions(
            token="test_token",
            moodle_url="https://example.com",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/test",
            download_metadata_files=True,
            global_opts=self.opts
        )

        self.thread_pool = ThreadPoolExecutor(max_workers=1)
        self.callback = lambda: None

        self.task = Task(1, self.file, self.course, self.options, self.thread_pool, self.callback)

    def tearDown(self):
        self.thread_pool.shutdown(wait=False)

    def test_build_kaltura_url_cdn(self):
        """测试构建带 CDN 的 Kaltura URL"""
        url = self.task._build_kaltura_url(
            partner_id="123456",
            uiconf_id="12345",
            entry_id="abc123",
            cdn="cdn.kaltura.com"
        )
        self.assertIn("cdn.kaltura.com", url)
        self.assertIn("123456", url)
        self.assertIn("abc123", url)

    def test_build_kaltura_url_all_params(self):
        """测试使用所有参数构建 URL"""
        url = self.task._build_kaltura_url(
            partner_id="987654",
            uiconf_id="54321",
            entry_id="xyz789",
            cdn="cdnapi.kaltura.com"
        )
        self.assertIn("987654", url)
        self.assertIn("54321", url)
        self.assertIn("xyz789", url)
        self.assertIn("cdnapi.kaltura.com", url)


class TestNoneHandlingInTask(unittest.TestCase):
    """测试 Task 对 content_fileurl 为 None 的处理"""

    def setUp(self):
        """设置测试环境"""
        self.opts = MoodleDlOpts()
        self.course = Course(1, "Test Course")
        self.thread_pool = ThreadPoolExecutor(max_workers=1)

    def tearDown(self):
        """清理测试环境"""
        self.thread_pool.shutdown(wait=False)

    def test_task_with_none_content_fileurl(self):
        """测试 content_fileurl 为 None 时 Task 不会崩溃"""
        # Use a real File so gen_path can read module_name
        # (which would otherwise be a MagicMock instance).
        from moodle_dl.types import File
        file = File(
            module_id=1, section_name="Week 1", section_id=1,
            module_name="Test Module", content_filepath="/",
            content_filename="test.pdf",
            content_fileurl="https://example.com/test.pdf",
            content_filesize=1, content_timemodified=1,
            module_modname="resource", content_type="file",
            content_isexternalfile=False,
        )
        file.content_fileurl = None
        download_options = DownloadOptions(
            token="test_token",
            moodle_url="https://example.com",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/test",
            download_metadata_files=True,
            global_opts=self.opts
        )

        callback = lambda: None
        task = Task(1, file, self.course, download_options, self.thread_pool, callback)

        # 验证 Task 可以正常创建
        self.assertIsNotNone(task)
        self.assertEqual(task.file.content_fileurl, None)

    def test_task_str_with_none_content_fileurl(self):
        """测试 content_fileurl 为 None 时 __str__ 方法正常工作"""
        # 使用真实的 File 对象而不是 MagicMock，以便测试 File.__str__ 的 None 处理
        from moodle_dl.types import File

        file = File(
            module_id=1,
            section_name="Week 1",
            section_id=1,
            module_name="Test Resource",
            content_filepath="/",
            content_filename="test.pdf",
            content_fileurl=None,  # URL 为 None
            content_filesize=1024,
            content_timemodified=1234567890,
            module_modname="resource",
            content_type="application/pdf",
            content_isexternalfile=False,
            saved_to="/tmp/test.pdf",
            time_stamp=1234567890,
            modified=0,
            moved=0,
            deleted=0,
            notified=0
        )

        # 创建 Task 对象
        download_options = DownloadOptions(
            token="test_token",
            moodle_url="https://example.com",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/test",
            download_metadata_files=True,
            global_opts=self.opts
        )

        callback = lambda: None
        task = Task(1, file, self.course, download_options, self.thread_pool, callback)

        # 验证 __str__ 不会崩溃（File.__str__ 中的 None 检查）
        task_str = str(task)
        self.assertIn("content_fileurl", task_str)
        self.assertIn('content_fileurl: ""', task_str)  # None URL 会规范化为空字符串


if __name__ == "__main__":
    unittest.main()
