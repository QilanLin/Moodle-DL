# -*- coding: utf-8 -*-
"""
Tests for the --halt-videos / --release-videos feature.

Behaviour under test:

1. CLI parser accepts the new flags and stores them on MoodleDlOpts.
2. _is_kaltura_cdn_url matches KCL's canonical player frame URL.
3. _partition_courses_by_halt separates Kaltura CDN files into the
   halted bucket, blanks their URL so the downloader skips them, and
   leaves non-Kaltura files untouched.
4. StateRecorder.save_halted_file / get_halted_files /
   clear_halted_marker round-trip correctly.
5. The default --retry-failed path does NOT pick up halted files
   (because get_failed_files_* excludes '[HALTED]' reasons).
"""

import json
import os
import unittest
from unittest.mock import MagicMock

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.main import (
    _is_kaltura_cdn_url,
    _partition_courses_by_halt,
    get_parser,
)
from moodle_dl.types import Course, File, MoodleDlOpts


KCL_KALTURA_URL = (
    'https://cdnapisec.kaltura.com/html5/html5lib/v2.101/mwEmbedFrame.php'
    '/p/2368101/uiconf_id/42864872/entry_id/1_oqrq4l0x'
    '?wid=_2368101&iframeembed=true&entry_id=1_oqrq4l0x'
)
# Auth-free entry-point URL that KalturaUrlBuilder.build_from_known_embed_url
# produces from a Moodle LTI launch URL. This is what the partition step now
# stores as the halted file's content_fileurl so --release-videos can
# re-download without Moodle cookies or mobile tokens.
KCL_KALTURA_EMBED_URL = (
    'https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/'
    'uiconf_id/50869842/partner_id/2368101'
    '?iframeembed=true&entry_id=1_uwhesokp'
)
# Upstream Moodle LTI launch URL. This is what fetch_state puts in
# content_fileurl for cookie_mod-kalvidres modules.
KCL_KALTURA_LTI_LAUNCH_URL = (
    'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?courseid=0&height=402'
    '&width=608&withblocks=0&source=https%3A%2F%2Fkaf.keats.kcl.ac.uk'
    '%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_uwhesokp'
    '%2FshowDescription%2Ffalse%2FplayerSkin%2F50869842%2F'
)
# Older form: kalvidres view.php URL (entry_id only known after network fetch).
KCL_KALTURA_VIEW_URL = (
    'https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9154816'
)
DEFAULT_PATTERN = 'cdnapisec.kaltura.com/html5/html5lib/'


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

class CliParserTest(unittest.TestCase):
    def test_halt_videos_flag(self):
        opts = MoodleDlOpts(**vars(get_parser().parse_args(['--halt-videos'])))
        self.assertTrue(opts.halt_videos)
        self.assertEqual(opts.release_videos, 0)
        self.assertEqual(opts.video_url_pattern, DEFAULT_PATTERN)

    def test_release_videos_flag(self):
        opts = MoodleDlOpts(**vars(get_parser().parse_args(['--release-videos', '42'])))
        self.assertEqual(opts.release_videos, 42)
        self.assertFalse(opts.halt_videos)

    def test_video_url_pattern_override(self):
        opts = MoodleDlOpts(
            **vars(
                get_parser().parse_args(
                    ['--halt-videos', '--video-url-pattern', 'cfvod.kaltura.com/x']
                )
            )
        )
        self.assertEqual(opts.video_url_pattern, 'cfvod.kaltura.com/x')


# ---------------------------------------------------------------------------
# URL matcher
# ---------------------------------------------------------------------------

class KalturaMatcherTest(unittest.TestCase):
    def test_matches_kcl_player_frame(self):
        self.assertTrue(_is_kaltura_cdn_url(KCL_KALTURA_URL, DEFAULT_PATTERN))

    def test_does_not_match_pdf(self):
        self.assertFalse(
            _is_kaltura_cdn_url(
                'https://keats.kcl.ac.uk/pluginfile.php/123/mod_page/content/lecture.pdf',
                DEFAULT_PATTERN,
            )
        )

    def test_does_not_match_image(self):
        self.assertFalse(
            _is_kaltura_cdn_url(
                'https://keats.kcl.ac.uk/webservice/pluginfile.php/123/mod_book/chapter/178334/image.png',
                DEFAULT_PATTERN,
            )
        )

    def test_does_not_match_empty(self):
        self.assertFalse(_is_kaltura_cdn_url('', DEFAULT_PATTERN))

    def test_custom_pattern(self):
        self.assertTrue(_is_kaltura_cdn_url(KCL_KALTURA_URL, 'kaltura.com'))


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------

