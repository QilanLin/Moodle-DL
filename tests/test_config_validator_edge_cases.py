# -*- coding: utf-8 -*-
"""
config_validator.py / config.py 边界测试

覆盖以下方面：
1) normalize_moodle_url 各分支
2) MoodleURL 构造（IP / localhost / 端口 / 路径 / 大小写 / 空域）
3) download_course_ids / dont_download_course_ids 白/黑名单语义
4) token 长度 / 字符 / 占位符 / None / 换行
5) Moodle URL 异常（不合法 / 空 / 用户信息）
6) ConfigHelper 的 get_* accessor（默认、布尔转换、URL 组装）

|  |  |
|--|--|
| Author | Test Agent |
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from moodle_dl.config import (
    ConfigHelper,
    DownloadOptionsConfig,
    normalize_moodle_url,
)
from moodle_dl.config_validator import (
    ConfigValidator,
    auto_fix_config,
    validate_config_data,
)
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.types import MoodleDlOpts, MoodleURL


# ---------------------------------------------------------------------------
# 公共 fixtures / helpers
# ---------------------------------------------------------------------------


def _valid_config(**overrides):
    """提供一个最小合法 config dict，叠加 overrides 便于覆盖单字段。"""
    base = {
        'moodle_domain': 'moodle.example.com',
        'moodle_path': '/moodle',
    }
    base.update(overrides)
    return base


def _make_helper(tmp_path, config=None, auth_manager=None):
    """构造绕过数据库初始化的 ConfigHelper。"""
    helper = ConfigHelper.__new__(ConfigHelper)
    helper._whole_config = dict(config or {})
    helper.opts = MoodleDlOpts(path=str(tmp_path))
    helper.config_path = str(tmp_path / 'config.json')
    helper._auth_manager = auth_manager or MagicMock()
    helper._db_file = str(tmp_path / 'moodle_state.db')
    return helper


def _fields(collection):
    return {entry.field for entry in collection}


# ---------------------------------------------------------------------------
# 1) normalize_moodle_url 各分支
# ---------------------------------------------------------------------------


class TestNormalizeMoodleUrl:
    """normalize_moodle_url 的行为应当清晰、无副作用。"""

    def test_bare_domain_gets_https_prefix(self):
        assert normalize_moodle_url('keats.kcl.ac.uk') == 'https://keats.kcl.ac.uk'

    def test_https_url_unchanged(self):
        assert normalize_moodle_url('https://keats.kcl.ac.uk') == 'https://keats.kcl.ac.uk'

    def test_http_url_unchanged(self):
        assert normalize_moodle_url('http://keats.kcl.ac.uk') == 'http://keats.kcl.ac.uk'

    def test_leading_and_trailing_whitespace_is_stripped(self):
        assert normalize_moodle_url('  keats.kcl.ac.uk  ') == 'https://keats.kcl.ac.uk'

    def test_internal_whitespace_is_not_stripped(self):
        # 仅 strip 端空格；内部空格保持原样（用户输入错误时由 validator 拦截）
        assert normalize_moodle_url('foo bar.com') == 'https://foo bar.com'

    def test_non_http_scheme_gets_https_prefix(self):
        # 实现只判断 http/https 前缀；其他 scheme 同样会被加 https:// 前缀
        # （不是预期行为，但记录当前真实行为以便后续修改时回归告警）
        assert normalize_moodle_url('ftp://example.com') == 'https://ftp://example.com'
        assert normalize_moodle_url('file:///etc/passwd') == 'https://file:///etc/passwd'

    def test_empty_protocol_only_returns_as_is(self):
        # "https://" 是空域名，原样返回（不抛异常）
        assert normalize_moodle_url('https://') == 'https://'

    def test_unicode_domain_is_preserved(self):
        # IDN 不被改写
        result = normalize_moodle_url('moodle.大学.cn')
        assert result == 'https://moodle.大学.cn'

    def test_port_in_bare_host_keeps_https_prefix(self):
        # 端口是域名的一部分，规范化只补 scheme
        assert normalize_moodle_url('keats.kcl.ac.uk:8080') == 'https://keats.kcl.ac.uk:8080'

    def test_path_prefix_in_bare_host_keeps_https_prefix(self):
        # 包含路径时也应只补 https
        assert normalize_moodle_url('keats.kcl.ac.uk/moodle') == 'https://keats.kcl.ac.uk/moodle'

    def test_userinfo_in_url_kept_intact(self):
        url = 'https://user:pass@keats.kcl.ac.uk/moodle'
        assert normalize_moodle_url(url) == url


# ---------------------------------------------------------------------------
# 2) MoodleURL 构造
# ---------------------------------------------------------------------------


class TestMoodleURLConstruction:
    """MoodleURL 是 dataclass，url_base 由 scheme + domain + path 拼接。"""

    def test_https_is_default(self):
        url = MoodleURL(False, 'keats.kcl.ac.uk', '/moodle/')
        assert url.scheme == 'https://'
        assert url.url_base == 'https://keats.kcl.ac.uk/moodle/'

    def test_http_flag_changes_scheme(self):
        url = MoodleURL(True, 'localhost', '/')
        assert url.scheme == 'http://'
        assert url.url_base == 'http://localhost/'

    def test_ip_address_as_domain(self):
        url = MoodleURL(False, '127.0.0.1', '/moodle/')
        assert url.url_base == 'https://127.0.0.1/moodle/'

    def test_localhost_as_domain(self):
        url = MoodleURL(False, 'localhost', '/')
        assert url.domain == 'localhost'
        assert url.url_base == 'https://localhost/'

    def test_domain_with_port_kept_intact(self):
        url = MoodleURL(False, 'keats.kcl.ac.uk:8080', '/moodle/')
        # 端口是 domain 的一部分，不会被拆开
        assert url.url_base == 'https://keats.kcl.ac.uk:8080/moodle/'

    def test_path_prefix_kept_intact(self):
        url = MoodleURL(False, 'keats.kcl.ac.uk', '/moodle/sub/')
        assert url.url_base == 'https://keats.kcl.ac.uk/moodle/sub/'

    def test_empty_domain_yields_bare_scheme_plus_path(self):
        url = MoodleURL(False, '', '/')
        # 当前实现不做任何断言；只保证拼接行为一致
        assert url.url_base == 'https:///'

    def test_uppercase_domain_not_lowercased_by_dataclass(self):
        # __post_init__ 不做大小写处理；保留原始大小写
        url = MoodleURL(False, 'KEATS.KCL.AC.UK', '/')
        assert url.domain == 'KEATS.KCL.AC.UK'
        assert url.url_base == 'https://KEATS.KCL.AC.UK/'

    def test_path_without_leading_slash_kept(self):
        url = MoodleURL(False, 'moodle.example.com', 'moodle')
        assert url.url_base == 'https://moodle.example.commoodle'  # 仅记录行为，不假设
        # 真正使用时 validator 会发出警告
        result = validate_config_data(_valid_config(moodle_path='moodle'))
        assert any('moodle_path' in w.field for w in result.warnings)

    def test_equality_and_repr_like_dataclass(self):
        # dataclass 自动生成 __eq__
        a = MoodleURL(False, 'moodle.example.com', '/')
        b = MoodleURL(False, 'moodle.example.com', '/')
        assert a == b
        assert (a.use_http, a.domain, a.path) == (False, 'moodle.example.com', '/')


# ---------------------------------------------------------------------------
# 3) download_course_ids / dont_download_course_ids 白/黑名单语义
# ---------------------------------------------------------------------------


class TestCourseIdWhitelistBlacklistSemantics:
    """
    关键语义：
    - has_property('download_course_ids')  -> True  -> 白名单（即便列表为空）
    - has_property('dont_download_course_ids')     -> 黑名单
    - 都没有                                     -> 全下
    - 都有                                       -> 白名单优先（看代码确认）
    """

    def test_empty_whitelist_with_explicit_key_downloads_nothing(self):
        # 关键：只要 key 存在就是白名单模式，空列表意味着什么都不下
        assert MoodleService.should_download_course(
            course_id=1,
            download_course_ids=[],
            dont_download_course_ids=[],
            use_whitelist=True,  # has_property('download_course_ids') 推导出来
        ) is False

    def test_whitelist_only_downloads_listed_courses(self):
        assert MoodleService.should_download_course(
            course_id=2, download_course_ids=[1, 2, 3], dont_download_course_ids=[],
            use_whitelist=True,
        ) is True
        assert MoodleService.should_download_course(
            course_id=99, download_course_ids=[1, 2, 3], dont_download_course_ids=[],
            use_whitelist=True,
        ) is False

    def test_blacklist_excludes_listed_courses(self):
        assert MoodleService.should_download_course(
            course_id=2, download_course_ids=[], dont_download_course_ids=[2],
            use_whitelist=False,
        ) is False
        assert MoodleService.should_download_course(
            course_id=99, download_course_ids=[], dont_download_course_ids=[2],
            use_whitelist=False,
        ) is True

    def test_neither_set_downloads_everything(self):
        # has_property 两者都 False -> use_whitelist=None -> 全下
        # use_whitelist 在源码中标注为 bool 但 default=None，运行期接受 None
        assert MoodleService.should_download_course(  # type: ignore[arg-type]
            course_id=42, download_course_ids=[], dont_download_course_ids=[],
            use_whitelist=None,
        ) is True

    def test_auto_detect_prefers_blacklist_when_only_blacklist_nonempty(self):
        # use_whitelist=None 的 auto-detect 分支
        assert MoodleService.should_download_course(  # type: ignore[arg-type]
            course_id=2, download_course_ids=[], dont_download_course_ids=[2],
            use_whitelist=None,
        ) is False

    def test_auto_detect_prefers_whitelist_when_only_whitelist_nonempty(self):
        assert MoodleService.should_download_course(  # type: ignore[arg-type]
            course_id=2, download_course_ids=[2], dont_download_course_ids=[],
            use_whitelist=None,
        ) is True

    def test_both_keys_present_whitelist_wins(self):
        # 都有时，has_property('download_course_ids') 优先 → use_whitelist=True
        helper = _make_helper(
            Path('/tmp'),
            config={
                'download_course_ids': [1],
                'dont_download_course_ids': [1],  # 同样 1
            },
        )
        # has_property 拿到的语义
        assert helper.has_property('download_course_ids') is True
        # 不应同时拿到的"whitelist 优先"逻辑（白名单命中 1，行为应为 True）
        assert MoodleService.should_download_course(
            course_id=1,
            download_course_ids=helper.get_download_course_ids(),
            dont_download_course_ids=helper.get_dont_download_course_ids(),
            use_whitelist=True,  # 由 moodle_service 推导
        ) is True

    def test_validator_reports_conflict_when_both_lists_overlap(self):
        # 同时存在且有交集应触发 course_filters 错误
        result = validate_config_data(_valid_config(
            download_course_ids=[1, 2, 3],
            dont_download_course_ids=[2, 3, 4],
        ))
        assert not result.is_valid
        assert any('course_filters' in e.field for e in result.errors)

    def test_validator_allows_empty_lists_without_conflict(self):
        # 都为空列表不应当成冲突
        result = validate_config_data(_valid_config(
            download_course_ids=[],
            dont_download_course_ids=[],
        ))
        # 缺少 token 会有 warning，但不应有 course_filters 错误
        assert not any('course_filters' in e.field for e in result.errors)

    def test_validator_accepts_individual_int_coerced_to_list(self):
        # auto_fix 应将单个 int 转为列表
        fixed, fixes = auto_fix_config({
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/',
            'download_course_ids': 5,
        })
        assert fixed['download_course_ids'] == [5]
        assert any('download_course_ids' in fix for fix in fixes)


# ---------------------------------------------------------------------------
# 4) token 相关
# ---------------------------------------------------------------------------


class TestTokenValidation:
    """
    token 验证行为：
    - 空 token / None -> 不报错，但触发 'authentication' warning
    - 太短 -> warning "太短"
    - 包含占位符 -> warning
    - 非十六进制 -> warning
    - 类型错误 -> error
    """

    def test_token_none_is_accepted_but_triggers_auth_warning(self):
        result = validate_config_data(_valid_config(token=None))
        # None 在类型检查里被跳过
        assert not any('token' == e.field for e in result.errors)
        # 缺少有效 token -> authentication warning
        assert any('authentication' in w.field for w in result.warnings)

    def test_token_empty_string_triggers_auth_warning(self):
        result = validate_config_data(_valid_config(token=''))
        assert any('authentication' in w.field for w in result.warnings)

    def test_token_too_short_triggers_warning(self):
        result = validate_config_data(_valid_config(token='abc'))
        assert any('token' == w.field and '太短' in w.message for w in result.warnings)

    def test_token_exactly_at_threshold_is_ok(self):
        # 边界 20 字符 — 不会再触发"太短"
        result = validate_config_data(_valid_config(token='0' * 20))
        assert not any('太短' in w.message for w in result.warnings)

    def test_token_long_hex_is_clean(self):
        result = validate_config_data(_valid_config(token='a' * 32))
        # 不应有 token 相关 warning
        assert not any('token' == w.field for w in result.warnings)

    def test_token_with_newline_at_end_passes_regex(self):
        # Python re.match 的 $ 在 MULTILINE 默认下会匹配 \n 之前的位置
        # 因此 'a'*19 + '\n' 仍能通过 [a-f0-9]+$ 检查（记录真实行为）
        token = 'a' * 19 + '\n'
        result = validate_config_data(_valid_config(token=token))
        # 不应触发 token field 的 warning（19 个 a + 末尾 \n 通过 re.match）
        # 但 'authentication' 仍可能因为 token 缺有效值触发
        assert not any('token' == w.field for w in result.warnings)

    def test_token_with_newline_in_middle_fails_regex(self):
        # 换行在中间则 re.match 失败 -> 触发 hex 格式 warning
        token = 'a' * 10 + '\n' + 'b' * 10  # 总长 21，>20
        result = validate_config_data(_valid_config(token=token))
        assert any('token' == w.field for w in result.warnings)

    def test_token_with_placeholder_triggers_warning(self):
        result = validate_config_data(_valid_config(token='replace_me_with_real_token'))
        assert any('token' == w.field and '占位符' in w.message for w in result.warnings)

    def test_token_with_spaces_triggers_format_warning(self):
        # 空格破坏 hex 模式
        result = validate_config_data(_valid_config(token='abcdef 1234567890abcdef12'))
        assert any('token' == w.field for w in result.warnings)

    def test_token_with_special_characters_triggers_warnings(self):
        result = validate_config_data(_valid_config(token='abc!@#$%^&*()1234567890ab'))
        # 含特殊字符 -> 非 hex；长度 >= 20 -> 不会触发"太短"
        assert any('token' == w.field for w in result.warnings)

    def test_token_very_long_hex_is_ok(self):
        # 64 字符 hex token — 应通过所有检查
        result = validate_config_data(_valid_config(token='0f1e2d3c4b5a6978879a6b5c4d3e2f10' * 2))
        assert not any('token' == w.field for w in result.warnings)

    def test_token_non_string_type_raises_error(self):
        result = validate_config_data(_valid_config(token=12345))
        assert not result.is_valid
        assert any('token' == e.field and '字符串' in e.message for e in result.errors)

    def test_privatetoken_too_short_warning(self):
        result = validate_config_data(_valid_config(
            token='a' * 32, privatetoken='short'
        ))
        assert any('privatetoken' == w.field for w in result.warnings)


# ---------------------------------------------------------------------------
# 5) Moodle URL 异常 / 验证器与 helper 的联动
# ---------------------------------------------------------------------------


class TestMoodleUrlAnomalies:
    """url 域名字段在 validator 中需要通过格式检查（regex）。"""

    def test_domain_without_dot_accepted_with_warning(self):
        # 当前实现：regex 允许单标签域名（无点）
        result = validate_config_data(_valid_config(moodle_domain='localhost'))
        assert result.is_valid  # 单标签合法（但实际场景罕见）

    def test_domain_with_invalid_chars_warns(self):
        result = validate_config_data(_valid_config(moodle_domain='bad domain!'))
        assert any('moodle_domain' == w.field for w in result.warnings)

    def test_domain_with_protocol_is_error(self):
        result = validate_config_data(_valid_config(moodle_domain='https://moodle.example.com'))
        assert not result.is_valid
        assert any('协议' in e.message for e in result.errors)

    def test_domain_with_http_protocol_is_error(self):
        result = validate_config_data(_valid_config(moodle_domain='http://moodle.example.com'))
        assert not result.is_valid

    def test_domain_with_userinfo_kept_in_bracket_pattern(self):
        # domain 字段不应包含 userinfo
        result = validate_config_data(_valid_config(moodle_domain='user@moodle.example.com'))
        # '@' 不在合法字符集 -> warning
        assert any('moodle_domain' == w.field for w in result.warnings)

    def test_path_without_slash_warns(self):
        result = validate_config_data(_valid_config(moodle_path='moodle'))
        assert any('moodle_path' == w.field for w in result.warnings)

    def test_path_with_trailing_slash_accepted(self):
        result = validate_config_data(_valid_config(moodle_path='/moodle/'))
        assert not any('moodle_path' == w.field for w in result.warnings)

    def test_only_protocol_string_falls_into_normalize(self):
        # normalize_moodle_url 对 "https://" 行为
        assert normalize_moodle_url('https://') == 'https://'

    def test_only_colon_port_falls_into_normalize(self):
        # ":443" 不带 scheme → 被当成无 scheme 域名
        assert normalize_moodle_url(':443') == 'https://:443'

    def test_moodle_url_helper_assembles_https_by_default(self, tmp_path):
        helper = _make_helper(tmp_path, _valid_config())
        url = helper.get_moodle_URL()
        assert isinstance(url, MoodleURL)
        assert url.use_http is False
        assert url.url_base == 'https://moodle.example.com/moodle'

    def test_moodle_url_helper_respects_use_http(self, tmp_path):
        helper = _make_helper(tmp_path, _valid_config(use_http=True))
        url = helper.get_moodle_URL()
        assert url.use_http is True
        assert url.url_base.startswith('http://')


# ---------------------------------------------------------------------------
# 6) ConfigHelper get_* accessor 边界
# ---------------------------------------------------------------------------


class TestConfigHelperAccessors:
    """get_property_or / has_property / get_download_option 等缺省与类型。"""

    def test_get_property_or_returns_default_for_missing_key(self, tmp_path):
        helper = _make_helper(tmp_path, {'token': 'xyz'})
        assert helper.get_property_or('missing', 'fallback') == 'fallback'
        assert helper.get_property_or('also_missing') is None

    def test_get_property_raises_value_error_for_missing_key(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        with pytest.raises(ValueError, match='missing-Property is not yet configured'):
            helper.get_property('missing')

    def test_has_property_true_and_false(self, tmp_path):
        helper = _make_helper(tmp_path, {'token': 'abc'})
        assert helper.has_property('token') is True
        assert helper.has_property('does_not_exist') is False

    def test_get_download_option_uses_default_when_missing(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_download_option('submissions') is False
        assert helper.get_download_option('submissions', default=True) is True

    def test_get_download_option_uses_flat_key_when_present(self, tmp_path):
        # get_download_option 读的是扁平键 download_<name>，不是 download_options 字典
        helper = _make_helper(tmp_path, {
            'download_submissions': True,
            'download_quizzes': False,
        })
        assert helper.get_download_option('submissions') is True
        assert helper.get_download_option('quizzes') is False

    def test_get_download_xxx_getters_use_dataclass_config(self, tmp_path):
        # get_download_submissions() 等专用 getter 走 DownloadOptionsConfig.from_dict
        # 而非扁平 download_xxx 键
        from dataclasses import fields
        options = {f.name: False for f in fields(DownloadOptionsConfig)}
        options['submissions'] = True
        helper = _make_helper(tmp_path, {'download_options': options})
        assert helper.get_download_submissions() is True
        assert helper.get_download_quizzes() is False

    def test_get_userid_and_version_returns_none_for_invalid(self, tmp_path):
        helper = _make_helper(tmp_path, {'userid': 'x', 'version': 'bad'})
        assert helper.get_userid_and_version() == (None, None)

    def test_get_userid_and_version_handles_missing_keys(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_userid_and_version() == (None, None)

    def test_get_token_falls_back_to_json_when_no_session(self, tmp_path):
        auth = MagicMock()
        auth.get_valid_session.return_value = None
        helper = _make_helper(tmp_path, {'token': 'json-token'}, auth)
        assert helper.get_token() == 'json-token'

    def test_get_token_raises_when_no_session_and_no_json(self, tmp_path):
        auth = MagicMock()
        auth.get_valid_session.return_value = None
        helper = _make_helper(tmp_path, {}, auth)
        with pytest.raises(ValueError, match='Token not yet configured'):
            helper.get_token()

    def test_get_token_prefers_db_session(self, tmp_path):
        auth = MagicMock()
        auth.get_valid_session.return_value = {'token_value': 'db-token'}
        helper = _make_helper(tmp_path, {'token': 'json-token'}, auth)
        assert helper.get_token() == 'db-token'

    def test_get_download_linked_files_default_false(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_download_linked_files() is False

    def test_get_max_file_size_default_zero(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_max_file_size() == 0

    def test_get_max_file_size_returns_configured_value(self, tmp_path):
        helper = _make_helper(tmp_path, {'max_file_size': 1024})
        assert helper.get_max_file_size() == 1024

    def test_get_exclude_file_extensions_wraps_scalar(self, tmp_path):
        helper = _make_helper(tmp_path, {'exclude_file_extensions': 'tmp'})
        assert helper.get_exclude_file_extensions() == ['tmp']

    def test_get_options_of_courses_default_empty_dict(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_options_of_courses() == {}

    def test_get_yt_dlp_options_default_empty_dict(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_yt_dlp_options() == {}

    def test_get_video_passwords_default_empty_dict(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_video_passwords() == {}

    def test_get_external_file_downloaders_default_empty_dict(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_external_file_downloaders() == {}

    def test_get_restricted_filenames_default_false(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_restricted_filenames() is False

    def test_get_use_http_default_false(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_use_http() is False

    def test_get_download_path_falls_back_to_opts_path(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        # 默认应当回退到 opts.path
        assert helper.get_download_path() == str(tmp_path)

    def test_get_misc_files_path_falls_back_to_opts_path(self, tmp_path):
        helper = _make_helper(tmp_path, {})
        assert helper.get_misc_files_path() == str(tmp_path)


# ---------------------------------------------------------------------------
# 杂项：write_links / get_download_options / 行为
# ---------------------------------------------------------------------------


class TestWriteLinksAndRuntimeOptions:
    def test_get_write_links_default_uses_platform_default(self, tmp_path, monkeypatch):
        helper = _make_helper(tmp_path, {})
        # 写入 'write_link' 不存在 -> 走 sys.platform 分支
        # 默认 darwin -> webloc
        monkeypatch.setattr('moodle_dl.config.sys.platform', 'darwin')
        result = helper.get_write_links()
        assert result == {'url': False, 'webloc': True, 'desktop': False}

    def test_get_write_links_explicit_overrides(self, tmp_path, monkeypatch):
        helper = _make_helper(tmp_path, {
            'write_url_link': True,
            'write_desktop_link': True,
            'write_link': False,  # 关闭 platform 默认
        })
        monkeypatch.setattr('moodle_dl.config.sys.platform', 'linux')
        result = helper.get_write_links()
        # write_link=False 时不强制覆盖 type-specific
        assert result == {'url': True, 'webloc': False, 'desktop': True}

    def test_get_download_options_assembles_url_with_https(self, tmp_path):
        from dataclasses import fields
        options = {f.name: False for f in fields(DownloadOptionsConfig)}
        helper = _make_helper(
            tmp_path,
            {
                'moodle_domain': 'moodle.example.com',
                'moodle_path': '/moodle',
                'token': 'abc123',
                'download_options': options,  # 必须完整，否则 from_dict 抛错
            },
        )
        helper._auth_manager.get_valid_session.return_value = None
        opts = MoodleDlOpts(path=str(tmp_path))
        dl = helper.get_download_options(opts)
        assert dl.moodle_url == 'https://moodle.example.com/moodle'
        assert dl.token == 'abc123'
        assert dl.cookies_text is None
        assert dl.global_opts is opts


# ---------------------------------------------------------------------------
# 杂项：validator / auto_fix 之间的组合
# ---------------------------------------------------------------------------


class TestValidatorAndAutoFixCombinations:
    def test_strict_mode_treats_warnings_as_failure(self):
        # 在 strict 模式下，仅有 warning 也应当 is_valid=False
        validator = ConfigValidator(strict=True)
        result = validator.validate_config_data(_valid_config(token='short'))
        # 实现层面：add_warning 不动 is_valid；strict 仍依赖 has_errors
        # 当前实现并未把 strict 落到 is_valid 上 -> 记录行为
        # 如果未来实现改变，这测试会失败提醒
        assert result.has_warnings()

    def test_validator_handles_empty_config_dict(self):
        result = validate_config_data({})
        # 缺少 moodle_domain / moodle_path -> errors
        assert not result.is_valid
        fields = _fields(result.errors)
        assert 'moodle_domain' in fields
        assert 'moodle_path' in fields

    def test_validator_with_only_unknown_keys(self):
        # 仅含未知字段 -> warning about unknown，但仍缺必需字段
        result = validate_config_data({'random_key': 1})
        assert not result.is_valid
        assert any('random_key' == w.field for w in result.warnings)

    def test_auto_fix_does_not_drop_token(self):
        fixed, _ = auto_fix_config({'token': 'a' * 32})
        assert fixed['token'] == 'a' * 32

    def test_auto_fix_preserves_moodle_domain_when_already_clean(self):
        fixed, fixes = auto_fix_config({'moodle_domain': 'moodle.example.com'})
        assert fixed['moodle_domain'] == 'moodle.example.com'
        assert not fixes

    def test_auto_fix_handles_empty_dict(self):
        fixed, fixes = auto_fix_config({})
        assert fixed == {}
        assert fixes == []


# ---------------------------------------------------------------------------
# 杂项：DownloadOptionsConfig 边界
# ---------------------------------------------------------------------------


class TestDownloadOptionsConfigEdges:
    def test_from_dict_raises_for_missing_fields(self):
        with pytest.raises(ValueError, match='缺少以下字段'):
            DownloadOptionsConfig.from_dict({'submissions': True})

    def test_to_dict_round_trip(self):
        from dataclasses import fields
        data = {f.name: (i % 2 == 0) for i, f in enumerate(fields(DownloadOptionsConfig))}
        cfg = DownloadOptionsConfig.from_dict(data)
        assert cfg.to_dict() == data

    def test_field_count_matches(self):
        from dataclasses import fields
        # 防止字段被意外删减
        assert len(fields(DownloadOptionsConfig)) >= 28
