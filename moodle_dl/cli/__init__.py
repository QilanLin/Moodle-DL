# -*- coding: utf-8 -*-
import os
import sys

from moodle_dl.cli.config_wizard import ConfigWizard
from moodle_dl.cli.database_manager import DatabaseManager
from moodle_dl.cli.localization import set_init_language, tr as _
from moodle_dl.cli.moodle_wizard import MoodleWizard
from moodle_dl.cli.notifications_wizard import NotificationsWizard
from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts
from moodle_dl.utils import Cutie, Log

__all__ = ["ConfigWizard", "DatabaseManager", "NotificationsWizard"]


def _select_init_language() -> str:
    print('')
    Log.blue('请选择语言 / Select language:')
    choice = Cutie.select(['中文', 'English'])
    language = 'en' if choice == 1 else 'zh'
    set_init_language(language)
    print('')
    return language


def init_config(config: ConfigHelper, opts: MoodleDlOpts):
    _select_init_language()

    if config.is_present():
        do_override_input = Cutie.prompt_yes_or_no(
            Log.error_str(_('你想要覆盖现有的配置吗？', 'Do you want to overwrite the existing configuration?'))
        )

        if not do_override_input:
            sys.exit(0)

    NotificationsWizard(config, opts).interactively_configure_all_services()

    MoodleWizard(config, opts).interactively_acquire_token()

    Log.success(_('配置已完成并保存！', 'Configuration completed and saved!'))

    if os.name != 'nt':
        working_dir = os.path.abspath(opts.path)
        moodle_dl_path = os.path.abspath(sys.argv[0])
        Log.info(
            _(
                '  在你的 Unix 系统上设置此程序的定时任务：\n'
                + '    1. `crontab -e`\n'
                + f'    2. 添加 `*/15 * * * * cd "{working_dir}" && "{moodle_dl_path}" >/dev/null 2>&1`\n'
                + '    3. 保存即可！',
                '  To schedule this program on your Unix system:\n'
                + '    1. `crontab -e`\n'
                + f'    2. Add `*/15 * * * * cd "{working_dir}" && "{moodle_dl_path}" >/dev/null 2>&1`\n'
                + '    3. Save the file.'
            )
        )

        Log.info(
            _(
                '有关定期运行 `moodle-dl` 的更多方法，请查看 wiki'
                + ' (https://github.com/C0D3D3V/Moodle-DL/wiki/Start-Moodle-dl-periodically-or-via-Telegram)',
                'For more ways to run `moodle-dl` periodically, see the wiki'
                + ' (https://github.com/C0D3D3V/Moodle-DL/wiki/Start-Moodle-dl-periodically-or-via-Telegram)'
            )
        )
    else:
        Log.info(
            _(
                '如果你想定期运行 moodle-dl，可以查看 wiki '
                + '(https://github.com/C0D3D3V/Moodle-DL/wiki/Start-Moodle-dl-periodically-or-via-Telegram)',
                'If you want to run moodle-dl periodically, see the wiki '
                + '(https://github.com/C0D3D3V/Moodle-DL/wiki/Start-Moodle-dl-periodically-or-via-Telegram)'
            )
        )

    print('')

    Log.info(_('你可以随时使用 --config 选项进行额外配置。', 'You can use the --config option for additional configuration at any time.'))

    # 默认直接进入配置向导，不再询问
    # do_config = Cutie.prompt_yes_or_no('你想要现在进行额外配置吗？')
    do_config = True
    if do_config:
        print('')
        steps_count = ConfigWizard.get_config_steps_count()
        Log.info(
            _(
                '开始额外配置向导（{steps_count}个配置步骤）...',
                'Starting the additional configuration wizard ({steps_count} configuration steps)...',
                steps_count=steps_count,
            )
        )
        ConfigWizard(config, opts).interactively_acquire_config()
    else:
        print('')
        Log.info(
            _(
                '跳过额外配置。你可以稍后运行 `moodle-dl --config` 进行配置。',
                'Skipping additional configuration. You can run `moodle-dl --config` later.'
            )
        )

    print('')
    Log.success(_('一切就绪，可以开始了！', 'Everything is ready. You can start now!'))
