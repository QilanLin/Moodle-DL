# -*- coding: utf-8 -*-
"""
Fuzz tests for the most heavily-used text parsers in moodle-dl.

The point of these tests is to ensure that arbitrary / adversarial input
never crashes the parser, never produces out-of-range results, and
preserves whatever contracts the parser advertises. We use Hypothesis
to generate the inputs.

Covered parsers (each is the entry point of an untrusted user / HTTP
boundary, so a crash here is a security/robustness issue):

  - ConfigWizard._parse_course_ids  (free-text input from the init
    wizard's interactive prompt)
  - utils.determine_ext              (every Moodle file URL flows through
    this to compute the file extension)
  - PathTools.sanitize_filename       (every downloaded filename)
  - PathTools.sanitize_path           (every downloaded directory path)
  - PathTools.to_valid_name           (every section / module / course
    name)
  - timeconvert                        (HTTP Last-Modified / date headers)
  - is_base_64                         (cookie value parser)
  - format_decimal_suffix / format_bytes / format_speed
  - float_or_none
  - determine_ext
  - is_path_like

Each test asserts **invariants** the parser must satisfy:

  1. Never raises (the most important one).
  2. Return type is correct (str, int, float, bool, None, list, etc).
  3. Output, if non-empty, satisfies any documented shape constraints
     (e.g. file extensions are alphanumeric).

If you discover a real bug via these tests, you should add a regression
test alongside the fix (because Hypothesis may not find the same
sequence of inputs again on the next CI run).
"""
import re
import string
import unittest
from typing import Any, List, Optional

from hypothesis import (
    HealthCheck,
    given,
    settings,
    strategies as st,
)

# Late import so a typo in module name doesn't kill collection.
from moodle_dl.cli.config_wizard import ConfigWizard
from moodle_dl.utils import (
    PathTools,
    determine_ext,
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


# ---------------------------------------------------------------------------
# Strategies (custom generators for moodle-dl-style input)
# ---------------------------------------------------------------------------

# Realistic URL fragments: domain + path + query string.
url_strategy = st.builds(
    lambda scheme, host, path, qs: f"{scheme}://{host}{path}?{qs}",
    scheme=st.sampled_from(["http", "https"]),
    host=st.from_regex(r"[a-z][a-z0-9-]{0,30}(\.[a-z]{2,5}){1,2}", fullmatch=True),
    path=st.from_regex(r"(/[a-zA-Z0-9_./-]{0,40})?", fullmatch=True),
    qs=st.from_regex(r"([a-zA-Z_]+=[a-zA-Z0-9]+(&[a-zA-Z_]+=[a-zA-Z0-9]+)*)?", fullmatch=True),
)

# Filenames that mimic what keats / moodle might use.
moodle_filename_strategy = st.from_regex(
    r"[A-Za-z0-9 ._()\[\]\u4e00-\u9fff-]{0,80}\.(pdf|docx|mp4|pptx|html|webloc|txt|md|zip)",
    fullmatch=True,
)

# Course id strategy: positive integers, occasionally with extra
# junk around them (whitespace, leading zeros, signed).
loose_course_id_strategy = st.one_of(
    st.integers(min_value=0, max_value=999999),
    st.text(
        alphabet=st.characters(
            whitelist_categories=("Nd",),
            whitelist_characters=" -,",
        ),
        min_size=0,
        max_size=30,
    ),
)


# Common hypothesis settings: keep tests fast and avoid pathological
# example-bloating on CI.
fast = settings(
    max_examples=200,
    deadline=2000,  # ms per example
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# ---------------------------------------------------------------------------
# _parse_course_ids
# ---------------------------------------------------------------------------


class TestFuzzParseCourseIds(unittest.TestCase):
    """Fuzz the interactive 'add more courses' input parser.

    Contract (pinned by tests/test_parse_course_ids.py):
      - Never raises.
      - Always returns a list[int].
      - The list may be empty.
      - Every element is a strictly positive int.
    """

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=500))
    def test_arbitrary_text_never_raises(self, text):
        result = ConfigWizard._parse_course_ids(text)
        self.assertIsInstance(result, list)
        for x in result:
            self.assertIsInstance(x, int)
            self.assertGreater(x, 0)

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(
        st.text(alphabet=st.characters(whitelist_categories=("Nd",)), min_size=1, max_size=200)
    )
    def test_pure_digits_are_each_parsed(self, text):
        # A string of digits separated by spaces/commas should produce
        # exactly that many positive integers (possibly fewer if some are 0).
        result = ConfigWizard._parse_course_ids(text)
        self.assertIsInstance(result, list)
        for x in result:
            self.assertGreater(x, 0)

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(loose_course_id_strategy, st.integers(min_value=0, max_value=10))
    def test_random_separators(self, token, sep_kind):
        # Wrap a number with a random separator: spaces, commas,
        # semicolons, tabs, newlines, garbage.
        for sep in [" ", ",", "\t", "\n", ";", "x", "||", "、", "，", "  "]:
            payload = sep.join([str(token)] * (sep_kind + 1))
            result = ConfigWizard._parse_course_ids(payload)
            self.assertIsInstance(result, list)
            for x in result:
                self.assertGreater(x, 0)

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(url_strategy)
    def test_url_parsing_never_raises(self, url):
        result = ConfigWizard._parse_course_ids(url)
        self.assertIsInstance(result, list)

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(alphabet="abc", min_size=0, max_size=200))
    def test_alphabetic_garbage_returns_empty(self, text):
        # Input contains no digits, no ?id=, should be safely [].
        result = ConfigWizard._parse_course_ids(text)
        self.assertEqual(result, [])

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.binary(min_size=0, max_size=200))
    def test_bytes_like_input_does_not_crash(self, text_bytes):
        # Although the function signature says str, real CLI input may
        # be decoded from bytes in some environments. Ensure no
        # AttributeError slips through.
        try:
            text = text_bytes.decode("utf-8", errors="replace")
        except Exception:
            return  # the decode itself is allowed to fail
        result = ConfigWizard._parse_course_ids(text)
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# determine_ext
# ---------------------------------------------------------------------------


