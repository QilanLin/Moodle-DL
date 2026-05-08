import json
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from moodle_dl.config import ConfigHelper, DownloadOptionsConfig, normalize_moodle_url
from moodle_dl.types import MoodleDlOpts, MoodleURL


def download_options_dict(**overrides):
    data = {field.name: False for field in fields(DownloadOptionsConfig)}
    data.update(overrides)
    return data


def make_config(tmp_path, config=None, auth_manager=None):
    helper = ConfigHelper.__new__(ConfigHelper)
    helper._whole_config = dict(config or {})
    helper.opts = MoodleDlOpts(path=str(tmp_path))
    helper.config_path = str(tmp_path / 'config.json')
    helper._auth_manager = auth_manager or MagicMock()
    helper._db_file = str(tmp_path / 'moodle_state.db')
    return helper


def test_normalize_moodle_url_adds_https_only_when_scheme_is_missing():
    assert normalize_moodle_url(' keats.kcl.ac.uk ') == 'https://keats.kcl.ac.uk'
    assert normalize_moodle_url('https://keats.kcl.ac.uk') == 'https://keats.kcl.ac.uk'
    assert normalize_moodle_url('http://localhost/moodle') == 'http://localhost/moodle'


def test_download_options_config_requires_complete_explicit_config():
    data = download_options_dict(resources=True, metadata_files=True)

    config = DownloadOptionsConfig.from_dict(data)

    assert config.resources is True
    assert config.metadata_files is True
    assert config.to_dict() == data

    incomplete = dict(data)
    incomplete.pop('resources')
    with pytest.raises(ValueError, match='resources'):
        DownloadOptionsConfig.from_dict(incomplete)


def test_normalize_id_list_accepts_mixed_values_and_scalars():
    assert ConfigHelper.normalize_id_list([1, '2', None, 'bad', 3.0]) == [1, 2, 3]
    assert ConfigHelper.normalize_id_list(None) == []
    assert ConfigHelper.normalize_id_list('42') == [42]


def test_property_helpers_save_and_remove_values(tmp_path):
    helper = make_config(tmp_path)

    assert not helper.is_present()
    assert helper.get_property_or('missing', 'fallback') == 'fallback'
    assert not helper.has_property('token')

    with pytest.raises(ValueError, match='token-Property'):
        helper.get_property('token')

    helper.set_property('token', 'abc', ensure_complete=False)
    assert helper.is_present()
    assert helper.has_property('token')
    assert helper.get_property('token') == 'abc'
    assert json.loads(Path(helper.config_path).read_text(encoding='utf-8')) == {'token': 'abc'}

    helper.remove_property('token')
    assert not helper.has_property('token')
    assert json.loads(Path(helper.config_path).read_text(encoding='utf-8')) == {'download_options': {}}


def test_save_can_skip_or_create_download_options_container(tmp_path):
    helper = make_config(tmp_path, {'moodle_domain': 'example.com'})

    helper._save(ensure_complete=False)
    assert json.loads(Path(helper.config_path).read_text(encoding='utf-8')) == {'moodle_domain': 'example.com'}

    helper._save(ensure_complete=True)
    saved = json.loads(Path(helper.config_path).read_text(encoding='utf-8'))
    assert saved == {'moodle_domain': 'example.com', 'download_options': {}}


def test_load_reads_json_and_reports_missing_or_invalid_config(tmp_path):
    helper = make_config(tmp_path)

    with pytest.raises(ConfigHelper.NoConfigError, match='could not be loaded'):
        helper.load(validate=False)

    Path(helper.config_path).write_text('{bad json', encoding='utf-8')
    with pytest.raises(ConfigHelper.NoConfigError, match='JSON'):
        helper.load(validate=False)

    Path(helper.config_path).write_text('{"token": "abc"}', encoding='utf-8')
    helper.load(validate=False)
    assert helper._whole_config == {'token': 'abc'}


