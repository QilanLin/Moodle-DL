# -*- coding: utf-8 -*-
from getpass import getpass

from moodle_dl.config import ConfigHelper
from moodle_dl.notifications.discord.discord_shooter import DiscordShooter
from moodle_dl.notifications.mail.mail_formatter import create_full_welcome_mail
from moodle_dl.notifications.mail.mail_shooter import MailShooter
from moodle_dl.notifications.ntfy.ntfy_shooter import NtfyShooter
from moodle_dl.notifications.telegram.telegram_shooter import (
    RequestRejectedError,
    TelegramShooter,
)
from moodle_dl.notifications.xmpp.xmpp_shooter import XmppShooter
from moodle_dl.types import MoodleDlOpts
from moodle_dl.utils import Cutie, Log
from moodle_dl.cli.localization import tr as _


class NotificationsWizard:
    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        self.config = config
        self.opts = opts

    def interactively_configure_all_services(self) -> None:
        """Guides the user through notification service selection and configuration."""

        # 定义所有可用的通知服务
        services = [
            ('mail', _('邮件通知 (Mail)', 'Mail notifications'), 'interactively_configure_mail'),
            ('telegram', _('Telegram 通知', 'Telegram notifications'), 'interactively_configure_telegram'),
            ('discord', _('Discord 通知', 'Discord notifications'), 'interactively_configure_discord'),
            ('ntfy', _('ntfy 通知', 'ntfy notifications'), 'interactively_configure_ntfy'),
            ('xmpp', _('XMPP 通知', 'XMPP notifications'), 'interactively_configure_xmpp'),
            ('sentry_dsn', _('Sentry 错误报告', 'Sentry error reporting'), 'interactively_configure_sentry'),
        ]

        # 获取当前已配置的服务
        current_selections = []
        for i, (config_key, name, method_name) in enumerate(services):
            if self.config.has_property(config_key):
                current_selections.append(i)

        # 创建选项列表
        choices = []
        for config_key, name, method_name in services:
            desc = self._get_service_description(config_key)
            choices.append(f'{name}\t{desc}')

        # 显示多选界面
        Log.blue(_('请选择要配置的通知服务：', 'Select notification services to configure:'))
        Log.info(
            _(
                '[使用↑↓键移动，空格键勾选/取消，回车键确认]',
                '[Use ↑↓ to move, Space to select/deselect, Enter to confirm]'
            )
        )
        print('')

        selected_indices = Cutie.select_multiple(
            options=choices,
            ticked_indices=current_selections,
            deselected_unticked_prefix='\033[1m[ ]\033[0m ',
            deselected_ticked_prefix='\033[1m[\033[32m✅\033[0;1m]\033[0m ',
            selected_unticked_prefix='\033[32;1m{ }\033[0m ',
            selected_ticked_prefix='\033[32;1m{✅}\033[0m ',
        )

        print('')

        # 配置选中的服务，移除未选中的服务
        for i, (config_key, name, method_name) in enumerate(services):
            if i in selected_indices:
                # 配置此服务
                method = getattr(self, method_name)
                method(skip_prompt=True)  # 跳过 y/N 提示，因为已经在多选界面选择了
            else:
                # 移除此服务的配置
                self.config.remove_property(config_key)

        if selected_indices:
            Log.success(
                _('已配置 {count} 个通知服务', 'Configured {count} notification service(s)', count=len(selected_indices))
            )
        else:
            Log.info(_('未配置任何通知服务', 'No notification services configured'))

    def _get_service_description(self, service_key: str) -> str:
        """Get description for each notification service."""
        descriptions = {
            'mail': _('通过 SMTP 发送电子邮件通知，支持错误报告', 'Send email notifications via SMTP, including error reports'),
            'telegram': _('通过 Telegram Bot 发送即时消息通知', 'Send instant notifications via Telegram Bot'),
            'discord': _('通过 Discord Webhook 发送消息到 Discord 频道', 'Send messages to a Discord channel via webhook'),
            'ntfy': _('通过 ntfy.sh 服务发送推送通知到手机/桌面', 'Send push notifications to phone/desktop via ntfy.sh'),
            'xmpp': _('通过 XMPP (Jabber) 协议发送即时消息', 'Send instant messages via XMPP (Jabber)'),
            'sentry_dsn': _('通过 Sentry 服务进行错误跟踪和日志记录', 'Track errors and logs with Sentry'),
        }
        return descriptions.get(service_key, '')

    def interactively_configure_mail(self, skip_prompt: bool = False) -> None:
        "Guides the user through the configuration of the mail notification."

        if not skip_prompt:
            do_mail = Cutie.prompt_yes_or_no(_('你想要激活邮件通知吗？', 'Do you want to enable mail notifications?'))

            if not do_mail:
                self.config.remove_property('mail')
                return

        if True:  # 保持原缩进结构
            print(_('[以下输入不会被验证！]', '[The following input will not be validated!]'))

            config_valid = False
            while not config_valid:
                sender = input(_('发件人的电子邮件地址:   ', 'Sender email address:   '))
                server_host = input(_('SMTP 服务器主机:   ', 'SMTP server host:   '))
                server_port = input(_('SMTP 服务器端口 [STARTTLS，默认 587]:   ', 'SMTP server port [STARTTLS, default 587]:   '))
                if server_port == '':
                    print(_('使用默认端口 587！', 'Using default port 587!'))
                    server_port = '587'
                username = input(_('SMTP 服务器用户名:   ', 'SMTP server username:   '))
                password = getpass(_('SMTP 服务器密码 [无输出显示]:   ', 'SMTP server password [no output shown]:   '))
                target = input(_('收件人的电子邮件地址:   ', 'Recipient email address:   '))

                print(_('正在测试邮件配置...', 'Testing mail configuration...'))
                welcome_content = create_full_welcome_mail()
                mail_shooter = MailShooter(sender, server_host, int(server_port), username, password)
                try:
                    mail_shooter.send(target, 'Hey!', welcome_content[0], welcome_content[1])
                except OSError as e:
                    print(_('发送测试邮件时出错: {error}', 'Error while sending test mail: {error}', error=str(e)))
                    continue
                else:
                    input(
                        _(
                            '请检查你是否收到了欢迎邮件。'
                            + '如果收到，按回车确认。\n如果没有，退出'
                            + '此程序（[CTRL]+[C]）并稍后重试。',
                            'Please check whether you received the welcome email. '
                            + 'If you did, press Enter to confirm.\nIf not, quit '
                            + 'this program ([CTRL]+[C]) and try again later.'
                        )
                    )
                    config_valid = True

                raw_send_error_msg = ''
                while raw_send_error_msg not in ['y', 'n']:
                    raw_send_error_msg = input(_('你想要同时通过邮件接收错误报告吗？[y/n]   ', 'Do you also want to receive error reports by mail? [y/n]   '))
                do_send_error_msg = raw_send_error_msg == 'y'

                mail_cfg = {
                    'sender': sender,
                    'server_host': server_host,
                    'server_port': server_port,
                    'username': username,
                    'password': password,
                    'target': target,
                    'send_error_msg': do_send_error_msg,
                }

                self.config.set_property('mail', mail_cfg)

    def interactively_configure_telegram(self, skip_prompt: bool = False) -> None:
        "Guides the user through the configuration of the telegram notification."

        if not skip_prompt:
            do_telegram = Cutie.prompt_yes_or_no(_('你想要激活 Telegram 通知吗？', 'Do you want to enable Telegram notifications?'))

            if not do_telegram:
                self.config.remove_property('telegram')
                return

        if True:  # 保持原缩进结构
            print(_('[以下输入不会被验证！]', '[The following input will not be validated!]'))
            print(
                _(
                    '打开以下链接获取设置 Telegram 通知的帮助：',
                    'Open the following link for help setting up Telegram notifications:'
                )
                + ' https://github.com/C0D3D3V/Moodle-DL/wiki/Telegram-Notification'
            )
            config_valid = False
            while not config_valid:
                telegram_token = input(_('Telegram 令牌:    ', 'Telegram token:    '))
                telegram_chatID = input(_('Telegram 聊天 ID:   ', 'Telegram chat ID:   '))

                print(_('正在测试 Telegram 配置...', 'Testing Telegram configuration...'))

                try:
                    telegram_shooter = TelegramShooter(telegram_token, telegram_chatID)
                    telegram_shooter.send(_('这是来自 moodle-dl 的测试消息！', 'This is a test message from moodle-dl!'))
                except (ConnectionError, RuntimeError, RequestRejectedError) as e:
                    print(_('发送测试消息时出错: {error}', 'Error while sending test message: {error}', error=str(e)))
                    continue

                else:
                    input(
                        _(
                            '请检查你是否收到了测试消息。'
                            + '如果收到，按回车确认。\n如果没有，退出'
                            + '此程序（[CTRL]+[C]）并稍后重试。',
                            'Please check whether you received the test message. '
                            + 'If you did, press Enter to confirm.\nIf not, quit '
                            + 'this program ([CTRL]+[C]) and try again later.'
                        )
                    )
                    config_valid = True

                raw_send_error_msg = ''
                while raw_send_error_msg not in ['y', 'n']:
                    raw_send_error_msg = input(_('你想要同时通过 Telegram 接收错误报告吗？[y/n]   ', 'Do you also want to receive error reports via Telegram? [y/n]   '))

                do_send_error_msg = raw_send_error_msg == 'y'

                telegram_cfg = {
                    'token': telegram_token,
                    'chat_id': telegram_chatID,
                    'send_error_msg': do_send_error_msg,
                }

                self.config.set_property('telegram', telegram_cfg)

    def interactively_configure_discord(self, skip_prompt: bool = False) -> None:
        "Guides the user through the configuration of the discord notification."

        if not skip_prompt:
            do_discord = Cutie.prompt_yes_or_no(_('你想要激活 Discord 通知吗？', 'Do you want to enable Discord notifications?'))

            if not do_discord:
                self.config.remove_property('discord')
                return

        if True:  # 保持原缩进结构
            print(_('[以下输入不会被验证！]', '[The following input will not be validated!]'))
            config_valid = False
            while not config_valid:
                webhook_urls = input(_('Discord webhook URLs（多个用逗号分隔）: ', 'Discord webhook URLs (comma-separated): '))
                webhook_urls = webhook_urls.split(',')
                webhook_urls = [webhook_url.strip() for webhook_url in webhook_urls]

                print(_('正在测试 Discord 配置...', 'Testing Discord configuration...'))

                try:
                    discord_shooter = DiscordShooter(webhook_urls)
                    discord_shooter.send_msg(_('这是来自 moodle-dl 的测试消息！', 'This is a test message from moodle-dl!'))

                except (ConnectionError, RuntimeError, RequestRejectedError) as e:
                    print(_('发送测试消息时出错: {error}', 'Error while sending test message: {error}', error=str(e)))
                    continue

                else:
                    input(
                        _(
                            '请检查你是否收到了测试消息。'
                            + '如果收到，按回车确认。\n如果没有，退出'
                            + '此程序（[CTRL]+[C]）并稍后重试。',
                            'Please check whether you received the test message. '
                            + 'If you did, press Enter to confirm.\nIf not, quit '
                            + 'this program ([CTRL]+[C]) and try again later.'
                        )
                    )
                    config_valid = True

                discord_cfg = {'webhook_urls': webhook_urls}

                self.config.set_property('discord', discord_cfg)

    def interactively_configure_ntfy(self, skip_prompt: bool = False) -> None:
        "Guides the user through the configuration of the ntfy notification."

        if not skip_prompt:
            do_ntfy = Cutie.prompt_yes_or_no(_('你想要激活 ntfy 通知吗？', 'Do you want to enable ntfy notifications?'))

            if not do_ntfy:
                self.config.remove_property('ntfy')
                return

        if True:  # 保持原缩进结构
            print(_('[以下输入不会被验证！]', '[The following input will not be validated!]'))
            config_valid = False
            while not config_valid:
                topic = input(_('ntfy 主题: ', 'ntfy topic: '))
                do_ntfy_server = Cutie.prompt_yes_or_no(_('你想要设置自定义 ntfy 服务器吗？', 'Do you want to set a custom ntfy server?'))
                server = None
                if do_ntfy_server:
                    server = input(_('ntfy 服务器: ', 'ntfy server: '))

                print(_('正在测试服务器配置...', 'Testing server configuration...'))

                try:
                    ntfy_shooter = NtfyShooter(topic=topic, server=server)
                    ntfy_shooter.send(title='', message=_('这是来自 moodle-dl 的测试消息！', 'This is a test message from moodle-dl!'))

                except (ConnectionError, RuntimeError) as e:
                    print(_('发送测试消息时出错: {error}', 'Error while sending test message: {error}', error=str(e)))
                    continue

                else:
                    input(
                        _(
                            '请检查你是否收到了测试消息。'
                            + '如果收到，按回车确认。\n如果没有，退出'
                            + '此程序（[CTRL]+[C]）并稍后重试。',
                            'Please check whether you received the test message. '
                            + 'If you did, press Enter to confirm.\nIf not, quit '
                            + 'this program ([CTRL]+[C]) and try again later.'
                        )
                    )
                    config_valid = True

                ntfy_cfg = {'topic': topic}
                if server:
                    ntfy_cfg['server'] = server

                self.config.set_property('ntfy', ntfy_cfg)

    def interactively_configure_xmpp(self, skip_prompt: bool = False) -> None:
        "Guides the user through the configuration of the xmpp notification."

        if not skip_prompt:
            do_xmpp = Cutie.prompt_yes_or_no(_('你想要激活 XMPP 通知吗？', 'Do you want to enable XMPP notifications?'))

            if not do_xmpp:
                self.config.remove_property('xmpp')
                return

        if True:  # 保持原缩进结构
            print(_('[以下输入不会被验证！]', '[The following input will not be validated!]'))
            config_valid = False
            while not config_valid:
                sender = input(_('发送者的 JID:   ', 'Sender JID:   '))
                password = getpass(_('发送者的密码 [无输出显示]:   ', 'Sender password [no output shown]:   '))
                target = input(_('接收者的 JID:   ', 'Recipient JID:   '))
                print(_('正在测试 XMPP 配置...', 'Testing XMPP configuration...'))

                try:
                    xmpp_shooter = XmppShooter(sender, password, target)
                    xmpp_shooter.send(_('这是来自 moodle-dl 的测试消息！', 'This is a test message from moodle-dl!'))
                except (
                    ConnectionError,
                    OSError,
                    RuntimeError,
                ) as e:
                    print(_('发送测试消息时出错: {error}', 'Error while sending test message: {error}', error=str(e)))
                    continue

                else:
                    input(
                        _(
                            '请检查你是否收到了测试消息。'
                            + '如果收到，按回车确认。\n如果没有，退出'
                            + '此程序（[CTRL]+[C]）并稍后重试。',
                            'Please check whether you received the test message. '
                            + 'If you did, press Enter to confirm.\nIf not, quit '
                            + 'this program ([CTRL]+[C]) and try again later.'
                        )
                    )
                    config_valid = True

                raw_send_error_msg = ''
                while raw_send_error_msg not in ['y', 'n']:
                    raw_send_error_msg = input(_('你想要同时通过 XMPP 接收错误报告吗？[y/n]   ', 'Do you also want to receive error reports via XMPP? [y/n]   '))

                do_send_error_msg = raw_send_error_msg == 'y'

                xmpp_cfg = {
                    'sender': sender,
                    'password': password,
                    'target': target,
                    'send_error_msg': do_send_error_msg,
                }

                self.config.set_property('xmpp', xmpp_cfg)

    def interactively_configure_sentry(self, skip_prompt: bool = False) -> None:
        "Guides the user through the configuration of Sentry error reporting."

        if not skip_prompt:
            do_sentry = Cutie.prompt_yes_or_no(_('你想要配置通过 Sentry 进行错误报告吗？', 'Do you want to configure error reporting through Sentry?'))

            if not do_sentry:
                self.config.remove_property('sentry_dsn')
                return

        if True:  # 保持原缩进结构
            print(_('[以下输入不会被验证！]', '[The following input will not be validated!]'))
            sentry_dsn = input(_('请输入你的 Sentry DSN:   ', 'Enter your Sentry DSN:   '))

            if sentry_dsn:
                self.config.set_property('sentry_dsn', sentry_dsn)
                Log.success(_('Sentry DSN 已保存', 'Sentry DSN saved'))
            else:
                Log.warning(_('未提供 Sentry DSN，已跳过配置', 'No Sentry DSN provided; skipped configuration'))
