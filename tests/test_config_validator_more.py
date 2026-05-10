# -*- coding: utf-8 -*-
import json
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from moodle_dl.config_validator import ConfigValidator, auto_fix_config, validate_config_data


def _valid_config(**overrides):
    config = {
        'moodle_domain': 'moodle.example.com',
        'moodle_path': '/moodle',
        'token': '0123456789abcdef0123456789abcdef',
    }
    config.update(overrides)
    return config


def _fields(collection):
    return {entry.field for entry in collection}


def test_validate_config_file_rejects_directory_and_unreadable_file(tmp_path):
    validator = ConfigValidator()
    directory_result = validator.validate_config_file(str(tmp_path))

    assert not directory_result.is_valid
    assert directory_result.errors[0].field == 'config_file'

    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(_valid_config()), encoding='utf-8')

    with patch('moodle_dl.config_validator.os.access', return_value=False):
        unreadable_result = validator.validate_config_file(str(config_path))

    assert not unreadable_result.is_valid
    assert unreadable_result.errors[0].field == 'config_file'
    assert unreadable_result.errors[0].suggestion == '检查文件权限'


def test_validate_config_file_reports_generic_read_errors(tmp_path):
    config_path = tmp_path / 'config.json'
    config_path.write_text('{}', encoding='utf-8')

    with patch('builtins.open', mock_open()) as opened:
        opened.side_effect = OSError('disk unavailable')
        result = ConfigValidator().validate_config_file(str(config_path))

    assert not result.is_valid
    assert result.errors[0].field == 'file_read'
    assert 'disk unavailable' in result.errors[0].message


def test_validate_config_data_reports_all_scalar_and_list_type_errors():
    config = _valid_config(
        moodle_path=123,
        token=object(),
        privatetoken=object(),
        courses_to_filter='course',
        download_public_course_ids='public',
        dont_download_course_ids='blocked',
        restricted_filenames='yes',
        include_noncourse_files='no',
        download_options='not an object',
        notifications='not an object',
    )

    result = ConfigValidator().validate_config_data(config)

    assert not result.is_valid
    fields = _fields(result.errors)
    assert {
        'moodle_path',
        'token',
        'privatetoken',
        'courses_to_filter',
        'download_public_course_ids',
        'dont_download_course_ids',
        'restricted_filenames',
        'include_noncourse_files',
        'download_options',
        'notifications',
    }.issubset(fields)


def test_validate_config_data_reports_range_and_logic_warnings():
    config = _valid_config(
        moodle_domain='bad domain!',
        moodle_path='course',
        token='not-hex-token-but-long-enough',
        privatetoken='short',
        download_course_ids=[0, -1],
        download_public_course_ids=['2'],
        dont_download_course_ids=[None],
        download_options={field: False for field in ConfigValidator.get_download_options_fields()},
    )

    result = ConfigValidator().validate_config_data(config)

    assert not result.is_valid
    error_fields = _fields(result.errors)
    assert {
        'download_course_ids[0]',
        'download_course_ids[1]',
        'download_public_course_ids[0]',
        'dont_download_course_ids[0]',
    }.issubset(error_fields)
    warning_fields = _fields(result.warnings)
    assert {'moodle_domain', 'moodle_path', 'token', 'privatetoken', 'download_options'}.issubset(
        warning_fields
    )


def test_validate_config_data_reports_notification_and_security_issues():
    config = _valid_config(
        token='replace_me_with_real_token_012345',
        notifications={
            'mail': {
                'enabled': True,
                'server': 'smtp.example.com',
                'sender': 'sender@example.com',
                'receiver': 'receiver@example.com',
                'password': 'password',
            },
            'telegram': {
                'enabled': True,
                'token': 'bot-token',
            },
            'xmpp': {
                'enabled': True,
                'sender': '',
                'password': '',
                'receiver': '',
            },
        },
    )

    result = ConfigValidator().validate_config_data(config)

    assert not result.is_valid
    assert {
        'notifications.telegram.chat_id',
        'notifications.xmpp.sender',
        'notifications.xmpp.password',
        'notifications.xmpp.receiver',
    }.issubset(_fields(result.errors))
    assert {'token', 'notifications.mail.password'}.issubset(_fields(result.warnings))


def test_convenience_validate_config_data_accepts_strict_flag():
    result = validate_config_data(_valid_config(), strict=True)

    assert result.is_valid


def test_auto_fix_config_coerces_falsy_and_truthy_non_list_fields():
    fixed_config, fixes = auto_fix_config({
        'download_course_ids': 42,
        'download_public_course_ids': '',
        'dont_download_course_ids': 42,
        'courses_to_filter': 'science',
        'restricted_filenames': 'yes',
    })

    assert fixed_config['download_course_ids'] == [42]
    assert fixed_config['download_public_course_ids'] == []
    assert fixed_config['dont_download_course_ids'] == []
    assert fixed_config['courses_to_filter'] == ['science']
    assert fixed_config['restricted_filenames'] is True
    assert any('download_course_ids' in fix for fix in fixes)
    assert any('download_public_course_ids' in fix for fix in fixes)
    assert any('restricted_filenames' in fix for fix in fixes)


@pytest.mark.parametrize(
    'bad_value',
    [
        {'a': 1},
        [{'nested': 'list'}],
    ],
)
def test_auto_fix_config_can_reset_unhashable_course_filters(bad_value):
    fixed_config, fixes = auto_fix_config({
        'download_course_ids': bad_value,
        'dont_download_course_ids': [1],
    })

    assert fixed_config['download_course_ids'] == []
    assert any('download_course_ids' in fix for fix in fixes)
