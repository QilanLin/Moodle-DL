import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class ChatMod(MoodleMod):
    """
    Chat module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/chat/

    Chat is a real-time communication module that supports scheduled chat
    sessions. While the app supports live chat functionality, this implementation
    focuses on exporting chat metadata, sessions, and message history.

    Supports:
    - Chat room metadata via mod_chat_get_chats_by_courses API
    - Chat sessions via mod_chat_get_sessions API
    - Session messages via mod_chat_get_session_messages API
    - Chat settings (method, keep days, schedule)
    """

    MOD_NAME = 'chat'
    MOD_PLURAL_NAME = 'chats'

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        return config.get_download_chats() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all Chat modules from courses

        Process:
        1. Get all chats via mod_chat_get_chats_by_courses API
        2. For each chat, get sessions via mod_chat_get_sessions
        3. For each session, get messages via mod_chat_get_session_messages
        4. Export comprehensive metadata including settings and history
        """

        result = {}

        if not self.config.get_download_chats():
            return result

        # Get all chats via API
        response = await self.client.async_post(
            'mod_chat_get_chats_by_courses',
            self.get_data_for_mod_entries_endpoint(courses),
        )

        chats = response.get('chats', [])

        for chat in chats:
            course_id = chat.get('course', 0)
            module_id = chat.get('coursemodule', 0)
            chat_id = chat.get('id', 0)
            chat_name = chat.get('name', 'Chat')

            chat_files = []

            # Get intro/description if available
            intro = chat.get('intro', '')
            intro_file = self.create_intro_file(intro)
            if intro_file:
                chat_files.append(intro_file)

            # Get chat sessions
            sessions_data = await self._get_chat_sessions(chat_id)

            # Create comprehensive metadata
            metadata = {
                'chat_id': chat_id,
                'course_id': course_id,
                'module_id': module_id,
                'name': chat_name,
                'intro': intro,
                'settings': {
                    'chatmethod': chat.get('chatmethod', 'ajax'),
                    'keepdays': chat.get('keepdays', 0),
                    'studentlogs': chat.get('studentlogs', 0),
                    'chattime': chat.get('chattime', 0),
                    'schedule': chat.get('schedule', 0),
                },
                'timestamps': {
                    'timemodified': chat.get('timemodified', 0),
                },
                'sessions': sessions_data,
                'features': self.get_features(purpose='communication'),
                'note': 'Chat is a real-time communication module with scheduled sessions. '
                + 'This export includes chat settings, session history, and messages.',
            }

            chat_files.append(self.create_metadata_file(metadata))

            # Export sessions as separate files
            for session_info in sessions_data:
                session_start = session_info.get('sessionstart', 0)
                session_filename = f"session_{session_start}.json"

                chat_files.append(
                    {
                        'filename': session_filename,
                        'filepath': '/sessions/',
                        'timemodified': session_start,
                        'content': json.dumps(session_info, indent=2, ensure_ascii=False),
                        'type': 'content',
                    }
                )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': module_id,
                    'name': chat_name,
                    'files': chat_files,
                },
            )

            logging.debug(
                f"Found chat module: {chat_name} (ID: {module_id}, "
                f"Sessions: {len(sessions_data)}) in course {course_id}"
            )

        return result

    async def _get_chat_sessions(self, chat_id: int) -> List[Dict]:
        """
        Get all sessions for a chat

        Returns list of session data including messages
        """
        sessions_result = []

        try:
            # Get sessions
            response = await self.client.async_post(
                'mod_chat_get_sessions',
                {
                    'chatid': chat_id,
                    'groupid': 0,  # 0 means determine from user
                    'showall': True,  # Include incomplete sessions
                },
            )

            sessions = response.get('sessions', [])

            for session in sessions:
                session_start = session.get('sessionstart', 0)
                session_end = session.get('sessionend', 0)
                session_users = session.get('sessionusers', [])

                session_data = {
                    'sessionstart': session_start,
                    'sessionend': session_end,
                    'duration_seconds': session_end - session_start if session_end > 0 else None,
                    'iscomplete': session.get('iscomplete', False),
                    'sessionusers': [
                        {
                            'userid': user.get('userid', 0),
                            'messagecount': user.get('messagecount', 0),
                        }
                        for user in session_users
                    ],
                    'user_count': len(session_users),
                    'messages': [],
                }

                # Get session messages
                try:
                    messages_response = await self.client.async_post(
                        'mod_chat_get_session_messages',
                        {
                            'chatid': chat_id,
                            'sessionstart': session_start,
                            'sessionend': session_end,
                            'groupid': 0,
                        },
                    )

                    messages = messages_response.get('messages', [])
                    session_data['messages'] = [
                        {
                            'id': msg.get('id', 0),
                            'userid': msg.get('userid', 0),
                            'message': msg.get('message', ''),
                            'timestamp': msg.get('timestamp', 0),
                            'issystem': msg.get('issystem', False),
                        }
                        for msg in messages
                    ]
                    session_data['message_count'] = len(messages)

                except Exception as e:
                    logging.debug(f"Could not fetch messages for chat {chat_id} session {session_start}: {e}")
                    session_data['message_count'] = 0
                    session_data['note'] = f'Messages could not be retrieved: {e}'

                sessions_result.append(session_data)

        except Exception as e:
            logging.debug(f"Could not fetch sessions for chat {chat_id}: {e}")
            # Return empty list if sessions can't be fetched

        return sessions_result
