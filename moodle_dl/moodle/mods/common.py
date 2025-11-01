import asyncio
import json
import logging
import math
from abc import ABCMeta, abstractmethod
from typing import Dict, List, Optional

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT
from moodle_dl.utils import get_nested, run_with_final_message

# Moodle 版本代码到版本号的映射
MOODLE_VERSION_MAP = {
    2012120300: '2.4',
    2013051400: '2.5',
    2015051100: '2.9',
    2015111600: '3.0',
    2016052300: '3.1',
    2017051500: '3.3',
    2017111300: '3.4',
    2019111800: '3.8',
    2020061500: '3.9',
    2023100900: '4.3',
}


class MoodleMod(metaclass=ABCMeta):
    """
    Common class for a Moodle module endpoint
    """

    MOD_NAME = None
    MOD_PLURAL_NAME = None
    MOD_MIN_VERSION = None

    def __init__(
        self,
        request_helper: RequestHelper,
        moodle_version: int,
        user_id: int,
        last_timestamps: Dict[str, Dict[int, int]],
        config: ConfigHelper,
    ):
        """
        @param last_timestamps: A Dict per mod of timestamps per course module id,
                                prevents downloading older content of a corse module
        """

        self.client = request_helper
        self.version = moodle_version
        self.user_id = user_id
        self.last_timestamps = last_timestamps
        self.config = config

    @classmethod
    @abstractmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        """
        Return True if moodle-dl is configured to downloaded the given file
        This condition is applied after comparing the current status with the local database
        """
        # TODO: Make module download conditions more granular and more generally
        # (do not only filter "deleted" mod files but all?)
        pass

    async def fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        if self.version < self.MOD_MIN_VERSION:
            # 将版本代码转换为可读的 Moodle 版本号
            required_version = MOODLE_VERSION_MAP.get(self.MOD_MIN_VERSION, str(self.MOD_MIN_VERSION))
            logging.warning(
                '⚠️  跳过 %s：当前 Moodle 版本不支持此模块（需要 Moodle %s 或更高版本）',
                self.MOD_PLURAL_NAME,
                required_version,
            )
            return {}

        result = await self.real_fetch_mod_entries(courses, core_contents)
        logging.info('已加载所有%s', self.MOD_PLURAL_NAME)
        return result

    def get_indexed_ids_of_mod_instances(self, mod_instances: Dict[int, Dict[int, Dict]]):
        """
        @param mod_instances: Dictionary of all mod instanced, indexed by courses, then module id
        """
        mod_instances_ids = {}
        idx = 0
        for _, modules in mod_instances.items():
            for _, mod in modules.items():
                mod_instances_ids[str(idx)] = mod['id']
                idx += 1
        return mod_instances_ids

    def get_data_for_mod_entries_endpoint(self, courses: List[Course]):
        # Create a dictionary with all the courses we want to request
        course_ids = {}
        for idx, course in enumerate(courses):
            course_ids[str(idx)] = course.id
        return {'courseids': course_ids}

    @abstractmethod
    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetches the mod entries for all courses
        @return: Dictionary of all course modules of that mod type, indexed by course id, then course module id
        """
        pass

    @staticmethod
    def set_props_of_file(file_dict: Dict, **props):
        for prop, value in props.items():
            if (
                file_dict.get(prop) is not None
                and file_dict[prop] != ''
                and file_dict[prop] != value
                and (prop != 'filepath' or file_dict[prop] != '/')
            ):
                logging.debug('Overwriting %r with %r of file %s', prop, value, file_dict)
            file_dict[prop] = value

    @classmethod
    def set_props_of_files(cls, files: List[Dict], **props):
        for file_dict in files:
            cls.set_props_of_file(file_dict, **props)

    @staticmethod
    def set_base_file_path_of_file(file_dict: Dict, base_file_path: str):
        old_file_path = file_dict.get('filepath')
        if old_file_path is not None and old_file_path != '/':
            file_dict['filepath'] = PT.make_path(base_file_path, old_file_path.strip('/'))
        else:
            file_dict['filepath'] = base_file_path

    @classmethod
    def set_base_file_path_of_files(cls, files: List[Dict], base_file_path: str):
        for file_dict in files:
            cls.set_base_file_path_of_file(file_dict, base_file_path)

    @classmethod
    async def run_async_load_function_on_mod_entries(cls, entries: Dict[int, Dict[int, Dict]], load_function):
        """
        Runs a load function on every module in a given entries list
        @param entries: Dictionary of all module entries, indexed by courses, then module id
        """
        ctr = 0
        total_entries = 0
        for _, entries_in_course in entries.items():
            total_entries += len(entries_in_course)

        if total_entries == 0:
            return
        ctr_digits = int(math.log10(total_entries)) + 1

        async_features = []
        for course_id, entries_in_course in entries.items():
            for module_id, entry in entries_in_course.items():
                ctr += 1

                # Example: [ 5/16] Loaded assign 123 in course 456 "Assignment name"
                loaded_message = (
                    f'[%(ctr){ctr_digits}d/%(total){ctr_digits}d] Loaded %(mod_name)s %(module_id)d'
                    + ' in course %(course_id)d "%(module_name)s"'
                )

                async_features.append(
                    run_with_final_message(
                        load_function,
                        entry,
                        loaded_message,
                        {
                            'ctr': ctr,
                            'total': total_entries,
                            'mod_name': cls.MOD_NAME,
                            'module_id': module_id,
                            'course_id': course_id,
                            'module_name': entry.get('name', ''),
                        },
                    )
                )

        await asyncio.gather(*async_features)

    @classmethod
    async def run_async_collect_function_on_list(
        cls,
        entries: List[Dict],
        collect_function,
        collect_kind: str,
        format_mapping: Dict,
    ) -> List[Dict]:
        "Runs a collect function on every entry in a given entries list"
        total_entries = len(entries)
        if total_entries == 0:
            return []
        ctr_digits = int(math.log10(total_entries)) + 1

        async_features = []
        for ctr, entry in enumerate(entries):
            # Example: [ 5/16] Loaded forum discussion 123 "Good discussion"
            loaded_message = (
                f'[%(ctr){ctr_digits}d/%(total){ctr_digits}d] Loaded %(mod_name)s %(collect_kind)s'
                + ' %(collect_id)d "%(collect_name)s"'
            )

            async_features.append(
                run_with_final_message(
                    collect_function,
                    entry,
                    loaded_message,
                    {
                        'ctr': ctr + 1,
                        'total': total_entries,
                        'mod_name': cls.MOD_NAME,
                        'collect_kind': collect_kind,
                        'collect_id': get_nested(entry, format_mapping['collect_id'], 0),
                        'collect_name': get_nested(entry, format_mapping['collect_name'], ''),
                    },
                )
            )

        result = []
        for feature_result in await asyncio.gather(*async_features):
            if isinstance(feature_result, list):
                result.extend(feature_result)
            elif feature_result is not None:
                result.append(feature_result)
        return result

    @staticmethod
    def get_module_in_core_contents(course_id: int, module_id: int, core_contents: Dict[int, List[Dict]]) -> Dict:
        course_section = core_contents.get(course_id, [])
        for section in course_section:
            for module in section.get('modules', []):
                if module.get('id', 0) == module_id:
                    return module
        return {}

    @staticmethod
    def add_module(result: Dict, course_id: int, module_id: int, module: Dict):
        if course_id not in result:
            result[course_id] = {}
        if module_id in result[course_id]:
            logging.warning('Got duplicated module %s in course %s', module_id, course_id)
        result[course_id][module_id] = module

    # ==================== DRY Helper Methods ====================
    # These methods reduce code duplication across module implementations

    @staticmethod
    def create_metadata_file(
        metadata: Dict,
        filename: str = 'metadata',
        filepath: str = '/',
        timemodified: int = 0
    ) -> Dict:
        """
        Helper method to create a metadata JSON file dictionary.

        Args:
            metadata: The metadata dictionary to export as JSON
            filename: Base filename (without .json extension), default 'metadata'
            filepath: File path, default '/'
            timemodified: Modification timestamp, default 0

        Returns:
            Dict: File dictionary ready to be appended to module files list

        Example:
            metadata_file = self.create_metadata_file(metadata)
            module_files.append(metadata_file)
        """
        return {
            'filename': PT.to_valid_name(filename, is_file=True) + '.json',
            'filepath': filepath,
            'timemodified': timemodified,
            'content': json.dumps(metadata, indent=2, ensure_ascii=False),
            'type': 'content',
        }

    @staticmethod
    def create_intro_file(intro: str, timemodified: int = 0) -> Optional[Dict]:
        """
        Helper method to create an Introduction description file.

        Args:
            intro: The introduction/description text
            timemodified: Modification timestamp, default 0

        Returns:
            Dict or None: File dictionary if intro is not empty, None otherwise

        Example:
            intro_file = self.create_intro_file(module_intro, module_time)
            if intro_file:
                module_files.append(intro_file)
        """
        if not intro or intro == '':
            return None

        return {
            'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
            'filepath': '/',
            'description': intro,
            'type': 'description',
            'timemodified': timemodified,
        }

    # Default features for Moodle modules
    DEFAULT_FEATURES = {
        'groups': True,
        'groupings': True,
        'intro_support': True,
        'completion_tracks_views': True,
        'grade_has_grade': False,
        'grade_outcomes': True,
        'backup_moodle2': True,
        'show_description': True,
    }

    @classmethod
    def get_features(cls, purpose: str = 'content', **overrides) -> Dict:
        """
        Get module features dictionary with optional overrides.

        Args:
            purpose: Module purpose ('content', 'communication', 'collaboration', 'assessment')
            **overrides: Any feature values to override (e.g., grade_has_grade=True)

        Returns:
            Dict: Complete features dictionary

        Example:
            'features': self.get_features(
                purpose='collaboration',
                grade_has_grade=True,
                completion_has_rules=True
            )
        """
        features = cls.DEFAULT_FEATURES.copy()
        features['purpose'] = purpose
        features.update(overrides)
        return features

    def get_introfiles(
        self,
        module: Dict,
        file_type: str,
        copy: bool = False,
        additional_keys: List[str] = None
    ) -> List[Dict]:
        """
        Helper method to get and process introfiles from a module dictionary.

        This helper reduces the repetitive pattern of getting introfiles,
        optionally merging with other file keys, and setting the file type.

        Args:
            module: Module dictionary containing 'introfiles' key
            file_type: Type to set for the files (e.g., 'module_introfile', 'module_file')
            copy: Whether to copy the list to avoid modifying original dict (default False)
            additional_keys: Additional file keys to merge (e.g., ['mediafiles', 'contentfiles'])

        Returns:
            List[Dict]: Processed file dictionaries with type property set

        Examples:
            # Simple case:
            files = self.get_introfiles(module, 'module_introfile')

            # With copy:
            files = self.get_introfiles(module, 'module_file', copy=True)

            # With additional file keys:
            files = self.get_introfiles(
                module, 'page_file',
                additional_keys=['contentfiles']
            )
        """
        # Get introfiles with optional copy
        if copy:
            files = list(module.get('introfiles', []))
        else:
            files = module.get('introfiles', [])

        # Merge additional file keys if specified
        if additional_keys:
            for key in additional_keys:
                additional_files = module.get(key, [])
                if isinstance(files, list):
                    files += additional_files
                else:
                    # If files was not a list, convert it
                    files = list(files) + additional_files

        # Set type for all files
        self.set_props_of_files(files, type=file_type)

        return files
