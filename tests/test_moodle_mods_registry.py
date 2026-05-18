# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace

import pytest

import moodle_dl.moodle.mods as mods


class DummyMod:
    MOD_NAME = "dummy"
    MOD_PLURAL_NAME = "dummies"

    def __init__(self, request_helper, moodle_version, user_id, last_timestamps, config):
        self.args = (request_helper, moodle_version, user_id, last_timestamps, config)

    async def fetch_mod_entries(self, courses_to_load, core_contents):
        return {
            "courses": courses_to_load,
            "core_contents": core_contents,
        }


class OtherDummyMod:
    MOD_NAME = "other"
    MOD_PLURAL_NAME = "others"

    def __init__(self, request_helper, moodle_version, user_id, last_timestamps, config):
        self.args = (request_helper, moodle_version, user_id, last_timestamps, config)

    async def fetch_mod_entries(self, courses_to_load, core_contents):
        return {"other": True}


class RecordingFetchMod:
    def __init__(self, mod_name, events, client=None):
        self.MOD_NAME = mod_name
        self.events = events
        self.client = client

    async def fetch_mod_entries(self, courses_to_load, core_contents):
        self.events.append(f'start:{self.MOD_NAME}')
        await asyncio.sleep(0)
        self.events.append(f'end:{self.MOD_NAME}')
        return {self.MOD_NAME: True}


def test_mod_registry_returns_classes_instances_and_plurals(monkeypatch):
    monkeypatch.setattr(mods, "ALL_MODS", [DummyMod, OtherDummyMod])
    request_helper = SimpleNamespace(name="request-helper")
    last_timestamps = {"dummy": {1: 2}}
    config = SimpleNamespace(name="config")

    assert mods.get_all_mods_classes() == [DummyMod, OtherDummyMod]
    instances = mods.get_all_mods(request_helper, 2024010100, 42, last_timestamps, config)

    assert [type(instance) for instance in instances] == [DummyMod, OtherDummyMod]
    assert instances[0].args == (request_helper, 2024010100, 42, last_timestamps, config)
    assert instances[1].args == (request_helper, 2024010100, 42, last_timestamps, config)
    assert mods.get_mod_plurals() == {"dummy": "dummies", "other": "others"}


@pytest.mark.asyncio
async def test_fetch_mods_files_indexes_results_by_mod_name():
    dummy = DummyMod(None, 0, 0, {}, None)
    other = OtherDummyMod(None, 0, 0, {}, None)
    courses = [SimpleNamespace(id=1)]
    core_contents = {1: [{"modules": []}]}

    result = await mods.fetch_mods_files([dummy, other], courses, core_contents)

    assert result == {
        "dummy": {
            "courses": courses,
            "core_contents": core_contents,
        },
        "other": {"other": True},
    }


@pytest.mark.asyncio
async def test_fetch_mods_files_runs_sequentially_when_network_throttled():
    events = []
    throttled_client = SimpleNamespace(network_throttle=object())
    first = RecordingFetchMod('first', events, throttled_client)
    second = RecordingFetchMod('second', events, throttled_client)

    result = await mods.fetch_mods_files([first, second], [SimpleNamespace(id=1)], {1: []})

    assert events == ['start:first', 'end:first', 'start:second', 'end:second']
    assert result == {'first': {'first': True}, 'second': {'second': True}}
