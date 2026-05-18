# -*- coding: utf-8 -*-
from typing import Dict, List


class PageContentFilter:
    KALTURA_HELPER_ICON_MAX_BYTES = 10 * 1024
    KALTURA_HELPER_ICON_FILENAMES = (
        'kaltura icon.png',
        'edit settings icon.png',
        'settings icon.png',
    )
    KALTURA_VIDEO_MARKERS = (
        '/filter/kaltura/lti_launch.php',
        'browseandembed/index/media/entryid',
        '/mod/kalvidres/view.php',
        'embediframejs',
    )

    @classmethod
    def page_contains_kaltura_video(cls, page_content: str, page_intro: str) -> bool:
        page_text = f'{page_content or ""}\n{page_intro or ""}'.lower()

        if any(marker in page_text for marker in cls.KALTURA_VIDEO_MARKERS):
            return True

        return (
            'kaltura' in page_text
            and ('entryid' in page_text or 'entry_id' in page_text or 'lti_launch' in page_text)
        )

    @classmethod
    def is_kaltura_helper_icon_from_page_content(cls, file: Dict) -> bool:
        filename = str(file.get('filename', '')).strip().lower()
        if filename not in cls.KALTURA_HELPER_ICON_FILENAMES:
            return False

        file_url = str(file.get('fileurl', '')).lower()
        return '/mod_page/content/' in file_url

    @classmethod
    def is_small_kaltura_helper_icon(cls, file: Dict) -> bool:
        if not cls.is_kaltura_helper_icon_from_page_content(file):
            return False

        try:
            filesize = int(file.get('filesize'))
        except (TypeError, ValueError):
            return False

        return 0 <= filesize <= cls.KALTURA_HELPER_ICON_MAX_BYTES

    @classmethod
    def filter_adjacent_kaltura_helper_icons(
        cls, page_files: List[Dict], page_content: str, page_intro: str
    ) -> List[Dict]:
        if not cls.page_contains_kaltura_video(page_content, page_intro):
            return page_files

        filenames = [str(file.get('filename', '')).strip().lower() for file in page_files]
        icon_run_indexes = set()
        run_length = len(cls.KALTURA_HELPER_ICON_FILENAMES)
        for index in range(0, len(filenames) - run_length + 1):
            run_files = page_files[index:index + run_length]
            if (
                tuple(filenames[index:index + run_length]) == cls.KALTURA_HELPER_ICON_FILENAMES
                and all(cls.is_kaltura_helper_icon_from_page_content(file) for file in run_files)
            ):
                icon_run_indexes.update(range(index, index + run_length))

        if not icon_run_indexes:
            return page_files

        return [
            file
            for index, file in enumerate(page_files)
            if index not in icon_run_indexes or not cls.is_small_kaltura_helper_icon(file)
        ]
