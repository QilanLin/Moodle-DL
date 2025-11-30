import pytest

from moodle_dl.utils import get_nested


def test_get_nested_with_none_intermediate_returns_default():
    data = {"a": None}

    assert get_nested(data, "a.b", default="fallback") == "fallback"


def test_get_nested_with_list_intermediate_returns_default_on_index_error():
    data = {"a": []}

    assert get_nested(data, "a.0", default="missing") == "missing"


def test_get_nested_with_missing_key_returns_default():
    data = {"a": {"b": 1}}

    assert get_nested(data, "a.c", default=0) == 0


def test_get_nested_with_none_root_returns_default():
    assert get_nested(None, "a.b", default={}) == {}


def test_get_nested_with_list_intermediate_type_error_returns_default():
    data = {"a": [1, 2, 3]}

    assert get_nested(data, "a.b", default="absent") == "absent"
