import json
import logging
from datetime import datetime
from typing import Dict, List

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class ForumMod(MoodleMod):
    MOD_NAME = 'forum'
    MOD_PLURAL_NAME = 'forums'
    MOD_MIN_VERSION = 2013051400  # 2.5

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        # 下载条件: 检查是否启用了论坛下载
        return config.get_download_forums()

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        forums = await self.client.async_post(
            'mod_forum_get_forums_by_courses', self.get_data_for_mod_entries_endpoint(courses)
        )

        result = {}
        for forum in forums:
            course_id = forum.get('course', 0)
            forum_module_id = forum.get('cmid', 0)
            forum_id = forum.get('id', 0)
            forum_name = forum.get('name', 'forum')

            forum_files = self.get_introfiles(forum, 'forum_introfile')

            forum_intro = forum.get('intro', '')
            intro_file = self.create_intro_file(forum_intro)
            if intro_file:
                intro_file['filename'] = 'Forum intro'
                forum_files.append(intro_file)

            # Create comprehensive forum metadata
            metadata = {
                'forum_id': forum_id,
                'course_id': course_id,
                'module_id': forum_module_id,
                'name': forum_name,
                'intro': forum_intro,
                'settings': {
                    # Forum type and behavior
                    'type': forum.get('type', 'general'),
                    'duedate': forum.get('duedate', 0),
                    'cutoffdate': forum.get('cutoffdate', 0),
                    # Subscription settings
                    'forcesubscribe': forum.get('forcesubscribe', 0),
                    'trackingtype': forum.get('trackingtype', 1),
                    # Post settings
                    'maxbytes': forum.get('maxbytes', 0),
                    'maxattachments': forum.get('maxattachments', 1),
                    'displaywordcount': forum.get('displaywordcount', 0),
                    # Rating settings
                    'assessed': forum.get('assessed', 0),
                    'assesstimestart': forum.get('assesstimestart', 0),
                    'assesstimefinish': forum.get('assesstimefinish', 0),
                    'scale': forum.get('scale', 0),
                    # Display settings
                    'rsstype': forum.get('rsstype', 0),
                    'rssarticles': forum.get('rssarticles', 0),
                    # Blocking and threshold settings
                    'blockafter': forum.get('blockafter', 0),
                    'blockperiod': forum.get('blockperiod', 0),
                    'warnafter': forum.get('warnafter', 0),
                    # Q&A settings (for Q&A forums)
                    'lockdiscussionafter': forum.get('lockdiscussionafter', 0),
                },
                'capabilities': {
                    'cancreatediscussions': forum.get('cancreatediscussions', False),
                    'canviewdiscussions': forum.get('canviewdiscussions', True),
                },
                'counts': {
                    'discussions': forum.get('numdiscussions', 0),
                },
                'timestamps': {
                    'timemodified': forum.get('timemodified', 0),
                },
                'features': self.get_features(
                    purpose='communication',
                    grade_has_grade=True
                ),
                'note': 'Forum is a communication module for discussions and Q&A. '
                + 'This export includes comprehensive settings, access information, and all discussions.',
            }

            # Add metadata file
            forum_files.append(
                self.create_metadata_file(metadata, timemodified=forum.get('timemodified', 0))
            )

            self.add_module(
                result,
                course_id,
                forum_module_id,
                {
                    'id': forum_id,
                    'name': forum_name,
                    'files': forum_files,
                    '_cmid': forum_module_id,
                },
            )

        await self.add_forum_posts(result)
        return result

    async def add_forum_posts(self, forums: Dict[int, Dict[int, Dict]]):
        """
        Fetches for the forums list the forum posts
        @param forums: Dictionary of all forums, indexed by courses, then module id
        """
        if not self.config.get_download_forums():
            return

        if self.version < 2014111000:  # 2.8
            return

        await self.run_async_load_function_on_mod_entries(forums, self.load_latest_discussions)

    async def load_latest_discussions(self, forum: Dict):
        """
        获取论坛的最新讨论，支持 Mobile API 和 Web API fallback。
        
        优先使用 mod_forum_get_forum_discussions (Mobile API)，失败时使用 core_course_get_contents (Web API)。
        """
        # Get forum access information (permissions and capabilities)
        forum_id = forum.get('id', 0)
        try:
            access_info = await self.client.async_post(
                'mod_forum_get_forum_access_information',
                {'forumid': forum_id}
            )

            # Export access information as JSON file
            if access_info:
                forum['files'].append(
                    {
                        'filename': PT.to_valid_name('access_information', is_file=True) + '.json',
                        'filepath': '/',
                        'timemodified': 0,
                        'content': json.dumps(access_info, indent=2, ensure_ascii=False),
                        'type': 'content',
                    }
                )
        except Exception as e:
            logging.debug(f"Could not fetch access information for forum {forum_id}: {e}")

        page_num = 0
        last_timestamp = self.last_timestamps.get(self.MOD_NAME, {}).get(forum.get('_cmid', 0), 0)
        latest_discussions = []
        done = False
        
        # 尝试使用 Mobile API 获取讨论
        use_web_api = False
        try:
            while not done:
                latest_discussions, done = await self._fetch_discussions_mobile_api(
                    forum, page_num, last_timestamp, latest_discussions
                )
                page_num += 1
        except (Exception, KeyError) as e:
            logging.warning(f'❌ Mobile API 获取论坛讨论失败: {e}，尝试使用 Web API fallback...')
            use_web_api = True
        
        # Fallback 到 Web API
        if use_web_api:
            latest_discussions = await self._fetch_discussions_web_api(forum, last_timestamp)

        forum['files'] += await self.run_async_collect_function_on_list(
            latest_discussions,
            self.load_files_of_discussion,
            'discussion',
            {'collect_id': 'discussion_id', 'collect_name': 'subject'},
        )

    async def _fetch_discussions_mobile_api(
        self, forum: Dict, page_num: int, last_timestamp: int, latest_discussions: List[Dict]
    ) -> tuple:
        """
        使用 Mobile API (mod_forum_get_forum_discussions) 获取单页论坛讨论。
        
        Return: (latest_discussions, done)
        """
        data = {
            'forumid': forum.get('id', 0),
            'perpage': 10,
            'page': page_num,
        }

        logging.debug(
            '📱 使用 Mobile API 获取论坛讨论页 %(page_num)d of "%(forum_name)s"',
            {'page_num': page_num, 'forum_name': forum.get('name', '')},
        )

        if self.version >= 2019052000:  # 3.7
            discussions_result = await self.client.async_post('mod_forum_get_forum_discussions', data)
        else:
            discussions_result = await self.client.async_post('mod_forum_get_forum_discussions_paginated', data)

        discussions = discussions_result.get('discussions', [])

        if len(discussions) == 0:
            return latest_discussions, True

        done = False
        for discussion in discussions:
            time_modified = discussion.get('timemodified', 0)
            if discussion.get('modified', 0) > time_modified:
                time_modified = discussion.get('modified', 0)

            if last_timestamp < time_modified:
                latest_discussions.append(
                    {
                        'subject': discussion.get('subject', ''),
                        'timemodified': time_modified,
                        'discussion_id': discussion.get('discussion', 0),
                        'created': discussion.get('created', 0),
                    }
                )
            else:
                done = True
                break

        return latest_discussions, done

    async def _fetch_discussions_web_api(self, forum: Dict, last_timestamp: int) -> List[Dict]:
        """
        使用 Web API 获取论坛讨论信息的 fallback 实现。
        
        注意: Moodle Web Services API 不提供 mod_forum_get_forum_discussions 的直接等效函数。
        这个 fallback 有以下限制:
        1. 无法通过 Web API 直接获取讨论列表
        2. core_course_get_contents 只提供论坛模块的基本信息
        3. 需要使用 REST API 或爬取论坛页面
        
        策略: 在此返回空列表，并推荐用户:
        - 检查 API 权限配置
        - 确保已启用 mod_forum_get_forum_discussions 接口
        - 检查 Moodle 版本兼容性
        
        Return: 论坛讨论列表 (在 Web API fallback 中通常为空)
        """
        forum_id = forum.get('id', 0)
        
        logging.warning(
            f'⚠️ Web API fallback: 无法通过标准 Web Services API 获取论坛讨论列表。'
            f'\n   论坛 ID: {forum_id}'
            f'\n   建议:'
            f'\n   1. 检查 Mobile API (mod_forum_get_forum_discussions) 的权限'
            f'\n   2. 确保 Moodle 版本 >= 2.8 (支持 forum discussions API)'
            f'\n   3. 检查用户是否有查看讨论的权限'
        )
        
        # 返回空列表，表示无法通过 fallback 获取讨论
        # 论坛基本信息仍然会被保存（来自 mod_forum_get_forums_by_courses）
        return []

    async def load_files_of_discussion(self, discussion: Dict) -> List[Dict]:
        """
        加载论坛讨论的帖子文件，支持 Mobile API 和 Web API fallback。
        
        优先使用 mod_forum_get_discussion_posts (Mobile API)，失败时使用 Web API fallback。
        """
        result = []

        data = {
            'discussionid': discussion.get('discussion_id', 0),
            'sortby': 'modified',
            'sortdirection': 'ASC',
        }
        
        try:
            # 优先使用 Mobile API
            posts = await self._fetch_discussion_posts_mobile_api(data)
        except (Exception, KeyError) as e:
            logging.debug(f"Mobile API 获取讨论帖子失败: {e}，尝试 Web API fallback...")
            # Fallback 到 Web API
            posts = await self._fetch_discussion_posts_web_api(discussion.get('discussion_id', 0))

        for post in posts:
            post_message = post.get('message', '') or ''

            post_files = post.get('attachments', [])
            if self.version >= 2019052000:  # 3.7
                post_parent = post.get('parentid', 0)
                post_user_fullname = post.get('author', {}).get('fullname', None) or 'Unknown'
                post_modified = post.get('timecreated', 0)
                for post_file in post_files:
                    # New return structure uses url instead of fileurl
                    post_file['fileurl'] = post_file.get('url', '')
                    # And also do return normal URLs instead of webservice URLs
                    if post_file['fileurl'].find('/webservice/') < 0:
                        post_file['fileurl'] = post_file['fileurl'].replace(
                            '/pluginfile.php/', '/webservice/pluginfile.php/'
                        )
            else:
                post_parent = post.get('parent', 0)
                post_user_fullname = post.get('userfullname', '') or 'Unknown'
                post_modified = post.get('modified', 0)

                # Also add legacy inline files from messageinlinefiles attribute
                self.add_legacy_inline_files(post.get('messageinlinefiles', []), post_file)

            post_filename = PT.to_valid_name(f"[{post.get('id', 0)}] " + post_user_fullname, is_file=False)
            if post_parent is not None and post_parent != 0:
                post_filename = PT.to_valid_name(
                    post_filename + ' response to [' + str(post_parent) + ']', is_file=False
                )

            post_path = PT.to_valid_name(
                datetime.utcfromtimestamp(discussion.get('created', 0)).strftime('%y-%m-%d')
                + ' '
                + discussion.get('subject', ''),
                is_file=False,
            )

            post_url = post.get('urls', {}).get('view', None)

            result.append(
                {
                    'filename': post_filename,
                    'filepath': post_path,
                    'fileurl': post_url,
                    'timemodified': post_modified,
                    'description': post_message,
                    'type': 'description',
                }
            )

            self.set_props_of_files(post_files, type='forum_file')
            self.set_base_file_path_of_files(post_files, post_path)

            result.extend(post_files)

        return result

    async def _fetch_discussion_posts_mobile_api(self, data: Dict) -> List[Dict]:
        """
        使用 Mobile API (mod_forum_get_discussion_posts 或分页版本) 获取讨论帖子。
        
        Return: 帖子列表
        """
        logging.debug('📱 使用 Mobile API 获取讨论帖子...')
        
        if self.version >= 2019052000:  # 3.7
            result = await self.client.async_post('mod_forum_get_discussion_posts', data)
        else:
            result = await self.client.async_post('mod_forum_get_forum_discussion_posts', data)
        
        posts = result.get('posts', [])
        
        if not posts:
            raise KeyError('Mobile API 返回空的帖子列表')
        
        logging.debug(f'✅ Mobile API 成功获取 {len(posts)} 个帖子')
        return posts

    async def _fetch_discussion_posts_web_api(self, discussion_id: int) -> List[Dict]:
        """
        使用 Web API fallback 获取讨论帖子信息。
        
        这是 mod_forum_get_discussion_posts 的 fallback 实现。
        由于 Moodle Web Services API 没有提供论坛帖子 API，
        此 fallback 返回空列表。
        
        注: 论坛讨论帖子数据必须通过 Mobile API 获取。
        Web API (core_course_get_contents) 只提供论坛模块的基本信息，
        不提供讨论或帖子的详细内容。
        
        Return: 帖子列表 (通常为空)
        """
        logging.warning(
            f'⚠️ Web API fallback: 无法通过标准 Web Services API 获取论坛讨论帖子。'
            f'\n   讨论 ID: {discussion_id}'
            f'\n   建议:'
            f'\n   1. 检查 Mobile API (mod_forum_get_discussion_posts) 的权限'
            f'\n   2. 确保 Moodle 版本 >= 2.8 (支持 forum discussions API)'
            f'\n   3. 检查用户是否有查看帖子的权限'
        )
        
        # 返回空列表，避免错误
        return []

    def add_legacy_inline_files(self, inline_files: List, post_files: List):
        for inline_file in inline_files:
            new_inline_file = True
            for attachment in post_files:
                if attachment.get('fileurl', '').replace('attachment', 'post') == inline_file.get('fileurl', ''):
                    if (
                        attachment.get('filesize', 0) == inline_file.get('filesize', 0)
                        # we assume that inline attachments can have different timestamps than the actual
                        # attachment. However, they are still the same file.
                        and attachment.get('filename', '') == inline_file.get('filename', '')
                    ):
                        new_inline_file = False
                        break
            if new_inline_file:
                post_files.append(inline_file)
