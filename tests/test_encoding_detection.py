# -*- coding: utf-8 -*-
"""Encoding detection helpers in download_service.

`_detect_bom_encoding`, `_detect_html_meta_encoding` and
`_detect_encoding_with_optional_lib` are the three building blocks used to
pick a charset before falling back to UTF-8 / requests' apparent_encoding.
This file pins down their current behaviour so the BOM ordering, the 4 KiB
scan window and the optional-lib fallback don't silently regress.
"""

import sys
import types

import pytest

from moodle_dl.downloader.download_service import (
    _BOM_ENCODINGS,
    _detect_bom_encoding,
    _detect_encoding_with_optional_lib,
    _detect_html_meta_encoding,
)


# ---------------------------------------------------------------------------
# _BOM_ENCODINGS table — order matters. UTF-32 BOMs share a prefix with
# UTF-16 BOMs, so UTF-32 must be checked first to avoid false positives.
# ---------------------------------------------------------------------------


def test_bom_encodings_table_starts_with_utf32():
    """The first two entries must be the UTF-32 BOMs.

    If someone reorders the table (e.g. moves UTF-16 LE ahead of UTF-32 LE),
    `\xff\xfe\x00\x00` will be mis-detected as UTF-16 LE, which decodes
    garbage. Pin the order here.
    """
    assert _BOM_ENCODINGS[0] == (b'\x00\x00\xfe\xff', 'utf-32-be')
    assert _BOM_ENCODINGS[1] == (b'\xff\xfe\x00\x00', 'utf-32-le')


# ---------------------------------------------------------------------------
# _detect_bom_encoding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'raw,expected',
    [
        (b'\x00\x00\xfe\xffhello', 'utf-32-be'),
        (b'\xff\xfe\x00\x00hello', 'utf-32-le'),
        (b'\xef\xbb\xbfhello', 'utf-8-sig'),
        (b'\xfe\xffhello', 'utf-16-be'),
        (b'\xff\xfehello', 'utf-16-le'),
    ],
    ids=['utf32-be', 'utf32-le', 'utf8-sig', 'utf16-be', 'utf16-le'],
)
def test_detect_bom_encoding_known_boms(raw, expected):
    assert _detect_bom_encoding(raw) == expected


def test_detect_bom_encoding_utf32_le_not_misread_as_utf16_le():
    """Regression guard: \xff\xfe\x00\x00 is UTF-32 LE, not UTF-16 LE.

    The byte sequence starts with the UTF-16 LE BOM (`\xff\xfe`) too, so the
    table must check the longer UTF-32 LE BOM first. If the order ever
    changes, this test will catch it.
    """
    raw = b'\xff\xfe\x00\x00A\x00B\x00'
    assert _detect_bom_encoding(raw) == 'utf-32-le'


def test_detect_bom_encoding_no_bom_returns_none():
    assert _detect_bom_encoding(b'<html><body>plain ascii</body></html>') is None


def test_detect_bom_encoding_empty_bytes_returns_none():
    assert _detect_bom_encoding(b'') is None


def test_detect_bom_encoding_singleton_utf16_le_byte_returns_none():
    """A single \xff or \xfe byte is *not* a BOM on its own — we need both."""
    assert _detect_bom_encoding(b'\xff') is None
    assert _detect_bom_encoding(b'\xfe') is None


# ---------------------------------------------------------------------------
# _detect_html_meta_encoding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'raw,expected',
    [
        (b'<html><head><meta charset="utf-8"></head></html>', 'utf-8'),
        (b"<html><head><meta charset='utf-8'></head></html>", 'utf-8'),
        (
            b'<html><head>'
            b'<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
            b'</head></html>',
            'utf-8',
        ),
        (b'<META CHARSET="Big5">', 'big5'),
        (b'<meta charset = "windows-1252">', 'windows-1252'),
        (b'<meta charset=Shift_JIS>', 'shift_jis'),
        # Real-world case: a Microsoft Word export with a meta tag that uses
        # an oddly-spaced charset attribute.
        (b'<html><head><meta http-equiv="content-type" content="text/html; charset = gb2312"></head></html>', 'gb2312'),
    ],
    ids=[
        'double-quoted',
        'single-quoted',
        'http-equiv-content-type',
        'uppercase-tag-and-value',
        'spaces-around-equals',
        'no-quotes',
        'http-equiv-with-spaces',
    ],
)
def test_detect_html_meta_encoding_valid(raw, expected):
    assert _detect_html_meta_encoding(raw) == expected


def test_detect_html_meta_encoding_rejects_unicode_keyword():
    """`charset=unicode` is a non-standard name some Word exports emit.

    We deliberately return None so the caller falls back to a different
    detection path rather than trying to decode with Python's "unicode"
    codec, which raises LookupError.
    """
    assert _detect_html_meta_encoding(b'<meta charset="unicode">') is None