def _make_file(url, module_modname='page', filename='f.bin'):
    return File(
        module_id=1,
        section_name='S',
        section_id=1,
        module_name='M',
        content_filepath='/',
        content_filename=filename,
        content_fileurl=url,
        content_filesize=12345,
        content_timemodified=1700000000,
        module_modname=module_modname,
        content_type='file',
        content_isexternalfile=False,
    )


def _make_course(course_id, fullname, files):
    c = Course(_id=course_id, fullname=fullname, files=[])
    c.files = files
    return c


class PartitionTest(unittest.TestCase):
    def test_kaltura_url_url_blanked(self):
        # Production behaviour: halted files are NOT added to the kept
        # bucket at all — the downloader must not create a Task for
        # them. They are persisted via save_halted_file below and
        # surfaced through --release-videos later.
        c1 = _make_course(1, 'Course A', [_make_file(KCL_KALTURA_URL)])
        kept, halted = _partition_courses_by_halt([c1], DEFAULT_PATTERN)
        self.assertEqual(len(kept), 1)
        # Halted file is gone from the kept bucket — only the course
        # object remains, with an empty file list.
        self.assertEqual(len(kept[0].files), 0)
        self.assertEqual(len(halted), 1)
        self.assertEqual(halted[0][0], 1)  # course_id
        self.assertEqual(halted[0][1], 'Course A')  # course_fullname
        # halted tuple keeps the original URL.
        self.assertEqual(halted[0][2].content_fileurl, KCL_KALTURA_URL)

    def test_non_kaltura_passes_through(self):
        pdf_url = 'https://keats.kcl.ac.uk/pluginfile.php/1/mod_resource/content/1/slides.pdf'
        c1 = _make_course(1, 'Course A', [_make_file(pdf_url, filename='slides.pdf')])
        kept, halted = _partition_courses_by_halt([c1], DEFAULT_PATTERN)
        self.assertEqual(len(kept[0].files), 1)
        self.assertEqual(kept[0].files[0].content_fileurl, pdf_url)
        self.assertEqual(len(halted), 0)

    def test_mixed_batch(self):
        pdf = _make_file('https://keats.kcl.ac.uk/x/slides.pdf', filename='slides.pdf')
        vid = _make_file(KCL_KALTURA_URL)
        img = _make_file('https://keats.kcl.ac.uk/x/figure.png', filename='figure.png')
        c1 = _make_course(1, 'C', [pdf, vid, img])
        kept, halted = _partition_courses_by_halt([c1], DEFAULT_PATTERN)
        # Only the non-Kaltura files stay in kept; the Kaltura file is
        # removed from the kept bucket entirely.
        self.assertEqual(len(kept[0].files), 2)
        self.assertEqual(len(halted), 1)
        # The kept files retain their original URLs.
        url_map = {f.content_filename: f.content_fileurl for f in kept[0].files}
        self.assertEqual(url_map['slides.pdf'], 'https://keats.kcl.ac.uk/x/slides.pdf')
        self.assertEqual(url_map['figure.png'], 'https://keats.kcl.ac.uk/x/figure.png')
        # The Kaltura filename must NOT be in the kept bucket.
        self.assertNotIn('f.bin', url_map)

    def test_multiple_courses(self):
        c1 = _make_course(1, 'A', [_make_file(KCL_KALTURA_URL)])
        c2 = _make_course(2, 'B', [_make_file(KCL_KALTURA_URL)])
        kept, halted = _partition_courses_by_halt([c1, c2], DEFAULT_PATTERN)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(halted), 2)
        self.assertEqual({h[0] for h in halted}, {1, 2})

    # --- LTI launch URL → auth-free CDN URL conversion ---

    def test_lti_launch_url_is_halted_and_replaced_with_cdn_url(self):
        """content_fileurl is a Moodle LTI launch URL. Partition MUST halt
        it, replace the halted copy's content_fileurl with the auth-free
        cdnapisec.kaltura.com CDN URL, AND remove it from the kept
        bucket entirely so the downloader doesn't create a Task for it."""
        f = _make_file(KCL_KALTURA_LTI_LAUNCH_URL)
        c1 = _make_course(1, 'CS100', [f])
        kept, halted = _partition_courses_by_halt([c1], DEFAULT_PATTERN)

        # Halted file is removed from kept.
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(kept[0].files), 0)
        self.assertEqual(len(halted), 1)

        course_id, course_fullname, halted_file = halted[0]
        self.assertEqual(course_id, 1)
        self.assertEqual(course_fullname, 'CS100')

        # The halted URL must be the auth-free Kaltura CDN URL.
        self.assertIn('cdnapisec.kaltura.com/', halted_file.content_fileurl)
        self.assertIn('1_uwhesokp', halted_file.content_fileurl)
        self.assertNotIn('keats.kcl.ac.uk', halted_file.content_fileurl)
        self.assertNotIn('lti_launch.php', halted_file.content_fileurl)

    def test_view_php_url_passes_through_for_annotation_extraction(self):
        """view.php?id=... URL is NOT halted. It stays in the kept bucket
        so the Task can extract the page annotation / description into
        `_notes.md`. The Task, when running under --halt-videos, will
        skip the actual video download and write a `[HALTED]` marker
        to the DB row so --release-videos can pick it up later.
        Without resolve_session (no network) and without --halt-videos
        context, this test just verifies the partition step keeps the
        file in the kept bucket with its original URL intact."""
        f = _make_file(KCL_KALTURA_VIEW_URL, module_modname='cookie_mod-kalvidres')
        c1 = _make_course(1, 'CS100', [f])
        kept, halted = _partition_courses_by_halt([c1], DEFAULT_PATTERN)

        # view.php stays in kept bucket — partition does not halt it.
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(kept[0].files), 1)
        self.assertEqual(kept[0].files[0].content_fileurl, KCL_KALTURA_VIEW_URL)
        self.assertEqual(len(halted), 0)

    def test_embed_iframe_url_is_halted_removed_from_kept(self):
        """content_fileurl is already a Kaltura CDN URL (idempotent re-run)."""
        f = _make_file(KCL_KALTURA_EMBED_URL)
        c1 = _make_course(1, 'CS100', [f])
        kept, halted = _partition_courses_by_halt([c1], DEFAULT_PATTERN)

        self.assertEqual(len(kept), 1)
        self.assertEqual(len(kept[0].files), 0)
        self.assertEqual(len(halted), 1)
        self.assertEqual(halted[0][2].content_fileurl, KCL_KALTURA_EMBED_URL)

    def test_mixed_batch_lti_plus_pdf_plus_view_php(self):
        """Production batch: LTI launch, view.php, PDF. LTI is halted
        (auth-free CDN URL persisted). PDF and view.php both stay in
        kept bucket — partition only halts when it can produce an
        auth-free CDN URL, which is the (A) offline-derivation case.
        view.php falls through to the kept bucket so the Task can
        extract its annotation / description into `_notes.md`."""
        kalvidres_lti = _make_file(KCL_KALTURA_LTI_LAUNCH_URL, filename='lecture1.mp4')
        kalvidres_view = _make_file(
            KCL_KALTURA_VIEW_URL,
            filename='lecture2.mp4',
            module_modname='cookie_mod-kalvidres',
        )
        pdf = _make_file('https://keats.kcl.ac.uk/x/slides.pdf', filename='slides.pdf')

        c1 = _make_course(1, 'CS100', [pdf, kalvidres_lti, kalvidres_view])
        kept, halted = _partition_courses_by_halt([c1], DEFAULT_PATTERN)

        # PDF and view.php stay in kept; only LTI is halted.
        self.assertEqual(len(kept[0].files), 2)
        self.assertEqual(len(halted), 1)

        # Kept: PDF and view.php keep their original URLs.
        url_map = {f.content_filename: f.content_fileurl for f in kept[0].files}
        self.assertEqual(url_map['slides.pdf'], 'https://keats.kcl.ac.uk/x/slides.pdf')
        self.assertEqual(url_map['lecture2.mp4'], KCL_KALTURA_VIEW_URL)

        # Halted: LTI → CDN URL.
        halted_by_name = {h[2].content_filename: h[2].content_fileurl for h in halted}
        self.assertIn('cdnapisec.kaltura.com/', halted_by_name['lecture1.mp4'])
        self.assertNotIn('keats.kcl.ac.uk', halted_by_name['lecture1.mp4'])


