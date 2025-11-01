import os
import sys

from moodle_dl.cli.config_wizard import ConfigWizard
from moodle_dl.cli.database_manager import DatabaseManager
from moodle_dl.cli.moodle_wizard import MoodleWizard
from moodle_dl.cli.notifications_wizard import NotificationsWizard
from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts
from moodle_dl.utils import Cutie, Log

__all__ = ["ConfigWizard", "DatabaseManager", "NotificationsWizard"]


def init_config(config: ConfigHelper, opts: MoodleDlOpts):
    if config.is_present():
        do_override_input = Cutie.prompt_yes_or_no(Log.error_str('你想要覆盖现有的配置吗？'))

        if not do_override_input:
            sys.exit(0)

    NotificationsWizard(config, opts).interactively_configure_all_services()

    do_sentry = Cutie.prompt_yes_or_no('你想要配置通过 Sentry 进行错误报告吗？')
    if do_sentry:
        sentry_dsn = input('请输入你的 Sentry DSN:   ')
        config.set_property('sentry_dsn', sentry_dsn)

    MoodleWizard(config, opts).interactively_acquire_token()

    Log.success('配置已完成并保存！')

    if os.name != 'nt':
        working_dir = os.path.abspath(opts.path)
        moodle_dl_path = os.path.abspath(sys.argv[0])
        Log.info(
            '  在你的 Unix 系统上设置此程序的定时任务：\n'
            + '    1. `crontab -e`\n'
            + f'    2. 添加 `*/15 * * * * cd "{working_dir}" && "{moodle_dl_path}" >/dev/null 2>&1`\n'
            + '    3. 保存即可！'
        )

        Log.info(
            '有关定期运行 `moodle-dl` 的更多方法，请查看 wiki'
            + ' (https://github.com/C0D3D3V/Moodle-DL/wiki/Start-Moodle-dl-periodically-or-via-Telegram)'
        )
    else:
        Log.info(
            '如果你想定期运行 moodle-dl，可以查看 wiki '
            + '(https://github.com/C0D3D3V/Moodle-DL/wiki/Start-Moodle-dl-periodically-or-via-Telegram)'
        )

    print('')

    Log.info('你可以随时使用 --config 选项进行额外配置。')

    # 默认直接进入配置向导，不再询问
    # do_config = Cutie.prompt_yes_or_no('你想要现在进行额外配置吗？')
    do_config = True
    if do_config:
        print('')
        Log.info('开始额外配置向导（7个配置步骤）...')
        ConfigWizard(config, opts).interactively_acquire_config()
    else:
        print('')
        Log.info('跳过额外配置。你可以稍后运行 `moodle-dl --config` 进行配置。')

    print('')
    Log.success('一切就绪，可以开始了！')
