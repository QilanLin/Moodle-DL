# -*- coding: utf-8 -*-
from typing import List

from moodle_dl.notifications.telegram.telegram_formatter import TelegramFormatter


class XmppFormatter(TelegramFormatter):
    """XMPP 消息格式化器"""
    
    @staticmethod
    def append_with_limit(new_line: str, one_msg_content: str, msg_list: List[str], limit: int = 4096):
        """Appends a new line to a message string,
        if the string is too long it is appended to the message list.
        Returns the new message string.

        Args:
            new_line (str): A new line to append to a message string
            one_msg_content (str): The current message string
            msg_list ([str]): The list of finished messages
        Returns:
            str: The new message string
        """
        if len(one_msg_content) + len(new_line) >= limit:
            msg_list.append(one_msg_content)
            if len(new_line) >= limit:
                new_line = new_line[: limit - 3] + '…'
            return new_line
        else:
            return one_msg_content + new_line

    @staticmethod
    def make_bold(string: str) -> str:
        """
        Makes a string bold in a XMPP message
        """
        return '*' + string + '*'


# 向后兼容别名（已弃用，请使用 XmppFormatter）
XmppFormater = XmppFormatter

