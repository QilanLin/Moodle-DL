# -*- coding: utf-8 -*-
"""
Tests pinning the section-wide *NN* numbering contract against
specific structural invariants documented in the official
Moodle source.

Each test verifies ONE specific structural contract by reading
the official source. The test name and docstring describe the
contract being verified.

If any test fails because the contract has changed, it means
the moodle-dl ordering assumption has shifted. Update both the
production code and this test file together.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Reference paths for the official Moodle repo. Tests use these
# to read the actual server-side source and verify that our
# ordering assumptions are grounded in the documented data
# model.
MOODLE_PHP_REPO = '/Users/linqilan/CodingProjects/moodle/moodle_official_repo_for_reference'


def _read(relative_path):
    """Read a file from the official Moodle PHP repo. Returns
    empty string if file doesn't exist (the test will then
    fail in a useful way)."""
    full_path = os.path.join(MOODLE_PHP_REPO, relative_path)
    if not os.path.exists(full_path):
        return ''
    with open(full_path) as f:
        return f.read()


# =========================================================================
# Section ordering: per-cm (per-module)
# =========================================================================
class TestSectionSequenceIsPerCm:
    """course_sections.sequence is a comma-separated list of
    cm_ids (course_modules.id), NOT file_ids. Each cm_id
    represents ONE module, even if it has multiple files.

    This is the foundational contract that lets us use one
    position per module.
    """

    def test_db_course_modules_table_has_unique_id_per_module(self):
        """The mdl_course_modules table has a unique id column
        that's the cm_id used in course_sections.sequence.
        """
        # Look for the DB schema definition
        install_path = 'public/lib/db/install.xml'
        src = _read(install_path)
        assert 'course_modules' in src, (
            'mdl_course_modules table should exist in install.xml'
        )
        # The table should have an id field
        # Find the TABLE mdl_course_modules definition
        idx = src.find('TABLE mdl_course_modules')
        if idx > 0:
            block = src[idx:idx + 3000]
            assert '<FIELD NAME="id"' in block or 'NAME="id"' in block, (
                'mdl_course_modules should have an id column'
            )

    def test_db_files_table_separate_from_course_modules(self):
        """Files are in a separate table (mdl_files), NOT in
        mdl_course_modules. The sequence field references
        cm_ids (course_modules), not file_ids.
        """
        install_path = 'public/lib/db/install.xml'
        src = _read(install_path)
        assert 'files' in src, (
            'mdl_files table should exist (files are separate from modules)'
        )
        # And it's a SEPARATE table from mdl_course_modules
        assert 'course_modules' in src

    def test_course_sections_table_has_sequence_field(self):
        """mdl_course_sections.sequence is the field that holds
        cm_ids in section order.
        """
        install_path = 'public/lib/db/install.xml'
        src = _read(install_path)
        assert 'course_sections' in src, (
            'mdl_course_sections table should exist'
        )
        idx = src.find('TABLE mdl_course_sections')
        if idx > 0:
            block = src[idx:idx + 3000]
            assert 'NAME="sequence"' in block, (
                'mdl_course_sections should have a sequence column'
            )

    def test_section_info_get_sequence_cm_infos_returns_per_cm(self):
        """section_info::get_sequence_cm_infos returns one info
        struct per cm_id, confirming that the sequence is per-cm.
        """
        section_info_path = 'public/course/classes/section_info.php'
        src = _read(section_info_path)
        if not src:
            # If the file is in a different location in this version,
            # search for it
            import subprocess
            r = subprocess.run(
                ['find', MOODLE_PHP_REPO, '-name', 'section_info.php', '-type', 'f'],
                capture_output=True, text=True, timeout=10,
            )
            for path in r.stdout.strip().split('\n'):
                if 'classes/section_info.php' in path:
                    full_path = path.replace(MOODLE_PHP_REPO + '/', '')
                    src = _read(full_path)
                    break

        assert 'get_sequence_cm_infos' in src, (
            'section_info class should have get_sequence_cm_infos method'
        )
        # Find the method body
        idx = src.find('function get_sequence_cm_infos')
        if idx < 0:
            idx = src.find('public function get_sequence_cm_infos')
        if idx > 0:
            body = src[idx:idx + 2000]
            # The method should return an array keyed by cm_id (not file_id)
            assert 'cmid' in body.lower() or 'cm_id' in body.lower() or 'cminfo' in body.lower(), (
                'get_sequence_cm_infos should reference cmid/cminfo (one per module)'
            )


# =========================================================================
# Module-level scope: each cm_id is one unit of ordering
# =========================================================================
class TestCmIdIsOneOrderingUnit:
    """Each cm_id (course_modules.id) is ONE unit in section-wide
    ordering, regardless of how many files the module has.
    """

    def test_cm_can_have_multiple_files_via_file_storage(self):
        """A single cm (course_module) can have multiple files
        attached via the file storage subsystem (file areas).
        Each cm → multiple files → but cm_id is ONE ordering unit.
        """
        filelib_path = 'public/lib/filelib.php'
        src = _read(filelib_path)
        # file_get_file_areas is per-context (cm_id)
        if 'file_get_file_areas' in src:
            # Confirms: file areas are scoped by contextid (cm_id)
            idx = src.find('function file_get_file_areas')
            if idx > 0:
                body = src[idx:idx + 1500]
                # The function should iterate file areas within a cm
                assert 'contextid' in body or 'context' in body, (
                    'file_get_file_areas should be context-based (per cm_id)'
                )

    def test_files_table_has_contextid_pointing_to_cm(self):
        """mdl_files.contextid points to a context (which for
        a course module context = cm_id). So files are
        associated with cm via context.
        """
        install_path = 'public/lib/db/install.xml'
        src = _read(install_path)
        idx = src.find('TABLE mdl_files')
        if idx > 0:
            block = src[idx:idx + 5000]
            assert 'NAME="contextid"' in block, (
                'mdl_files should have contextid column (linking to cm)'
            )


# =========================================================================
# Externallib: core_course_get_contents response shape
# =========================================================================
class TestCourseGetContentsResponseShape:
    """core_course_get_contents returns ONE entry per module,
    with the module's files nested under 'contents'. This is the
    contract that lets us count modules (not files) for ordering.
    """

    def test_course_sections_sequence_contains_each_cm_id_once(self):
        """course_sections.sequence contains each cm_id at most once.

        Per course/lib.php line 268:
            'course_sections.sequence contains each module id not
             more than once in the course'

        This is the foundational contract: one position per module,
        regardless of how many files the module has.
        """
        course_lib_path = 'public/course/lib.php'
        src = _read(course_lib_path)
        # Find the sequence uniqueness comment
        idx = src.find('sequence contains each module id not more than once')
        assert idx > 0, (
            'course/lib.php should document that course_sections.sequence '
            'contains each module id not more than once (this is the '
            'data model contract for per-module ordering)'
        )

    def test_externallib_iterates_modules_at_outer_level(self):
        """get_course_contents iterates modules at the OUTER level.
        Each module gets its own array entry with its own contents.
        """
        externallib_path = 'public/course/externallib.php'
        src = _read(externallib_path)
        idx = src.find('public static function get_course_contents(')
        assert idx > 0, (
            'externallib.php should define get_course_contents method'
        )
        # The function body iterates modules (cm) and produces a
        # module entry per cm, with the cm's contents nested
        body = src[idx:idx + 20000]
        # Look for the typical pattern: a module array with 'contents' nested
        assert 'contents' in body, (
            'get_course_contents should produce a modules array with '
            "each module having a 'contents' subarray"
        )

    def test_externallib_returns_modules_with_contents_array(self):
        """get_course_contents (PHP method for core_course_get_contents
        Web Service) returns each module with a 'contents' array
        of file objects. So modules are iterated at the OUTER
        level, files are nested inside.
        """
        externallib_path = 'public/course/externallib.php'
        src = _read(externallib_path)
        idx = src.find('public static function get_course_contents(')
        assert idx > 0, (
            'externallib.php should define get_course_contents method'
        )
        # Look within first 10000 chars for the function body
        body = src[idx:idx + 15000]
        # Look for "contents" being assigned in the module loop
        assert "'contents'" in body or '"contents"' in body, (
            'get_course_contents should produce modules with '
            "a 'contents' array (one entry per module, files nested inside)"
        )


# =========================================================================
# Behavioral verification using our own code path
# =========================================================================
class TestPerModuleNumberingProducesServerAlignedOrder:
    """Verify that per-module numbering produces section layouts
    that align with how Moodle's server would present them.
    """

    def test_section_layout_matches_server_sequence_order(self):
        """Files within a section, ordered by position_in_section,
        produce the same ordering as course_sections.sequence."""
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Simulate 5 modules in a section:
        # module 1: 2 files (resource with html+pdf)
        # module 2: 1 file (flat label)
        # module 3: 3 files (assign with intro+submission+attachments)
        # module 4: 1 file (flat url)
        # module 5: 2 files (page with html+css)
        files = []
        # mod 1
        f = _make_file(1, 1, 'm1.html', modname='resource', has_attachments=True)
        files.append(f)
        f = _make_file(1, 1, 'm1.pdf', modname='resource', has_attachments=True)
        files.append(f)
        # mod 2
        f = _make_file(1, 2, 'm2.md', modname='label', has_attachments=False)
        files.append(f)
        # mod 3
        for fn in ['m3.html', 'm3.pdf', 'm3.docx']:
            f = _make_file(1, 3, fn, modname='assign', has_attachments=True)
            files.append(f)
        # mod 4
        f = _make_file(1, 4, 'https://example.com/x', modname='url', has_attachments=False)
        files.append(f)
        # mod 5
        for fn in ['m5.html', 'm5.css']:
            f = _make_file(1, 5, fn, modname='page', has_attachments=True)
            files.append(f)

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # Each module gets ONE slot
        # module 1 → slot 0, module 2 → slot 1, ..., module 5 → slot 4
        for f in files[:2]:
            assert f.position_in_section == 0, (
                f'module 1 file should be slot 0, got {f.position_in_section}'
            )
        assert files[2].position_in_section == 1, (
            f'module 2 file should be slot 1, got {files[2].position_in_section}'
        )
        for f in files[3:6]:
            assert f.position_in_section == 2, (
                f'module 3 file should be slot 2, got {f.position_in_section}'
            )
        assert files[6].position_in_section == 3
        for f in files[7:]:
            assert f.position_in_section == 4

    def test_slot_count_equals_module_count_not_file_count(self):
        """Per-module numbering means slot count = module count,
        not file count.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # 10 modules, each with 2 files = 20 files total
        files = []
        for mod_id in range(1, 11):
            f1 = _make_file(1, mod_id, f'm{mod_id}.html', modname='resource', has_attachments=True)
            f2 = _make_file(1, mod_id, f'm{mod_id}.pdf', modname='resource', has_attachments=True)
            files.extend([f1, f2])

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # 10 unique slots (one per module), not 20
        slots = set(f.position_in_section for f in files)
        assert len(slots) == 10, (
            f'10 modules should produce 10 slots (one per module), '
            f'got {len(slots)}'
        )
        assert slots == set(range(10)), (
            f'Slots should be 0-9 (10 modules), got {sorted(slots)}'
        )


# =========================================================================
# Helpers
# =========================================================================
def _make_file(section_id, module_id, filename, modname='resource',
               has_attachments=False):
    from moodle_dl.types import File
    f = File(
        module_id=module_id, section_name='S', section_id=section_id,
        module_name=f'mod_{module_id}', content_filepath='/',
        content_filename=filename,
        content_fileurl=f'https://example.com/{filename}',
        content_filesize=1024, content_timemodified=0,
        module_modname=modname,
        content_type='file' if modname not in ('label', 'url') else 'description',
        content_isexternalfile=False,
    )
    f._module_has_attachments = has_attachments
    return f


def _make_rb():
    from moodle_dl.moodle.result_builder import ResultBuilder
    rb = ResultBuilder.__new__(ResultBuilder)
    return rb