# -*- coding: utf-8 -*-
"""Small language switch used by the interactive init wizards."""

from typing import Literal

InitLanguage = Literal['zh', 'en']

_current_language: InitLanguage = 'zh'


def set_init_language(language: str) -> InitLanguage:
    """Set the language for the current interactive init run."""
    global _current_language

    normalized = (language or '').strip().lower()
    if normalized in {'en', 'english'}:
        _current_language = 'en'
    else:
        _current_language = 'zh'
    return _current_language


def get_init_language() -> InitLanguage:
    return _current_language


def is_english() -> bool:
    return _current_language == 'en'


def tr(zh: str, en: str = None, **kwargs) -> str:
    """Return the localized text for the current init language."""
    text = en if is_english() and en is not None else zh
    if kwargs:
        return text.format(**kwargs)
    return text