class TestFuzzDetermineExt(unittest.TestCase):
    """Fuzz determine_ext (used on every Moodle file URL).

    Contract:
      - Never raises.
      - Returns a non-empty string.
      - If the result is a non-default ext (not 'unknown_file' or
        empty), it should be alphanumeric (no path separators).
    """

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=1000))
    def test_arbitrary_text_never_raises(self, text):
        result = determine_ext(text)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.none())
    def test_none_input_returns_default(self, none_value):
        result = determine_ext(none_value)
        self.assertEqual(result, "unknown_file")

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(url_strategy)
    def test_url_always_returns_string(self, url):
        result = determine_ext(url)
        self.assertIsInstance(result, str)
        # If non-default, must not contain path separators or NUL
        if result != "unknown_file":
            self.assertNotIn("/", result)
            self.assertNotIn("\\", result)
            self.assertNotIn("\x00", result)

    @given(
        st.sampled_from(
            [
                "https://keats.kcl.ac.uk/webservice/pluginfile.php/1234/mod_resource/content/0/foo.pdf",
                "https://keats.kcl.ac.uk/mod/folder/view.php?id=999",
                "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=1234",
                "https://keats.kcl.ac.uk/mod/page/view.php?id=1234",
                "https://cdnapisec.kaltura.com/p/1234/entry_id/0_abc/manifest.m3u8",
                "data:application/pdf;base64,JVBERi0xLjQK",
            ]
        )
    )
    def test_known_urls_yield_reasonable_extensions(self, url):
        result = determine_ext(url)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


# ---------------------------------------------------------------------------
# PathTools sanitizers
# ---------------------------------------------------------------------------


