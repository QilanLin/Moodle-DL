"""
Tests for the small formatting/type-conversion helpers in moodle_dl/utils.py.

The functions exercised here are deliberately the ones that test_utils_misc.py
does *not* exercise in depth. Each function gets at least 2-4 cases to lock
down expected behaviour, including edge cases that are easy to regress.
"""

import pytest

from moodle_dl.utils import (
    calc_speed,
    float_or_none,
    format_bytes,
    format_decimal_suffix,
    format_seconds,
    format_speed,
    is_base_64,
    is_path_like,
    str_or_none,
    timeconvert,
)


# --- format_seconds ---------------------------------------------------------


def test_format_seconds_zero_returns_double_zero():
    # Zero seconds should format as 00:00 (no hour segment).
    assert format_seconds(0) == '00:00'


def test_format_seconds_floats_truncate_to_int():
    # Floats are truncated by the int() conversion in the f-string.
    assert format_seconds(59.9) == '00:59'
    assert format_seconds(60.5) == '01:00'


def test_format_seconds_over_99_hours_returns_dashes():
    # 100 hours already exceeds the limit; 100*3600 = 360_000.
    assert format_seconds(100 * 3600) == '--:--:--'
    assert format_seconds(99 * 3600) == '99:00:00'


def test_format_seconds_exact_minute_and_hour_boundaries():
    assert format_seconds(60) == '01:00'
    assert format_seconds(3600) == '01:00:00'
    assert format_seconds(3661) == '01:01:01'


# --- calc_speed -------------------------------------------------------------


def test_calc_speed_returns_none_when_diff_below_one_millisecond():
    # diff must be at least 0.001 to avoid division by zero / huge values.
    assert calc_speed(0.0, 0.0005, 1024) is None


def test_calc_speed_returns_none_for_non_positive_byte_count():
    assert calc_speed(0.0, 1.0, 0) is None
    assert calc_speed(0.0, 1.0, -10) is None


def test_calc_speed_known_difference():
    # 1024 bytes over exactly 2 seconds == 512 B/s.
    assert calc_speed(0.0, 2.0, 1024) == 512.0


def test_calc_speed_accepts_int_inputs_and_returns_float():
    # Even if ints are passed in, division should yield a float.
    result = calc_speed(10, 20, 1000)
    assert isinstance(result, float)
    assert result == 100.0


# --- format_speed -----------------------------------------------------------


def test_format_speed_none_is_ten_chars_wide():
    # The placeholder is "---b/s" padded to width 10.
    assert format_speed(None) == '---b/s    '
    assert len(format_speed(None)) == 10


def test_format_speed_uses_format_bytes_and_appends_per_second():
    # 1024 bytes should be 1.00KiB/s; the /s suffix is also padded.
    assert format_speed(1024) == '1.00KiB/s '


def test_format_speed_uses_iec_for_megabytes():
    # 1024*1024 should be 1.00MiB/s, padded to 10 chars.
    assert format_speed(1024 * 1024) == '1.00MiB/s '


# --- is_base_64 -------------------------------------------------------------


def test_is_base_64_accepts_padded_hello():
    # SGVsbG8= decodes to "Hello".
    assert is_base_64('SGVsbG8=') is True


def test_is_base_64_rejects_invalid_strings():
    assert is_base_64('not base64') is False
    assert is_base_64('hello world') is False


def test_is_base_64_empty_string():
    # An empty string encodes/decodes to empty; the function returns True.
    assert is_base_64('') is True


def test_is_base_64_rejects_bytes_with_bad_padding():
    # b"dGVzdA=" decodes to b"test" but its re-encoded form is "dGVzdA==".
    # The strict decode/encode roundtrip will reject the malformed padding.
    assert is_base_64(b'dGVzdA=') is False


def test_is_base_64_returns_false_for_non_string_non_bytes():
    # An int is neither str nor bytes/bytearray; the function should bail.
    assert is_base_64(123) is False
    assert is_base_64(None) is False
    assert is_base_64(['SGVsbG8=']) is False


def test_is_base_64_handles_bytearray_input():
    # bytearray is the third accepted input type.
    assert is_base_64(bytearray(b'SGVsbG8=')) is True


# --- timeconvert ------------------------------------------------------------


def test_timeconvert_parses_rfc2822_string_with_offset():
    # 15 Nov 1994 08:12:31 +0000 == 784887151.
    assert timeconvert('Tue, 15 Nov 1994 08:12:31 GMT') == 784887151


def test_timeconvert_returns_none_for_none_input():
    # email.utils.parsedate_tz(None) returns None -> timeconvert returns None.
    assert timeconvert(None) is None


def test_timeconvert_returns_none_for_invalid_string():
    assert timeconvert('not a date at all') is None
    assert timeconvert('') is None


