import json
from datetime import datetime
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.moodle_constants import (
    course_events_module_id,
    course_events_section_id,
    moodle_event_footer,
    moodle_event_header,
)
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT

# TODO: should we use locale.setlocale(locale.LC_TIME, "") to set localized version of Date format
# or should we use https://babel.pocoo.org/en/latest/dates.html
# babel.dates.format_datetime(datetime_obj) is enough to use local setting,
# but we could also use user language settings of moodle,
# we can pass it to format_datetime by defining locale='user_lang' as extra argument
# We can also use babel.dates.format_timedelta(time_delta) to print the time delta into the date file


class CalendarMod(MoodleMod):
    MOD_NAME = 'calendar'
    MOD_PLURAL_NAME = 'events'
    MOD_MIN_VERSION = 2013051400  # 2.5

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_calendars() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        result = {}
        if not self.config.get_download_calendars():
            return result

        last_timestamp = self.last_timestamps.get(self.MOD_NAME, {}).get(course_events_module_id, 0)
        calendar_req_data = {
            'options': {'timestart': last_timestamp, 'userevents': 0},
            'events': self.get_data_for_mod_entries_endpoint(courses),
        }

        events = (await self.client.async_post('core_calendar_get_calendar_events', calendar_req_data)).get(
            'events', []
        )

        events_per_course = self.sort_by_courseid(events)

        for course_id, events in events_per_course.items():
            event_files = []
            events_metadata = []

            for event in events:
                event_id = event.get('id', 0)
                event_name = event.get('name', 'unnamed event')
                event_description = event.get('description', '')
                event_modulename = event.get('modulename', None)
                event_timestart = event.get('timestart', 0)
                event_timeduration = event.get('timeduration', 0)
                event_timeend = event_timestart + event_timeduration if event_timeduration != 0 else None

                event_filename = PT.to_valid_name(
                    f'{datetime.fromtimestamp(event_timestart).strftime("%Y.%m.%d %H:%M")} {event_name}', is_file=False
                )

                # Create HTML representation
                event_content = moodle_event_header
                event_content += f'<div class="event-title"><span class="icon">&#128197;</span>{event_name}</div>'
                event_content += (
                    '<div class="attribute"><span class="icon">&#9201;</span>'
                    + f'Start Time: {datetime.fromtimestamp(event_timestart).strftime("%c")}</div>'
                )
                if event_timeduration != 0:
                    event_content += (
                        '<div class="attribute"><span class="icon">&#9201;</span>'
                        + f'End Time: {datetime.fromtimestamp(event_timeend).strftime("%c")}</div>'
                    )
                if event_description is not None and event_description != '':
                    event_content += (
                        '<div class="attribute"><span class="icon">&#128196;</span>' + f'{event_description}</div>'
                    )
                if event_modulename is not None:
                    event_content += (
                        '<div class="attribute"><span class="icon">&#128218;</span>'
                        f'Module Type: {event_modulename}</div>'
                    )

                event_content += moodle_event_footer

                event_files.append(
                    {
                        'filename': event_filename,
                        'filepath': '/',
                        'html': event_content,
                        'type': 'html',
                        'timemodified': event.get('timemodified', 0),
                        'filesize': len(event_content),
                        'no_search_for_urls': True,
                    }
                )

                # Create comprehensive event metadata
                event_metadata = {
                    'event_id': event_id,
                    'name': event_name,
                    'description': event_description,
                    'event_type': event.get('eventtype', ''),
                    'course_id': event.get('courseid', 0),
                    'category_id': event.get('categoryid'),
                    'user_id': event.get('userid'),
                    'instance': event.get('instance'),
                    'module_name': event_modulename,
                    'times': {
                        'timestart': event_timestart,
                        'timestart_formatted': datetime.fromtimestamp(event_timestart).strftime("%Y-%m-%d %H:%M:%S"),
                        'timeduration': event_timeduration,
                        'timeend': event_timeend,
                        'timeend_formatted': datetime.fromtimestamp(event_timeend).strftime("%Y-%m-%d %H:%M:%S") if event_timeend else None,
                        'timemodified': event.get('timemodified', 0),
                        'timesort': event.get('timesort', 0),
                    },
                    'repeat': {
                        'repeatid': event.get('repeatid'),
                        'eventcount': event.get('eventcount'),
                    },
                    'display': {
                        'visible': event.get('visible', 1),
                        'overdue': event.get('overdue', False),
                        'icon': event.get('icon', {}),
                        'url': event.get('url', ''),
                        'formattedtime': event.get('formattedtime', ''),
                    },
                    'permissions': {
                        'canedit': event.get('canedit', False),
                        'candelete': event.get('candelete', False),
                    },
                    'location': event.get('location', ''),
                    'format': event.get('format', 1),
                    'normalisedeventtype': event.get('normalisedeventtype', ''),
                    'normalisedeventtypetext': event.get('normalisedeventtypetext', ''),
                }

                # Add metadata as JSON file for this event
                event_metadata_filename = PT.to_valid_name(
                    f'{datetime.fromtimestamp(event_timestart).strftime("%Y.%m.%d %H:%M")} {event_name} metadata',
                    is_file=True
                ) + '.json'

                event_files.append(
                    {
                        'filename': event_metadata_filename,
                        'filepath': '/',
                        'timemodified': 0,
                        'content': json.dumps(event_metadata, indent=2, ensure_ascii=False),
                        'type': 'content',
                    }
                )

                events_metadata.append(event_metadata)

            # Add comprehensive calendar metadata file
            calendar_metadata = {
                'course_id': course_id,
                'event_count': len(events),
                'events': events_metadata,
                'note': 'Calendar events exported from Moodle. '
                + 'Each event includes comprehensive metadata including type, timing, permissions, and display information.',
            }

            event_files.append(
                {
                    'filename': PT.to_valid_name('calendar_metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': 0,
                    'content': json.dumps(calendar_metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )
            if course_id not in core_contents:
                core_contents[course_id] = []
            core_contents[course_id].append(
                {
                    'id': course_events_section_id,
                    'name': 'Events',
                    'modules': [{'id': course_events_module_id, 'name': 'Events', 'modname': 'calendar'}],
                }
            )

            self.add_module(
                result,
                course_id,
                course_events_module_id,
                {
                    'id': course_events_module_id,
                    'name': 'Events',
                    'files': event_files,
                },
            )

        return result

    @staticmethod
    def sort_by_courseid(events):
        sorted_dict = {}
        for event in events:
            course_id = event.get('courseid', 0)
            if course_id not in sorted_dict:
                sorted_dict[course_id] = []
            sorted_dict[course_id].append(event)
        return sorted_dict
