import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class BigbluebuttonbnMod(MoodleMod):
    """
    BigBlueButton (BBB) module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/bigbluebuttonbn/services/bigbluebuttonbn.ts

    Supports:
    - BBB meeting/conference room metadata
    - Meeting status and participant information
    - Recording information and playback links
    - Presentation files associated with meetings
    - Meeting scheduling information
    """

    MOD_NAME = 'bigbluebuttonbn'
    MOD_PLURAL_NAME = 'bigbluebuttonbns'
    MOD_MIN_VERSION = 2020061500  # 3.9

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_bigbluebuttonbns() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

    def _get_status_name(self, meeting_info: Dict) -> str:
        """Get human-readable meeting status"""
        if meeting_info.get('statusrunning', False):
            return 'Running'
        elif meeting_info.get('statusclosed', False):
            return 'Closed'
        elif meeting_info.get('statusopen', False):
            return 'Open'
        else:
            return 'Unknown'

    def _format_timestamp(self, timestamp: int) -> Dict:
        """Format timestamp to both unix time and readable format"""
        if not timestamp or timestamp == 0:
            return {'unix': 0, 'readable': 'N/A'}

        from datetime import datetime

        dt = datetime.fromtimestamp(timestamp)
        return {'unix': timestamp, 'readable': dt.strftime('%Y-%m-%d %H:%M:%S')}

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all BigBlueButton modules from courses

        Process:
        1. Get BBB instances by courses
        2. Get meeting info for each instance
        3. Get recordings for each instance
        4. Export comprehensive metadata
        """

        result = {}

        if not self.config.get_download_bigbluebuttonbns():
            return result

        # Get all BBB instances for the courses
        try:
            response = await self.client.async_post(
                'mod_bigbluebuttonbn_get_bigbluebuttonbns_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            bbbs = response.get('bigbluebuttonbns', [])
        except RequestRejectedError:
            logging.debug("No access to BigBlueButton modules or WS not available")
            return result
        except Exception as e:
            logging.debug("Error getting BigBlueButton modules: %s", str(e))
            return result

        for bbb in bbbs:
            course_id = bbb.get('course', 0)
            module_id = bbb.get('coursemodule', 0)
            bbb_id = bbb.get('id', 0)
            bbb_name = bbb.get('name', 'BigBlueButton')
            meeting_id = bbb.get('meetingid', '')

            bbb_files = []

            # Copy introfiles to avoid modifying the original dict
            intro_files = list(bbb.get('introfiles', []))
            self.set_props_of_files(intro_files, type='bbb_file')
            bbb_files.extend(intro_files)

            # Get BBB intro/description
            bbb_intro = bbb.get('intro', '')
            if bbb_intro:
                bbb_files.append(
                    {
                        'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                        'filepath': '/',
                        'description': bbb_intro,
                        'type': 'description',
                        'timemodified': bbb.get('timemodified', 0),
                    }
                )

            # Get meeting info
            meeting_info = None
            meeting_info_error = None
            try:
                meeting_response = await self.client.async_post(
                    'mod_bigbluebuttonbn_meeting_info',
                    {
                        'bigbluebuttonbnid': bbb_id,
                        'groupid': 0,  # Default group
                    },
                )
                meeting_info = meeting_response
            except Exception as e:
                meeting_info_error = str(e)
                logging.debug("Error getting meeting info for BBB %s: %s", bbb_id, str(e))

            # Get recordings
            recordings = None
            recordings_error = None
            try:
                recordings_response = await self.client.async_post(
                    'mod_bigbluebuttonbn_get_recordings',
                    {
                        'bigbluebuttonbnid': bbb_id,
                        'groupid': 0,  # Default group
                    },
                )

                if recordings_response.get('status', False) and recordings_response.get('tabledata'):
                    recordings = recordings_response['tabledata']
            except Exception as e:
                recordings_error = str(e)
                logging.debug("Error getting recordings for BBB %s: %s", bbb_id, str(e))

            # Process meeting info
            if meeting_info:
                # Export presentations if available
                presentations = meeting_info.get('presentations', [])
                if presentations and meeting_info.get('showpresentations', True):
                    for idx, pres in enumerate(presentations):
                        pres_url = pres.get('url', '')
                        pres_name = pres.get('name', f'presentation_{idx}')

                        if pres_url:
                            bbb_files.append(
                                {
                                    'filename': PT.to_valid_name(pres_name, is_file=True),
                                    'filepath': '/',
                                    'content_fileurl': pres_url,
                                    'type': 'url',
                                    'timemodified': bbb.get('timemodified', 0),
                                }
                            )

            # Process recordings
            recording_list = []
            if recordings:
                # Parse recording data
                try:
                    recordings_data_str = recordings.get('data', '[]')
                    recordings_data = json.loads(recordings_data_str) if isinstance(recordings_data_str, str) else []

                    for rec in recordings_data:
                        recording_info = {
                            'id': rec.get('id', ''),
                            'name': rec.get('name', 'Recording'),
                            'date': rec.get('date', ''),
                            'duration': rec.get('duration', ''),
                            'has_playback': rec.get('playback', False),
                        }

                        # Try to extract playback URLs if available
                        if isinstance(rec.get('playback'), dict):
                            playback = rec['playback']
                            recording_info['playback_type'] = playback.get('type', '')
                            recording_info['playback_url'] = playback.get('url', '')

                        recording_list.append(recording_info)

                        # Create URL file for each recording with playback URL
                        if recording_info.get('playback_url'):
                            bbb_files.append(
                                {
                                    'filename': PT.to_valid_name(f"Recording - {recording_info['name']}", is_file=True),
                                    'filepath': '/recordings/',
                                    'content_fileurl': recording_info['playback_url'],
                                    'type': 'url',
                                    'timemodified': bbb.get('timemodified', 0),
                                }
                            )
                except (json.JSONDecodeError, TypeError) as e:
                    logging.debug("Error parsing recordings data for BBB %s: %s", bbb_id, str(e))

            # Create comprehensive metadata
            metadata = {
                'bbb_id': bbb_id,
                'course_id': course_id,
                'name': bbb_name,
                'intro': bbb_intro,
                'meeting_info': {
                    'meeting_id': meeting_id,
                    'time_modified': bbb.get('timemodified', 0),
                },
            }

            # Add meeting details if available
            if meeting_info:
                metadata['meeting_details'] = {
                    'status': self._get_status_name(meeting_info),
                    'status_message': meeting_info.get('statusmessage', ''),
                    'user_limit': meeting_info.get('userlimit', 0),
                    'schedule': {
                        'opening_time': self._format_timestamp(meeting_info.get('openingtime', 0)),
                        'closing_time': self._format_timestamp(meeting_info.get('closingtime', 0)),
                        'started_at': self._format_timestamp(meeting_info.get('startedat', 0)),
                    },
                    'participants': {
                        'moderator_count': meeting_info.get('moderatorcount', 0),
                        'participant_count': meeting_info.get('participantcount', 0),
                        'has_multiple_moderators': meeting_info.get('moderatorplural', False),
                        'has_multiple_participants': meeting_info.get('participantplural', False),
                    },
                    'access': {
                        'can_join': meeting_info.get('canjoin', False),
                        'is_moderator': meeting_info.get('ismoderator', False),
                        'join_url': meeting_info.get('joinurl', ''),
                    },
                    'presentations': [
                        {
                            'name': p.get('name', ''),
                            'url': p.get('url', ''),
                            'icon': p.get('iconname', ''),
                        }
                        for p in meeting_info.get('presentations', [])
                    ],
                }

                # Add features if available (Moodle 4.1+)
                if meeting_info.get('features'):
                    features_dict = {}
                    for feature in meeting_info['features']:
                        features_dict[feature.get('name', '')] = feature.get('isenabled', False)
                    metadata['meeting_details']['features'] = features_dict
            elif meeting_info_error:
                metadata['meeting_details'] = {'error': meeting_info_error}

            # Add recordings info
            if recordings:
                metadata['recordings'] = {
                    'total_count': len(recording_list),
                    'recordings': recording_list,
                    'locale': recordings.get('locale', ''),
                    'ping_interval': recordings.get('ping_interval', 0),
                }
            elif recordings_error:
                metadata['recordings'] = {'error': recordings_error}
            else:
                metadata['recordings'] = {'total_count': 0, 'recordings': []}

            # Add metadata file
            bbb_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': bbb.get('timemodified', 0),
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': bbb_id,
                    'name': bbb_name,
                    'files': bbb_files,
                },
            )

        return result
