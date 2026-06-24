# -*- coding: utf-8 -*-
"""
Regression tests for the double-extension bug fix (commit 48b26e9).

User report (2026-06-23, CS6 /Volumes/Untitled/CS6):

  Found a NEW regression in the Coursework section. When a resource
  module's display name already ends with the file extension
  (e.g. "pacman-cw1.zip" where the actual file is "cw1_pacman.zip"),
  the downloaded file was saved with a DOUBLE extension
  ("pacman-cw1.zip.zip" instead of "pacman-cw1.zip").

  Same bug applied to many file types:
    - .py   → "classify-iris.py.py"
    - .sh   → "install-anaconda.sh.sh"
    - .dat  → "StoneFlakes.dat.dat"
    - .csv  → "winequality-white.csv.csv"
    - .zip  → "reinforcement.zip.zip"
    - .py   → "regression.py.py"

Fix (commit 48b26e9): In _handle_files, when processing a resource
module, if `module_name` already ends with the file extension, use it
as-is (don't append the extension again).

This file pins the contract that moodle-dl MUST NOT create double
extensions on disk. The tests exercise _handle_files with various
real-world filename patterns.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_builder(version=2024010100):
    """Build a ResultBuilder with KCL Moodle production config."""
    from moodle_dl.moodle.result_builder import ResultBuilder
    from moodle_dl.types import MoodleURL
    return ResultBuilder(
        moodle_url=MoodleURL(use_http=False, domain='keats.kcl.ac.uk', path='/'),
        version=version,
        mod_plurals={},
        token='secret_token_123',
    )


def make_location(module_id=100, module_name='Resource',
                  section_id=1, section_name='S',
                  module_modname='resource'):
    """Build a location dict mimicking the get_files_in_modules flow."""
    return {
        'module_id': module_id,
        'module_name': module_name,
        'section_id': section_id,
        'section_name': section_name,
        'module_modname': module_modname,
    }


class TestResourceModuleNoDoubleExtension:
    """Pin the contract: moodle-dl MUST NOT create double extensions
    on disk when module_name already ends with the file extension.

    Real-world reproducer (6CCS3ML1 Machine Learning, June 2026):
      - module_name="pacman-cw1.zip", API filename="cw1_pacman.zip"
        → saved as "pacman-cw1.zip" (NOT "pacman-cw1.zip.zip")
      - module_name="classify-iris.py", API filename="classify-iris.py"
        → saved as "classify-iris.py" (NOT "classify-iris.py.py")
    """

    def test_zip_module_name_not_doubled(self):
        """module_name='pacman-cw1.zip' + api='cw1_pacman.zip'
        → content_filename='pacman-cw1.zip' (not .zip.zip).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'cw1_pacman.zip',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/cw1_pacman.zip?token=secret',
            'filesize': 1024,
            'timemodified': 1700000000,
            'mimetype': 'application/zip',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='pacman-cw1.zip'),
        )
        assert len(files) == 1
        assert files[0].content_filename == 'pacman-cw1.zip', (
            f'Double extension! Got: {files[0].content_filename!r}. '
            f'Should be: pacman-cw1.zip'
        )

    def test_py_module_name_not_doubled(self):
        """module_name='classify-iris.py' → content_filename
        stays as 'classify-iris.py' (not .py.py).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'classify-iris.py',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/classify-iris.py?token=secret',
            'filesize': 2048,
            'timemodified': 1700000000,
            'mimetype': 'text/x-python',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='classify-iris.py'),
        )
        assert files[0].content_filename == 'classify-iris.py'

    def test_sh_module_name_not_doubled(self):
        """module_name='install-anaconda.sh' → content_filename
        stays as 'install-anaconda.sh' (not .sh.sh).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'install-anaconda.sh',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/install-anaconda.sh?token=secret',
            'filesize': 512,
            'timemodified': 1700000000,
            'mimetype': 'application/x-sh',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='install-anaconda.sh'),
        )
        assert files[0].content_filename == 'install-anaconda.sh'

    def test_csv_module_name_not_doubled(self):
        """module_name='winequality-white.csv' → content_filename
        stays as 'winequality-white.csv' (not .csv.csv).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'winequality-white.csv',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/winequality-white.csv?token=secret',
            'filesize': 4096,
            'timemodified': 1700000000,
            'mimetype': 'text/csv',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='winequality-white.csv'),
        )
        assert files[0].content_filename == 'winequality-white.csv'

    def test_dat_module_name_not_doubled(self):
        """module_name='StoneFlakes.dat' → content_filename
        stays as 'StoneFlakes.dat' (not .dat.dat).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'StoneFlakes.dat',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/StoneFlakes.dat?token=secret',
            'filesize': 8192,
            'timemodified': 1700000000,
            'mimetype': 'application/octet-stream',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='StoneFlakes.dat'),
        )
        assert files[0].content_filename == 'StoneFlakes.dat'

    def test_txt_module_name_not_doubled(self):
        """module_name='data_banknote_authentication.txt' →
        content_filename stays as is (not .txt.txt).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'data_banknote_authentication.txt',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/data_banknote_authentication.txt?token=secret',
            'filesize': 4096,
            'timemodified': 1700000000,
            'mimetype': 'text/plain',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='data_banknote_authentication.txt'),
        )
        assert files[0].content_filename == 'data_banknote_authentication.txt'


class TestResourceModuleWithDifferentExtension:
    """module_name and API filename have DIFFERENT extensions.
    In this case the bug fix should append the API filename's
    extension (the historical behavior is preserved).
    """

    def test_module_name_without_ext_api_with_ext(self):
        """module_name='Lecture 1' + api='lect01.pdf' →
        content_filename='Lecture 1.pdf' (extension appended).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'lect01.pdf',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/lect01.pdf?token=secret',
            'filesize': 4096,
            'timemodified': 1700000000,
            'mimetype': 'application/pdf',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='Lecture 1'),
        )
        assert files[0].content_filename == 'Lecture 1.pdf'

    def test_module_name_with_no_ext_api_with_ext(self):
        """module_name='NoExtension' + api='file.pdf' →
        content_filename='NoExtension.pdf'.
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'file.pdf',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/file.pdf?token=secret',
            'filesize': 4096,
            'timemodified': 1700000000,
            'mimetype': 'application/pdf',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='NoExtension'),
        )
        assert files[0].content_filename == 'NoExtension.pdf'


class TestResourceModuleWithSameExtensionCaseInsensitive:
    """Case-insensitive comparison: module_name='PACMAN-CW1.ZIP'
    with api='cw1_pacman.zip' should still NOT be doubled.
    """

    def test_uppercase_zip_module_name(self):
        """module_name='PACMAN-CW1.ZIP' + api='cw1_pacman.zip'
        → content_filename='PACMAN-CW1.ZIP' (not doubled).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'cw1_pacman.zip',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/cw1_pacman.zip?token=secret',
            'filesize': 1024,
            'timemodified': 1700000000,
            'mimetype': 'application/zip',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='PACMAN-CW1.ZIP'),
        )
        assert files[0].content_filename == 'PACMAN-CW1.ZIP'

    def test_mixed_case_module_name(self):
        """module_name='PacMan-Cw1.Zip' + api='cw1_pacman.zip'
        → content_filename='PacMan-Cw1.Zip' (not doubled).
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'cw1_pacman.zip',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/cw1_pacman.zip?token=secret',
            'filesize': 1024,
            'timemodified': 1700000000,
            'mimetype': 'application/zip',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='PacMan-Cw1.Zip'),
        )
        assert files[0].content_filename == 'PacMan-Cw1.Zip'


class TestNonResourceModuleNoDoubleExtension:
    """The fix is specific to resource modules. Other module types
    (page, label, url) should not be affected by the fix.
    """

    def test_page_module_unaffected(self):
        """For a 'page' module, the resource-extension logic doesn't apply.
        The content_filename is the API filename.
        """
        builder = make_builder()
        contents = [{
            'type': 'file',
            'filename': 'page.html',
            'filepath': '/',
            'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_page/content/0/page.html?token=secret',
            'filesize': 2048,
            'timemodified': 1700000000,
            'mimetype': 'text/html',
            'isexternalfile': False,
        }]
        files = builder._handle_files(
            contents,
            **make_location(module_name='My Page', module_modname='page'),
        )
        # For page modules, the filename should be the API filename
        assert files[0].content_filename == 'page.html'


class TestResourceModuleMultipleFilesNoDoubleExtension:
    """Resource module with multiple files, each with potentially
    already-included extensions.
    """

    def test_multiple_resource_files_each_correctly_named(self):
        """Multiple files in one resource module, each correctly named."""
        builder = make_builder()
        contents = [
            {
                'type': 'file',
                'filename': 'cw1_pacman.zip',
                'filepath': '/',
                'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/cw1_pacman.zip?token=secret',
                'filesize': 1024,
                'timemodified': 1700000000,
                'mimetype': 'application/zip',
                'isexternalfile': False,
            },
            {
                'type': 'file',
                'filename': 'classify-iris.py',
                'filepath': '/',
                'fileurl': 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/classify-iris.py?token=secret',
                'filesize': 2048,
                'timemodified': 1700000000,
                'mimetype': 'text/x-python',
                'isexternalfile': False,
            },
        ]
        # But _handle_files is called per-content, not multi-content.
        # So we call it twice with different module_name
        files1 = builder._handle_files(
            [contents[0]],
            **make_location(module_name='pacman-cw1.zip'),
        )
        files2 = builder._handle_files(
            [contents[1]],
            **make_location(module_name='classify-iris.py'),
        )
        assert files1[0].content_filename == 'pacman-cw1.zip'
        assert files2[0].content_filename == 'classify-iris.py'


class TestPluginfileUrlsInSectionSummary:
    """Pluginfile.php URLs in section summaries MUST be downloaded
    (regression from commit 75d2393).

    Real-world case: section_summary contains HTML with <img src="...">
    or <a href="..."> pointing to pluginfile.php URLs on the Moodle
    domain. These were silently dropped by the early-skip filter
    in _find_all_urls.
    """

    def _make_section_summary_files(self, builder, summary_html):
        """Helper to extract URLs from a section summary HTML."""
        return builder._find_all_urls(
            summary_html,
            no_search_for_moodle_urls=False,
            filter_urls_containing=[],
            **{
                'module_id': 0,
                'module_name': 'Section summary',
                'section_id': 1,
                'section_name': 'S',
                'module_modname': 'section_summary',
                'content_filepath': '/',
            },
        )

    def test_banner_png_in_section_summary_kept(self):
        """Section summary with <img src='pluginfile.php/.../banner.png'>
        must produce a File entry (not silently dropped).
        """
        builder = make_builder()
        banner_url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/'
            'course/section/1801074/Informatics-banner4.png'
            '?token=secret&offline=1'
        )
        summary = (
            '<p>Welcome to the course.</p>'
            f'<img src="{banner_url}" alt="banner"/>'
        )
        files = self._make_section_summary_files(builder, summary)

        assert len(files) >= 1, (
            f'Banner image URL was dropped from section summary. '
            f'Got {len(files)} files.'
        )
        # The banner URL (or its normalized form) should be among files
        urls = {f.content_fileurl for f in files}
        assert any('Informatics-banner4.png' in u for u in urls), (
            f'Banner URL not in extracted files. Got: {urls}'
        )

    def test_book_chapter_pdf_in_section_summary_kept(self):
        """Section summary with <a href='pluginfile.php/.../chapter.pdf'>
        must produce a File entry.
        """
        builder = make_builder()
        chapter_pdf_url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/12113071/'
            'mod_book/chapter/866060/6CCS3ML1 Machine Learning.pdf'
            '?token=secret&offline=1'
        )
        summary = (
            '<p>See the chapter:</p>'
            f'<a href="{chapter_pdf_url}">Chapter PDF</a>'
        )
        files = self._make_section_summary_files(builder, summary)

        assert len(files) >= 1, (
            f'Chapter PDF URL was dropped from section summary. '
            f'Got {len(files)} files.'
        )
        urls = {f.content_fileurl for f in files}
        assert any('6CCS3ML1' in u for u in urls), (
            f'Chapter PDF URL not in extracted files. Got: {urls}'
        )

    def test_research_banner_png_in_section_summary_kept(self):
        """Section summary with research banner PNG kept."""
        builder = make_builder()
        research_banner_url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/2655601/'
            'course/section/690976/Research-banner-2.3.png'
            '?token=secret&offline=1'
        )
        summary = (
            '<p>Research data:</p>'
            f'<img src="{research_banner_url}" alt="research"/>'
        )
        files = self._make_section_summary_files(builder, summary)

        assert len(files) >= 1, (
            f'Research banner URL was dropped. Got {len(files)} files.'
        )
        urls = {f.content_fileurl for f in files}
        assert any('Research-banner' in u for u in urls)

    def test_internal_moodle_page_link_still_skipped(self):
        """Internal Moodle links (e.g. /mod/page/view.php?id=42) must
        STILL be skipped in section summary (no shortcut files).
        """
        builder = make_builder()
        internal_url = 'https://keats.kcl.ac.uk/mod/page/view.php?id=42'
        summary = (
            '<p>See also:</p>'
            f'<a href="{internal_url}">related page</a>'
        )
        files = self._make_section_summary_files(builder, summary)

        urls = {f.content_fileurl for f in files}
        assert internal_url not in urls, (
            f'Internal Moodle link should still be skipped. Got: {urls}'
        )

    def test_thumbnail_image_in_section_summary_kept(self):
        """Image used in section summary, e.g. /theme/image.php/...
        urls — also should be kept if they are pluginfile.php on moodle domain.
        """
        builder = make_builder()
        theme_url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/'
            'course/section/42/theme_icon.png?token=secret'
        )
        summary = f'<img src="{theme_url}"/>'
        files = self._make_section_summary_files(builder, summary)

        urls = {f.content_fileurl for f in files}
        assert any('theme_icon.png' in u for u in urls)


class TestPluginfileUrlsInResourceDescription:
    """pluginfile.php URLs in resource module descriptions.
    (The original bug from 75d2393 was for resource descriptions.)
    """

    def test_resource_description_with_pluginfile_pdf(self):
        """Resource module description with pluginfile.php PDF URL.
        Real case (CS2 audit): 370 files across 35 courses were
        silently dropped. The fix preserves these URLs.
        """
        builder = make_builder()
        pdf_url = (
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/12679107/'
            'mod_resource/content/0/cw1.pdf?token=secret&offline=1'
        )
        description = (
            '<p>Coursework 1:</p>'
            f'<a href="{pdf_url}">Coursework 1 PDF</a>'
        )

        files = builder._find_all_urls(
            description,
            no_search_for_moodle_urls=False,
            filter_urls_containing=[],
            **{
                'module_id': 100,
                'module_name': 'Coursework 1',
                'section_id': 1,
                'section_name': 'S',
                'module_modname': 'resource',
                'content_filepath': '/',
            },
        )
        urls = {f.content_fileurl for f in files}
        assert any('cw1.pdf' in u for u in urls), (
            f'Coursework PDF URL was dropped. Got: {urls}'
        )

    def test_resource_description_with_multiple_pluginfile_urls(self):
        """Resource description with multiple pluginfile.php URLs.
        Real case: KLaSS Research Data had PNG, GIF, MP3, ZIP all
        embedded as <a>/<img>/<source> in the same description.
        """
        builder = make_builder()
        png = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/icon.png?token=T'
        gif = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/anim.gif?token=T'
        mp3 = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/audio.mp3?token=T'
        zip_url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_resource/content/0/data.zip?token=T'

        description = (
            f'<p>See:</p>'
            f'<a href="{png}">icon</a>'
            f'<a href="{gif}">anim</a>'
            f'<a href="{mp3}">audio</a>'
            f'<a href="{zip_url}">data</a>'
        )

        files = builder._find_all_urls(
            description,
            no_search_for_moodle_urls=False,
            filter_urls_containing=[],
            **{
                'module_id': 100,
                'module_name': 'Data Lifecycle',
                'section_id': 1,
                'section_name': 'S',
                'module_modname': 'resource',
                'content_filepath': '/',
            },
        )

        urls = {f.content_fileurl for f in files}
        assert any('icon.png' in u for u in urls)
        assert any('anim.gif' in u for u in urls)
        assert any('audio.mp3' in u for u in urls)
        assert any('data.zip' in u for u in urls)


class TestE2EProductionLikeScenario:
    """End-to-end tests simulating the 6CCS3ML1 course download scenario."""

    def test_coursework_section_does_not_create_double_extension_files(self):
        """The 6CCS3ML1 coursework section has 4 modules: cw1, cw2,
        marksheet, FAQ, plus pacman-cw1.zip and pacman-cw2.zip.

        After the fix:
          - pacman-cw1.zip → pacman-cw1.zip (NOT .zip.zip)
          - pacman-cw2.zip → pacman-cw2.zip (NOT .zip.zip)
          - classify-iris.py → classify-iris.py (NOT .py.py)
          - install-anaconda.sh → install-anaconda.sh (NOT .sh.sh)
        """
        builder = make_builder()

        # Simulate the 4 coursework modules with their files
        scenarios = [
            ('pacman-cw1.zip', 'cw1_pacman.zip', 'application/zip',
             'pacman-cw1.zip'),
            ('pacman-cw2.zip', 'pacman-cw2.zip', 'application/zip',
             'pacman-cw2.zip'),
            ('classify-iris.py', 'classify-iris.py', 'text/x-python',
             'classify-iris.py'),
            ('install-anaconda.sh', 'install-anaconda.sh', 'application/x-sh',
             'install-anaconda.sh'),
            ('StoneFlakes.dat', 'StoneFlakes.dat', 'application/octet-stream',
             'StoneFlakes.dat'),
            ('winequality-white.csv', 'winequality-white.csv', 'text/csv',
             'winequality-white.csv'),
            ('reinforcement.zip', 'reinforcement.zip', 'application/zip',
             'reinforcement.zip'),
            ('evolutionary.zip', 'evolutionary.zip', 'application/zip',
             'evolutionary.zip'),
        ]

        for module_name, api_filename, mimetype, expected_filename in scenarios:
            contents = [{
                'type': 'file',
                'filename': api_filename,
                'filepath': '/',
                'fileurl': (
                    f'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/'
                    f'mod_resource/content/0/{api_filename}?token=secret'
                ),
                'filesize': 1024,
                'timemodified': 1700000000,
                'mimetype': mimetype,
                'isexternalfile': False,
            }]
            files = builder._handle_files(
                contents,
                **make_location(module_name=module_name),
            )
            assert files[0].content_filename == expected_filename, (
                f'For module_name={module_name!r}: '
                f'got content_filename={files[0].content_filename!r}, '
                f'expected {expected_filename!r}'
            )
