"""
Tests for the MoodleDLCookieJar persistence helpers.

These tests complement test_utils_misc.py by covering additional edge cases
related to cookie expiration handling, comment-skipping, and round-trip
serialisation of various cookie shapes.
"""

import http.cookiejar
import io

import pytest

from moodle_dl.utils import MoodleDLCookieJar


def _make_cookie(
    name='session',
    value='abc',
    domain='.example.com',
    path='/',
    expires=None,
    discard=True,
    secure=False,
):
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith('.'),
        path=path,
        path_specified=True,
        secure=secure,
        expires=expires,
        discard=discard,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _cookie_line(domain='.example.com', secure='FALSE', expires='1893456000', name='sid', value='v', path='/'):
    return f'{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{name}\t{value}\n'


# --- _true_or_false static helper ------------------------------------------


def test_true_or_false_static_method():
    # The static method is a trivial boolean -> string mapper.
    assert MoodleDLCookieJar._true_or_false(True) == 'TRUE'
    assert MoodleDLCookieJar._true_or_false(False) == 'FALSE'
    # Works with truthy/falsy values too.
    assert MoodleDLCookieJar._true_or_false(1) == 'TRUE'
    assert MoodleDLCookieJar._true_or_false(0) == 'FALSE'


# --- save + load round trip -------------------------------------------------


def test_round_trip_preserves_basic_cookie_fields(tmp_path):
    # Set a cookie, save it to a file, load it back, and verify equality.
    jar = MoodleDLCookieJar()
    cookie = _make_cookie(
        name='mycookie',
        value='myvalue',
        domain='.roundtrip.example',
        path='/api',
        expires=4_102_444_800,
        discard=False,
    )
    jar.set_cookie(cookie)

    cookie_path = tmp_path / 'cookies.txt'
    jar.save(cookie_path, ignore_discard=True, ignore_expires=True)

    loaded = MoodleDLCookieJar()
    loaded.load(cookie_path, ignore_discard=True, ignore_expires=True)

    cookies = list(loaded)
    assert len(cookies) == 1
    roundtripped = cookies[0]
    assert roundtripped.name == 'mycookie'
    assert roundtripped.value == 'myvalue'
    assert roundtripped.path == '/api'
    assert roundtripped.expires == 4_102_444_800


def test_save_with_secure_cookie_writes_TRUE_in_secure_column(tmp_path):
    jar = MoodleDLCookieJar()
    jar.set_cookie(_make_cookie(name='securecookie', secure=True, expires=4_102_444_800))

    cookie_path = tmp_path / 'cookies.txt'
    jar.save(cookie_path, ignore_discard=True, ignore_expires=True)

    text = cookie_path.read_text(encoding='utf-8')
    # The secure flag in the file should be TRUE, the expires field non-zero.
    assert '\tTRUE\t' in text
    assert 'securecookie' in text


def test_save_writes_zero_for_none_expires(tmp_path):
    # save() rewrites expires=None -> 0 so the file is Netscape-compatible.
    jar = MoodleDLCookieJar()
    jar.set_cookie(_make_cookie(name='sess', value='xyz', expires=None, discard=True))

    cookie_path = tmp_path / 'cookies.txt'
    jar.save(cookie_path, ignore_discard=True, ignore_expires=True)

    text = cookie_path.read_text(encoding='utf-8')
    # The session cookie line should have a 0 in the expires column.
    assert '\t0\tsess\txyz\n' in text


def test_load_ignores_comment_and_blank_lines(tmp_path):
    # The file should contain a normal cookie plus comments and blank lines.
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER
        + '\n'
        + '# This is a comment\n'
        + '\n'
        + _cookie_line(name='good', value='v1')
        + '# Another comment that should be ignored\n'
        + _cookie_line(name='also_good', value='v2'),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True, ignore_expires=True)

    names = sorted(c.name for c in jar)
    assert names == ['also_good', 'good']


def test_load_skips_invalid_length_lines(tmp_path):
    # A line with 6 tab-separated fields is "invalid length" and should be
    # silently skipped (not raised) per the prepare_line error handler.
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER
        + _cookie_line(name='ok1')
        + '.example.com\tTRUE\t/\tFALSE\t1234567890\tincomplete\n'  # 6 fields
        + _cookie_line(name='ok2'),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True, ignore_expires=True)

    names = [c.name for c in jar]
    # The malformed line is dropped; the two good ones are kept.
    assert sorted(names) == ['ok1', 'ok2']


def test_load_raises_for_invalid_length_starting_with_bracket(tmp_path):
    # If the first character of the invalid line is '[' or '{', the loader
    # assumes JSON input and raises a LoadError with a helpful message.
    cookie_path = tmp_path / 'cookies.json'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER + '[1, 2, 3]\n',
        encoding='utf-8',
    )

    with pytest.raises(http.cookiejar.LoadError, match='Netscape formatted'):
        MoodleDLCookieJar().load(cookie_path, ignore_discard=True, ignore_expires=True)


