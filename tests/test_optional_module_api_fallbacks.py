# -*- coding: utf-8 -*-
import unittest
from unittest.mock import AsyncMock, MagicMock

from moodle_dl.moodle.mods.feedback import FeedbackMod
from moodle_dl.moodle.mods.glossary import GlossaryMod
from moodle_dl.moodle.mods.h5pactivity import H5PActivityMod


class TestOptionalModuleAPIFallbacks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.async_post = AsyncMock()

        self.config = MagicMock()
        self.config.get_download_h5pactivities.return_value = True
        self.config.get_download_h5p_attempts.return_value = True
        self.config.get_download_glossaries.return_value = True
        self.config.get_download_feedbacks.return_value = True

    async def test_h5p_attempts_use_current_user_endpoint(self):
        mod = H5PActivityMod(self.client, 2024010100, 42, {}, self.config)
        h5p = {'id': 99, 'files': []}

        async def side_effect(wsfunction, data):
            if wsfunction == 'mod_h5pactivity_get_attempts':
                self.assertEqual(data, {'h5pactivityid': 99, 'userids': [42]})
                return {
                    'usersattempts': [
                        {
                            'attempts': [
                                {
                                    'id': 7,
                                    'attempt': 1,
                                    'duration': 12,
                                    'completion': True,
                                    'success': True,
                                    'timemodified': 100,
                                }
                            ]
                        }
                    ]
                }
            if wsfunction == 'mod_h5pactivity_get_results':
                return {
                    'attemptsdata': [
                        {
                            'scored': {
                                'rawscore': 2,
                                'maxscore': 3,
                                'scaledscore': 2 / 3,
                            },
                            'results': [],
                        }
                    ]
                }
            raise AssertionError(f'Unexpected WS call: {wsfunction}')

        self.client.async_post.side_effect = side_effect

        await mod.load_h5p_attempts(h5p)

        called_functions = [call.args[0] for call in self.client.async_post.await_args_list]
        self.assertIn('mod_h5pactivity_get_attempts', called_functions)
        self.assertNotIn('mod_h5pactivity_get_user_attempts', called_functions)
        self.assertEqual(h5p['files'][0]['filepath'], '/attempts/')

    async def test_glossary_falls_back_from_letter_to_date_and_paginates(self):
        mod = GlossaryMod(self.client, 2024010100, 42, {}, self.config)
        glossary = {
            'id': 10,
            'files': [],
            # Simulate missing/incorrect browse mode metadata so the loader must fall back.
            'browsemodes': [],
        }

        async def side_effect(wsfunction, data):
            if wsfunction == 'mod_glossary_get_entries_by_letter':
                raise Exception('invalidbrowsemode')
            if wsfunction == 'mod_glossary_get_entries_by_date':
                if data['from'] == 0:
                    return {
                        'count': 2,
                        'entries': [
                            {
                                'id': 1,
                                'glossaryid': 10,
                                'concept': 'Alpha',
                                'definition': 'First',
                                'userfullname': 'Teacher',
                            }
                        ],
                    }
                if data['from'] == 1:
                    return {
                        'count': 2,
                        'entries': [
                            {
                                'id': 2,
                                'glossaryid': 10,
                                'concept': 'Beta',
                                'definition': 'Second',
                                'userfullname': 'Teacher',
                            }
                        ],
                    }
            raise AssertionError(f'Unexpected WS call: {wsfunction} {data}')

        self.client.async_post.side_effect = side_effect

        await mod.load_glossary_entries(glossary)

        called_functions = [call.args[0] for call in self.client.async_post.await_args_list]
        self.assertEqual(called_functions[0], 'mod_glossary_get_entries_by_letter')
        self.assertEqual(called_functions[1:], ['mod_glossary_get_entries_by_date', 'mod_glossary_get_entries_by_date'])
        filenames = [entry['filename'] for entry in glossary['files']]
        self.assertIn('Alpha', filenames)
        self.assertIn('Beta', filenames)

    async def test_feedback_skips_analysis_when_access_denies_it(self):
        mod = FeedbackMod(self.client, 2024010100, 42, {}, self.config)

        async def side_effect(wsfunction, data):
            if wsfunction == 'mod_feedback_get_feedback_access_information':
                return {'canviewanalysis': False}
            raise AssertionError(f'Unexpected WS call: {wsfunction}')

        self.client.async_post.side_effect = side_effect

        result = await mod._get_feedback_analysis(123)

        self.assertEqual(result, {})
        called_functions = [call.args[0] for call in self.client.async_post.await_args_list]
        self.assertEqual(called_functions, ['mod_feedback_get_feedback_access_information'])

    async def test_feedback_fetches_analysis_when_access_allows_it(self):
        mod = FeedbackMod(self.client, 2024010100, 42, {}, self.config)

        async def side_effect(wsfunction, data):
            if wsfunction == 'mod_feedback_get_feedback_access_information':
                return {'canviewanalysis': True}
            if wsfunction == 'mod_feedback_get_analysis':
                return {'completedcount': 5, 'itemscount': 2, 'itemsdata': [{'id': 1}], 'warnings': []}
            raise AssertionError(f'Unexpected WS call: {wsfunction}')

        self.client.async_post.side_effect = side_effect

        result = await mod._get_feedback_analysis(123)

        self.assertEqual(result['completedcount'], 5)
        called_functions = [call.args[0] for call in self.client.async_post.await_args_list]
        self.assertEqual(
            called_functions,
            ['mod_feedback_get_feedback_access_information', 'mod_feedback_get_analysis'],
        )
