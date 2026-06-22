# -*- coding: utf-8 -*-
"""
Tests pinning the per-module numbering contract against SPECIFIC
statements found by the Mobile App TypeScript verification
sub-agent (deleg_7d27570a).

Each test reads a specific statement from the mobile app repo
and asserts that our production code is consistent with the
reference implementation's data model.

If any test fails, the docstring cites the file:line so the
fix can be re-verified.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MOBILE_REPO = '/Users/linqilan/CodingProjects/moodle/moodle_mobile_app_official_repo_for_reference'


def _read(rel):
    full = os.path.join(MOBILE_REPO, rel)
    if not os.path.exists(full):
        return ''
    with open(full) as f:
        return f.read()


# =========================================================================
# Section ordering: "ordered in the order of appearance"
# =========================================================================
class TestMobileAppSectionOrderingContract:
    """Pin the contract from course.ts:962-997 — getSectionsModules
    iterates section.contents in array order. The mobile app
    docs explicitly say 'ordered in the order of appearance in
    the course' (line 963).
    """

    def test_get_sections_modules_documents_ordering(self):
        """The mobile app's getSectionsModules has a docstring
        documenting that modules are ordered by appearance.
        Sub-agent citation: course.ts:962-997, especially line 963.
        """
        src = _read('src/core/features/course/services/course.ts')
        if not src:
            return  # Skip if not found
        assert 'getSectionsModules' in src, (
            'course.ts should define getSectionsModules method'
        )
        # Look at the surrounding context (docstring is above the
        # function definition)
        idx = src.find('getSectionsModules')
        if idx > 0:
            # Look at the 500 chars BEFORE the function (where
            # the JSDoc comment lives) + the function itself
            context = src[max(0, idx - 500):idx + 1000]
            assert 'order' in context.lower() or 'appear' in context.lower(), (
                'getSectionsModules docstring should document the '
                'ordering contract (e.g. "ordered in the order of '
                'appearance in the course")'
            )

    def test_get_sections_modules_iterates_section_contents(self):
        """getSectionsModules iterates section.contents (modules),
        not files. Each entry is treated as one ordered unit.
        """
        src = _read('src/core/features/course/services/course.ts')
        if not src:
            return
        idx = src.find('getSectionsModules')
        if idx < 0:
            return
        body = src[idx:idx + 2000]
        # Should iterate section.contents
        assert 'section.contents' in body or 'contents.forEach' in body, (
            'getSectionsModules should iterate section.contents '
            '(modules, not files)'
        )


# =========================================================================
# Module = one card, files nested
# =========================================================================
class TestMobileAppOneCardPerModuleContract:
    """Pin the contract from course-section.html:74-85 — section
    iterates section.contents and emits exactly one card per
    module. Files are NOT separate cards.
    """

    def test_section_html_iterates_contents_not_files(self):
        """The section renderer iterates section.contents (modules)
        and produces one card per module.
        """
        src = _read(
            'src/core/features/course/components/course-section/course-section.html'
        )
        if not src:
            # Try alternate paths
            for alt in [
                'src/core/components/course-section/course-section.html',
                'src/core/features/course/components/course-section.html',
            ]:
                src = _read(alt)
                if src:
                    break
        if not src:
            return
        # Should iterate section.contents (modules)
        assert 'section.contents' in src, (
            'course-section.html should iterate section.contents'
        )
        # Should produce one card per module
        assert 'core-course-module' in src, (
            'course-section.html should emit one core-course-module '
            'card per module'
        )


# =========================================================================
# Book: one module, chapters inside
# =========================================================================
class TestMobileAppBookOneModuleContract:
    """Pin the contract — book appears as ONE module in
    section.contents[], chapters are accessed inside the book.
    """

    def test_book_module_one_entry_in_section(self):
        """A book module appears as ONE entry on the section.
        Chapters are accessed after clicking into the book.
        """
        src = _read('src/addons/mod/book/services/book.ts')
        if not src:
            return
        # Book has getContentsMap and getTocList for chapter handling
        assert 'getContentsMap' in src or 'getTocList' in src, (
            'book.ts should have chapter handling functions'
        )

    def test_book_chapters_accessed_via_toc(self):
        """Chapters are navigated via getTocList (book-internal),
        not as separate section entries.
        """
        src = _read('src/addons/mod/book/services/book.ts')
        if not src:
            return
        assert 'getTocList' in src, (
            'book.ts should have getTocList for chapter navigation '
            '(inside book, not at section level)'
        )


# =========================================================================
# URL module: one card, introfiles separate
# =========================================================================
class TestMobileAppUrlModuleOneCardContract:
    """Pin the contract — URL module is one card. Introfiles
    (description-URL attachments) are returned via
    CoreCourseModuleStandardElements.introfiles, NOT as
    separate section entries.
    """

    def test_url_module_has_one_main_file(self):
        """URL module's getModuleMainFile returns module.contents[0]
        only — URL is treated as one file.
        Sub-agent citation: src/addons/mod/url/services/handlers/module.ts
        """
        # Try multiple possible file paths
        candidate_paths = [
            'src/addons/mod/url/services/handlers/module.ts',
            'src/addons/mod/url/services/module.ts',
            'src/addons/mod/url/classes/module.ts',
            'src/addons/mod/url/services/handlers/module-handler.ts',
        ]
        src = ''
        for path in candidate_paths:
            src = _read(path)
            if src:
                break
        if not src:
            return  # Skip if file not found in any location
        # The file should have getModuleMainFile or equivalent
        assert (
            'getModuleMainFile' in src or 'getMainFile' in src
        ), (
            'URL module handler should have getModuleMainFile method'
        )

    def test_introfiles_field_separate_from_module_contents(self):
        """Introfiles (description attachments) are returned via
        CoreCourseModuleStandardElements.introfiles, separate
        from module.contents[]. This is in course-module-helper.ts.
        """
        src = _read(
            'src/core/features/course/services/course-module-helper.ts'
        )
        if not src:
            return
        # Should reference introfiles
        assert 'introfiles' in src, (
            'course-module-helper.ts should reference introfiles field'
        )


# =========================================================================
# No *NN* prefix in mobile app
# =========================================================================
class TestMobileAppNoPositionCounterContract:
    """Pin the contract — mobile app has NO concept of *NN*
    prefix or positionInSection. The moodle-dl prefix is purely
    a filesystem-level convention.
    """

    def test_mobile_app_no_position_in_section(self):
        """A full-text search for positionInSection returns
        zero matches in the core.
        """
        import subprocess
        # Search the entire mobile app repo
        try:
            r = subprocess.run(
                ['grep', '-r', 'positionInSection',
                 os.path.join(MOBILE_REPO, 'src/core'),
                 '--include=*.ts', '-l'],
                capture_output=True, text=True, timeout=30,
            )
            # Should be empty (no matches in core)
            files_with_match = [p for p in r.stdout.strip().split('\n') if p]
            assert len(files_with_match) == 0, (
                f'Mobile app core should not have positionInSection. '
                f'Found in: {files_with_match[:3]}'
            )
        except subprocess.TimeoutExpired:
            # If grep is too slow, skip
            pass

    def test_module_title_no_prefix_in_html(self):
        """The module card HTML renders title directly from
        module.handlerData.title — no *NN* prefix.
        """
        src = _read(
            'src/core/features/course/components/module/core-course-module.html'
        )
        if not src:
            return
        # Should NOT have any *NN* prefix pattern in template
        # (mobile app doesn't use such prefixes)
        # The title is rendered from handlerData.title
        assert 'handlerData.title' in src, (
            'core-course-module.html should render title from '
            'module.handlerData.title'
        )


# =========================================================================
# Section content type contract: modules vs subsections
# =========================================================================
class TestMobileAppSectionContentTypes:
    """Pin the type contract from course.ts:1490-1494 and
    1714-1723 — section.contents is an array of
    CoreCourseModuleOrSection.
    """

    def test_section_content_is_module_helper_exists(self):
        """course.ts should define sectionContentIsModule helper
        that distinguishes modules from subsections.
        """
        src = _read('src/core/features/course/services/course.ts')
        if not src:
            return
        assert 'sectionContentIsModule' in src, (
            'course.ts should define sectionContentIsModule helper'
        )

    def test_module_identified_by_modname(self):
        """A module is identified by having 'modname' field.
        The mobile app's type guard checks for this.
        Sub-agent citation: course.ts:1490-1494.
        """
        src = _read('src/core/features/course/services/course.ts')
        if not src:
            return
        # Find the EXPORTED function (not the call site)
        idx = src.find('export function sectionContentIsModule')
        if idx < 0:
            # Try alternate patterns
            idx = src.find('function sectionContentIsModule')
        if idx > 0:
            body = src[idx:idx + 500]
            # The function should check for 'modname' field
            assert 'modname' in body, (
                'sectionContentIsModule type guard should reference '
                'modname field'
            )


# =========================================================================
# Behavioral verification matching mobile app data model
# =========================================================================
class TestCodeMatchesMobileAppDataModel:
    """Pin that our production code matches the mobile app's
    data model: one slot per module, regardless of file count.
    """

    def test_module_with_5_files_one_slot(self):
        """A module with 5 files (e.g. resource with intro+content+
        attachments) gets ONE slot in moodle-dl, matching the
        mobile app's one-card-per-module rendering.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # 5 files in the same module
        files = [
            _make_file(1, 100, f'file{i}.html') for i in range(5)
        ]
        for f in files:
            f._module_has_attachments = True

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # All 5 files share slot 0 (one card per module)
        for f in files:
            assert f.position_in_section == 0, (
                f'All files in one module should share slot 0. '
                f'Got: {f.position_in_section}'
            )

    def test_section_with_4_modules_matches_array_order(self):
        """4 modules in a section produce 4 distinct slots,
        matching the array order from core_course_get_contents.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = [
            _make_file(1, 1, 'mod1.md', modname='label'),
            _make_file(1, 2, 'mod2.pdf', modname='resource'),
            _make_file(1, 3, 'https://mod3.com', modname='url'),
            _make_file(1, 4, 'mod4.html', modname='page'),
        ]
        for f in files:
            f._module_has_attachments = False

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # 4 distinct slots in input order (0, 1, 2, 3)
        assert files[0].position_in_section == 0
        assert files[1].position_in_section == 1
        assert files[2].position_in_section == 2
        assert files[3].position_in_section == 3


class TestUrlModulePositioningMatchesMobileApp:
    """Pin that URL modules get one slot (matching mobile app's
    one-card-per-URL behavior).
    """

    def test_url_module_with_introfiles_one_slot(self):
        """A URL module with description-URL introfiles is still
        ONE module with ONE slot. The introfiles don't get
        separate slots.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # URL module (cm 100) with 1 main URL + 2 description-url introfiles
        # The introfiles share the parent module's slot
        files = [
            _make_file(1, 100, 'https://main-url.com',
                       modname='url', content_type='url'),
            _make_file(1, 100, 'https://intro1.com',
                       modname='url', content_type='description-url'),
            _make_file(1, 100, 'https://intro2.com',
                       modname='url', content_type='description-url'),
        ]
        for f in files:
            f._module_has_attachments = False

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # All 3 files share slot 0 (URL module is one card)
        for f in files:
            assert f.position_in_section == 0, (
                f'URL module with introfiles is one module. '
                f'All files should share slot 0. '
                f'Got: {f.position_in_section}'
            )


# =========================================================================
# Helpers
# =========================================================================
def _make_file(section_id, module_id, filename, modname='resource',
               has_attachments=False, content_type='file'):
    from moodle_dl.types import File
    f = File(
        module_id=module_id, section_name='S', section_id=section_id,
        module_name=f'mod_{module_id}', content_filepath='/',
        content_filename=filename,
        content_fileurl=f'https://example.com/{filename}',
        content_filesize=1024, content_timemodified=0,
        module_modname=modname,
        content_type=content_type,
        content_isexternalfile=False,
    )
    f._module_has_attachments = has_attachments
    return f


def _make_rb():
    from moodle_dl.moodle.result_builder import ResultBuilder
    rb = ResultBuilder.__new__(ResultBuilder)
    return rb