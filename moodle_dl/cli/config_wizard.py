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
            ('配置作业提交下载', self._select_should_download_submissions),
            ('配置课程描述下载', self._select_should_download_descriptions),
            ('配置描述中的链接下载', self._select_should_download_links_in_descriptions),
            ('配置数据库下载', self._select_should_download_databases),
            ('配置论坛下载', self._select_should_download_forums),
            ('配置测验下载', self._select_should_download_quizzes),
            ('配置课程教材下载', self._select_should_download_lessons),
            ('配置研讨会下载', self._select_should_download_workshops),
            ('配置书籍下载', self._select_should_download_books),
            ('配置日历下载', self._select_should_download_calendars),
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
            + '\n\n- 使用白名单时，只有勾选✅的课程会被下载，'
            + '未来注册的新课程默认不下载（需要手动添加到白名单）。'
            + '\n- 使用黑名单时，勾选✅的课程会被下载，取消勾选的课程不会被下载，'
            + '未来注册的新课程会自动下载（除非添加到黑名单）。'
        )
        print('')
        use_whitelist = len(dont_download_course_ids) == 0

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

            # 复选框的含义统一为"是否下载这门课程"
            # 白名单模式：默认全不选（空白名单），勾选的加入白名单
            # 黑名单模式：默认全选（下载所有），取消勾选的加入黑名单
            if use_whitelist:
                # 白名单：只勾选在 download_course_ids 中的课程
                if course.id in download_course_ids:
                    defaults.append(i)
            else:
                # 黑名单：勾选所有不在 dont_download_course_ids 中的课程（即要下载的课程）
                if course.id not in dont_download_course_ids:
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
            Log.blue('哪些课程应该被下载？')
            Log.info('[勾选✅的课程会被下载，新课程默认自动下载]')
            # 黑名单模式：统一使用 ✅ 表示"会下载"，取消勾选的进入黑名单
            selected_courses = Cutie.select_multiple(
                options=choices,
                ticked_indices=defaults,
                deselected_unticked_prefix='\033[1m( )\033[0m ',
                deselected_ticked_prefix='\033[1m(\033[32m✅\033[0;1m)\033[0m ',
                selected_unticked_prefix='\033[32;1m{ }\033[0m ',
                selected_ticked_prefix='\033[32;1m{✅}\033[0m ',
            )
        print('')

        # 白名单模式：保存勾选的课程（要下载的）
        # 黑名单模式：保存未勾选的课程（不要下载的）
        course_ids = []
        for i, course in enumerate(courses):
            if use_whitelist:
                # 白名单：勾选的课程加入白名单
                if i in selected_courses:
                    course_ids.append(course.id)
            else:
                # 黑名单：未勾选的课程加入黑名单
                if i not in selected_courses:
                    course_ids.append(course.id)

        if use_whitelist:
            self.config.set_property('download_course_ids', course_ids)
            self.config.remove_property('dont_download_course_ids')
        else:
            self.config.set_property('dont_download_course_ids', course_ids)
            self.config.remove_property('download_course_ids')

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
                if MoodleService.should_download_course(course.id, download_course_ids, dont_download_course_ids):
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

    def _select_should_download_submissions(self):
        """
        Asks the user if submissions should be downloaded
        """
        download_submissions = self.config.get_download_submissions()

        self.section_seperator()
        Log.info(
            'Submissions（提交）是你或老师上传到作业的文件。'
            + 'Moodle 没有提供一次性下载课程所有提交信息的接口。'
        )
        Log.warning('因此，监控提交的变化可能会比较慢。')
        print('')

        download_submissions = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载作业提交吗？'),
            default_is_yes=download_submissions,
        )

        self.config.set_property('download_submissions', download_submissions)

    def _select_should_download_databases(self):
        """
        Asks the user if databases should be downloaded
        """
        download_databases = self.config.get_download_databases()

        self.section_seperator()
        Log.info(
            '在 Moodle 的数据库模块中，数据可以结构化存储。'
            + '学生通常也可以在那里上传数据。由于下载器的实现'
            + '还未完全优化，下载数据库是可选的。'
            + '目前只下载文件，缩略图会被忽略。'
        )
        print('')

        download_databases = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载课程的数据库吗？'), default_is_yes=download_databases
        )

        self.config.set_property('download_databases', download_databases)

    def _select_should_download_forums(self):
        """
        Asks the user if forums should be downloaded
        """
        download_forums = self.config.get_download_forums()

        self.section_seperator()
        Log.info('在论坛中，学生和老师可以一起讨论和交流信息。')
        print('')

        download_forums = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载课程的论坛吗？'), default_is_yes=download_forums
        )

        self.config.set_property('download_forums', download_forums)

    def _select_should_download_quizzes(self):
        """
        Asks the user if quizzes should be downloaded
        """
        download_quizzes = self.config.get_download_quizzes()

        self.section_seperator()
        Log.info(
            '测验是学生必须在课程中完成的考试，会被评分。'
            + '只有正在进行或已完成的测验会被下载。'
        )
        print('')

        download_quizzes = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载课程的测验吗？'), default_is_yes=download_quizzes
        )

        self.config.set_property('download_quizzes', download_quizzes)

    def _select_should_download_lessons(self):
        """
        Asks the user if lessons should be downloaded
        """
        download_lessons = self.config.get_download_lessons()

        self.section_seperator()
        Log.info(
            'Lessons（课程）是一种自学方式，包含信息页面和需要回答的问题页面。'
            + '学生完成课程后会根据答案被评分。目前只下载不带答案的课程。'
            + '答案可能也可以下载，但尚未实现。'
        )
        print('')

        download_lessons = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载课程的 Lessons 吗？'), default_is_yes=download_lessons
        )

        self.config.set_property('download_lessons', download_lessons)

    def _select_should_download_workshops(self):
        """
        Asks the user if workshops should be downloaded
        """
        download_workshops = self.config.get_download_workshops()

        self.section_seperator()
        Log.info(
            'Workshops（研讨会）按照同行评审流程运作。'
            + '学生可以提交作业并需要评估其他学生的提交。'
        )
        print('')

        download_workshops = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载课程的研讨会吗？'), default_is_yes=download_workshops
        )

        self.config.set_property('download_workshops', download_workshops)

    def _select_should_download_books(self):
        """
        Asks the user if books should be downloaded
        """
        download_books = self.config.get_download_books()

        self.section_seperator()
        Log.info('Books（书籍）是页面的集合。每本书都会创建一个目录。')
        print('')

        download_books = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载课程的书籍吗？'), default_is_yes=download_books
        )

        self.config.set_property('download_books', download_books)

    def _select_should_download_calendars(self):
        """
        Asks the user if calendars should be downloaded
        """
        download_calendars = self.config.get_download_calendars()

        self.section_seperator()
        Log.info('日历可以作为每个事件单独生成的 HTML 文件下载。')
        print('')

        download_calendars = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载课程的日历吗？'), default_is_yes=download_calendars
        )

        self.config.set_property('download_calendars', download_calendars)

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
            + '还有一些 Moodle 插件无法在 Moodle 应用中显示，'
            + '所以你需要浏览器 cookie 来下载这些插件文件。'
        )

        Log.debug(
            'Moodle 浏览器 cookie 使用你的私有令牌创建，并存储在 `Configs.txt` 文件中。'
            + '只要此选项启用，文件就始终包含有效的 cookie。'
        )

        if self.config.get_privatetoken() is None:
            Log.error(
                '当前配置中没有存储私有令牌。'
                + '使用 moodle-dl --new-token 创建私有令牌（必要时加上 --sso）'
            )

        print('')

        download_also_with_cookie = Cutie.prompt_yes_or_no(
            Log.blue_str('你想要下载需要 cookie 的文件吗？'),
            default_is_yes=download_also_with_cookie,
        )

        self.config.set_property('download_also_with_cookie', download_also_with_cookie)

    def section_seperator(self):
        """Print a seperator line."""
        print('\n' + '-' * shutil.get_terminal_size().columns + '\n')
