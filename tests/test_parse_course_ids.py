# -*- coding: utf-8 -*-
"""
Tests for ConfigWizard._parse_course_ids.

The parser must support the following input shapes (the realistic
"copy-paste from a browser" cases):

1. Single URL:
   https://keats.kcl.ac.uk/course/view.php?id=86124
2. URL without protocol:
   keats.kcl.ac.uk/course/view.php?id=86124
3. Two full URLs joined by ", " (comma + space):
   https://keats.kcl.ac.uk/course/view.php?id=86124, https://keats.kcl.ac.uk/course/view.php?id=86123
4. Two full URLs joined by " " (space only):
   https://keats.kcl.ac.uk/course/view.php?id=86124 https://keats.kcl.ac.uk/course/view.php?id=86123
5. Mixed: one URL + one protocol-less URL + two bare IDs, comma-separated:
   https://keats.kcl.ac.uk/course/view.php?id=86124, keats.kcl.ac.uk/course/view.php?id=86123, 86246, 86122
6. Mixed: one URL + one protocol-less URL + two bare IDs, all spaces:
   https://keats.kcl.ac.uk/course/view.php?id=86124 keats.kcl.ac.uk/course/view.php?id=86123 86246 86122

Plus the previously-supported cases (regression guard):

- Single bare ID: 86124
- Space-separated bare IDs: 86124 86123
- Comma-separated bare IDs: 86124,86123
- Empty / whitespace-only: []
- Garbage input: []
- Mixed bare IDs with extra whitespace and trailing comma: tolerantly parsed

Implementation note: _parse_course_ids is a @staticmethod, so it can be
invoked without instantiating ConfigWizard.
"""

import pytest

from moodle_dl.cli.config_wizard import ConfigWizard


# ---------------------------------------------------------------------------
# 1. New: multiple URLs in a single input
# ---------------------------------------------------------------------------


def test_two_full_urls_joined_by_comma_space():
    """https://...?id=A, https://...?id=B → [A, B]"""
    text = 'https://keats.kcl.ac.uk/course/view.php?id=86124, https://keats.kcl.ac.uk/course/view.php?id=86123'
    assert ConfigWizard._parse_course_ids(text) == [86124, 86123]


def test_two_full_urls_joined_by_space_only():
    """https://...?id=A https://...?id=B → [A, B]"""
    text = 'https://keats.kcl.ac.uk/course/view.php?id=86124 https://keats.kcl.ac.uk/course/view.php?id=86123'
    assert ConfigWizard._parse_course_ids(text) == [86124, 86123]


def test_full_url_then_protocol_less_url_then_two_bare_ids_comma_separated():
    """Mixed input with comma+space separators must produce all four IDs."""
    text = (
        'https://keats.kcl.ac.uk/course/view.php?id=86124, '
        'keats.kcl.ac.uk/course/view.php?id=86123, 86246, 86122'
    )
    assert ConfigWizard._parse_course_ids(text) == [86124, 86123, 86246, 86122]


def test_full_url_then_protocol_less_url_then_two_bare_ids_space_separated():
    """Mixed input with space separators must produce all four IDs."""
    text = (
        'https://keats.kcl.ac.uk/course/view.php?id=86124 '
        'keats.kcl.ac.uk/course/view.php?id=86123 86246 86122'
    )
    assert ConfigWizard._parse_course_ids(text) == [86124, 86123, 86246, 86122]


def test_three_full_urls_joined_by_comma_space():
    """Three full URLs with comma+space must produce all three IDs."""
    text = (
        'https://keats.kcl.ac.uk/course/view.php?id=86124, '
        'https://keats.kcl.ac.uk/course/view.php?id=86123, '
        'https://keats.kcl.ac.uk/course/view.php?id=86246'
    )
    assert ConfigWizard._parse_course_ids(text) == [86124, 86123, 86246]


