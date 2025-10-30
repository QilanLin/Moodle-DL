import os

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.types import File, MoodleDlOpts
from moodle_dl.utils import Cutie, Log


class DatabaseManager:
    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        self.config = config
        self.opts = opts
        self.state_recorder = StateRecorder(config, opts)

    def interactively_manage_database(self):
        stored_files = self.state_recorder.get_stored_files()

        stored_files = MoodleService.filter_courses(stored_files, self.config)

        if len(stored_files) <= 0:
            return

        course_options = []
        courses = []
        for course in stored_files:
            for course_file in course.files:
                if not os.path.exists(course_file.saved_to):
                    course_options.append(Log.blue_str(course.fullname))
                    courses.append(course)
                    break

        print(
            '此管理工具将引导你通过菜单选择性地从数据库中删除文件条目，'
            + '以便可以重新下载这些文件。'
        )

        Log.warning(
            '此工具仅显示本地缺失但存储在本地数据库中的文件。'
            + '如果课程中的文件没有缺失，则根本不会在此处列出。'
            + '此外，仅显示已选择下载的课程。'
        )

        Log.magenta(
            '对于数据库的更复杂操作，建议使用 SQLite 的数据库浏览器'
            + '（https://sqlitebrowser.org/）。'
        )

        if not courses:
            print('没有文件在本地缺失但存储在本地数据库中。无需操作。')
            return

        print('选择一个课程：')
        print('[用回车键确认你的选择]')
        print('')
        selected_course_id = Cutie.select(options=course_options)

        selected_course = courses[selected_course_id]

        section_options = []
        sections = []

        # Add the option to select all sections
        section_options.append(Log.magenta_str('[所有章节]'))
        sections.append(None)  # Add None at index 0 to avoid index shifting

        for course_file in selected_course.files:
            if not os.path.exists(course_file.saved_to) and (course_file.section_name not in sections):
                section_options.append(Log.magenta_str(course_file.section_name))
                sections.append(course_file.section_name)

        print('你想从哪些章节中选择文件？')
        print('[你可以用空格键选择，用回车键确认选择]')
        print('')

        selected_sections_ids = Cutie.select_multiple(options=section_options, minimal_count=1)

        selected_sections = []
        for selected_sections_id in selected_sections_ids:
            if selected_sections_id == 0:
                selected_sections = sections[1:]
                break
            elif (selected_sections_id) < len(sections):
                selected_sections.append(sections[selected_sections_id])

        file_options = []
        files = []

        # Add the option to select all files
        file_options.append(Log.cyan_str('[所有文件]'))
        files.append(None)  # Add None at index 0 to avoid index shifting

        for course_file in selected_course.files:
            if not os.path.exists(course_file.saved_to) and (course_file.section_name in selected_sections):
                file_options.append(Log.cyan_str(course_file.content_filename))
                files.append(course_file)

        print('应该从数据库中删除哪些文件，以便重新下载它们？')
        print('[你可以用空格键选择，用回车键确认选择]')
        print('')
        selected_files = Cutie.select_multiple(options=file_options)

        files_to_delete = []
        for file_index in selected_files:
            if file_index == 0:  # If all files is selected
                for file_to_delete in files[1:]:  # Ignore the first element of the array set as None
                    if isinstance(file_to_delete, File):
                        files_to_delete.append(file_to_delete)

                break

            elif file_index < len(files) and isinstance(files[file_index], File):
                files_to_delete.append(files[file_index])

        self.state_recorder.batch_delete_files_from_db(files_to_delete)

    def delete_old_files(self):
        stored_files = self.state_recorder.get_old_files()

        if len(stored_files) <= 0:
            print('未找到文件的旧副本。无需操作。')
            return

        print(
            '此管理工具将引导你通过菜单选择性地从数据库'
            + '和文件系统中删除文件的旧副本。'
        )

        course_options = []
        for course in stored_files:
            course_options.append(Log.blue_str(course.fullname))

        print('选择一个课程：')
        print('[用回车键确认你的选择]')
        print('')
        selected_course_id = Cutie.select(options=course_options)

        selected_course = stored_files[selected_course_id]

        section_options = []
        sections = []

        # Add the option to select all sections
        section_options.append(Log.magenta_str('[所有章节]'))
        sections.append(None)  # Add None at index 0 to avoid index shifting

        for course_file in selected_course.files:
            if course_file.section_name not in sections:
                section_options.append(Log.magenta_str(course_file.section_name))
                sections.append(course_file.section_name)

        print('你想从哪些章节中删除旧文件？')
        print('[你可以用空格键选择，用回车键确认选择]')
        print('')

        selected_sections_ids = Cutie.select_multiple(options=section_options, minimal_count=1)

        selected_sections = []
        for selected_sections_id in selected_sections_ids:
            if selected_sections_id == 0:
                selected_sections = sections[1:]
                break
            elif (selected_sections_id) < len(sections):
                selected_sections.append(sections[selected_sections_id])

        file_options = []
        files = []

        # Add the option to select all files
        file_options.append(Log.cyan_str('[所有文件]'))
        files.append(None)  # Add None at index 0 to avoid index shifting

        for course_file in selected_course.files:
            if course_file.section_name in selected_sections:
                file_options.append(Log.cyan_str(course_file.content_filename))
                files.append(course_file)

        print('应该删除哪些文件？')
        print('[你可以用空格键选择，用回车键确认选择]')
        print('')
        selected_files = Cutie.select_multiple(options=file_options)

        files_to_delete = []
        for file_index in selected_files:
            if file_index == 0:  # If all files is selected
                for file_to_delete in files[1:]:  # Ignore the first element of the array set as None
                    if isinstance(file_to_delete, File):
                        files_to_delete.append(file_to_delete)
                        if os.path.exists(file_to_delete.saved_to):
                            os.remove(file_to_delete.saved_to)

                break

            elif file_index < len(files) and isinstance(files[file_index], File):
                files_to_delete.append(files[file_index])
                if os.path.exists(files[file_index].saved_to):
                    os.remove(files[file_index].saved_to)

        self.state_recorder.batch_delete_files_from_db(files_to_delete)