def test_validate_uses_validator_result(monkeypatch, tmp_path, capsys):
    helper = make_config(tmp_path, {'token': 'abc'})
    result = MagicMock()
    result.has_errors.return_value = True
    result.has_warnings.return_value = False
    result.get_summary.return_value = 'broken config'
    validator = MagicMock()
    validator.validate_config_data.return_value = result
    monkeypatch.setattr('moodle_dl.config_validator.ConfigValidator', MagicMock(return_value=validator))

    assert helper.validate() is False
    assert 'broken config' in capsys.readouterr().out
    validator.validate_config_data.assert_called_once_with({'token': 'abc'})


def test_download_option_getters_use_dataclass_config(tmp_path):
    data = download_options_dict(
        submissions=True,
        descriptions=True,
        links_in_descriptions=True,
        resources=True,
        calendars=True,
        metadata_files=True,
    )
    helper = make_config(tmp_path, {'download_options': data})

    assert helper.get_download_submissions() is True
    assert helper.get_download_descriptions() is True
    assert helper.get_download_links_in_descriptions() is True
    assert helper.get_download_databases() is False
    assert helper.get_download_forums() is False
    assert helper.get_download_quizzes() is False
    assert helper.get_download_lessons() is False
    assert helper.get_download_workshops() is False
    assert helper.get_download_books() is False
    assert helper.get_download_bigbluebuttonbns() is False
    assert helper.get_download_wikis() is False
    assert helper.get_download_glossaries() is False
    assert helper.get_download_h5pactivities() is False
    assert helper.get_download_h5p_attempts() is False
    assert helper.get_download_imscps() is False
    assert helper.get_download_scorms() is False
    assert helper.get_download_scorm_scos() is False
    assert helper.get_download_scorm_attempts() is False
    assert helper.get_download_subsections() is False
    assert helper.get_download_qbanks() is False
    assert helper.get_download_resources() is True
    assert helper.get_download_urls() is False
    assert helper.get_download_labels() is False
    assert helper.get_download_chats() is False
    assert helper.get_download_choices() is False
    assert helper.get_download_feedbacks() is False
    assert helper.get_download_surveys() is False
    assert helper.get_download_ltis() is True
    assert helper.get_download_calendars() is True
    assert helper.get_download_metadata_files() is True


def test_course_id_getters_normalize_lists(tmp_path):
    helper = make_config(
        tmp_path,
        {
            'download_course_ids': ['1', None, 'bad', 2],
            'download_public_course_ids': '3',
            'dont_download_course_ids': ['4', 5.0],
        },
    )

    assert helper.get_download_course_ids() == [1, 2]
    assert helper.get_download_public_course_ids() == [3]
    assert helper.get_dont_download_course_ids() == [4, 5]
    assert helper.get_manually_specified_course_ids() == []


def test_set_manually_specified_course_ids_validates_and_sorts(tmp_path):
    helper = make_config(tmp_path)
    helper._save = MagicMock()

    helper.set_manually_specified_course_ids([5, 1, 5])

    assert helper._whole_config['manually_specified_course_ids'] == [1, 5]
    helper._save.assert_called_once()

    with pytest.raises(ValueError):
        helper.set_manually_specified_course_ids('5')

    with pytest.raises(ValueError, match='ID'):
        helper.set_manually_specified_course_ids([1, '2'])


def test_token_getters_prefer_auth_manager_sessions_and_fallback_to_json(tmp_path):
    auth_manager = MagicMock()
    auth_manager.get_valid_session.return_value = {
        'token_value': 'db-token',
        'private_token_value': 'db-private',
    }
    helper = make_config(tmp_path, {'token': 'json-token', 'privatetoken': 'json-private'}, auth_manager)

    assert helper.get_token() == 'db-token'
    assert helper.get_privatetoken() == 'db-private'

    auth_manager.get_valid_session.return_value = None
    assert helper.get_token() == 'json-token'
    assert helper.get_privatetoken() == 'json-private'

    helper._whole_config.clear()
    with pytest.raises(ValueError, match='Token not yet configured'):
        helper.get_token()
    assert helper.get_privatetoken() is None


