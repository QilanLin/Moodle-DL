# -*- coding: utf-8 -*-
"""
Shared file classification helpers used before and during downloads.
"""

from typing import Any


OPTIONAL_METADATA_SUFFIXES = (
    '.json',
    '_info',
    '_notes.md',
)

OPTIONAL_METADATA_FILENAMES = {
    'launch form.html',
}


def is_optional_metadata_filename(filename: str) -> bool:
    filename_lower = str(filename or '').lower()
    return filename_lower.endswith(OPTIONAL_METADATA_SUFFIXES) or filename_lower in OPTIONAL_METADATA_FILENAMES


def is_optional_metadata_file(file: Any) -> bool:
    return is_optional_metadata_filename(getattr(file, 'content_filename', ''))
