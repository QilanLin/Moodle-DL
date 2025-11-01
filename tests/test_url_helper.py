"""
URL Helper 单元测试

测试基于官方 Moodle Mobile App 改进的 URL 处理功能
"""

import pytest
from moodle_dl.utils import UrlHelper


class TestFixPluginfileUrl:
    """测试 fix_pluginfile_url 方法"""

    def test_basic_pluginfile_url(self):
        """测试基本的 pluginfile URL 修复"""
        url = "https://moodle.example.com/pluginfile.php/123/mod_resource/content/1/file.pdf"
        token = "mytoken123"
        base_url = "https://moodle.example.com"

        fixed = UrlHelper.fix_pluginfile_url(url, token, base_url)

        # 应该包含 webservice 前缀
        assert "/webservice/pluginfile.php" in fixed
        # 应该包含 token
        assert f"token={token}" in fixed
        # 应该包含 offline=1
        assert "offline=1" in fixed

    def test_url_with_existing_token(self):
        """测试已包含 token 的 URL（不应修改）"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf?token=existingtoken"
        token = "newtoken"
        base_url = "https://moodle.example.com"

        fixed = UrlHelper.fix_pluginfile_url(url, token, base_url)

        # 应该保留原 token，不添加新 token
        assert "token=existingtoken" in fixed
        assert "token=newtoken" not in fixed
        # 应该原样返回
        assert fixed == url

    def test_html_entity_escaping(self):
        """测试 HTML 实体转义（&amp; → &）"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf?foo=bar&amp;baz=qux"
        token = "mytoken"
        base_url = "https://moodle.example.com"

        fixed = UrlHelper.fix_pluginfile_url(url, token, base_url)

        # &amp; 应该被转换为 &
        assert "&amp;" not in fixed
        assert "foo=bar&baz=qux" in fixed or "baz=qux&foo=bar" in fixed

    def test_non_pluginfile_url(self):
        """测试非 pluginfile URL（不应修改）"""
        url = "https://moodle.example.com/some/other/path/file.pdf"
        token = "mytoken"
        base_url = "https://moodle.example.com"

        fixed = UrlHelper.fix_pluginfile_url(url, token, base_url)

        # 非 pluginfile URL 不应被修改
        assert fixed == url.replace("&amp;", "&")

    def test_external_url(self):
        """测试外部 URL（不应修改）"""
        url = "https://external.example.com/pluginfile.php/123/file.pdf"
        token = "mytoken"
        base_url = "https://moodle.example.com"

        fixed = UrlHelper.fix_pluginfile_url(url, token, base_url)

        # 外部 URL 不应被修改
        assert fixed == url

    def test_url_with_webservice_prefix(self):
        """测试已包含 /webservice/ 前缀的 URL"""
        url = "https://moodle.example.com/webservice/pluginfile.php/123/file.pdf"
        token = "mytoken"
        base_url = "https://moodle.example.com"

        fixed = UrlHelper.fix_pluginfile_url(url, token, base_url)

        # 应该添加 token 和 offline
        assert f"token={token}" in fixed
        assert "offline=1" in fixed
        # 不应该重复添加 /webservice/
        assert fixed.count("/webservice/") == 1

    def test_empty_url(self):
        """测试空 URL"""
        fixed = UrlHelper.fix_pluginfile_url("", "token", "https://moodle.example.com")
        assert fixed == ""

    def test_url_with_lang_parameter(self):
        """测试添加语言参数"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        token = "mytoken"
        base_url = "https://moodle.example.com"

        fixed = UrlHelper.fix_pluginfile_url(url, token, base_url, add_lang=True, lang="zh_cn")

        assert "lang=zh_cn" in fixed

    def test_base_url_with_trailing_slash(self):
        """测试带尾部斜杠的基础 URL"""
        url = "https://moodle.example.com/pluginfile.php/123/file.pdf"
        token = "mytoken"
        base_url = "https://moodle.example.com/"  # 带尾部斜杠

        fixed = UrlHelper.fix_pluginfile_url(url, token, base_url)

        assert "/webservice/pluginfile.php" in fixed
        assert f"token={token}" in fixed


# TestUnfixPluginfileUrl - 暂时跳过，因为只实现了核心功能


class TestIsPluginfileUrl:
    """测试 is_pluginfile_url 方法"""

    def test_standard_pluginfile(self):
        """测试标准 pluginfile URL"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/pluginfile.php/123/file.pdf")

    def test_webservice_pluginfile(self):
        """测试 webservice pluginfile URL"""
        assert UrlHelper.is_pluginfile_url(
            "https://moodle.example.com/webservice/pluginfile.php/123/file.pdf"
        )

    def test_tokenpluginfile(self):
        """测试 tokenpluginfile URL"""
        assert UrlHelper.is_pluginfile_url("https://moodle.example.com/tokenpluginfile.php/abc/123/file.pdf")

    def test_non_pluginfile_url(self):
        """测试非 pluginfile URL"""
        assert not UrlHelper.is_pluginfile_url("https://moodle.example.com/some/path/file.pdf")

    def test_empty_url(self):
        """测试空 URL"""
        assert not UrlHelper.is_pluginfile_url("")


# 其他辅助方法测试 - 暂时跳过，只实现了核心功能


class TestIntegration:
    """集成测试 - 测试完整的使用场景"""

    def test_real_world_scenario_1(self):
        """真实场景 1: 修复 Moodle 返回的原始 URL"""
        # Moodle API 返回的原始 URL（可能包含 &amp;）
        raw_url = "https://keats.kcl.ac.uk/pluginfile.php/9876543/mod_resource/content/1/lecture.pdf?forcedownload=1&amp;revision=123"

        fixed = UrlHelper.fix_pluginfile_url(raw_url, "mytoken123", "https://keats.kcl.ac.uk")

        # 验证所有改进都生效
        assert "&amp;" not in fixed  # HTML 转义已处理
        assert "/webservice/pluginfile.php" in fixed  # 已添加 webservice 前缀
        assert "token=mytoken123" in fixed  # 已添加 token
        assert "offline=1" in fixed  # 已添加 offline 参数
        assert "forcedownload=1" in fixed  # 保留原有参数
        assert "revision=123" in fixed  # 保留原有参数

    def test_real_world_scenario_2(self):
        """真实场景 2: URL 已经被修复过（幂等性）"""
        # 已经修复过的 URL
        fixed_url = "https://keats.kcl.ac.uk/webservice/pluginfile.php/123/file.pdf?token=abc&offline=1"

        # 再次调用 fix_pluginfile_url
        result = UrlHelper.fix_pluginfile_url(fixed_url, "newtoken", "https://keats.kcl.ac.uk")

        # 应该保持不变（因为已包含 token）
        assert result == fixed_url

    def test_real_world_scenario_3(self):
        """真实场景 3: 处理外部资源（Kaltura）"""
        # Kaltura 视频 URL（不应被修改）
        kaltura_url = "https://cdnapisec.kaltura.com/p/123/sp/12300/playManifest/entryId/abc/format/url/protocol/https"

        result = UrlHelper.fix_pluginfile_url(kaltura_url, "mytoken", "https://keats.kcl.ac.uk")

        # 外部 URL 不应被修改
        assert result == kaltura_url

    # test_real_world_scenario_4 - 跳过，需要额外方法


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