def test_cookie_text_is_rendered_from_cookie_batch_session(tmp_path):
    auth_manager = MagicMock()
    auth_manager.get_valid_session.return_value = {'session_id': 'session-1'}
    auth_manager.get_session_cookies.return_value = [
        {
            'domain': 'example.com',
            'path': '/',
            'secure': 1,
            'expires': -1,
            'name': 'sid',
            'value': 'abc',
        },
        {
            'domain': 'localhost',
            'path': '/moodle',
            'secure': 0,
            'expires': 'bad',
            'name': 'local',
            'value': 'xyz',
        },
        {'domain': 'ignored.example.com', 'name': '', 'value': 'missing-name'},
    ]
    helper = make_config(tmp_path, auth_manager=auth_manager)

    cookie_text = helper.get_cookies_text()

    assert '# Netscape HTTP Cookie File' in cookie_text
    assert '.example.com\tTRUE\t/\tTRUE\t0\tsid\tabc' in cookie_text
    assert 'localhost\tFALSE\t/moodle\tFALSE\t0\tlocal\txyz' in cookie_text
    assert 'ignored.example.com' not in cookie_text


def test_cookie_text_returns_none_when_database_has_no_cookies(tmp_path):
    auth_manager = MagicMock()
    auth_manager.get_valid_session.return_value = None
    helper = make_config(tmp_path, auth_manager=auth_manager)

    assert helper.get_cookies_text() is None

    auth_manager.get_valid_session.side_effect = RuntimeError('database locked')
    assert helper.get_cookies_text() is None


def test_moodle_url_and_path_getters(tmp_path):
    helper = make_config(
        tmp_path,
        {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/course/',
            'use_http': True,
            'download_path': str(tmp_path / 'downloads'),
            'misc_files_path': str(tmp_path / 'misc'),
        },
    )

    moodle_url = helper.get_moodle_URL()

    assert moodle_url == MoodleURL(True, 'moodle.example.com', '/course/')
    assert moodle_url.url_base == 'http://moodle.example.com/course/'
    assert helper.get_moodle_domain() == 'moodle.example.com'
    assert helper.get_moodle_path() == '/course/'
    assert helper.get_download_path() == str(tmp_path / 'downloads')
    assert helper.get_misc_files_path() == str(tmp_path / 'misc')
    assert helper.get_auth_manager() is helper._auth_manager


def test_misc_getters_return_defaults_and_configured_values(tmp_path, monkeypatch):
    helper = make_config(
        tmp_path,
        {
            'download_linked_files': True,
            'download_domains_whitelist': ['example.com'],
            'download_domains_blacklist': ['blocked.example.com'],
            'yt_dlp_options': {'format': 'best'},
            'video_passwords': {'video': 'secret'},
            'external_file_downloaders': {'pdf': 'aria2c'},
            'exclude_file_extensions': 'tmp',
            'max_file_size': 1234,
            'download_also_with_cookie': True,
            'restricted_filenames': True,
            'do_not_ask_to_save_userid_and_version': True,
            'options_of_courses': {'1': {'overwrite_name_with': 'Course'}},
        },
    )

    assert helper.get_download_linked_files() is True
    assert helper.get_download_domains_whitelist() == ['example.com']
    assert helper.get_download_domains_blacklist() == ['blocked.example.com']
    assert helper.get_yt_dlp_options() == {'format': 'best'}
    assert helper.get_video_passwords() == {'video': 'secret'}
    assert helper.get_external_file_downloaders() == {'pdf': 'aria2c'}
    assert helper.get_exclude_file_extensions() == ['tmp']
    assert helper.get_max_file_size() == 1234
    assert helper.get_download_also_with_cookie() is True
    assert helper.get_restricted_filenames() is True
    assert helper.get_do_not_ask_to_save_userid_and_version() is True
    assert helper.get_options_of_courses() == {'1': {'overwrite_name_with': 'Course'}}
    assert helper.get_download_option('submissions', default=True) is True

    monkeypatch.setattr('moodle_dl.config.sys.platform', 'darwin')
    assert helper.get_write_links() == {'url': False, 'webloc': True, 'desktop': False}

    helper._whole_config.update({'write_link': False, 'write_url_link': True, 'write_desktop_link': True})
    assert helper.get_write_links() == {'url': True, 'webloc': False, 'desktop': True}