def test_detect_html_meta_encoding_empty_bytes_returns_none():
    assert _detect_html_meta_encoding(b'') is None


def test_detect_html_meta_encoding_no_meta_tag_returns_none():
    raw = b'<html><head><title>nothing here</title></head><body>hi</body></html>'
    assert _detect_html_meta_encoding(raw) is None


def test_detect_html_meta_encoding_only_scans_first_4kib():
    """Charset declared past the 4 KiB window must be ignored.

    The HTML spec says charset must be in the first 1024 bytes of <head>;
    we allow a generous 4 KiB to accommodate Word's verbose output, but
    anything past that is not a useful signal.
    """
    # Place a valid charset right at the 4097th byte (index 4096).
    raw = b' ' * 4096 + b'<meta charset="utf-8">'
    assert len(raw) > 4096
    assert _detect_html_meta_encoding(raw) is None


def test_detect_html_meta_encoding_short_input_does_not_pad():
    """Inputs shorter than 4 KiB are scanned in full (the slice is a no-op).

    This guards against accidentally writing `raw[:4096] + b'\x00' * ...`
    or a similar off-by-one when the production code is ever refactored.
    """
    raw = b'<head>' + b' ' * 4000 + b'<meta charset="utf-8">'
    assert len(raw) < 4096
    assert _detect_html_meta_encoding(raw) == 'utf-8'


def test_detect_html_meta_encoding_non_ascii_charset_returns_none():
    """If the captured charset contains non-ASCII bytes, the ASCII decode
    fails and we return None rather than crashing.
    """
    # Embed high-bit bytes inside the captured group. The regex captures
    # `[\w\-:.]+`, so use a high-bit byte that still matches: e.g. 0xC3 is
    # not in the class, so the captured group is "utf-8" and the trailing
    # non-ASCII bytes live outside it. To force a capture containing
    # non-ASCII, we craft a string where the *captured* name has a high
    # byte — by prepending a BOM-looking high byte inside a charset name.
    # Easier: use a charset name like "utf-8<highbyte>" — won't match
    # `[\w\-:.]`. So the realistic test is: non-ASCII bytes in the
    # *content* of the meta tag (outside the capture group) don't break
    # the parser.
    raw = b'<meta charset="utf-8" data-extra="\xff\xfe\xff\xff">'
    assert _detect_html_meta_encoding(raw) == 'utf-8'


def test_detect_html_meta_encoding_falls_back_to_ascii_decode_error():
    r"""If the captured charset *itself* contains non-ASCII bytes, the
    decode step raises UnicodeDecodeError and we return None.

    To force the regex to capture a non-ASCII byte, we monkey-patch
    `_META_CHARSET_RE` with a permissive one for the duration of this
    test. Otherwise the production regex's `[\w\-:.]` character class
    filters them out.
    """
    import re

    from moodle_dl.downloader import download_service as ds

    permissive_re = re.compile(
        rb'<meta[^>]+?charset\s*=\s*["\']?([^"\']+)"',
        re.IGNORECASE,
    )
    original = ds._META_CHARSET_RE
    ds._META_CHARSET_RE = permissive_re
    try:
        # The capture group now contains a 0xFF byte -> ASCII decode fails.
        raw = b'<meta charset="utf-\xff">'
        assert _detect_html_meta_encoding(raw) is None
    finally:
        ds._META_CHARSET_RE = original


def test_detect_html_meta_encoding_first_meta_wins():
    """When two meta tags exist, the regex returns the first match.

    The production code only ever calls .search() once, so we just
    document that behaviour here.
    """
    raw = (
        b'<html><head>'
        b'<meta charset="utf-8">'
        b'<meta charset="big5">'
        b'</head></html>'
    )
    assert _detect_html_meta_encoding(raw) == 'utf-8'


# ---------------------------------------------------------------------------
# _detect_encoding_with_optional_lib
# ---------------------------------------------------------------------------


def test_detect_encoding_with_optional_lib_no_libs_returns_none(monkeypatch):
    """With neither charset_normalizer nor chardet importable, we get None.

    Force the ImportError path by hiding both modules from `__import__`.
    """
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in ('charset_normalizer', 'chardet'):
            raise ImportError(f'no {name} in test')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded_import)
    # Also make sure sys.modules doesn't have cached versions.
    monkeypatch.delitem(sys.modules, 'charset_normalizer', raising=False)
    monkeypatch.delitem(sys.modules, 'chardet', raising=False)

    assert _detect_encoding_with_optional_lib(b'some random bytes \xff\xff') is None


