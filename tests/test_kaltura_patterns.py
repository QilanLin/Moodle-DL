# -*- coding: utf-8 -*-
"""
Tests for the centralized Kaltura pattern module.
"""
import sys
import unittest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.downloader.kaltura_patterns import (
    CDN_HOST,
    CONTENT_TYPE_COOKIE_MOD,
    CONTENT_TYPE_KALVIDRES_EMBEDDED,
    COOKIE_MOD_MODNAMES,
    EMBED_IFRAME_PATH,
    ENTRY_ID_PATH_RE,
    ENTRY_ID_QUERY_RE,
    IFRAME_RE,
    KALTURA_VIDEO_FILENAME_PREFIX,
    KALTURA_VIDEO_FILENAME_RE,
    LTI_LAUNCH_PATH,
    LTI_SOURCE_RE,
    MODULE_COOKIE_HELIXMEDIA,
    MODULE_COOKIE_KALVIDRES,
    entry_id_from_filename,
    extract_entry_id,
    is_direct_embed_url,
    is_kaltura_synthetic_filename,
    is_kaltura_url,
    is_lti_launch_url,
    kaltura_video_filename,
    reconstruct_url_from_entry_id,
)


class TestLtiLaunchPath(unittest.TestCase):
    def test_constant(self):
        self.assertEqual(LTI_LAUNCH_PATH, '/filter/kaltura/lti_launch.php')

    def test_is_lti_launch_url(self):
        self.assertTrue(is_lti_launch_url(
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?x=1'
        ))
        self.assertFalse(is_lti_launch_url(
            'https://cdnapisec.kaltura.com/p/1/embedIframeJs/...?entry_id=1_x'
        ))
        self.assertFalse(is_lti_launch_url(''))
        self.assertFalse(is_lti_launch_url(None))


class TestDirectEmbedUrl(unittest.TestCase):
    def test_is_direct_embed_url(self):
        self.assertTrue(is_direct_embed_url(
            f'https://{CDN_HOST}/p/2368101/sp/236810100/embedIframeJs/foo?entry_id=1_x'
        ))
        self.assertFalse(is_direct_embed_url(
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?x=1'
        ))
        self.assertFalse(is_direct_embed_url(''))

    def test_is_kaltura_url(self):
        # Both forms
        self.assertTrue(is_kaltura_url(
            f'https://{CDN_HOST}/p/1/embedIframeJs/...?entry_id=1_x'
        ))
        self.assertTrue(is_kaltura_url(
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?x=1'
        ))
        # Neither
        self.assertFalse(is_kaltura_url('https://www.youtube.com/embed/xxx'))
        self.assertFalse(is_kaltura_url(''))
        self.assertFalse(is_kaltura_url(None))


class TestExtractEntryId(unittest.TestCase):
    def test_lti_launch_form(self):
        url = (
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?'
            'custom_parameter=1'
            '&source=https%3A%2F%2Fkaf.keats.kcl.ac.uk%2F'
            'browseandembed%2Findex%2Fmedia%2Fentryid%2F1_er5gtb0g%2F...'
        )
        self.assertEqual(extract_entry_id(url), '1_er5gtb0g')

    def test_direct_embed_form(self):
        url = (
            f'https://{CDN_HOST}/p/2368101/sp/236810100/embedIframeJs/'
            f'uiconf_id/42864872/partner_id/2368101?'
            f'entry_id=1_bn1vhn06&playerId=kaltura_player'
        )
        self.assertEqual(extract_entry_id(url), '1_bn1vhn06')

    def test_empty_url(self):
        self.assertEqual(extract_entry_id(''), '')
        self.assertEqual(extract_entry_id(None), '')

    def test_non_kaltura_url(self):
        self.assertEqual(extract_entry_id('https://www.youtube.com/embed/abc'), '')

    def test_lti_launch_missing_source(self):
        url = 'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php'
        self.assertEqual(extract_entry_id(url), '')

    def test_html_entity_amp(self):
        # URL has &amp; instead of & (HTML-escaped ampersand)
        url = (
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php'
            '?foo=1&amp;source=https%3A%2F%2Fkaf.example.com%2F'
            'browseandembed%2Findex%2Fmedia%2Fentryid%2F1_a%2Fview'
        )
        self.assertEqual(extract_entry_id(url), '1_a')

    def test_double_encoded_source(self):
        # Source param itself is double-encoded (e.g. %252F for %2F)
        url = (
            'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?source='
            'https%253A%252F%252Fkaf.example.com%252Fbrowseandembed%252F'
            'index%252Fmedia%252Fentryid%252F1_double%252Fview'
        )
        self.assertEqual(extract_entry_id(url), '1_double')

    def test_camelcase_entryId(self):
        # Some KCL URLs use entryId (camelCase) instead of entryid
        url = (
            'https://media.kcl.ac.uk/embed/secure/iframe/'
            'entryId/1_5eu7vehb/uiConfId/50622292'
        )
        self.assertEqual(extract_entry_id(url), '1_5eu7vehb')