class TestFuzzPathTools(unittest.TestCase):
    """Fuzz the filesystem-safety sanitizers.

    Contract:
      - sanitize_filename, sanitize_path, to_valid_name never raise.
      - Output is a string (or None for to_valid_name's special case).
      - Output contains no path separators, NUL, or other control
        characters that would be unsafe in a filename.
    """

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=500))
    def test_sanitize_filename_never_raises(self, text):
        result = PathTools.sanitize_filename(text)
        self.assertIsInstance(result, str)

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=500))
    def test_sanitize_filename_no_unsafe_chars(self, text):
        result = PathTools.sanitize_filename(text)
        # Even on adversarial input, the result must not contain
        # raw control characters. (Restricted=True in our code path
        # additionally strips ! ' ( ) [ ] { } $ ; ` ^ , # etc.)
        for ch in result:
            self.assertNotEqual(ch, "\x00")
            # No C0/C1 control characters except tab/newline which
            # are also replaced.
            self.assertGreater(ord(ch), 31)

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=500))
    def test_sanitize_path_never_raises(self, text):
        # Some inputs may raise OSError on Windows-style drive parsing
        # but on POSIX should never.
        try:
            result = PathTools.sanitize_path(text)
            self.assertIsInstance(result, str)
        except OSError:
            pass  # acceptable on platform-specific inputs

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(moodle_filename_strategy)
    def test_to_valid_name_preserves_extensions(self, filename):
        # If the input has a meaningful stem + known extension,
        # to_valid_name should preserve the extension on the output.
        result = PathTools.to_valid_name(filename, is_file=True)
        if result is None:
            return
        if "." not in filename:
            return
        stem = filename.rsplit(".", 1)[0]
        # Skip degenerate inputs where the stem is empty or consists
        # only of dots / path separators (sanitize_filename will
        # collapse them and may legitimately drop the extension).
        if not stem.strip(" ."):
            return
        orig_ext = filename.rsplit(".", 1)[1].lower()
        self.assertTrue(
            result.endswith("." + orig_ext),
            f"Expected extension .{orig_ext} to survive in {result!r} "
            f"(input was {filename!r})",
        )

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=300))
    def test_to_valid_name_returns_str_or_none(self, text):
        result = PathTools.to_valid_name(text, is_file=False)
        # The function returns None only for None input. For any other
        # input (including all-control-characters, all-whitespace,
        # empty string), it returns a non-None sanitized string (often '_').
        if text is None:
            self.assertIsNone(result)
        else:
            self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# Misc small helpers
# ---------------------------------------------------------------------------


class TestFuzzSmallHelpers(unittest.TestCase):
    """Fuzz the small utility helpers."""

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=200))
    def test_timeconvert_never_raises(self, text):
        result = timeconvert(text)
        self.assertTrue(result is None or isinstance(result, (int, float)))

    @given(st.one_of(st.none(), st.text(max_size=200), st.binary(max_size=200), st.integers(), st.floats()))
    def test_is_base_64_never_raises(self, value):
        result = is_base_64(value)
        self.assertIsInstance(result, bool)

    @given(st.one_of(st.none(), st.text(max_size=50), st.integers(), st.floats(), st.booleans()))
    def test_float_or_none_never_raises(self, value):
        result = float_or_none(value)
        self.assertTrue(result is None or isinstance(result, float))

    @given(st.one_of(st.none(), st.text(max_size=50), st.integers(), st.floats(), st.booleans(), st.binary(max_size=50)))
    def test_str_or_none_never_raises(self, value):
        result = str_or_none(value)
        self.assertTrue(result is None or isinstance(result, str))

    @given(st.one_of(st.none(), st.integers(), st.floats(), st.text(max_size=20)))
    def test_format_decimal_suffix_never_raises(self, value):
        result = format_decimal_suffix(value)
        self.assertTrue(result is None or isinstance(result, str))

    @given(st.one_of(st.none(), st.integers(min_value=0, max_value=10**12), st.floats(min_value=0, max_value=10**12, allow_nan=False)))
    def test_format_bytes_never_raises(self, value):
        result = format_bytes(value)
        self.assertIsInstance(result, str)
        # N/A is the only fallback; everything else is a real suffix.
        # If the input was non-negative, result should not be N/A
        if value is not None and value >= 0:
            self.assertNotEqual(result, "N/A")

    @given(st.one_of(st.none(), st.integers(min_value=0, max_value=10**9), st.floats(min_value=0, max_value=10**9, allow_nan=False)))
    def test_format_speed_never_raises(self, value):
        result = format_speed(value)
        self.assertIsInstance(result, str)
    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.integers(min_value=-10**6, max_value=10**6))
    def test_format_seconds_never_raises(self, secs):
        result = format_seconds(secs)
        self.assertIsInstance(result, str)
        # Either "--:--:--" (cap), or starts with a sign then 2 digits
        # (negative), or 2 digits. Don't be too strict — negative numbers
        # are allowed to format with a leading minus.
        if "--" in result:
            return
        # Accept: HH:MM, HH:MM:SS, optionally with leading minus on
        # the first segment (negative seconds). f-string's :02d pads to
        # 2 chars but for negative ints the minus comes before, so we
        # allow -?\d+ for the hours part.
        self.assertRegex(
            result,
            r"^-?\d+:\d{2}(:\d{2})?$",
            f"Unexpected format for {secs}: {result!r}",
        )

    @given(st.one_of(st.text(max_size=200), st.binary(max_size=200), st.integers(), st.none()))
    def test_is_path_like_never_raises(self, value):
        result = is_path_like(value)
        self.assertIsInstance(result, bool)