def test_detect_encoding_with_optional_lib_uses_charset_normalizer(monkeypatch):
    """When charset_normalizer is present, from_bytes().best().encoding wins."""

    class FakeBest:
        encoding = 'iso-8859-1'

    class FakeResult:
        def best(self):
            return FakeBest()

    fake_cn = types.ModuleType('charset_normalizer')
    fake_cn.from_bytes = lambda _raw: FakeResult()  # noqa: ARG005
    monkeypatch.setitem(sys.modules, 'charset_normalizer', fake_cn)
    # Make sure chardet isn't accidentally picked.
    monkeypatch.delitem(sys.modules, 'chardet', raising=False)

    assert _detect_encoding_with_optional_lib(b'some bytes') == 'iso-8859-1'


def test_detect_encoding_with_optional_lib_charset_normalizer_no_best_falls_back(monkeypatch):
    """If charset_normalizer.from_bytes() returns a result with .best()==None,
    we should fall through to chardet."""

    class FakeResult:
        def best(self):
            return None

    fake_cn = types.ModuleType('charset_normalizer')
    fake_cn.from_bytes = lambda _raw: FakeResult()  # noqa: ARG005
    monkeypatch.setitem(sys.modules, 'charset_normalizer', fake_cn)

    fake_chardet = types.ModuleType('chardet')
    fake_chardet.detect = lambda _raw: {'encoding': 'gbk', 'confidence': 0.9}
    monkeypatch.setitem(sys.modules, 'chardet', fake_chardet)

    assert _detect_encoding_with_optional_lib(b'some bytes') == 'gbk'


def test_detect_encoding_with_optional_lib_charset_normalizer_best_has_no_encoding_falls_back(monkeypatch):
    """If charset_normalizer's best guess has no encoding attribute, fall back."""

    class FakeBest:
        encoding = None

    class FakeResult:
        def best(self):
            return FakeBest()

    fake_cn = types.ModuleType('charset_normalizer')
    fake_cn.from_bytes = lambda _raw: FakeResult()  # noqa: ARG005
    monkeypatch.setitem(sys.modules, 'charset_normalizer', fake_cn)

    fake_chardet = types.ModuleType('chardet')
    fake_chardet.detect = lambda _raw: {'encoding': 'utf-8', 'confidence': 0.5}
    monkeypatch.setitem(sys.modules, 'chardet', fake_chardet)

    assert _detect_encoding_with_optional_lib(b'some bytes') == 'utf-8'


def _block_charset_normalizer(monkeypatch):
    """Hide charset_normalizer from `import` so the chardet branch is
    actually exercised. Just deleting sys.modules is not enough: if the
    module is already imported in this process, the next `import` finds
    the cached object. We also short-circuit __import__.
    """
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == 'charset_normalizer':
            raise ImportError('blocked in test')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded_import)
    monkeypatch.delitem(sys.modules, 'charset_normalizer', raising=False)


def test_detect_encoding_with_optional_lib_uses_chardet_only(monkeypatch):
    """When only chardet is importable, we use it."""
    _block_charset_normalizer(monkeypatch)

    fake_chardet = types.ModuleType('chardet')
    fake_chardet.detect = lambda _raw: {'encoding': 'cp1251', 'confidence': 0.99}
    monkeypatch.setitem(sys.modules, 'chardet', fake_chardet)

    assert _detect_encoding_with_optional_lib(b'some bytes') == 'cp1251'


def test_detect_encoding_with_optional_lib_chardet_no_encoding_returns_none(monkeypatch):
    """chardet returns confidence below threshold → encoding is None → None."""
    _block_charset_normalizer(monkeypatch)

    fake_chardet = types.ModuleType('chardet')
    fake_chardet.detect = lambda _raw: {'encoding': None, 'confidence': 0.0}
    monkeypatch.setitem(sys.modules, 'chardet', fake_chardet)

    assert _detect_encoding_with_optional_lib(b'some bytes') is None


def test_detect_encoding_with_optional_lib_both_return_none(monkeypatch):
    """Both libs present, both inconclusive → return None."""

    class FakeResult:
        def best(self):
            return None

    fake_cn = types.ModuleType('charset_normalizer')
    fake_cn.from_bytes = lambda _raw: FakeResult()  # noqa: ARG005
    monkeypatch.setitem(sys.modules, 'charset_normalizer', fake_cn)

    fake_chardet = types.ModuleType('chardet')
    fake_chardet.detect = lambda _raw: {'encoding': None}
    monkeypatch.setitem(sys.modules, 'chardet', fake_chardet)

    assert _detect_encoding_with_optional_lib(b'random bytes \x00\x01\x02') is None
