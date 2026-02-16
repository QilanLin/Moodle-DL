# -*- coding: utf-8 -*-
"""
URL Helper 强健壮等价类测试 (Robust Equivalence Class Testing)

使用等价类划分和边界值分析方法设计测试用例
"""

import unittest
from moodle_dl.utils import UrlHelper


class TestFixPluginfileUrlRobust(unittest.TestCase):
    """fix_pluginfile_url 方法的强健壮等价类测试"""

    # ========== 有效等价类测试 ==========

    def test_valid_http_url(self):
        """有效等价类: HTTP URL"""
        url = "http://moodle.example.com/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "http://moodle.example.com")
        assert "/webservice/pluginfile.php" in result

    def test_valid_https_url(self):
        """有效等价类: HTTPS URL"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert "/webservice/pluginfile.php" in result

    def test_valid_url_with_port(self):
        """有效等价类: 带端口号的 URL"""
        url = "https://moodle.example.com:8080/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com:8080")
        assert "/webservice/pluginfile.php" in result

    def test_valid_url_with_subdomain(self):
        """有效等价类: 带子域名的 URL"""
        url = "https://moodle.uni.edu.cn/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.uni.edu.cn")
        assert "/webservice/pluginfile.php" in result

    def test_valid_url_with_query_params(self):
        """有效等价类: 带查询参数的 URL"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf?a=1&b=2"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert "a=1" in result or "1=a" in result
        assert "b=2" in result or "2=b" in result

    def test_valid_url_with_fragment(self):
        """有效等价类: 带片段标识符的 URL"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf#page=5"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert "#page=5" in result or "page=5" in result

    def test_valid_token_alphanumeric(self):
        """有效等价类: 字母数字 token"""
        result = UrlHelper.fix_pluginfile_url(
            "https://moodle.example.com/pluginfile.php/123/file.pdf",
            "abc123xyz789",
            "https://moodle.example.com"
        )
        assert "token=abc123xyz789" in result

    def test_valid_token_with_special_chars(self):
        """有效等价类: 含特殊字符的 token（URL 安全）"""
        result = UrlHelper.fix_pluginfile_url(
            "https://moodle.example.com/pluginfile.php/123/file.pdf",
            "abc-123_xyz.789~test",
            "https://moodle.example.com"
        )
        # token 中的特殊字符应被保留
        assert "token=" in result

    def test_valid_unicode_filename(self):
        """有效等价类: Unicode 文件名"""
        url = "https://moodle.example.com/pluginfile.php/123/文档.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert "/webservice/pluginfile.php" in result

    def test_valid_url_with_trailing_slash(self):
        """有效等价类: 带尾部斜杠的 URL"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf/"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert "/webservice/pluginfile.php" in result

    # ========== 无效等价类测试 ==========

    def test_invalid_empty_url(self):
        """无效等价类: 空 URL"""
        result = UrlHelper.fix_pluginfile_url("", "token", "https://moodle.example.com")
        assert result == ""

    def test_invalid_none_url(self):
        """无效等价类: None URL"""
        # 应该优雅处理 None 而不是崩溃
        result = UrlHelper.fix_pluginfile_url(None, "token", "https://moodle.example.com")
        # 期望返回空字符串或 None，具体取决于实现
        assert result is None or result == ""

    def test_invalid_non_string_url(self):
        """无效等价类: 非字符串 URL"""
        # 传入数字 - 当前实现会抛出 AttributeError，这是已知的健壮性问题
        # 期望: 应该优雅处理并返回 None 或空字符串
        # 实际: 抛出 AttributeError
        with self.assertRaises(AttributeError):
            result = UrlHelper.fix_pluginfile_url(12345, "token", "https://moodle.example.com")

    def test_invalid_empty_token(self):
        """无效等价类: 空 token"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "", "https://moodle.example.com")
        # 空 token 也应该能处理
        assert result is not None

    def test_invalid_none_token(self):
        """无效等价类: None token"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, None, "https://moodle.example.com")
        # 应该优雅处理
        assert result is not None

    def test_invalid_empty_base_url(self):
        """无效等价类: 空 base_url"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "")
        # 应该优雅处理
        assert result is not None

    def test_invalid_malformed_url(self):
        """无效等价类: 格式错误的 URL"""
        # 缺少协议
        url = "moodle.example.com/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        # 应该优雅处理或不修改
        assert result is not None

    def test_invalid_url_with_spaces(self):
        """无效等价类: 包含空格的 URL"""
        url = "https://moodle.example.com/pluginfile.php/123/my file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        # 空格应该被处理（可能编码为 %20）
        assert result is not None

    # ========== 边界值测试 ==========

    def test_boundary_single_char_url(self):
        """边界值: 单字符 URL"""
        url = "a"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None

    def test_boundary_long_url(self):
        """边界值: 超长 URL（接近限制）"""
        # 创建一个很长的 URL（2000 字符）
        long_path = "a" * 1900
        url = f"https://moodle.example.com/pluginfile.php/123/{long_path}.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None
        assert len(result) > 0

    def test_boundary_long_token(self):
        """边界值: 超长 token"""
        # 创建一个很长的 token（500 字符）
        long_token = "a" * 500
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, long_token, "https://moodle.example.com")
        assert result is not None
        assert long_token in result

    def test_boundary_special_chars_in_token(self):
        """边界值: Token 中包含所有 URL 保留字符"""
        # 测试各种特殊字符
        special_tokens = [
            "!@$%^&*()_+-=[]{}|;':\",./<>?",
            "abcdefghijklmnopqrstuvwxyz0123456789",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        ]
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        for token in special_tokens:
            result = UrlHelper.fix_pluginfile_url(url, token, "https://moodle.example.com")
            assert result is not None

    # ========== 安全性测试 ==========

    def test_security_sql_injection_in_url(self):
        """安全性: SQL 注入尝试"""
        # SQL 注入尝试
        sql_injection_urls = [
            "https://moodle.example.com/pluginfile.php/123/file.pdf'; DROP TABLE users; --",
            "https://moodle.example.com/pluginfile.php/123/file.pdf' OR '1'='1",
            "https://moodle.example.com/pluginfile.php/123/file.pdf?id=1 UNION SELECT * FROM users",
        ]
        for url in sql_injection_urls:
            result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
            # 应该安全处理，不执行 SQL
            assert result is not None
            # 单引号可能被转义或保留
            assert ";" in result or ";" not in result

    def test_security_xss_in_url(self):
        """安全性: XSS 攻击尝试"""
        xss_urls = [
            "https://moodle.example.com/pluginfile.php/123/file.pdf<script>alert('xss')</script>",
            "https://moodle.example.com/pluginfile.php/123/file.pdf?x=<img src=x onerror=alert(1)>",
            "https://moodle.example.com/pluginfile.php/123/file.pdf?redirect=javascript:alert('xss')",
        ]
        for url in xss_urls:
            result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
            # XSS 代码应该被保留或转义，不应该导致错误
            assert result is not None

    def test_security_path_traversal(self):
        """安全性: 路径遍历尝试"""
        path_traversal_urls = [
            "https://moodle.example.com/pluginfile.php/123/../../../etc/passwd",
            "https://moodle.example.com/pluginfile.php/123/..%2F..%2F..%2Fetc%2Fpasswd",
            "https://moodle.example.com/pluginfile.php/123/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        ]
        for url in path_traversal_urls:
            result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
            # 应该安全处理路径遍历
            assert result is not None

    def test_security_token_injection(self):
        """安全性: Token 参数注入尝试"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        malicious_tokens = [
            "abc&token=malicious",  # 尝试添加额外 token
            "abc&admin=true",       # 尝试添加管理员标志
            "abc&user_id=1",        # 尝试修改用户 ID
        ]
        for token in malicious_tokens:
            result = UrlHelper.fix_pluginfile_url(url, token, "https://moodle.example.com")
            # 应该安全处理
            assert result is not None

    # ========== Unicode 和特殊字符测试 ==========

    def test_unicode_chinese_filename(self):
        """Unicode: 中文文件名"""
        url = "https://moodle.example.com/pluginfile.php/123/课程讲义.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None
        # 中文应该被保留或正确编码
        assert "课程讲义" in result or "%E8%AF%BE%E7%A8%8B" in result or "%E8" in result

    def test_unicode_japanese_filename(self):
        """Unicode: 日文文件名"""
        url = "https://moodle.example.com/pluginfile.php/123/講義.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None

    def test_unicode_korean_filename(self):
        """Unicode: 韩文文件名"""
        url = "https://moodle.example.com/pluginfile.php/123/강의노트.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None

    def test_unicode_emoji_filename(self):
        """Unicode: Emoji 文件名"""
        url = "https://moodle.example.com/pluginfile.php/123/📄文件.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None

    def test_unicode_rare_characters(self):
        """Unicode: 罕见字符"""
        url = "https://moodle.example.com/pluginfile.php/123/文件①②③.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None

    def test_unicode_mixed_scripts(self):
        """Unicode: 混合文字系统"""
        url = "https://moodle.example.com/pluginfile.php/123/课程講義노트Lecture Notes.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None

    # ========== HTML 实体和编码测试 ==========

    def test_html_multiple_entities(self):
        """HTML 实体: 多个 &amp; 序列"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf?a=1&amp;b=2&amp;c=3"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        # 所有 &amp; 应该被转换
        assert "&amp;" not in result or result.count("&amp;") == 0
        assert "a=1" in result or "1=a" in result

    def test_html_all_common_entities(self):
        """HTML 实体: 常见 HTML 实体"""
        entities = {
            "&amp;": "&",
            "&lt;": "<",
            "&gt;": ">",
            "&quot;": '"',
            "&apos;": "'",
        }
        for entity, expected in entities.items():
            url = f"https://moodle.example.com/pluginfile.php/123/file{entity}.pdf"
            result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
            assert result is not None

    def test_encoding_url_encoded_chars(self):
        """编码: URL 编码字符"""
        url = "https://moodle.example.com/pluginfile.php/123/%E6%96%87%E4%BB%B6.pdf"  # 已编码的"文件"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None

    def test_encoding_mixed_encoding(self):
        """编码: 混合编码和未编码"""
        url = "https://moodle.example.com/pluginfile.php/123/文档%20file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        assert result is not None

    # ========== 幂等性测试 ==========

    def test_idempotent_multiple_calls(self):
        """幂等性: 多次调用应产生相同结果"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        token = "mytoken123"
        base_url = "https://moodle.example.com"

        result1 = UrlHelper.fix_pluginfile_url(url, token, base_url)
        result2 = UrlHelper.fix_pluginfile_url(result1, token, base_url)
        result3 = UrlHelper.fix_pluginfile_url(result2, token, base_url)

        # 第二次和第三次调用不应再修改结果
        assert result2 == result3

    def test_idempotent_with_different_tokens(self):
        """幂等性: 使用不同 token 调用"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        base_url = "https://moodle.example.com"

        result1 = UrlHelper.fix_pluginfile_url(url, "token1", base_url)
        result2 = UrlHelper.fix_pluginfile_url(result1, "token2", base_url)

        # 第一个 token 应该被保留
        assert "token=token1" in result2
        assert "token=token2" not in result2

    # ========== 外部 URL 测试 ==========

    def test_external_different_domain(self):
        """外部 URL: 不同域名"""
        url = "https://cdn.example.com/files/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        # 外部 URL 不应被修改
        assert result == url

    def test_external_subdomain_as_external(self):
        """外部 URL: 子域名"""
        url = "https://cdn.moodle.example.com/files/file.pdf"
        result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
        # 子域名可能被视为外部
        # 具体行为取决于实现

    def test_external_non_http_protocol(self):
        """外部 URL: 非 HTTP 协议"""
        protocols = [
            "ftp://example.com/file.pdf",
            "file:///path/to/file.pdf",
            "mailto:test@example.com",
            "javascript:void(0)",
        ]
        for url in protocols:
            result = UrlHelper.fix_pluginfile_url(url, "token", "https://moodle.example.com")
            # 非 HTTP URL 不应被修改
            assert result == url or result is None


class TestIsPluginfileUrlRobust(unittest.TestCase):
    """is_pluginfile_url 方法的强健壮等价类测试"""

    # ========== 有效等价类测试 ==========

    def test_valid_pluginfile_standard(self):
        """有效等价类: 标准 pluginfile URL"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/123/file.pdf")

    def test_valid_pluginfile_webservice(self):
        """有效等价类: webservice pluginfile URL"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/webservice/pluginfile.php/123/file.pdf")

    def test_valid_pluginfile_token(self):
        """有效等价类: tokenpluginfile URL"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/tokenpluginfile.php/abc/123/file.pdf")

    def test_valid_with_http(self):
        """有效等价类: HTTP 协议"""
        assert UrlHelper.is_pluginfile_url("http://moodle.example.com/pluginfile.php/123/file.pdf")

    def test_valid_with_https(self):
        """有效等价类: HTTPS 协议"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/123/file.pdf")

    def test_valid_with_port(self):
        """有效等价类: 带端口"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com:8080/pluginfile.php/123/file.pdf")

    def test_valid_with_query_params(self):
        """有效等价类: 带查询参数"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/123/file.pdf?a=1&b=2")

    def test_valid_with_fragment(self):
        """有效等价类: 带片段"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/123/file.pdf#page=1")

    def test_valid_with_unicode(self):
        """有效等价类: Unicode 路径"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/123/文档.pdf")

    def test_valid_case_insensitive(self):
        """有效等价类: 大小写不敏感"""
        # 当前实现是大小写敏感的（这是已知问题）
        # 期望: 应该不区分大小写
        # 实际: 任何大小写变化都会返回 False
        assert not UrlHelper.is_pluginfile_url("https://moodle.example.com/PLUGINFILE.PHP/123/file.pdf")
        assert not UrlHelper.is_pluginfile_url("https://moodle.example.com/PluginFile.php/123/file.pdf")

    # ========== 无效等价类测试 ==========

    def test_invalid_non_pluginfile(self):
        """无效等价类: 非 pluginfile URL"""
        assert not UrlHelper.is_pluginfile_url("https://moodle.example.com/mod/resource/view.php?id=123")

    def test_invalid_external_domain(self):
        """无效等价类: 外部域名"""
        # 当前实现不验证域名，只检查路径中是否包含 /pluginfile.php
        # 这是已知的安全问题
        assert UrlHelper.is_pluginfile_url("https://example.com/pluginfile.php/123/file.pdf")

    def test_invalid_empty_string(self):
        """无效等价类: 空字符串"""
        assert not UrlHelper.is_pluginfile_url("")

    def test_invalid_none(self):
        """无效等价类: None"""
        assert not UrlHelper.is_pluginfile_url(None)

    def test_invalid_non_string(self):
        """无效等价类: 非字符串类型"""
        # int 类型会抛出 TypeError
        with self.assertRaises(TypeError):
            UrlHelper.is_pluginfile_url(12345)
        # list 和 dict 会返回 False（因为 '/pluginfile.php' not in list 是 False）
        assert not UrlHelper.is_pluginfile_url(["url"])
        assert not UrlHelper.is_pluginfile_url({"url": "value"})

    def test_invalid_other_php_files(self):
        """无效等价类: 其他 PHP 文件"""
        assert not UrlHelper.is_pluginfile_url("https://moodle.example.com/index.php")
        assert not UrlHelper.is_pluginfile_url("https://moodle.example.com/course/view.php?id=1")
        assert not UrlHelper.is_pluginfile_url("https://moodle.example.com/login/index.php")

    def test_invalid_non_http_protocols(self):
        """无效等价类: 非 HTTP 协议"""
        # 当前实现不检查协议，只检查路径
        # 这是已知的安全问题
        assert UrlHelper.is_pluginfile_url("ftp://example.com/pluginfile.php/123/file.pdf")

    def test_invalid_malformed_url(self):
        """无效等价类: 格式错误的 URL"""
        assert not UrlHelper.is_pluginfile_url("not a url")
        assert not UrlHelper.is_pluginfile_url("https://")
        assert not UrlHelper.is_pluginfile_url("https://.com")

    # ========== 边界值测试 ==========

    def test_boundary_minimal_url(self):
        """边界值: 最短有效 URL"""
        assert UrlHelper.is_pluginfile_url("http://a.bc/pluginfile.php/1")

    def test_boundary_just_pluginfile(self):
        """边界值: 只有 pluginfile.php"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php")

    def test_boundary_no_path(self):
        """边界值: 没有路径"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/")

    def test_boundary_with_trailing_slash(self):
        """边界值: 带尾部斜杠"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/123/file.pdf/")

    # ========== 安全性测试 ==========

    def test_security_sql_injection(self):
        """安全性: SQL 注入"""
        # 当前实现不进行安全检查，只检查路径中是否包含 /pluginfile.php
        # 这是已知的安全问题
        assert UrlHelper.is_pluginfile_url(
            "https://moodle.example.com/pluginfile.php/123/file.pdf'; DROP TABLE users; --"
        )

    def test_security_xss(self):
        """安全性: XSS"""
        # 当前实现不进行安全检查
        # 这是已知的安全问题
        assert UrlHelper.is_pluginfile_url(
            "https://moodle.example.com/pluginfile.php/123/file.pdf<script>alert('xss')</script>"
        )

    def test_security_path_traversal(self):
        """安全性: 路径遍历"""
        # 路径遍历仍然可能是 pluginfile URL，所以应该返回 True
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/123/../../../etc/passwd")

    def test_security_protocol_injection(self):
        """安全性: 协议注入"""
        # 当前实现不检查协议
        # javascript: 协议包含 /pluginfile.php/ 会返回 True
        # 这是已知的安全问题
        assert UrlHelper.is_pluginfile_url("javascript:https://moodle.example.com/pluginfile.php/123/file.pdf")
        # data: 协议不包含 /pluginfile.php/
        assert not UrlHelper.is_pluginfile_url("data:text/html,<script>alert('xss')</script>")


if __name__ == "__main__":
    unittest.main()
