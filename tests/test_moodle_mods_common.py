# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

import pytest

from moodle_dl.moodle.mods.common import MoodleMod
from moodle_dl.types import Course


class DummyMod(MoodleMod):
    MOD_NAME = 'dummy'
    MOD_PLURAL_NAME = 'dummies'
    MOD_MIN_VERSION = 2020061500

    @classmethod
    def download_condition(cls, config, file):
        return True

    async def real_fetch_mod_entries(self, courses, core_contents):
        if hasattr(self, 'error'):
            raise self.error
        return getattr(self, 'entries', {})


@pytest.fixture
def dummy_mod():
    return DummyMod(
        request_helper=MagicMock(),
        moodle_version=2023100900,
        user_id=42,
        last_timestamps={},
        config=MagicMock(),
    )


@pytest.mark.asyncio
async def test_fetch_mod_entries_returns_results_and_handles_failures(dummy_mod):
    dummy_mod.entries = {101: {7: {'id': 70, 'name': 'Entry'}}}

    assert await dummy_mod.fetch_mod_entries([Course(101, 'Course')], {}) == dummy_mod.entries

    dummy_mod.error = RuntimeError('mobile and web unavailable')
    assert await dummy_mod.fetch_mod_entries([Course(101, 'Course')], {}) == {}

    old_version = DummyMod(MagicMock(), 2019111800, 42, {}, MagicMock())
    old_version.error = RuntimeError('too old')
    assert await old_version.fetch_mod_entries([Course(101, 'Course')], {}) == {}


def test_common_mod_id_indexing_and_endpoint_payload(dummy_mod):
    entries = {
        101: {7: {'id': 700}, 8: {'id': 800}},
        202: {9: {'id': 900}},
    }

    assert dummy_mod.get_indexed_ids_of_mod_instances(entries) == {
        '0': 700,
        '1': 800,
        '2': 900,
    }
    assert dummy_mod.get_data_for_mod_entries_endpoint([
        Course(101, 'Course One'),
        Course(202, 'Course Two'),
    ]) == {'courseids': {'0': 101, '1': 202}}


def test_common_file_property_helpers(dummy_mod):
    file_dict = {'filename': 'old.pdf', 'filepath': '/old/'}
    dummy_mod.set_props_of_file(file_dict, filename='new.pdf', type='resource')
    assert file_dict == {'filename': 'new.pdf', 'filepath': '/old/', 'type': 'resource'}

    files = [{'filepath': '/sub/'}, {'filepath': '/'}, {}]
    dummy_mod.set_base_file_path_of_files(files, 'Module')
    assert files[0]['filepath'].endswith('Module/sub')
    assert files[1]['filepath'] == 'Module'
    assert files[2]['filepath'] == 'Module'


@pytest.mark.asyncio
async def test_common_async_entry_loading_and_collection():
    loaded = []

    async def load_entry(entry):
        loaded.append(entry['id'])
        return entry['id']

    await DummyMod.run_async_load_function_on_mod_entries(
        {101: {7: {'id': 7, 'name': 'Seven'}, 8: {'id': 8, 'name': 'Eight'}}},
        load_entry,
    )
    assert sorted(loaded) == [7, 8]

    async def collect(entry):
        if entry['id'] == 1:
            return [{'file': 'a'}, {'file': 'b'}]
        if entry['id'] == 2:
            return {'file': 'c'}
        return None

    assert await DummyMod.run_async_collect_function_on_list([], collect, 'item', {}) == []
    collected = await DummyMod.run_async_collect_function_on_list(
        [{'id': 1, 'meta': {'name': 'One'}}, {'id': 2, 'meta': {'name': 'Two'}}, {'id': 3}],
        collect,
        'item',
        {'collect_id': 'id', 'collect_name': 'meta.name'},
    )
    assert collected == [{'file': 'a'}, {'file': 'b'}, {'file': 'c'}]


def test_common_core_content_helpers(dummy_mod):
    courses = [Course(101, 'Course One'), Course(202, 'Course Two')]
    core_contents = {
        101: [
            {
                'modules': [
                    {'id': 1, 'modname': 'page'},
                    {'id': 2, 'modname': 'dummy'},
                ]
            }
        ],
        202: [{'modules': [{'id': 3, 'modname': 'dummy'}]}],
    }

    assert dummy_mod.get_module_in_core_contents(101, 2, core_contents) == {'id': 2, 'modname': 'dummy'}
    assert dummy_mod.get_module_in_core_contents(999, 1, core_contents) == {}
    assert dummy_mod.extract_modules_from_core_contents(courses, core_contents, 'dummy') == {
        101: [{'id': 2, 'modname': 'dummy'}],
        202: [{'id': 3, 'modname': 'dummy'}],
    }

    result = {}
    dummy_mod.add_module(result, 101, 2, {'id': 2})
    dummy_mod.add_module(result, 101, 2, {'id': 22})
    assert result == {101: {2: {'id': 22}}}


def test_common_metadata_intro_feature_and_introfile_helpers(dummy_mod):
    metadata_file = dummy_mod.create_metadata_file({'title': 'Über'}, filename='Meta: data', timemodified=123)
    assert metadata_file['filename'] == 'Meta\uff1a data.json'
    assert metadata_file['timemodified'] == 123
    assert '"title": "Über"' in metadata_file['content']

    assert dummy_mod.create_intro_file('', timemodified=1) is None
    intro_file = dummy_mod.create_intro_file('<p>Hello</p>', timemodified=2)
    assert intro_file == {
        'filename': 'Introduction.html',
        'filepath': '/',
        'description': '<p>Hello</p>',
        'type': 'description',
        'timemodified': 2,
    }

    features = dummy_mod.get_features('assessment', grade_has_grade=True, custom=True)
    assert features['purpose'] == 'assessment'
    assert features['grade_has_grade'] is True
    assert features['custom'] is True
    assert features['groups'] is True

    module = {
        'introfiles': [{'filename': 'intro.txt'}],
        'contentfiles': [{'filename': 'content.txt'}],
    }
    files = dummy_mod.get_introfiles(module, 'module_file', copy=True, additional_keys=['contentfiles'])
    assert [file['type'] for file in files] == ['module_file', 'module_file']
    assert module['introfiles'] is not files

    tuple_module = {
        'introfiles': ({'filename': 'tuple.txt'},),
        'mediafiles': [{'filename': 'media.txt'}],
    }
    tuple_files = dummy_mod.get_introfiles(tuple_module, 'media_file', additional_keys=['mediafiles'])
    assert [file['type'] for file in tuple_files] == ['media_file', 'media_file']