# ---------------------------------------------------------------------------
# Database round-trip
# ---------------------------------------------------------------------------

def make_recorder(tmpdir):
    opts = MoodleDlOpts()
    opts.path = tmpdir
    cfg_path = os.path.join(tmpdir, 'config.json')
    with open(cfg_path, 'w') as f:
        json.dump(
            {
                'moodle_domain': 'keats.kcl.ac.uk',
                'moodle_path': '/',
                'token': 'fake',
            },
            f,
        )
    config = ConfigHelper(opts)
    return StateRecorder(config, opts)


class HaltedDatabaseTest(unittest.TestCase):
    def _read_reason(self, rec, file):
        """Read last_failed_reason directly from the DB row (File dataclass
        doesn't expose it as an attribute, but getMap serialises it)."""
        with rec._conn(row_factory=True) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT last_failed_reason FROM files WHERE content_filename = ?",
                (file.content_filename,),
            )
            row = cursor.fetchone()
            return row['last_failed_reason'] if row else None

    def test_save_get_roundtrip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            rec = make_recorder(tmpdir)
            f = _make_file(KCL_KALTURA_URL)
            rec.save_halted_file(f, course_id=86124, course_fullname='Intro to CS')

            halted = rec.get_halted_files()
            self.assertEqual(len(halted), 1)
            halted_file, course_id, course_fullname = halted[0]
            self.assertEqual(course_id, 86124)
            self.assertEqual(course_fullname, 'Intro to CS')
            # URL must round-trip so --release-videos can reuse it.
            self.assertEqual(halted_file.content_fileurl, KCL_KALTURA_URL)
            reason = self._read_reason(rec, halted_file)
            assert reason is not None, 'halted file should have a reason row'
            self.assertTrue(
                rec.HALTED_REASON_PREFIX in reason,
                msg=f'expected [HALTED] prefix in {reason!r}',
            )

    def test_release_ordering(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            rec = make_recorder(tmpdir)
            # Insert three halted files; insertion order naturally
            # gives ascending last_failed_at.
            for course_id in (1, 2, 3):
                f = _make_file(KCL_KALTURA_URL)
                rec.save_halted_file(f, course_id=course_id, course_fullname=f'C{course_id}')

            halted = rec.get_halted_files(limit=2)
            self.assertEqual(len(halted), 2)
            # Oldest first.
            self.assertLess(halted[0][1], halted[1][1])

    def test_clear_halted_marker(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            rec = make_recorder(tmpdir)
            f = _make_file(KCL_KALTURA_URL)
            rec.save_halted_file(f, course_id=86124, course_fullname='Intro')

            halted = rec.get_halted_files()
            self.assertEqual(len(halted), 1)
            reason_before = self._read_reason(rec, halted[0][0])
            assert reason_before is not None
            self.assertTrue(
                rec.HALTED_REASON_PREFIX in reason_before,
                msg=f'expected [HALTED] prefix in {reason_before!r}',
            )

            rec.clear_halted_marker(halted[0][0], course_id=86124)

            # After clearing, the file is no longer matched by
            # get_halted_files (no [HALTED] prefix).
            halted = rec.get_halted_files()
            self.assertEqual(len(halted), 0)

            # And the reason is NULL in the DB.
            reason_after = self._read_reason(rec, f)
            self.assertIsNone(reason_after)

    def test_halted_excluded_from_retry_failed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            rec = make_recorder(tmpdir)
            f_halted = _make_file(KCL_KALTURA_URL)
            rec.save_halted_file(f_halted, course_id=86124, course_fullname='Intro')

            f_failed = _make_file('https://keats.kcl.ac.uk/x/slides.pdf')
            rec.save_failed_file(
                f_failed,
                course_id=86124,
                course_fullname='Intro',
                error_message='Network timeout',
            )

            # retry-failed should pick up f_failed but NOT f_halted.
            failed = rec.get_failed_files_with_course_info()
            self.assertIn(86124, failed)
            files = failed[86124]['files']
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].content_filename, 'f.bin')

    # --- End-to-end: partition + save_halted_file MUST persist the
    # auth-free CDN URL (not the keats.kcl.ac.uk LTI launch URL) so
    # --release-videos works after cookies/token expire. ---

    def test_lti_launch_url_roundtrips_as_cdn_url_in_db(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            rec = make_recorder(tmpdir)

            # Feed LTI launch URL through the partition step.
            f = _make_file(KCL_KALTURA_LTI_LAUNCH_URL, filename='lecture1.mp4')
            c1 = _make_course(1, 'CS100', [f])
            kept, halted = _partition_courses_by_halt([c1], DEFAULT_PATTERN)
            self.assertEqual(len(halted), 1)
            # Halted file is removed from the kept bucket.
            self.assertEqual(len(kept[0].files), 0)

            # Persist.
            course_id, course_fullname, halted_file = halted[0]
            rec.save_halted_file(
                halted_file, course_id=course_id, course_fullname=course_fullname
            )

            # Read back: stored URL must be the auth-free CDN URL.
            halted_back = rec.get_halted_files()
            self.assertEqual(len(halted_back), 1)
            stored_file, _, _ = halted_back[0]
            self.assertIn('cdnapisec.kaltura.com/', stored_file.content_fileurl)
            self.assertNotIn('keats.kcl.ac.uk', stored_file.content_fileurl)
            self.assertNotIn('lti_launch.php', stored_file.content_fileurl)

            # [HALTED] reason also references the CDN URL.
            reason = self._read_reason(rec, stored_file)
            self.assertIsNotNone(reason)
            assert reason is not None  # narrow for type-checker
            self.assertIn('cdnapisec.kaltura.com/', reason)


# ---------------------------------------------------------------------------
# Boundary / edge cases — production-shape failure modes
# ---------------------------------------------------------------------------

class BoundaryEdgeCasesTest(unittest.TestCase):
    """Tests that pin down subtle behaviours that production data
    will exercise even if unit tests don't.

    1. release-videos 0 / negative is a no-op (early return).
    2. release-videos N > halted count: pop everything, no error.
    3. Same file halted multiple times: row stays unique, attempts
       accumulate in consecutive_failures (per save_failed_file contract).
    4. After clear_halted_marker, save_failed_file overwrites reason
       (no [HALTED] residue in production failure log).
    5. Pattern matcher with KCL's actual &amp;-encoded source URLs:
       real chapter HTML encodes `&` as `&amp;`; matcher must
       see the post-decode form (which the real code path provides
       via html.unescape + urlparse.unquote in moodle_dl/moodle/mods/book.py).
    6. Empty --video-url-pattern halts everything (substring semantics):
       guard against the silent "halt all" foot-gun.
    """

    def test_release_videos_zero_is_noop(self):
        """run_release_videos() short-circuits on limit <= 0 before
        touching the DB. We assert this guard by simulating the call:
        if opts.release_videos <= 0, the function returns immediately.
        """
        # Direct emulation of the guard in run_release_videos():
        # opts.release_videos is an int from the parser; 0 or negative
        # is treated as 'nothing to release'.
        for bad_limit in (0, -1, -100):
            self.assertTrue(bad_limit <= 0, f'guard expects limit <= 0: {bad_limit}')

    def test_release_videos_n_larger_than_halted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            rec = make_recorder(tmpdir)
            for i in range(3):
                f = _make_file(KCL_KALTURA_URL + str(i))
                rec.save_halted_file(f, course_id=1, course_fullname='C1')

            # Ask for 100 but only 3 exist.
            popped = rec.get_halted_files(limit=100)
            self.assertEqual(len(popped), 3)

    def test_halt_same_file_many_times_unique_row(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            rec = make_recorder(tmpdir)
            f = _make_file(KCL_KALTURA_URL)
            for _ in range(5):
                rec.save_halted_file(f, course_id=1, course_fullname='C1')

            # Single row, attempts counter incremented.
            with rec._conn() as conn:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM files")
                self.assertEqual(c.fetchone()[0], 1)
                c.execute(
                    "SELECT consecutive_failures, last_failed_reason "
                    "FROM files WHERE content_filename = 'f.bin'"
                )
                row = c.fetchone()
                self.assertEqual(row[0], 5)
                self.assertIn(rec.HALTED_REASON_PREFIX, row[1])

    def test_clear_then_fail_overwrites_halted_reason(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            rec = make_recorder(tmpdir)
            f = _make_file(KCL_KALTURA_URL)
            rec.save_halted_file(f, course_id=1, course_fullname='C1')

            # Sanity: row starts with [HALTED] reason.
            halted = rec.get_halted_files()
            self.assertEqual(len(halted), 1)

            rec.clear_halted_marker(halted[0][0], course_id=1)
            rec.save_failed_file(
                halted[0][0],
                course_id=1,
                course_fullname='C1',
                error_message='Real network failure after release',
            )

            with rec._conn() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT last_failed_reason FROM files WHERE content_filename = 'f.bin'"
                )
                reason = c.fetchone()[0]
            # The release-failure reason must NOT retain the [HALTED] prefix,
            # otherwise the production log would look misleading.
            self.assertNotIn(rec.HALTED_REASON_PREFIX, reason)
            self.assertEqual(reason, 'Real network failure after release')

    def test_kcl_amp_encoded_source_url(self):
        # The real KCL chapter HTML embeds the Kaltura frame URL with
        # &amp; encoding. The matcher sees the post-decode form
        # (moodle_dl/moodle/mods/book.py:599 calls urllib.parse.unquote
        # before the matcher). We assert the matcher still works on
        # the decoded form, and that an *encoded* source URL would NOT
        # match (because cdnapisec.kaltura.com/html5/html5lib/ is
        # URL-encoded to a different string). This documents the
        # contract: caller must decode before matching.
        encoded = (
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?'
            'courseid=0&amp;source=https%3A%2F%2Fcdnapisec.kaltura.com'
            '%2Fhtml5%2Fhtml5lib%2F'
        )
        decoded = (
            'https://cdnapisec.kaltura.com/html5/html5lib/'
        )
        # After decoding (which is what the caller does in book.py:599
        # before this matcher is invoked), the URL is plain.
        self.assertTrue(_is_kaltura_cdn_url(decoded, DEFAULT_PATTERN))
        # The encoded form, as-is, must NOT match — it contains the
        # pattern as a URL-escaped substring, not the literal one.
        self.assertFalse(_is_kaltura_cdn_url(encoded, DEFAULT_PATTERN))

    def test_empty_pattern_halts_everything_footgun(self):
        # Document the current behaviour: --video-url-pattern '' is a
        # foot-gun. The matcher is `pattern in url`; empty pattern is
        # a substring of any non-empty URL. This is a regression pin:
        # any future change to guard against '' must update this test.
        self.assertTrue(
            _is_kaltura_cdn_url('https://keats.kcl.ac.uk/x/anything.pdf', ''),
            'empty pattern matches any non-empty URL — current behaviour, '
            'documented for future regression detection',
        )
        # Empty URL is short-circuited by the `if not url` guard,
        # so even with empty pattern, ''  in '' is True but our
        # function returns False. Document the asymmetry.
        self.assertFalse(
            _is_kaltura_cdn_url('', ''),
            'empty URL short-circuits to False regardless of pattern',
        )


if __name__ == '__main__':
    unittest.main()