class TestIframeRe(unittest.TestCase):
    def test_lti_launch_iframe(self):
        html = '<iframe src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?x=1"></iframe>'
        m = IFRAME_RE.search(html)
        self.assertIsNotNone(m)
        self.assertIn('lti_launch.php', m.group(1))

    def test_direct_embed_iframe(self):
        html = (
            '<iframe src="https://cdnapisec.kaltura.com/p/2368101/sp/236810100/'
            'embedIframeJs/uiconf_id/42864872/partner_id/2368101?'
            'entry_id=1_x&playerId=k"></iframe>'
        )
        m = IFRAME_RE.search(html)
        self.assertIsNotNone(m)
        self.assertIn(CDN_HOST, m.group(1))

    def test_youtube_iframe_not_matched(self):
        html = '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
        m = IFRAME_RE.search(html)
        self.assertIsNone(m)

    def test_path_with_multiple_segments(self):
        # /embedIframeJs/ can be at any path depth
        url = (
            f'https://{CDN_HOST}/a/b/c/embedIframeJs/foo?x=1'
        )
        m = IFRAME_RE.search(f'<iframe src="{url}"></iframe>')
        self.assertIsNotNone(m)
        self.assertIn('embedIframeJs', m.group(1))


class TestReconstructUrl(unittest.TestCase):
    def test_reconstruct_from_entry_id(self):
        url = reconstruct_url_from_entry_id('1_test')
        self.assertIn('entry_id=1_test', url)
        self.assertIn(CDN_HOST, url)
        self.assertIn(EMBED_IFRAME_PATH, url)


class TestModuleConstants(unittest.TestCase):
    """Pin the values of the module modname constants so any
    change triggers an explicit test update."""

    def test_cookie_kalvidres_constant(self):
        self.assertEqual(MODULE_COOKIE_KALVIDRES, 'cookie_mod-kalvidres')

    def test_cookie_helixmedia_constant(self):
        self.assertEqual(MODULE_COOKIE_HELIXMEDIA, 'cookie_mod-helixmedia')

    def test_cookie_mod_modnames(self):
        self.assertEqual(COOKIE_MOD_MODNAMES,
                         ('cookie_mod-kalvidres', 'cookie_mod-helixmedia'))

    def test_content_type_constants(self):
        self.assertEqual(CONTENT_TYPE_KALVIDRES_EMBEDDED, 'kalvidres_embedded')
        self.assertEqual(CONTENT_TYPE_COOKIE_MOD, 'cookie_mod')


class TestKalturaVideoFilename(unittest.TestCase):
    """Pin the synthetic Kaltura video filename pattern."""

    def test_filename_prefix(self):
        self.assertEqual(KALTURA_VIDEO_FILENAME_PREFIX, 'kaltura_video_')

    def test_kaltura_video_filename(self):
        self.assertEqual(
            kaltura_video_filename('1_a'),
            'kaltura_video_1_a.mp4',
        )

    def test_kaltura_video_filename_with_dashes(self):
        # entry_id may contain underscores
        self.assertEqual(
            kaltura_video_filename('1_test_video'),
            'kaltura_video_1_test_video.mp4',
        )

    def test_entry_id_from_filename(self):
        self.assertEqual(
            entry_id_from_filename('kaltura_video_1_a.mp4'),
            '1_a',
        )

    def test_entry_id_from_filename_no_match(self):
        self.assertEqual(entry_id_from_filename('not_kaltura.mp4'), '')
        self.assertEqual(entry_id_from_filename(''), '')

    def test_is_kaltura_synthetic_filename(self):
        self.assertTrue(is_kaltura_synthetic_filename('kaltura_video_1_a.mp4'))
        self.assertFalse(is_kaltura_synthetic_filename('not_kaltura.mp4'))
        self.assertFalse(is_kaltura_synthetic_filename(''))

    def test_filename_pattern_is_strict(self):
        # The pattern should only match the exact kaltura_video_*.mp4
        # shape — not other video filenames.
        self.assertFalse(is_kaltura_synthetic_filename('kaltura_video_1_a.txt'))
        self.assertFalse(is_kaltura_synthetic_filename('prefix_kaltura_video_1_a.mp4'))


class TestRegexReusability(unittest.TestCase):
    """All entry_id regexes are the same shape and produce
    the same groups; this pins that."""

    def test_path_and_query_match_same_pattern(self):
        # Both should be the same regex shape
        self.assertIn('entryid', ENTRY_ID_PATH_RE.pattern)
        self.assertIn('entry_id', ENTRY_ID_QUERY_RE.pattern)

    def test_lti_source_re(self):
        m = LTI_SOURCE_RE.search('https://x?source=ABC&foo=1')
        self.assertEqual(m.group(1), 'ABC')


if __name__ == '__main__':
    unittest.main()
