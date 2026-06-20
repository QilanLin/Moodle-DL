# -*- coding: utf-8 -*-
import asyncio
import os
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods.common import MoodleMod
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.types import Course

from moodle_dl.moodle.mods.assign import AssignMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.bigbluebuttonbn import BigbluebuttonbnMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.book import BookMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.calendar import CalendarMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.chat import ChatMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.choice import ChoiceMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.data import DataMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.feedback import FeedbackMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.folder import FolderMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.forum import ForumMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.glossary import GlossaryMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.h5pactivity import H5PActivityMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.imscp import ImscpMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.label import LabelMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.lesson import LessonMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.lti import LtiMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.page import PageMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.qbank import QbankMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.quiz import QuizMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.resource import ResourceMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.scorm import ScormMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.subsection import SubsectionMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.survey import SurveyMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.url import UrlMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.wiki import WikiMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.workshop import WorkshopMod  # noqa: F401 isort:skip

ALL_MODS = [Class for name, Class in globals().items() if name.endswith('Mod') and name != 'MoodleMod']


def get_all_mods_classes() -> List[MoodleMod]:
    return ALL_MODS


def get_all_mods(
    request_helper: RequestHelper,
    moodle_version: int,
    user_id: int,
    last_timestamps: Dict[str, Dict[int, int]],
    config: ConfigHelper,
) -> List[MoodleMod]:
    result = []
    for mod in ALL_MODS:
        result.append(mod(request_helper, moodle_version, user_id, last_timestamps, config))
    return result


async def fetch_mods_files(
    mods_to_fetch: List[MoodleMod], courses_to_load: List[Course], core_contents: Dict[int, List[Dict]]
) -> Dict[str, Dict]:
    "@return: Dictionary of all fetched files, indexed by mod name, then by courses, then module id"
    if _uses_network_throttle(mods_to_fetch):
        # With a global user-facing throttle, starting every module fetch at
        # once just queues many requests before any of them can run. Fetching
        # modules in registry order keeps the queue short and lets multi-step
        # modules such as books finish their required page fetches before the
        # rest of the API endpoints reserve throttle slots.
        mods_results = []
        for mod in mods_to_fetch:
            mods_results.append(await mod.fetch_mod_entries(courses_to_load, core_contents))
    else:
        # 🔧 Hang fix: bound the gather with wait_for so a single
        # stuck mod fetch (e.g. Book fetching 1000 pages) doesn't
        # hang the whole download. The default is 5 minutes per
        # mod batch; the operator can override with the
        # ``MOODLE_DL_MOD_FETCH_TIMEOUT`` env var.
        mod_fetch_timeout = float(
            os.environ.get('MOODLE_DL_MOD_FETCH_TIMEOUT', '300')
        )
        mods_results = await asyncio.wait_for(
            asyncio.gather(
                *[mod.fetch_mod_entries(courses_to_load, core_contents) for mod in mods_to_fetch],
                return_exceptions=False,
            ),
            timeout=mod_fetch_timeout,
        )
    result = {}
    for idx, mod in enumerate(mods_to_fetch):
        result[mod.MOD_NAME] = mods_results[idx]
    return result


def _uses_network_throttle(mods_to_fetch: List[MoodleMod]) -> bool:
    return any(getattr(getattr(mod, 'client', None), 'network_throttle', None) is not None for mod in mods_to_fetch)


def get_mod_plurals():
    result = {}
    for mod in ALL_MODS:
        result[mod.MOD_NAME] = mod.MOD_PLURAL_NAME
    return result