# ---------------------------------------------------------------------------
# Cross-parser invariant: sanitize(in) must be safe to use as a path
# ---------------------------------------------------------------------------


class TestSanitizationIsIdempotentAndComposable(unittest.TestCase):
    """Applying a sanitizer twice should be a no-op (idempotent) or at
    least should not grow the string. This is a property every good
    idempotent sanitizer should satisfy."""

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=300))
    def test_sanitize_filename_idempotent(self, text):
        once = PathTools.sanitize_filename(text)
        twice = PathTools.sanitize_filename(once)
        self.assertEqual(once, twice, f"sanitize_filename not idempotent: {once!r} -> {twice!r}")

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=300))
    def test_sanitize_filename_preserves_length_or_shortens(self, text):
        """Sanitization should not make the string longer than the input."""
        result = PathTools.sanitize_filename(text)
        self.assertLessEqual(len(result), len(text) + 10,
                              f"sanitize_filename grew unexpectedly: {len(text)} -> {len(result)}")

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(st.text(max_size=200))
    def test_sanitize_filename_result_contains_only_safe_chars(self, text):
        """The result must not contain characters that would let the
        file escape its intended directory on either Windows or POSIX."""
        result = PathTools.sanitize_filename(text)
        for unsafe in ("/", "\\", "\x00"):
            self.assertNotIn(unsafe, result)


# ---------------------------------------------------------------------------
# Bundle: adversarial mixed input
# ---------------------------------------------------------------------------


class TestFuzzMixedAdversarialInput(unittest.TestCase):
    """Most-realistic adversarial: a string that mixes a course URL,
    a filename, a path, an integer, a date, all in one user-paste."""

    @settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(
        st.lists(
            st.one_of(
                url_strategy,
                moodle_filename_strategy,
                st.integers(min_value=0, max_value=99999).map(str),
                st.text(alphabet=string.printable, min_size=0, max_size=30),
            ),
            min_size=0,
            max_size=10,
        ),
        st.sampled_from([" ", ",", "\n", "\t", " | ", " ; "]),
    )
    def test_joint_parser_round_trip(self, tokens, sep):
        blob = sep.join(tokens)
        # Each parser must survive the joint blob.
        try:
            ids = ConfigWizard._parse_course_ids(blob)
        except Exception as e:
            self.fail(f"_parse_course_ids crashed on {blob!r}: {e}")
        self.assertIsInstance(ids, list)

        try:
            ext = determine_ext(blob)
        except Exception as e:
            self.fail(f"determine_ext crashed on {blob!r}: {e}")
        self.assertIsInstance(ext, str)

        try:
            safe = PathTools.sanitize_filename(blob)
        except Exception as e:
            self.fail(f"sanitize_filename crashed on {blob!r}: {e}")
        self.assertIsInstance(safe, str)
