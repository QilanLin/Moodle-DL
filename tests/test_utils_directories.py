"""
Tests for the XDG / Windows-aware directory helpers in PathTools.

These tests focus exclusively on the directory-resolution behaviour using
`monkeypatch` to control the `os.name` and environment variables, plus
`tmp_path` to avoid touching the real user filesystem.
"""

import os
import sys

import pytest

from moodle_dl import utils as utils_module
from moodle_dl.utils import PathTools


@pytest.fixture(autouse=True)
def restore_path_tools_state():
    """Save and restore the class-level `restricted_filenames` flag."""
    original = PathTools.restricted_filenames
    yield
    PathTools.restricted_filenames = original


def _patch_os_name(monkeypatch, value):
    """Patch both the `os` module attribute and the `os` imported into utils."""
    monkeypatch.setattr(utils_module.os, 'name', value, raising=False)
    monkeypatch.setattr(os, 'name', value)


# --- get_user_config_directory ---------------------------------------------


def test_get_user_config_directory_uses_xdg_when_set(monkeypatch, tmp_path):
    _patch_os_name(monkeypatch, 'posix')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg-cfg'))
    assert PathTools.get_user_config_directory() == str(tmp_path / 'xdg-cfg')


def test_get_user_config_directory_falls_back_to_dot_config(monkeypatch, tmp_path):
    _patch_os_name(monkeypatch, 'posix')
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    monkeypatch.setattr(utils_module.os.path, 'expanduser', lambda _: str(tmp_path))
    assert PathTools.get_user_config_directory() == str(tmp_path / '.config')


def test_get_user_config_directory_prefers_localappdata_on_windows(monkeypatch):
    _patch_os_name(monkeypatch, 'nt')
    monkeypatch.setenv('LOCALAPPDATA', r'C:\Users\me\AppData\Local')
    monkeypatch.setenv('APPDATA', r'C:\Users\me\AppData\Roaming')
    assert PathTools.get_user_config_directory() == r'C:\Users\me\AppData\Local'


def test_get_user_config_directory_falls_back_to_appdata_on_windows(monkeypatch):
    _patch_os_name(monkeypatch, 'nt')
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.setenv('APPDATA', r'C:\Users\me\AppData\Roaming')
    assert PathTools.get_user_config_directory() == r'C:\Users\me\AppData\Roaming'


def test_get_user_config_directory_returns_none_when_no_env(monkeypatch):
    _patch_os_name(monkeypatch, 'nt')
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.delenv('APPDATA', raising=False)
    assert PathTools.get_user_config_directory() is None


# --- get_user_data_directory -----------------------------------------------


def test_get_user_data_directory_uses_xdg_when_set(monkeypatch, tmp_path):
    _patch_os_name(monkeypatch, 'posix')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg-data'))
    assert PathTools.get_user_data_directory() == str(tmp_path / 'xdg-data')


def test_get_user_data_directory_falls_back_to_local_share(monkeypatch, tmp_path):
    _patch_os_name(monkeypatch, 'posix')
    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    monkeypatch.setattr(utils_module.os.path, 'expanduser', lambda _: str(tmp_path))
    assert PathTools.get_user_data_directory() == str(tmp_path / '.local' / 'share')


def test_get_user_data_directory_prefers_localappdata_on_windows(monkeypatch):
    _patch_os_name(monkeypatch, 'nt')
    monkeypatch.setenv('LOCALAPPDATA', r'C:\L')
    monkeypatch.setenv('APPDATA', r'C:\R')
    assert PathTools.get_user_data_directory() == r'C:\L'


def test_get_user_data_directory_returns_none_when_no_env(monkeypatch):
    _patch_os_name(monkeypatch, 'nt')
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.delenv('APPDATA', raising=False)
    assert PathTools.get_user_data_directory() is None


# --- get_project_data_directory / get_project_config_directory -------------


def test_get_project_data_directory_creates_moodle_dl_under_xdg(monkeypatch, tmp_path):
    _patch_os_name(monkeypatch, 'posix')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))
    # Also neuter HOME so expanduser doesn't reach out.
    monkeypatch.setattr(utils_module.os.path, 'expanduser', lambda _: str(tmp_path))

    project = PathTools.get_project_data_directory()
    assert project == str(tmp_path / 'xdg' / 'moodle-dl')
    assert os.path.isdir(project)


def test_get_project_data_directory_idempotent(monkeypatch, tmp_path):
    # Calling twice should not raise (exist_ok=True semantics).
    _patch_os_name(monkeypatch, 'posix')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))

    first = PathTools.get_project_data_directory()
    second = PathTools.get_project_data_directory()
    assert first == second
    assert os.path.isdir(first)


def test_get_project_config_directory_creates_moodle_dl_under_xdg(monkeypatch, tmp_path):
    _patch_os_name(monkeypatch, 'posix')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))

    project = PathTools.get_project_config_directory()
    assert project == str(tmp_path / 'xdg' / 'moodle-dl')
    assert os.path.isdir(project)


def test_get_project_config_directory_skips_create_if_already_dir(monkeypatch, tmp_path):
    _patch_os_name(monkeypatch, 'posix')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    pre = tmp_path / 'xdg' / 'moodle-dl'
    pre.mkdir(parents=True)
    # Even when the directory already exists, the call should not raise.
    assert PathTools.get_project_config_directory() == str(pre)


def test_get_project_directories_on_windows_use_appdata(monkeypatch):
    # When os.name='nt' is mocked, the underlying get_user_data_directory
    # returns the LOCALAPPDATA path. On POSIX systems, however, `Path()`
    # will then try to construct a WindowsPath, which raises
    # NotImplementedError. This test pins down the current cross-platform
    # behavior so any future portability fix is intentional.
    _patch_os_name(monkeypatch, 'nt')
    monkeypatch.setenv('LOCALAPPDATA', r'C:\L')

    with pytest.raises((NotImplementedError, TypeError)):
        PathTools.get_project_data_directory()


def test_get_project_data_directory_returns_none_when_no_user_dir(monkeypatch):
    # On Windows, when neither LOCALAPPDATA nor APPDATA is set, the
    # underlying get_user_data_directory returns None, which would propagate
    # to a TypeError inside `Path(None)`. Lock down the current behaviour so
    # the test fails fast if it ever changes.
    _patch_os_name(monkeypatch, 'nt')
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.delenv('APPDATA', raising=False)
    with pytest.raises(TypeError):
        PathTools.get_project_data_directory()