def test_amp_separator_in_url_still_extracts_id():
    """&id= (e.g. when URL has additional query params) must also be found."""
    text = 'https://keats.kcl.ac.uk/course/view.php?id=86124&foo=bar'
    assert ConfigWizard._parse_course_ids(text) == [86124]


def test_url_with_extra_query_params_and_more_urls():
    """URL with &id= plus other params, followed by another URL."""
    text = (
        'https://keats.kcl.ac.uk/course/view.php?id=86124&foo=bar, '
        'https://keats.kcl.ac.uk/course/view.php?id=86123'
    )
    assert ConfigWizard._parse_course_ids(text) == [86124, 86123]


def test_url_mixed_with_bare_id_space_separated():
    """One URL + one bare ID, separated by space, must produce both."""
    text = 'https://keats.kcl.ac.uk/course/view.php?id=86124 86246'
    assert ConfigWizard._parse_course_ids(text) == [86124, 86246]


# ---------------------------------------------------------------------------
# 2. Regression: previously-supported cases
# ---------------------------------------------------------------------------


def test_single_bare_id():
    assert ConfigWizard._parse_course_ids('86124') == [86124]


def test_space_separated_bare_ids():
    assert ConfigWizard._parse_course_ids('86124 86123 86246') == [86124, 86123, 86246]


def test_comma_separated_bare_ids():
    assert ConfigWizard._parse_course_ids('86124,86123,86246') == [86124, 86123, 86246]


def test_comma_space_separated_bare_ids():
    assert ConfigWizard._parse_course_ids('86124, 86123, 86246') == [86124, 86123, 86246]


def test_single_full_url():
    assert ConfigWizard._parse_course_ids('https://keats.kcl.ac.uk/course/view.php?id=86124') == [86124]


def test_single_protocol_less_url():
    assert ConfigWizard._parse_course_ids('keats.kcl.ac.uk/course/view.php?id=86124') == [86124]


def test_empty_string_returns_empty_list():
    assert ConfigWizard._parse_course_ids('') == []


def test_whitespace_only_returns_empty_list():
    assert ConfigWizard._parse_course_ids('   \t  ') == []


def test_garbage_input_returns_empty_list():
    """Pure garbage (no parseable ID and no ?id=) should return []."""
    assert ConfigWizard._parse_course_ids('not a course') == []


def test_trailing_comma_does_not_break_parsing():
    """Trailing comma should be ignored, the valid IDs are returned."""
    assert ConfigWizard._parse_course_ids('86124, 86123,') == [86124, 86123]


def test_bare_ids_with_extra_whitespace():
    """Extra whitespace around tokens must not cause failures."""
    assert ConfigWizard._parse_course_ids('  86124   86123  ') == [86124, 86123]


def test_url_with_http_scheme():
    """Both http and https must be accepted."""
    assert ConfigWizard._parse_course_ids('http://keats.kcl.ac.uk/course/view.php?id=86124') == [86124]


def test_rejects_zero_or_negative_bare_id():
    """Bare IDs that are <= 0 must be rejected (existing behavior)."""
    # 0 is not > 0 per the original logic, negative raises ValueError on int()
    # We assert the contract: parser returns no IDs when only invalid bare IDs given.
    assert ConfigWizard._parse_course_ids('0') == []
    assert ConfigWizard._parse_course_ids('-5') == []


# ---------------------------------------------------------------------------
# 3. Robustness: mixed invalid + valid inputs
# ---------------------------------------------------------------------------


def test_one_invalid_bare_id_among_valid_returns_empty():
    """Original contract: one bad bare ID rejects the whole input.

    This is the existing behavior of _parse_course_ids — guard against
    accidentally relaxing it during the refactor.
    """
    assert ConfigWizard._parse_course_ids('86124 abc 86123') == []


def test_invalid_url_does_not_crash():
    """A URL that doesn't contain id= should fall through to bare-id parsing."""
    # This is a URL without id= — parser should try to treat it as a bare
    # number, fail, and return [].
    assert ConfigWizard._parse_course_ids('https://keats.kcl.ac.uk/course/') == []