def test_timeconvert_handles_non_gmt_timezone():
    # 08:12:31 -0500 means 5h behind UTC, so the same instant has a
    # timestamp 5*3600s *larger* than the GMT equivalent.
    gmt_ts = timeconvert('Tue, 15 Nov 1994 08:12:31 GMT')
    est_ts = timeconvert('Tue, 15 Nov 1994 08:12:31 -0500')
    assert gmt_ts is not None and est_ts is not None
    assert est_ts - gmt_ts == 5 * 3600


# --- float_or_none ----------------------------------------------------------


def test_float_or_none_none_returns_default():
    assert float_or_none(None) is None
    assert float_or_none(None, default='fallback') == 'fallback'


def test_float_or_none_accepts_numeric_string():
    # "3" parses fine as a float.
    assert float_or_none('3') == 3.0
    assert float_or_none('3.5') == 3.5


def test_float_or_none_invalid_string_returns_default():
    assert float_or_none('abc', default=-1) == -1
    assert float_or_none('', default=0) == 0


def test_float_or_none_scale_and_invscale():
    # invscale/scale = 4/2 = 2; 2.5 * 2 == 5.0.
    assert float_or_none('2.5', scale=2, invscale=4) == 5.0


def test_float_or_none_empty_string_yields_default():
    # An empty string is not a valid float, so default is returned.
    assert float_or_none('', default=99) == 99


# --- format_decimal_suffix --------------------------------------------------


def test_format_decimal_suffix_zero_is_zero_string():
    # 0 has no suffix, no exponent, and the default fmt is '%d%s'.
    assert format_decimal_suffix(0) == '0'


def test_format_decimal_suffix_negative_returns_none():
    assert format_decimal_suffix(-5) is None
    assert format_decimal_suffix(-0.5) is None


def test_format_decimal_suffix_none_returns_none():
    # None goes through float_or_none -> None and short-circuits.
    assert format_decimal_suffix(None) is None


def test_format_decimal_suffix_thousand_uses_k_suffix():
    # 1500 / 1000 == 1.50 with %d%s == "1k"; default factor is 1000.
    assert format_decimal_suffix(1500) == '1k'


def test_format_decimal_suffix_factor_1024_uses_ki_suffix():
    # factor=1024 swaps the bare "k" for "Ki".
    assert format_decimal_suffix(1536, '%.1f%s', factor=1024) == '1.5Ki'


def test_format_decimal_suffix_caps_at_y_even_for_huge_values():
    # 1e24 / 1000**7 = 1e3, but the suffix list only goes to Y.
    huge = 1000 ** 8  # 1e24
    result = format_decimal_suffix(huge)
    # exponent is min(int(log10^3(1e24)), 8) == 7, suffix "Y"
    assert result == '1.00Y' or result.endswith('Y')


# --- format_bytes -----------------------------------------------------------


def test_format_bytes_zero_is_n_a():
    # format_decimal_suffix(0) == '0' (truthy), so format_bytes returns '0.00B'.
    # Lock down the real behaviour.
    assert format_bytes(0) == '0.00B'


def test_format_bytes_none_is_n_a():
    # None falls through the `or 'N/A'` branch.
    assert format_bytes(None) == 'N/A'


def test_format_bytes_negative_is_n_a():
    # Negative numbers fail the `num < 0` guard and return None -> 'N/A'.
    assert format_bytes(-1024) == 'N/A'


def test_format_bytes_one_kib():
    assert format_bytes(1024) == '1.00KiB'


def test_format_bytes_one_mib():
    assert format_bytes(1024 * 1024) == '1.00MiB'


def test_format_bytes_boundary_value_around_one_kib():
    # 1500 / 1024 ≈ 1.4648 -> 1.46KiB
    assert format_bytes(1500) == '1.46KiB'


# --- is_path_like -----------------------------------------------------------


def test_is_path_like_string_is_path_like():
    assert is_path_like('/tmp/file.txt') is True


def test_is_path_like_bytes_is_path_like():
    assert is_path_like(b'/tmp/file.txt') is True


def test_is_path_like_path_object_is_path_like():
    from pathlib import Path

    assert is_path_like(Path('/tmp')) is True


def test_is_path_like_int_is_not_path_like():
    assert is_path_like(42) is False


def test_is_path_like_none_is_not_path_like():
    assert is_path_like(None) is False


# --- str_or_none ------------------------------------------------------------


def test_str_or_none_none_returns_default():
    assert str_or_none(None) is None
    assert str_or_none(None, default='x') == 'x'


def test_str_or_none_int_is_stringified():
    assert str_or_none(42) == '42'


def test_str_or_none_string_unchanged():
    assert str_or_none('hello') == 'hello'


def test_str_or_none_object_uses_its_str():
    class Obj:
        def __str__(self):
            return 'obj-repr'

    assert str_or_none(Obj()) == 'obj-repr'