def test_get_userid_and_version_handles_valid_and_invalid_values(tmp_path):
    helper = make_config(tmp_path, {'userid': '123', 'version': '2024010100'})
    assert helper.get_userid_and_version() == ('123', 2024010100)

    helper._whole_config['version'] = 'not-a-number'
    assert helper.get_userid_and_version() == (None, None)


def test_get_download_options_assembles_runtime_options(tmp_path):
    helper = make_config(
        tmp_path,
        {
            'download_options': download_options_dict(metadata_files=True),
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/',
            'token': 'token',
            'download_linked_files': True,
            'download_domains_whitelist': ['allowed.example.com'],
            'download_domains_blacklist': ['blocked.example.com'],
            'yt_dlp_options': {'format': 'best'},
            'video_passwords': {'video': 'secret'},
            'external_file_downloaders': {'pdf': 'aria2c'},
            'restricted_filenames': True,
            'download_path': str(tmp_path / 'downloads'),
        },
    )
    helper._auth_manager.get_valid_session.return_value = None
    opts = MoodleDlOpts(path=str(tmp_path), verbose=True)

    download_options = helper.get_download_options(opts)

    assert download_options.token == 'token'
    assert download_options.moodle_url == 'https://moodle.example.com/'
    assert download_options.download_linked_files is True
    assert download_options.download_domains_whitelist == ['allowed.example.com']
    assert download_options.download_domains_blacklist == ['blocked.example.com']
    assert download_options.cookies_text is None
    assert download_options.yt_dlp_options == {'format': 'best'}
    assert download_options.video_passwords == {'video': 'secret'}
    assert download_options.external_file_downloaders == {'pdf': 'aria2c'}
    assert download_options.restricted_filenames is True
    assert download_options.write_links['url'] in (True, False)
    assert download_options.download_path == str(tmp_path / 'downloads')
    assert download_options.download_metadata_files is True
    assert download_options.global_opts is opts


def test_set_moodle_url_updates_http_flag_only_when_needed(tmp_path):
    helper = make_config(tmp_path)
    helper.set_property = MagicMock(side_effect=lambda key, value: helper._whole_config.update({key: value}))

    helper.set_moodle_URL(MoodleURL(False, 'moodle.example.com', '/moodle/'))

    helper.set_property.assert_any_call('moodle_domain', 'moodle.example.com')
    helper.set_property.assert_any_call('moodle_path', '/moodle/')
    assert 'use_http' not in helper._whole_config

    helper._whole_config['use_http'] = True
    helper.set_moodle_URL(MoodleURL(False, 'moodle.example.com', '/'))
    helper.set_property.assert_any_call('use_http', False)

    helper.set_moodle_URL(MoodleURL(True, 'moodle.example.com', '/'))
    helper.set_property.assert_any_call('use_http', True)


def test_set_tokens_creates_or_refreshes_auth_session_and_updates_json(tmp_path):
    auth_manager = MagicMock()
    auth_manager.get_valid_session.return_value = None
    helper = make_config(tmp_path, auth_manager=auth_manager)
    helper.set_property = MagicMock()

    helper.set_tokens('token-1', 'private-1')

    auth_manager.create_session.assert_called_once_with(
        session_type='token',
        source='api_login',
        token='token-1',
        private_token='private-1',
    )
    helper.set_property.assert_any_call('token', 'token-1')
    helper.set_property.assert_any_call('privatetoken', 'private-1')

    auth_manager.reset_mock()
    helper.set_property.reset_mock()
    auth_manager.get_valid_session.return_value = {'session_id': 'old-session'}

    helper.set_tokens('token-2', None)

    auth_manager.refresh_session.assert_called_once_with(
        old_session_id='old-session',
        new_token='token-2',
        new_private_token=None,
    )
    helper.set_property.assert_called_once_with('token', 'token-2')
