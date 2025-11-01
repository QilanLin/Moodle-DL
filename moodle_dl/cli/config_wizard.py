import os
import shutil
import sys
from typing import Dict, List, Tuple

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.core_handler import CoreHandler
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.moodle.request_helper import RequestHelper, RequestRejectedError
from moodle_dl.types import Course, MoodleDlOpts
from moodle_dl.utils import Cutie, Log
from moodle_dl.utils import PathTools as PT


class ConfigWizard:
    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        self.config = config
        self.opts = opts

        self.core_handler = CoreHandler(
            RequestHelper(config, opts, self.config.get_moodle_URL(), self.config.get_token())
        )

    def get_user_id_and_version(self) -> Tuple[int, int]:
        user_id, version = self.config.get_userid_and_version()
        if user_id is None or version is None:
            user_id, version = self.core_handler.fetch_userid_and_version()
        else:
            self.core_handler.version = version
        return user_id, version

    def interactively_acquire_config(self):
        """
        Guides the user through the process of configuring the downloader
        for the courses to be downloaded and in what way
        """
        courses = []
        try:
            user_id, _ = self.get_user_id_and_version()
            courses = self.core_handler.fetch_courses(user_id)

        except (RequestRejectedError, ValueError, RuntimeError, ConnectionError) as error:
            Log.error(f'与 Moodle 系统通信时出错！({error})')
            sys.exit(1)

        # 配置步骤菜单
        steps = [
            ('选择要下载的课程', lambda: self._select_courses_to_download(courses)),
            ('设置课程选项', lambda: self._set_options_of_courses(courses)),
            ('配置要下载的模块类型', self._select_modules_to_download),
            ('配置课程描述下载', self._select_should_download_descriptions),
            ('配置描述中的链接下载', self._select_should_download_links_in_descriptions),
            ('配置外部链接文件下载', self._select_should_download_linked_files),
            ('配置需要 Cookie 的文件下载', self._select_should_download_also_with_cookie),
        ]

        current_step = 0

        while current_step < len(steps):
            print('\n' + '=' * 80)
            Log.info(f'额外配置步骤 {current_step + 1}/{len(steps)}: {steps[current_step][0]}')
            print('=' * 80 + '\n')

            # 执行当前步骤
            steps[current_step][1]()

            # 询问下一步操作
            print('\n')
            choices = []
            if current_step < len(steps) - 1:
                choices.append('继续下一步')
            if current_step > 0:
                choices.append('返回上一步')
            choices.append('完成配置并退出')

            Log.blue('请选择：')
            choice = Cutie.select(choices)

            if choices[choice] == '继续下一步':
                current_step += 1
            elif choices[choice] == '返回上一步':
                current_step -= 1
            elif choices[choice] == '完成配置并退出':
                break

        Log.success('配置已成功更新！')

    def interactively_add_all_visible_courses(self):
        """
        Guides the user through the process of adding all visible courses
        to the list of courses to download in the configuration
        """
        print('')
        Log.info(
            'It is possible to automatically complete the moodle-dl configuration'
            + ' with all the courses you can see on your moodle. These are either'
            + ' courses to which you have the appropriate rights to see the'
            + ' course or the course is visible without enrollment.'
        )

        Log.magenta(
            'This process can take several minutes for large Moodels, as is common at'
            + ' large universities. Timeout is set to 20 minutes.'
        )

        print('')

        add_all_visible_courses = Cutie.prompt_yes_or_no(
            Log.blue_str('Do you want to add all visible courses of your Moodle to the configuration?'),
            default_is_yes=False,
        )

        if not add_all_visible_courses:
            return
        Log.warning(
            'Please wait for the result, this may take several minutes.'
            + ' In addition to adding the courses to the configuration,'
            + ' it will also create an `all_courses.json` file with all'
            + ' the courses available on your Moodle.'
        )

        courses = []
        all_visible_courses = []
        try:
            user_id, _ = self.get_user_id_and_version()
            courses = self.core_handler.fetch_courses(user_id)
            log_all_courses_to = PT.make_path(self.config.get_misc_files_path(), 'all_courses.json')
            all_visible_courses = self.core_handler.fetch_all_visible_courses(log_all_courses_to)

        except (RequestRejectedError, ValueError, RuntimeError, ConnectionError) as error:
            Log.error(f'Error while communicating with the Moodle System! ({error})')
            sys.exit(1)

        # Filter out courses the user is enroled in
        filtered_all_courses = []
        for visible_course in all_visible_courses:
            add_to_final_list = True
            for course in courses:
                if visible_course.id == course.id:
                    add_to_final_list = False
                    break
            if add_to_final_list:
                filtered_all_courses.append(visible_course)

        # Update Public Courses IDs
        download_public_course_ids = self.config.get_download_public_course_ids()
        # Update Course settings List for all new Courses
        options_of_courses = self.config.get_options_of_courses()
        for course in filtered_all_courses:
            current_course_settings = options_of_courses.get(str(course.id), None)

            # create default settings
            if current_course_settings is None:
                current_course_settings = {
                    'original_name': course.fullname,
                    'overwrite_name_with': None,
                    'create_directory_structure': True,
                }

                options_of_courses.update({str(course.id): current_course_settings})

            if course.id not in download_public_course_ids:
                download_public_course_ids.append(course.id)

        self.config.set_property('options_of_courses', options_of_courses)
        self.config.set_property('download_public_course_ids', download_public_course_ids)

        Log.success('配置已成功更新！')

    def _select_courses_to_download(self, courses: List[Course]):
        """
        Asks the user for the courses that should be downloaded.
        @param courses: All available courses
        """
        download_course_ids = self.config.get_download_course_ids()
        dont_download_course_ids = self.config.get_dont_download_course_ids()

        print('')
        Log.info(
            '为了避免下载你注册的所有 Moodle 课程，你可以在这里选择要下载的课程。'
            + '你可以创建白名单或黑名单。'
            + '\n\n- 使用白名单时，勾选✅的课程会被下载，未勾选的不下载。'
            + '未来注册的新课程默认不下载（需要手动添加到白名单）。'
            + '\n- 使用黑名单时，勾选❌的课程会被排除（不下载），未勾选的会下载。'
            + '未来注册的新课程会自动下载（除非手动添加到黑名单）。'
        )
        print('')
        use_whitelist = len(dont_download_course_ids) == 0

        while True:
            use_whitelist = Cutie.prompt_yes_or_no(
                Log.blue_str('你想为课程创建白名单还是黑名单？'),
                default_is_yes=use_whitelist,
                yes_text='白名单',
                no_text='黑名单',
            )

            choices = []
            defaults = []
            for i, course in enumerate(courses):
                choices.append(f'{int(course.id):5}\t{course.fullname}')

                # 复选框的含义：
                # 白名单模式：勾选 = 下载这门课程
                # 黑名单模式：勾选 = 排除这门课程（不下载）
                if use_whitelist:
                    # 白名单：勾选在 download_course_ids 中的课程
                    if course.id in download_course_ids:
                        defaults.append(i)
                else:
                    # 黑名单：勾选在 dont_download_course_ids 中的课程（要排除的）
                    if course.id in dont_download_course_ids:
                        defaults.append(i)

            if use_whitelist:
                Log.blue('哪些课程应该被下载？')
                Log.info('[勾选✅的课程会被下载，新课程默认不下载]')
                selected_courses = Cutie.select_multiple(
                    options=choices,
                    ticked_indices=defaults,
                    deselected_unticked_prefix='\033[1m( )\033[0m ',
                    deselected_ticked_prefix='\033[1m(\033[32m✅\033[0;1m)\033[0m ',
                    selected_unticked_prefix='\033[32;1m{ }\033[0m ',
                    selected_ticked_prefix='\033[32;1m{✅}\033[0m ',
                )
            else:
                Log.blue('哪些课程应该被排除（不下载）？')
                Log.info('[勾选❌的课程会被排除，新课程默认自动下载]')
                # 黑名单模式：使用 ❌ 表示"排除/不下载"
                selected_courses = Cutie.select_multiple(
                    options=choices,
                    ticked_indices=defaults,
                    deselected_unticked_prefix='\033[1m( )\033[0m ',
                    deselected_ticked_prefix='\033[1m(\033[31m❌\033[0;1m)\033[0m ',  # 红色 ❌
                    selected_unticked_prefix='\033[31;1m{ }\033[0m ',
                    selected_ticked_prefix='\033[31;1m{❌}\033[0m ',  # 红色 ❌
                )
            print('')

            # 只询问是否确认选择，不添加完整的导航菜单（导航由外层统一处理）
            confirm = Cutie.prompt_yes_or_no(
                Log.blue_str('确认选择？'),
                yes_text='确认',
                no_text='重新选择白名单/黑名单',
            )

            if not confirm:
                # 用户想重新选择白名单/黑名单，继续循环
                print('')
                continue

            # 保存课程选择
            # 白名单模式：保存勾选的课程（要下载的）
            # 黑名单模式：保存勾选的课程（要排除的）
            course_ids = []
            for i, course in enumerate(courses):
                if i in selected_courses:
                    course_ids.append(course.id)

            if use_whitelist:
                self.config.set_property('download_course_ids', course_ids)
                self.config.remove_property('dont_download_course_ids')
            else:
                # 黑名单模式
                if len(course_ids) == 0:
                    # 用户没有勾选任何课程要排除，黑名单为空
                    # 删除配置项，等同于"下载所有课程"
                    self.config.remove_property('dont_download_course_ids')
                    self.config.remove_property('download_course_ids')
                else:
                    # 保存要排除的课程
                    self.config.set_property('dont_download_course_ids', course_ids)
                    self.config.remove_property('download_course_ids')

            # 退出循环，返回外层导航
            break

    def _select_sections_to_download(self, sections: List[Dict], excluded: List[int]) -> List[int]:
        """
        Asks the user for the sections that should be downloaded.
        @param sections: All available sections
        @param excluded sections currently excluded
        """

        choices = []
        defaults = []
        for i, section in enumerate(sections):
            section_id = section.get("id")
            choices.append(f"{int(section_id):5}\t{section.get('name')}")

            if MoodleService.should_download_section(section_id, excluded):
                defaults.append(i)

        Log.blue('应该下载哪些章节？')
        Log.info('[你可以用空格键选择，用回车键确认选择]')
        print('')
        selected_sections = Cutie.select_multiple(options=choices, ticked_indices=defaults)

        dont_download_section_ids = []
        for i, section in enumerate(sections):
            if i not in selected_sections:
                dont_download_section_ids.append(section.get("id"))

        return dont_download_section_ids

    def _set_options_of_courses(self, courses: List[Course]):
        """
        Let the user set special options for every single course
        """
        download_course_ids = self.config.get_download_course_ids()
        dont_download_course_ids = self.config.get_dont_download_course_ids()

        # Determine filter mode based on which property exists in config
        use_whitelist = None
        if self.config.has_property('download_course_ids'):
            use_whitelist = True  # Whitelist mode (even if empty list)
        elif self.config.has_property('dont_download_course_ids'):
            use_whitelist = False  # Blacklist mode

        self.section_seperator()
        Log.info(
            '你可以为每个课程设置特殊选项。\n'
            + '可以设置这些选项：\n'
            + ' - 课程的自定义名称\n'
            + ' - 是否为课程创建目录结构 [create_directory_structure (cfs)]\n'
            + ' - 应该下载哪些章节（默认全部）。'
        )
        print('')

        while True:
            choices = []
            choices_courses = []

            options_of_courses = self.config.get_options_of_courses()

            choices.append('None')

            for course in courses:
                if MoodleService.should_download_course(course.id, download_course_ids, dont_download_course_ids, use_whitelist):
                    current_course_settings = options_of_courses.get(str(course.id), None)

                    # create default settings
                    if current_course_settings is None:
                        current_course_settings = {
                            'original_name': course.fullname,
                            'overwrite_name_with': None,
                            'create_directory_structure': True,
                            'excluded_sections': [],
                        }

                    # create list of options
                    overwrite_name_with = current_course_settings.get('overwrite_name_with', None)

                    create_directory_structure = current_course_settings.get('create_directory_structure', True)

                    if overwrite_name_with is not None and overwrite_name_with != course.fullname:
                        choices.append(
                            (
                                f'{int(course.id):5}\t{overwrite_name_with} ({course.fullname})'
                                + f' cfs={create_directory_structure}'
                            )
                        )

                    else:
                        choices.append(f'{int(course.id):5}\t{course.fullname}  cfs={create_directory_structure}')

                    choices_courses.append(course)

            print('')
            Log.blue('你想更改以下哪个课程的设置？')
            print('[用方向键选择，用回车键确认]')
            print('[选择 "None" 完成此步骤]')
            print('')

            selected_course = Cutie.select(options=choices)
            if selected_course == 0:
                break
            else:
                sel = choices_courses[selected_course - 1]
                self._change_settings_of(sel, options_of_courses)

    def _change_settings_of(self, course: Course, options_of_courses: Dict):
        """
        Ask for a new Name for the course.
        Then asks if a file structure should be created.
        """

        current_course_settings = options_of_courses.get(str(course.id), None)

        # create default settings
        if current_course_settings is None:
            current_course_settings = {
                'original_name': course.fullname,
                'overwrite_name_with': None,
                'create_directory_structure': True,
                'excluded_sections': [],
            }

        changed = False

        # Ask for new name
        overwrite_name_with = input(f'为此课程输入新名称 [留空则使用 "{course.fullname}"]:   ')

        if overwrite_name_with == '':
            overwrite_name_with = None

        if (
            overwrite_name_with != course.fullname
            and current_course_settings.get('overwrite_name_with', None) != overwrite_name_with
        ):
            current_course_settings.update({'overwrite_name_with': overwrite_name_with})
            changed = True

        # Ask if a file structure should be created
        create_directory_structure = current_course_settings.get('create_directory_structure', True)

        create_directory_structure = Cutie.prompt_yes_or_no(
            Log.blue_str('是否为此课程创建目录结构？'),
            default_is_yes=create_directory_structure,
        )

        if create_directory_structure is not current_course_settings.get('create_directory_structure', True):
            changed = True
            current_course_settings.update({'create_directory_structure': create_directory_structure})

        excluded_sections = current_course_settings.get('excluded_sections', [])

        change_excluded_sections_prompt = '你想要从下载中排除此课程的某些章节吗？'
        if len(excluded_sections) > 0:
            change_excluded_sections_prompt = (
                '你想要更改不下载的章节选择吗？'
                + f'当前此课程有 {len(excluded_sections)} 个章节被排除下载。'
            )

        change_excluded_sections = Cutie.prompt_yes_or_no(
            Log.blue_str(change_excluded_sections_prompt),
            default_is_yes=(len(excluded_sections) > 0),
        )

        if change_excluded_sections:
            Log.info('请稍等，正在下载课程章节信息。')
            sections = self.core_handler.fetch_sections(course.id)

            dont_download_section_ids = self._select_sections_to_download(sections, excluded_sections)

            if dont_download_section_ids != current_course_settings.get('excluded_sections', []):
                changed = True
                current_course_settings.update({'excluded_sections': dont_download_section_ids})

        if changed:
            options_of_courses.update({str(course.id): current_course_settings})
            self.config.set_property('options_of_courses', options_of_courses)

    def _select_modules_to_download(self):
        """
        让用户一次性选择所有要下载的模块类型（整合的多选步骤）
        """
        self.section_seperator()
        Log.info(
            'Moodle-DL 支持下载 26 种不同类型的模块。你可以选择要下载哪些类型。\n'
            + '使用空格键勾选/取消，回车键确认选择。\n'
            + '✅ = 将会下载   [ ] = 不下载'
        )
        print('')

        # 定义所有模块及其描述
        modules = [
            # 评估与作业模块
            ('download_submissions', '作业提交 (Submissions)',
             'Submissions（提交）是你或老师上传到作业的文件。Moodle 没有提供一次性下载课程所有提交信息的接口，因此监控提交的变化可能会比较慢。'),
            ('download_quizzes', '测验 (Quizzes)',
             '测验是学生必须完成的在线考试，会被评分。包含题目、答案和成绩。只有正在进行或已完成的测验会被下载。'),
            ('download_lessons', '课程 (Lessons)',
             'Lessons（课程）是一种自适应学习方式，包含信息页面和需要回答的问题页面。学生完成课程后会根据答案被评分。'),
            ('download_workshops', '研讨会 (Workshops)',
             'Workshops（研讨会）按照同行评审流程运作。学生可以提交作业并需要评估其他学生的提交。包含提交内容和评审信息。'),

            # 内容与资源模块
            ('download_books', '书籍 (Books)',
             'Books（书籍）是页面的集合。每本书都会创建一个包含章节的目录结构，适合长篇内容阅读。'),
            ('download_scorms', 'SCORM包 (SCORM)',
             'SCORM 是电子学习的国际标准格式。包含交互式课程内容包、学习对象(SCO)信息和用户跟踪数据。'),
            ('download_h5pactivities', 'H5P活动 (H5P Activities)',
             'H5P 是现代的交互式HTML5内容创作工具。支持测验、视频、演示文稿等多种交互形式，可下载内容包和用户答题记录。'),
            ('download_imscps', 'IMS内容包 (IMS Content Package)',
             'IMS CP 是IMS Global标准的学习内容包格式，包含结构化的学习材料和资源。'),
            ('download_urls', 'URL链接 (URLs)',
             'URL 模块提供指向外部资源的链接。会为每个链接创建快捷方式文件(.url/.webloc/.desktop)和元数据。'),
            ('download_labels', '标签 (Labels)',
             'Labels（标签）是课程页面中嵌入的文本、图片或媒体内容，通常用于说明和装饰课程页面。'),

            # 协作与交流模块
            ('download_forums', '论坛 (Forums)',
             '论坛是学生和老师讨论交流的地方。包含讨论帖、回复、附件和评分信息。'),
            ('download_wikis', '维基 (Wikis)',
             'Wikis（维基）是协作文档编辑工具，支持多人共同创建和编辑内容。支持小组维基和个人维基，包含所有页面、附件和标签。'),
            ('download_glossaries', '词汇表 (Glossaries)',
             'Glossaries（词汇表）用于创建和维护术语定义列表。支持分类、评论、评分和附件，可导出完整的术语数据库。'),
            ('download_databases', '数据库 (Databases)',
             '数据库模块允许结构化数据收集和展示。包含数据库结构定义(schema)、所有条目数据、附件和元数据。学生通常可以添加和编辑条目。'),
            ('download_chats', '聊天 (Chats)',
             'Chat（聊天）模块提供实时文字聊天功能。可以导出聊天记录，包含消息历史和参与者信息。'),

            # 调查与反馈模块
            ('download_feedbacks', '反馈 (Feedbacks)',
             'Feedbacks（反馈）是自定义问卷调查工具。包含问题设计、学生回答、统计分析和附件。支持匿名反馈和多次提交。'),
            ('download_surveys', '调查 (Surveys)',
             'Surveys（调查）是预定义的标准化调查问卷，如COLLES、ATTLS等教育学调查。包含问题和回答数据。'),
            ('download_choices', '投票/选择 (Choices)',
             'Choice（投票）是简单的单选或多选投票工具。包含选项、投票结果和统计信息，可用于快速收集意见。'),

            # 其他模块
            ('download_calendars', '日历 (Calendar)',
             '日历包含课程的所有事件和截止日期。每个事件导出为HTML文件，包含详细的事件信息、时间和附件。'),
            ('download_bigbluebuttonbns', 'BigBlueButton会议 (BigBlueButton)',
             'BigBlueButton 是在线会议和虚拟教室系统。可下载会议信息、录像和相关资源。'),
            ('download_qbanks', '题库 (Question Banks)',
             'Question Banks（题库）包含用于创建测验的题目集合。包含题目内容、答案和元数据。'),
        ]

        # 获取当前配置
        # 检查是否是初始化（配置项不存在）还是重新配置（配置项已存在）
        is_initial_setup = not self.config.has_property(modules[0][0])  # 检查第一个配置项是否存在

        current_selections = []
        for i, (config_key, name, desc) in enumerate(modules):
            if is_initial_setup:
                # 初始化时默认全选
                current_selections.append(i)
            else:
                # 重新配置时使用现有配置值
                getter_method = getattr(self.config, f'get_{config_key}', None)
                if getter_method and getter_method():
                    current_selections.append(i)

        # 创建选项列表
        choices = []
        for config_key, name, desc in modules:
            choices.append(f'{name}\t{desc}')

        # 显示多选界面
        Log.blue('请选择要下载的模块类型：')
        Log.info('[使用↑↓键移动，空格键勾选/取消，回车键确认]')
        print('')

        selected_indices = Cutie.select_multiple(
            options=choices,
            ticked_indices=current_selections,
            deselected_unticked_prefix='\033[1m[ ]\033[0m ',
            deselected_ticked_prefix='\033[1m[\033[32m✅\033[0;1m]\033[0m ',
            selected_unticked_prefix='\033[32;1m{ }\033[0m ',
            selected_ticked_prefix='\033[32;1m{✅}\033[0m ',
        )

        # 保存配置
        for i, (config_key, name, desc) in enumerate(modules):
            should_download = i in selected_indices
            self.config.set_property(config_key, should_download)

        print('')
        Log.success(f'已选择 {len(selected_indices)}/{len(modules)} 个模块类型进行下载')

    def _select_should_download_descriptions(self):
        """
        Asks the user if descriptions should be downloaded
        """
        download_descriptions = self.config.get_download_descriptions()

        self.section_seperator()
        Log.info(
            '在 Moodle 课程中，可以为各种资源添加描述，'
            + '例如文件、任务、作业或纯文本。'
            + '这些描述通常不需要下载，因为你已经读过信息或'
            + '从上下文中知道了。但在某些情况下，'
            + '下载这些描述可能会有用。'
            + '描述会创建为 Markdown 文件，可以随意删除。'
        )
        Log.debug(
            '创建描述文件不会花费额外时间，但如果它们只包含不必要的信息，可能会很烦人。'
        )

        print('')

        download_descriptions = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载所选课程的描述吗？'),
            default_is_yes=download_descriptions,
        )

        self.config.set_property('download_descriptions', download_descriptions)

    def _select_should_download_links_in_descriptions(self):
        """
        Asks the user if links in descriptions should be downloaded
        """
        download_links_in_descriptions = self.config.get_download_links_in_descriptions()

        self.section_seperator()
        Log.info(
            '在文件、章节、作业或课程的描述中，老师可以添加网页、'
            + '文件或视频的链接。这些链接可以指向 Moodle 内部页面或外部网页。'
        )
        print('')

        download_links_in_descriptions = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载描述中的链接吗？'),
            default_is_yes=download_links_in_descriptions,
        )

        self.config.set_property('download_links_in_descriptions', download_links_in_descriptions)

    def _select_should_download_linked_files(self):
        """
        Asks the user if linked files should be downloaded
        """
        download_linked_files = self.config.get_download_linked_files()

        self.section_seperator()
        Log.info(
            '在 Moodle 课程中，老师也可以链接到外部文件。'
            + '这可以是音频、视频、文本或其他任何内容。'
            + '特别是，老师可以链接到 YouTube 视频。'
        )
        Log.debug('要正确下载视频，你必须安装 ffmpeg。')

        Log.error('这些文件可能会显著增加下载量。')

        Log.info(
            '如果你想按域名过滤外部链接，'
            + '可以手动设置白名单和黑名单'
            + '（详见 https://github.com/C0D3D3V/Moodle-DL/wiki/Download-(external)-linked-files）。'
        )
        Log.warning(
            '请注意，外部文件的大小在下载过程中确定，所以总大小会在下载过程中变化。'
        )
        print('')

        download_linked_files = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载所选课程的外部链接文件吗？'),
            default_is_yes=download_linked_files,
        )

        self.config.set_property('download_linked_files', download_linked_files)

    def _select_should_download_also_with_cookie(self):
        """
        Ask the user whether files for which a cookie is required should be downloaded.
        """
        download_also_with_cookie = self.config.get_download_also_with_cookie()

        self.section_seperator()
        Log.info(
            '描述中可能包含需要浏览器 cookie 才能下载的文件链接。'
            + '还有一些 Moodle 插件（如 kalvidres）无法在 Moodle 应用中显示，'
            + '所以你需要浏览器 cookie 来下载这些插件文件。'
        )

        Log.debug(
            'Moodle 浏览器 cookie（MoodleSession）会使用你的私有令牌自动生成，并存储在 `Cookies.txt` 文件中。'
            + '对于 SSO 登录（如 Microsoft、Google、Okta 等），你还需要手动从浏览器导出额外的 SSO 认证 cookies。'
        )

        if self.config.get_privatetoken() is None:
            Log.error(
                '当前配置中没有存储私有令牌。'
                + '使用 moodle-dl --new-token 获取 Moodle Token（SSO 登录需加上 --sso）'
            )

        print('')

        download_also_with_cookie = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载需要 cookie 的文件吗？'),
            default_is_yes=download_also_with_cookie,
        )

        self.config.set_property('download_also_with_cookie', download_also_with_cookie)

        # 如果用户选择 Yes，引导导出浏览器 cookies
        if download_also_with_cookie:
            self._export_browser_cookies_if_needed()

    def _export_browser_cookies_if_needed(self):
        """
        引导用户导出浏览器 cookies（包含 SSO 提供商的认证 cookies）
        """
        print('')
        Log.warning('⚠️  对于 SSO 登录（如 Microsoft、Google、Okta 等），需要从浏览器导出额外的 cookies。')
        print('')

        # 获取 Moodle URL 和输出路径
        moodle_url = self.config.get_moodle_URL()
        if moodle_url is None:
            Log.error('错误：未找到 Moodle URL 配置，无法导出 cookies')
            return

        moodle_domain = moodle_url.domain
        cookies_path = PT.get_cookies_path(self.config.get_misc_files_path())

        # 检查是否已有 Cookies.txt 以及是否包含 SSO cookies
        cookies_exist = os.path.exists(cookies_path)
        has_sso_cookies = False

        if cookies_exist:
            # 检查是否包含 SSO cookies
            has_sso_cookies = self._check_sso_cookies_exist(cookies_path, moodle_domain)

            if has_sso_cookies:
                Log.info(f'✅ 已存在完整的 Cookies.txt 文件（包含 SSO cookies）: {cookies_path}')
            else:
                Log.warning(f'⚠️  已存在 Cookies.txt 文件，但可能缺少 SSO cookies: {cookies_path}')
                Log.info('   建议重新导出以获取完整的浏览器 cookies。')
            print('')

        # 询问是否要导出浏览器 cookies
        # 默认 Yes 的情况：1) 没有 cookies 文件  2) 有文件但缺少 SSO cookies
        should_export = Cutie.prompt_yes_or_no(
            Log.blue_str('是否现在从浏览器导出完整的 cookies（包含 SSO 登录所需的认证 cookies）？'),
            default_is_yes=(not cookies_exist or not has_sso_cookies),
        )

        if not should_export:
            if not cookies_exist:
                Log.warning(
                    '跳过浏览器 cookies 导出。'
                    + f'你可以稍后手动运行：python3 export_browser_cookies.py'
                    + f'\n或将 Cookies.txt 文件放置到: {cookies_path}'
                )
            return

        # 询问用户选择浏览器
        print('')
        Log.blue('请选择你使用的浏览器或内核：')
        browser_choices = [
            'Chrome',
            'Edge',
            'Firefox',
            'Safari',
            'Chromium 内核浏览器（Brave, Vivaldi, Arc, Opera 等）',
            'Firefox 内核浏览器（Zen, Waterfox, LibreWolf 等）',
            '自动检测所有浏览器',
        ]
        browser_choice = Cutie.select(browser_choices)

        # 处理内核选择的二级菜单
        selected_browser = None
        if browser_choice == 4:
            # Chromium 内核 - 二级选择
            print('')
            Log.blue('请选择具体的 Chromium 内核浏览器：')
            chromium_choices = [
                'Chrome',
                'Brave',
                'Vivaldi',
                'Opera',
                'Chromium',
                'Arc（通过自定义路径支持）'
            ]
            chromium_choice = Cutie.select(chromium_choices)

            chromium_map = {
                0: 'chrome',
                1: 'brave',      # ✅ 有专门的 brave() 方法
                2: 'vivaldi',    # ✅ 有专门的 vivaldi() 方法
                3: 'opera',      # ✅ 有专门的 opera() 方法
                4: 'chromium',   # ✅ 有专门的 chromium() 方法
                5: 'arc',        # ✅ 通过自定义路径支持
            }
            selected_browser = chromium_map[chromium_choice]

        elif browser_choice == 5:
            # Firefox 内核 - 二级选择
            print('')
            Log.blue('请选择具体的 Firefox 内核浏览器：')
            firefox_choices = [
                'Firefox',
                'LibreWolf',
                'Zen Browser（通过自定义路径支持）',
                'Waterfox（通过自定义路径支持）'
            ]
            firefox_choice = Cutie.select(firefox_choices)

            firefox_map = {
                0: 'firefox',
                1: 'librewolf',  # ✅ 有专门的 librewolf() 方法
                2: 'zen',        # ✅ 通过自定义路径支持
                3: 'waterfox',   # ✅ 通过自定义路径支持
            }
            selected_browser = firefox_map[firefox_choice]

        elif browser_choice == 6:
            # 自动检测
            selected_browser = None
        else:
            # 直接选择的浏览器
            browser_map = {
                0: 'chrome',
                1: 'edge',
                2: 'firefox',
                3: 'safari',
            }
            selected_browser = browser_map[browser_choice]

        # 尝试导入并运行 export_browser_cookies
        print('')
        Log.info('正在从浏览器导出 cookies...')
        print('')

        try:
            # 动态导入 export_browser_cookies
            import importlib.util
            script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'export_browser_cookies.py')

            if not os.path.exists(script_path):
                # 尝试在当前工作目录查找
                script_path = os.path.join(os.getcwd(), 'export_browser_cookies.py')

            if not os.path.exists(script_path):
                Log.error('错误：未找到 export_browser_cookies.py 文件')
                Log.info('请确保该文件在项目根目录，或手动运行：')
                Log.info('  python3 export_browser_cookies.py')
                return

            # 加载模块
            spec = importlib.util.spec_from_file_location("export_browser_cookies", script_path)
            export_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(export_module)

            # 根据用户选择导出 cookies
            if selected_browser:
                # 用户选择了特定浏览器
                success = export_module.export_cookies_from_browser(
                    domain=moodle_domain,
                    output_file=cookies_path,
                    browser_name=selected_browser
                )
                if success:
                    # 验证 cookies
                    success = export_module.test_cookies(moodle_domain, cookies_path)
            else:
                # 用户选择自动检测
                success = export_module.export_cookies_interactive(
                    domain=moodle_domain,
                    output_file=cookies_path,
                    ask_browser=False  # 已经在这里选择了
                )

            if success:
                Log.success('✅ 浏览器 cookies 导出成功！')
            else:
                Log.error('❌ 浏览器 cookies 导出失败')
                Log.warning('你可以稍后手动导出 cookies：')
                Log.info('  1. 在浏览器中登录你的 Moodle')
                Log.info('  2. 运行：python3 export_browser_cookies.py')
                Log.info(f'  3. 或手动将 Cookies.txt 放置到: {cookies_path}')

        except ImportError as e:
            Log.error(f'错误：无法导入 browser-cookie3 库: {e}')
            Log.info('请先安装依赖：pip install browser-cookie3')
            Log.info('然后手动运行：python3 export_browser_cookies.py')
        except Exception as e:
            Log.error(f'导出过程出错: {e}')
            Log.warning('你可以稍后手动运行：python3 export_browser_cookies.py')

    def _check_sso_cookies_exist(self, cookies_path: str, moodle_domain: str) -> bool:
        """
        检查 Cookies.txt 是否包含 SSO cookies（方案C - Cookie域名检测）

        Args:
            cookies_path: Cookies.txt 文件路径
            moodle_domain: Moodle 主域名

        Returns:
            bool: True 如果包含非 Moodle 域名的 cookies（可能是 SSO），False 否则
        """
        try:
            # 提取 Moodle 主域名（去掉子域名）
            moodle_main_domain = '.'.join(moodle_domain.split('.')[-2:]) if '.' in moodle_domain else moodle_domain

            with open(cookies_path, 'r') as f:
                for line in f:
                    # 跳过注释和空行
                    if line.startswith('#') or not line.strip():
                        continue

                    # Netscape cookie format: domain, flag, path, secure, expiration, name, value
                    parts = line.strip().split('\t')
                    if len(parts) >= 7:
                        cookie_domain = parts[0].lstrip('.')

                        # 检测是否有非 Moodle 域名的 cookies（可能是 SSO）
                        if cookie_domain != moodle_domain and \
                           moodle_main_domain not in cookie_domain and \
                           cookie_domain not in ['localhost', '127.0.0.1']:
                            # 发现了可能的 SSO cookie
                            return True

            return False
        except Exception:
            return False

    def section_seperator(self):
        """Print a seperator line."""
        print('\n' + '-' * shutil.get_terminal_size().columns + '\n')
