# -*- coding: utf-8 -*-
import os
from unittest.mock import MagicMock

import pytest
import readchar

from moodle_dl import utils as utils_module
from moodle_dl.utils import Cutie


@pytest.fixture(autouse=True)
def stable_terminal(monkeypatch):
    monkeypatch.setattr(utils_module.shutil, 'get_terminal_size', lambda: os.terminal_size((60, 8)))


def test_get_number_reprompts_for_invalid_non_integer_and_range_errors(monkeypatch):
    inputs = iter(['bad', '1.5', '0', '9', '4'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(inputs))

    assert Cutie.get_number('Pick', min_value=2, max_value=5, allow_float=False) == 4


def test_get_number_allows_float_values(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda prompt: '3.5')

    assert Cutie.get_number('Pick', allow_float=True) == 3.5


def test_secure_input_delegates_to_getpass(monkeypatch):
    getpass = MagicMock(return_value='secret')
    monkeypatch.setattr(utils_module.getpass, 'getpass', getpass)

    assert Cutie.secure_input('Password') == 'secret'
    getpass.assert_called_once_with('Password ')


def test_select_moves_around_caption_rows_and_confirms(monkeypatch):
    keys = iter([readchar.key.UP, readchar.key.DOWN, readchar.key.DOWN, readchar.key.ENTER])
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: next(keys))

    selected = Cutie.select(
        ['Header', 'First', 'Second'],
        caption_indices=[0],
        selected_index=1,
        confirm_on_select=False,
    )

    assert selected == 2


def test_select_can_confirm_with_space_and_raise_keyboard_interrupt(monkeypatch):
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: readchar.key.SPACE)

    assert Cutie.select(['Only'], selected_index=0, confirm_on_select=True) == 0

    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: readchar.key.CTRL_C)
    with pytest.raises(KeyboardInterrupt):
        Cutie.select(['Only'])


def test_select_multiple_toggles_items_selects_all_and_honors_confirm_constraints(monkeypatch):
    keys = iter([
        readchar.key.ENTER,
        readchar.key.SPACE,
        readchar.key.DOWN,
        readchar.key.SPACE,
        readchar.key.CTRL_A,
        readchar.key.ENTER,
    ])
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: next(keys))

    selected = Cutie.select_multiple(
        ['Header', 'First', 'Second'],
        caption_indices=[0],
        cursor_index=1,
        minimal_count=1,
        maximal_count=3,
        hide_confirm=True,
    )

    assert selected == [1, 2, 0]


def test_select_multiple_can_untick_and_raise_keyboard_interrupt(monkeypatch):
    keys = iter([readchar.key.SPACE, readchar.key.ENTER])
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: next(keys))

    assert Cutie.select_multiple(['First'], ticked_indices=[0], minimal_count=0, hide_confirm=True) == []

    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: readchar.key.CTRL_D)
    with pytest.raises(KeyboardInterrupt):
        Cutie.select_multiple(['First'])


def test_prompt_yes_or_no_supports_default_navigation_typing_tab_and_delete(monkeypatch):
    keys = iter([readchar.key.ENTER])
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: next(keys))
    assert Cutie.prompt_yes_or_no('Continue?', default_is_yes=True) is True

    keys = iter([readchar.key.DOWN, readchar.key.ENTER])
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: next(keys))
    assert Cutie.prompt_yes_or_no('Continue?') is True

    keys = iter(['n', readchar.key.ENTER])
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: next(keys))
    assert Cutie.prompt_yes_or_no('Continue?') is False

    keys = iter(['x', readchar.key.BACKSPACE, 'y', '\t', readchar.key.ENTER])
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: next(keys))
    assert Cutie.prompt_yes_or_no('Continue?', char_prompt=False) is True


def test_prompt_yes_or_no_can_be_interrupted(monkeypatch):
    monkeypatch.setattr(utils_module.readchar, 'readkey', lambda: readchar.key.CTRL_C)

    with pytest.raises(KeyboardInterrupt):
        Cutie.prompt_yes_or_no('Continue?')