def test_load_raises_for_invalid_length_starting_with_brace(tmp_path):
    cookie_path = tmp_path / 'cookies.json'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER + '{"a": 1}\n',
        encoding='utf-8',
    )

    with pytest.raises(http.cookiejar.LoadError, match='Netscape formatted'):
        MoodleDLCookieJar().load(cookie_path, ignore_discard=True, ignore_expires=True)


def test_load_handles_httponly_prefix(tmp_path):
    # Cookies saved by some browsers (e.g. Firefox) carry the `#HttpOnly_`
    # prefix; the loader should strip it before parsing.
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER
        + '#HttpOnly_.example.com\tTRUE\t/\tFALSE\t0\thttponly\tsecret\n',
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True, ignore_expires=True)

    cookie = next(iter(jar))
    assert cookie.name == 'httponly'
    assert cookie.value == 'secret'


def test_load_with_empty_expires_is_treated_as_session_cookie(tmp_path):
    # An empty expires field is the canonical Netscape session-cookie marker.
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER + _cookie_line(name='emptysession', expires=''),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True, ignore_expires=True)

    cookie = next(iter(jar))
    assert cookie.name == 'emptysession'
    assert cookie.expires is None
    assert cookie.discard is True


def test_load_with_zero_expires_is_treated_as_session_cookie(tmp_path):
    # An expires=0 value should also be re-interpreted as a session cookie.
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER + _cookie_line(name='zerosession', expires='0'),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True, ignore_expires=True)

    cookie = next(iter(jar))
    assert cookie.name == 'zerosession'
    assert cookie.expires is None
    assert cookie.discard is True


def test_load_with_minus_one_expires_is_treated_as_session_cookie(tmp_path):
    # -1 is sometimes emitted by other cookie exporters; treat it as session.
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER + _cookie_line(name='minusone', expires='-1'),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True, ignore_expires=True)

    cookie = next(iter(jar))
    assert cookie.name == 'minusone'
    assert cookie.expires is None
    assert cookie.discard is True


def test_load_rejects_negative_expires_other_than_minus_one(tmp_path):
    # A value like -5 is neither 0/empty/-1 nor a valid Netscape expires.
    # Since the offending line does not start with '[' or '{', it should
    # be silently skipped (not raised).
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER
        + _cookie_line(name='bad', expires='-5')
        + _cookie_line(name='good'),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True, ignore_expires=True)

    names = [c.name for c in jar]
    assert names == ['good']


def test_load_skips_non_numeric_expires(tmp_path):
    cookie_path = tmp_path / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER
        + _cookie_line(name='junk_expires', expires='not-a-number')
        + _cookie_line(name='good'),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar()
    jar.load(cookie_path, ignore_discard=True, ignore_expires=True)

    assert [c.name for c in jar] == ['good']


# --- save() with StringIO ---------------------------------------------------


def test_save_to_stringio_writes_header_and_cookies():
    # File-like targets are supported alongside filesystem paths.
    jar = MoodleDLCookieJar()
    jar.set_cookie(_make_cookie(name='sio', value='siov', expires=4_102_444_800))

    output = io.StringIO()
    jar.save(output, ignore_discard=True, ignore_expires=True)
    text = output.getvalue()

    assert text.startswith(MoodleDLCookieJar._HEADER)
    assert 'sio' in text


def test_save_to_existing_stringio_truncates_before_writing():
    # When the open() context yields a writable file, the implementation
    # calls file.truncate(0) on file-like targets. Verify the new content
    # fully replaces the pre-existing data.
    output = io.StringIO('old data that should not survive\n')
    jar = MoodleDLCookieJar()
    jar.set_cookie(_make_cookie(name='trunc', value='tv', expires=4_102_444_800))

    jar.save(output, ignore_discard=True, ignore_expires=True)
    text = output.getvalue()

    assert 'old data that should not survive' not in text
    assert 'trunc' in text


# --- constructor: filename via PathLike / str / None ------------------------


def test_constructor_accepts_pathlib(tmp_path):
    from pathlib import Path

    cookie_path = Path(tmp_path) / 'cookies.txt'
    cookie_path.write_text(
        MoodleDLCookieJar._HEADER + _cookie_line(name='pathlib', value='v'),
        encoding='utf-8',
    )

    jar = MoodleDLCookieJar(cookie_path)
    # The filename is normalised to a string (os.fspath) and stored.
    assert jar.filename is not None
    jar.load(ignore_discard=True, ignore_expires=True)
    assert [c.name for c in jar] == ['pathlib']
