# -*- coding: utf-8 -*-
"""
Tests for extracting Kaltura videos from book chapter HTML.

Currently the extractor matches only `filter/kaltura/lti_launch.php`
URLs (the wrapper URL that KCL Moodle uses). But some book
chapters (and HTML pages) embed Kaltura videos with the
direct `cdnapisec.kaltura.com/embedIframeJs/...` URL.

This test pins the contract that BOTH patterns should be
extracted as downloadable videos.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.moodle.mods.book import BookMod


class TestExtractKalturaVideos(unittest.TestCase):
    """Pin the contract for Kaltura iframe extraction."""

    def _make_mod(self):
        # BookMod is a class; we only need a static-like method,
        # but the extraction methods need a chapter_name and
        # chapter_folder_name argument. Pass a minimal instance.
        # The methods are instance methods; we can construct a
        # mock-like BookMod by setting the minimum attrs.
        mod = BookMod.__new__(BookMod)
        return mod

    def test_extracts_lti_launch_iframe(self):
        mod = self._make_mod()
        html = '''
        <html><body>
        <iframe src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?custom_parameter=1
        &source=https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2Fentryid%2F1_er5gtb0g%2F..."></iframe>
        </body></html>
        '''
        videos = mod._extract_kaltura_videos_from_html(
            html, 'Chapter 1', course_id=1, module_id=1,
        )
        self.assertEqual(len(videos), 1)
        self.assertIn('1_er5gtb0g', videos[0]['entry_id'])

    def test_extracts_direct_embed_iframe(self):
        # NEW PATTERN: cdnapisec.kaltura.com/embedIframeJs/...
        mod = self._make_mod()
        html = '''
        <html><body>
        <iframe src="https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/uiconf_id/42864872/partner_id/2368101?iframeembed=true&playerId=kaltura_player&entry_id=1_bn1vhn06&..."></iframe>
        </body></html>
        '''
        videos = mod._extract_kaltura_videos_from_html(
            html, 'PCR Video', course_id=1, module_id=1,
        )
        # The new pattern should also extract this video
        self.assertEqual(len(videos), 1)
        self.assertIn('1_bn1vhn06', videos[0]['entry_id'])

    def test_extracts_both_patterns_in_same_chapter(self):
        mod = self._make_mod()
        html = '''
        <html><body>
        <iframe src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?...&source=...%2Fentryid%2F1_aaa/..."></iframe>
        <iframe src="https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/uiconf_id/42864872/partner_id/2368101?entry_id=1_bbb&..."></iframe>
        </body></html>
        '''
        videos = mod._extract_kaltura_videos_from_html(
            html, 'Mixed', course_id=1, module_id=1,
        )
        # Should extract BOTH videos
        self.assertEqual(len(videos), 2)
        entry_ids = {v['entry_id'] for v in videos}
        self.assertIn('1_aaa', entry_ids)
        self.assertIn('1_bbb', entry_ids)

    def test_ignores_youtube_iframe(self):
        # YouTube is public, should NOT be extracted
        mod = self._make_mod()
        html = '''
        <html><body>
        <iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
        </body></html>
        '''
        videos = mod._extract_kaltura_videos_from_html(
            html, 'Public Video', course_id=1, module_id=1,
        )
        self.assertEqual(len(videos), 0)


if __name__ == '__main__':
    unittest.main()
