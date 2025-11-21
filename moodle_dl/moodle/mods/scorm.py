# -*- coding: utf-8 -*-
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class ScormMod(MoodleMod):
    """
    SCORM module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/scorm/services/scorm.ts

    Supports:
    - Downloading SCORM packages
    - SCO (Sharable Content Object) information
    - User attempt tracking (optional)
    - Metadata export
    """

    MOD_NAME = 'scorm'
    MOD_PLURAL_NAME = 'scorms'
    MOD_MIN_VERSION = 2015111600  # 3.0

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_scorms() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all SCORM packages from courses

        Process:
        1. Get SCORM activities by courses
        2. Download SCORM packages
        3. Get SCOs (content objects) information
        4. Optionally download user tracking data
        """

        result = {}
        if not self.config.get_download_scorms():
            return result

        # 首先尝试使用 Mobile API
        try:
            response = await self.client.async_post(
                'mod_scorm_get_scorms_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            scorms = response.get('scorms', [])
        except (RequestRejectedError, Exception) as e:
            # Mobile API 失败，尝试 Web API fallback
            logging.debug(f"Mobile API 获取 SCORM 模块失败: {e}，尝试使用 Web API fallback...")
            scorms = await self._fetch_scorms_web_api(courses, core_contents)

        for scorm in scorms:
            course_id = scorm.get('course', 0)
            module_id = scorm.get('coursemodule', 0)
            scorm_name = scorm.get('name', 'unnamed scorm')

            # Get intro files
            scorm_files = self.get_introfiles(scorm, 'scorm_introfile')

            # Add intro description
            scorm_intro = scorm.get('intro', '')
            intro_file = self.create_intro_file(scorm_intro)
            if intro_file:
                intro_file['filename'] = 'SCORM intro'
                scorm_files.append(intro_file)

            # Add SCORM package file
            package_url = scorm.get('packageurl', '')
            if package_url:
                package_file = {
                    'filename': PT.to_valid_name(scorm_name, is_file=True) + '.zip',
                    'filepath': '/package/',
                    'fileurl': package_url,
                    'filesize': scorm.get('packagesize', 0),
                    'timemodified': scorm.get('timemodified', 0),
                }
                self.set_props_of_file(package_file, type='scorm_package')
                scorm_files.append(package_file)

            # Create metadata file
            metadata = {
                'scorm_id': scorm.get('id', 0),
                'course_id': course_id,
                'name': scorm_name,
                'version': scorm.get('version', 'unknown'),  # SCORM_12, SCORM_13, SCORM_AICC
                'reference': scorm.get('reference', ''),
                'grade': {
                    'max_grade': scorm.get('maxgrade', 0),
                    'grade_method': scorm.get('grademethod', 0),
                    'what_grade': scorm.get('whatgrade', 0),  # How attempts are graded
                },
                'attempts': {
                    'max_attempt': scorm.get('maxattempt', 0),
                    'force_completed': scorm.get('forcecompleted', False),
                    'force_new_attempt': scorm.get('forcenewattempt', 0),
                    'last_attempt_lock': scorm.get('lastattemptlock', False),
                },
                'display': {
                    'display_course_structure': scorm.get('displaycoursestructure', False),
                    'display_attempt_status': scorm.get('displayattemptstatus', 0),
                    'hide_browse': scorm.get('hidebrowse', False),
                    'hide_toc': scorm.get('hidetoc', 0),
                    'skip_view': scorm.get('skipview', 0),
                    'popup': scorm.get('popup', 0),
                    'width': scorm.get('width', 0),
                    'height': scorm.get('height', 0),
                },
                'navigation': {
                    'nav': scorm.get('nav', 0),
                    'nav_position_left': scorm.get('navpositionleft', -100),
                    'nav_position_top': scorm.get('navpositiontop', -100),
                },
                'options': {
                    'auto_continue': scorm.get('auto', False),
                    'auto_commit': scorm.get('autocommit', False),
                },
                'completion': {
                    'completion_status_required': scorm.get('completionstatusrequired', 0),
                    'completion_score_required': scorm.get('completionscorerequired', 0),
                    'completion_status_all_scos': scorm.get('completionstatusallscos', 0),
                },
                'timestamps': {
                    'time_open': scorm.get('timeopen', 0),
                    'time_close': scorm.get('timeclose', 0),
                    'time_modified': scorm.get('timemodified', 0),
                },
                'hashes': {
                    'sha1': scorm.get('sha1hash', ''),
                    'md5': scorm.get('md5hash', ''),
                    'revision': scorm.get('revision', 0),
                },
            }

            scorm_files.append(
                self.create_metadata_file(metadata, timemodified=scorm.get('timemodified', 0))
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': scorm.get('id', 0),
                    'name': scorm_name,
                    'files': scorm_files,
                },
            )

        # Optionally load SCO information and user attempts
        if self.config.get_download_scorm_scos():
            await self.add_scorm_scos(result)

        return result

    async def add_scorm_scos(self, scorms: Dict[int, Dict[int, Dict]]):
        """
        Fetch SCORM SCOs (Sharable Content Objects) and user data

        @param scorms: Dictionary of all SCORM activities
        """
        await self.run_async_load_function_on_mod_entries(scorms, self.load_scorm_scos)

    async def load_scorm_scos(self, scorm: Dict):
        """
        Load SCOs and user tracking data for a SCORM activity

        Process:
        1. Get all SCOs for the SCORM
        2. Optionally get user attempt data
        3. Create summary files
        """
        scorm_id = scorm.get('id', 0)

        try:
            # Get SCOs
            scos_response = await self.client.async_post(
                'mod_scorm_get_scorm_scoes',
                {'scormid': scorm_id},
            )

            scos = scos_response.get('scoes', [])
            if not scos:
                return

            # Create SCO summary
            sco_summary = self._create_sco_summary(scos)
            if sco_summary:
                scorm['files'].append(sco_summary)

            # Optionally get user tracking data
            if self.config.get_download_scorm_attempts():
                await self._add_user_attempts(scorm, scorm_id, scos)

        except RequestRejectedError:
            logging.debug("No access to SCOs for SCORM %d", scorm_id)
        except Exception as e:
            logging.debug("Error getting SCOs for SCORM %d: %s", scorm_id, str(e))

    def _create_sco_summary(self, scos: List[Dict]) -> Dict:
        """
        Create a summary file of all SCOs in the SCORM package

        @param scos: List of SCO objects
        @return: File dictionary
        """
        if not scos:
            return None

        # Build SCO summary in Markdown format
        summary = "# SCORM Content Structure\n\n"
        summary += f"Total SCOs: {len(scos)}\n\n"

        # Organize SCOs by type
        organizations = [sco for sco in scos if sco.get('scormtype') == 'organization']
        assets = [sco for sco in scos if sco.get('scormtype') == 'asset']
        sco_items = [sco for sco in scos if sco.get('scormtype') == 'sco']

        if organizations:
            summary += "## Organizations\n\n"
            for org in organizations:
                summary += f"- **{org.get('title', 'Untitled')}** (ID: {org.get('id')})\n"
                summary += f"  - Identifier: {org.get('identifier', 'N/A')}\n"
            summary += "\n"

        if sco_items:
            summary += "## SCOs (Sharable Content Objects)\n\n"
            for sco in sco_items:
                summary += f"### {sco.get('title', 'Untitled')}\n\n"
                summary += f"- **ID:** {sco.get('id')}\n"
                summary += f"- **Identifier:** {sco.get('identifier', 'N/A')}\n"
                summary += f"- **Launch:** {sco.get('launch', 'N/A')}\n"

                # Add prerequisites if present
                prereq = sco.get('prerequisites', '')
                if prereq:
                    summary += f"- **Prerequisites:** {prereq}\n"

                # Add max time if present
                max_time = sco.get('maxtime', '')
                if max_time:
                    summary += f"- **Max Time:** {max_time}\n"

                summary += "\n"

        if assets:
            summary += "## Assets\n\n"
            for asset in assets:
                summary += f"- {asset.get('title', 'Untitled')} (ID: {asset.get('id')})\n"
            summary += "\n"

        return {
            'filename': PT.to_valid_name('SCO_Structure', is_file=True),
            'filepath': '/scos/',
            'description': summary,
            'type': 'description',
            'timemodified': 0,
        }

    async def _add_user_attempts(self, scorm: Dict, scorm_id: int, scos: List[Dict]):
        """
        Add user attempt tracking data

        @param scorm: SCORM activity dictionary
        @param scorm_id: SCORM ID
        @param scos: List of SCOs
        """
        # For simplicity, we'll try to get attempt 1 data
        # In a full implementation, we would first get the attempt count
        for attempt_num in range(1, 4):  # Try first 3 attempts
            try:
                user_data_response = await self.client.async_post(
                    'mod_scorm_get_scorm_user_data',
                    {
                        'scormid': scorm_id,
                        'attempt': attempt_num,
                    },
                )

                data_entries = user_data_response.get('data', [])
                if not data_entries:
                    # No more attempts
                    break

                # Create attempt summary
                attempt_file = self._create_attempt_summary(attempt_num, data_entries, scos)
                if attempt_file:
                    scorm['files'].append(attempt_file)

            except RequestRejectedError:
                logging.debug("No access to attempt %d for SCORM %d", attempt_num, scorm_id)
                break
            except Exception as e:
                logging.debug("Error getting attempt %d for SCORM %d: %s", attempt_num, scorm_id, str(e))
                break

    def _create_attempt_summary(self, attempt_num: int, data_entries: List[Dict], scos: List[Dict]) -> Dict:
        """
        Create a summary of user attempt data

        @param attempt_num: Attempt number
        @param data_entries: User data entries
        @param scos: List of SCOs
        @return: File dictionary
        """
        if not data_entries:
            return None

        # Create SCO lookup
        sco_lookup = {sco.get('id'): sco for sco in scos}

        summary = f"# SCORM Attempt {attempt_num}\n\n"

        for entry in data_entries:
            sco_id = entry.get('scoid', 0)
            sco = sco_lookup.get(sco_id, {})
            sco_title = sco.get('title', f'SCO {sco_id}')

            summary += f"## {sco_title}\n\n"

            # Get user data and default data
            user_data = entry.get('userdata', [])
            default_data = entry.get('defaultdata', [])

            # Combine and display tracking data
            all_data = {}
            for item in default_data:
                all_data[item.get('element', 'unknown')] = ('default', item.get('value', ''))

            for item in user_data:
                all_data[item.get('element', 'unknown')] = ('user', item.get('value', ''))

            # Display important tracking elements
            important_keys = [
                'cmi.core.lesson_status',
                'cmi.core.score.raw',
                'cmi.core.score.min',
                'cmi.core.score.max',
                'cmi.core.total_time',
                'cmi.core.session_time',
                'cmi.completion_status',
                'cmi.success_status',
                'cmi.score.scaled',
            ]

            for key in important_keys:
                if key in all_data:
                    _, value = all_data[key]
                    display_key = key.replace('cmi.core.', '').replace('cmi.', '').replace('_', ' ').title()
                    summary += f"**{display_key}:** {value}\n"

            summary += "\n"

            # Show other data if present
            other_data = {k: v for k, v in all_data.items() if k not in important_keys}
            if other_data:
                summary += "### Additional Data\n\n"
                for key, (_, value) in sorted(other_data.items()):
                    if value:  # Only show non-empty values
                        summary += f"- {key}: {value}\n"
                summary += "\n"

        return {
            'filename': PT.to_valid_name(f'Attempt_{attempt_num}', is_file=True),
            'filepath': '/attempts/',
            'description': summary,
            'type': 'description',
            'timemodified': 0,
        }

    async def _fetch_scorms_web_api(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> List[Dict]:
        """
        使用 Web API fallback 获取 SCORM 模块信息。
        
        这是 mod_scorm_get_scorms_by_courses 的 fallback 实现。
        通过 core_course_get_contents 获取 scorm 模块信息。
        
        Return: 转换为与 Mobile API 相同格式的 scorm 列表
        """
        logging.debug('🌐 使用 Web API fallback 获取 SCORM 模块信息...')
        
        scorms = []
        
        # 从 core_contents 中提取 scorm 模块
        modules_by_course = self.extract_modules_from_core_contents(courses, core_contents, 'scorm')
        
        for course in courses:
            course_id = course.id
            if course_id not in modules_by_course:
                continue
            
            for module in modules_by_course[course_id]:
                # 将 Web API 的 scorm 模块转换为 Mobile API 的格式
                scorm = {
                    'id': module.get('instance', 0),
                    'coursemodule': module.get('id', 0),
                    'course': course_id,
                    'name': module.get('name', 'SCORM'),
                    'intro': module.get('description', ''),
                    'introformat': 1,
                    'scormtype': 'local',
                    'reference': '',
                    'sha1hash': '',
                    'version': '',
                    'maxgrade': 0,
                    'grademethod': 0,
                    'whatgrade': 0,
                    'maxattempt': 0,
                    'forcecompleted': 0,
                    'forcenewattempt': 0,
                    'lastattemptlock': 0,
                    'masteryoverride': 0,
                    'displayattemptstatus': 0,
                    'displaycoursestructure': 0,
                    'updatefreq': 0,
                    'popup': 0,
                    'width': 0,
                    'height': 0,
                    'skipview': 0,
                    'hidebrowse': 0,
                    'hidetoc': 0,
                    'nav': 0,
                    'navpositionleft': 0,
                    'navpositiontop': 0,
                    'auto': 0,
                    'autocommit': 0,
                    'timemodified': module.get('timemodified', 0),
                }
                scorms.append(scorm)
        
        if not scorms:
            logging.warning('⚠️ Web API fallback 未找到任何 SCORM 模块')
            raise ValueError('Web API 未能检索任何 SCORM 模块信息')
        
        logging.debug(f'✅ Web API fallback 成功获取 {len(scorms)} 个 SCORM 模块')
        return scorms
