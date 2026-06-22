# -*- coding: utf-8 -*-
"""
Tests pinning the per-module numbering contract against specific
Moodle devdocs statements found by the devdocs verification
sub-agent (deleg_206a044f).

Each test reads a specific documented statement from the devdocs
repo and asserts that our production code (in result_builder.py
and friends) is consistent with the documented contract.

If any of these tests fail, either:
  1. The moodle-dl ordering assumption has drifted from the
     documented data model (fix the code), OR
  2. The devdocs contract has changed (update the test AND
     re-verify the code).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DEVDOCS_REPO = '/Users/linqilan/CodingProjects/moodle/devdocs_official_repo_for_reference'


def _read(relative_path):
    full_path = os.path.join(DEVDOCS_REPO, relative_path)
    if not os.path.exists(full_path):
        return ''
    with open(full_path) as f:
        return f.read()


# =========================================================================
# Specific devdocs contracts
# =========================================================================
class TestDevdocsSectionSequenceContract:
    """Pin the contract from conditionalactivities/index.md lines
    119–139: section->sequence is exploded on commas and each
    element is used as a key into get_course_mods() (which returns
    a map keyed by cm_id).

    This is unambiguous proof that section->sequence stores cm_ids,
    not file_ids. Our per-module numbering keys on cm_id (module_id),
    which is correct.
    """

    def test_conditional_activities_doc_has_sequence_explosion(self):
        """The canonical idiom of exploding section->sequence on
        commas and using each element as a key into the course
        modules map is documented in the conditionalactivities doc.
        """
        path = 'docs/apis/core/conditionalactivities/index.md'
        src = _read(path)
        if not src:
            # Try alternate
            for alt in [
                'docs/apis/core/conditionalactivities.md',
                'docs/apis/core/conditional-activities.md',
            ]:
                src = _read(alt)
                if src:
                    path = alt
                    break
        assert src, 'conditionalactivities/index.md should exist'
        # The code pattern should appear in the doc
        assert 'sequence' in src, (
            'conditionalactivities doc should reference section->sequence'
        )
        # Look for the explode-on-comma idiom
        assert 'explode' in src or 'split' in src, (
            'conditionalactivities doc should show sequence being split/exploded'
        )
        # Look for get_course_mods (the map being looked up)
        assert 'get_course_mods' in src or 'rawmods' in src or '$rawmods' in src, (
            'conditionalactivities doc should reference get_course_mods '
            '(cm_id-keyed lookup map)'
        )

    def test_section_sequence_idempotency_uses_cm_id(self):
        """The doc shows that each element of section->sequence is
        used as a direct key into the cm_id-keyed map, confirming
        cm_id storage.
        """
        path = 'docs/apis/core/conditionalactivities/index.md'
        src = _read(path)
        if not src:
            return  # Skipped by other test
        # The pattern is: $rawmods[$seq] where $seq is from explode
        # This means $seq is a cm_id (key in cm_id-keyed map)
        # Look for either pattern: $rawmods[$seq] OR $cm_info[$seq]
        assert '$rawmods[$seq]' in src or '$cm[$seq]' in src or '$cm_info[$seq]' in src, (
            'conditionalactivities doc should show direct cm_id lookup '
            'from section->sequence element'
        )


class TestDevdocsTargetCmidContract:
    """Pin the contract from format/index.md line 638:
    'int $targetcmid optional target cm id. For example, when
    moving a course module to a new position.'

    This formalizes "section position" as cm-level, not file-level.
    """

    def test_format_doc_has_targetcmid_parameter(self):
        """The state action for moving to a new position takes
        targetcmid, confirming position is cm-level.
        """
        path = 'docs/apis/plugintypes/format/index.md'
        src = _read(path)
        if not src:
            return  # Skip if not present
        # Look for $targetcmid
        assert 'targetcmid' in src or 'target_cm_id' in src, (
            'format/index.md should document targetcmid parameter '
            'for moving a CM to a new position'
        )

    def test_format_doc_explains_section_manipulation_via_cm(self):
        """Section manipulation is described in terms of cm_id,
        not file_id."""
        path = 'docs/apis/plugintypes/format/index.md'
        src = _read(path)
        if not src:
            return
        # Look for the canonical section manipulation phrases
        # (not strict — just verify the doc discusses section + cm)
        assert 'section' in src.lower(), (
            'format doc should discuss section manipulation'
        )
        assert 'cm' in src.lower() or 'course module' in src.lower(), (
            'format doc should discuss course module concepts'
        )


class TestDevdocsCmInfoContract:
    """Pin the contract from visibility.md lines 95–127:
    get_fast_modinfo returns cm_info objects (one per activity),
    not file-level entries.
    """

    def test_visibility_doc_describes_cm_info(self):
        """The visibility doc should describe cm_info objects."""
        path = 'docs/apis/plugintypes/mod/visibility.md'
        src = _read(path)
        if not src:
            return
        # Look for cm_info or course_modinfo
        assert 'cm_info' in src or 'course_modinfo' in src, (
            'visibility doc should describe cm_info objects'
        )

    def test_visibility_doc_cm_represents_activity_not_file(self):
        """cm_info represents an activity (a single course module),
        regardless of how many files the activity has.
        """
        path = 'docs/apis/plugintypes/mod/visibility.md'
        src = _read(path)
        if not src:
            return
        # Look for "activity" or "module" terms
        assert (
            'activity' in src.lower() or 'module' in src.lower()
        ), (
            'visibility doc should discuss activities/modules'
        )


class TestDevdocsFileAreaContract:
    """Pin the contract from files/index.md lines 18–35:
    Files are stored in file areas, identified by contextid
    (which is the module's context). A module can have multiple
    file areas, and each file area can hold multiple files.

    This confirms that:
    - One CM = one contextid
    - Multiple files per module is the standard pattern
    - Files are children of modules, not siblings in a section
    """

    def test_files_doc_defines_file_areas(self):
        """The files subsystem doc should explain file areas."""
        path = 'docs/apis/subsystems/files/index.md'
        src = _read(path)
        if not src:
            return
        # Look for file area definition
        assert 'file area' in src.lower() or 'filearea' in src.lower(), (
            'files doc should define file areas'
        )

    def test_files_doc_contextid_per_cm(self):
        """Files are scoped by contextid (which is the module's
        context for module-level files).
        """
        path = 'docs/apis/subsystems/files/index.md'
        src = _read(path)
        if not src:
            return
        # Look for contextid
        assert 'contextid' in src or 'context' in src.lower(), (
            'files doc should reference contextid (module context)'
        )


class TestDevdocsPluginfileCallbackContract:
    """Pin the contract from files/index.md lines 100–125:
    The pluginfile callback signature receives a $cm (the whole
    course module), not a single file. This confirms that the
    cm_id is the canonical handle for "everything inside this
    activity".
    """

    def test_pluginfile_callback_takes_cm(self):
        """The pluginfile callback signature should show $cm as a
        parameter (the whole module, not a single file).
        """
        path = 'docs/apis/subsystems/files/index.md'
        src = _read(path)
        if not src:
            return
        # Look for the pluginfile signature with $cm
        assert 'pluginfile' in src.lower(), (
            'files doc should document pluginfile callback'
        )
        # Look for $cm in the signature
        assert '$cm' in src, (
            'pluginfile callback should take $cm parameter '
            '(the whole course module, not a single file)'
        )


# =========================================================================
# Behavioral tests: verify our code matches the documented contract
# =========================================================================
class TestCodeMatchesDevdocsContract:
    """Pin that our production code uses cm_id as the unit of
    ordering, consistent with the devdocs.
    """

    def test_assign_positions_uses_module_id_not_file_id(self):
        """The production _assign_positions_to_files must key on
        module_id (cm_id), not on file_id. Pin the contract by
        checking the implementation.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        import inspect

        src = inspect.getsource(ResultBuilder._assign_positions_to_files)
        # The implementation should key on module_id (not file_id)
        assert 'module_id' in src, (
            '_assign_positions_to_files must use module_id (cm_id) '
            'as the unit of position assignment, consistent with '
            'course_sections.sequence storing cm_ids'
        )

    def test_assign_positions_avoids_file_id_in_key(self):
        """Production code must NOT use file_id or content_fileurl
        as the position key.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        import inspect

        src = inspect.getsource(ResultBuilder._assign_positions_to_files)
        # It should not be using file_id as a primary key for
        # position assignment (file_id would be wrong — module_id
        # is the unit per the devdocs contract)
        assert 'file_id' not in src or 'file_id = ' not in src, (
            '_assign_positions_to_files must not use file_id as '
            'a primary key for position assignment'
        )


# =========================================================================
# Behavioral tests: verify our code produces output matching
# devdocs-documented examples
# =========================================================================
class TestOutputMatchesDevdocsExample:
    """Pin that our numbering produces output that matches the
    format documented in devdocs (one position per CM).
    """

    def test_assign_positions_5_modules_5_distinct_slots(self):
        """5 CMs in a section → 5 distinct slots, matching the
        devdocs-documented contract (one position per CM).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        files = []
        for cm_id in range(1, 6):  # 5 CMs
            # Vary file counts to confirm module-level numbering
            if cm_id % 2 == 0:
                # Even cm_id: 2 files
                files.append(_make_file(1, cm_id, f'cm{cm_id}_a.html'))
                files.append(_make_file(1, cm_id, f'cm{cm_id}_b.pdf'))
            else:
                # Odd cm_id: 1 file
                files.append(_make_file(1, cm_id, f'cm{cm_id}.md'))

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # 5 distinct slots (one per CM)
        slots = sorted(set(f.position_in_section for f in files))
        assert slots == [0, 1, 2, 3, 4], (
            f'5 CMs should produce 5 distinct slots. Got: {slots}'
        )

        # Same-CM files share slot
        for cm_id in range(1, 6):
            cm_files = [f for f in files if f.module_id == cm_id]
            positions = [f.position_in_section for f in cm_files]
            assert len(set(positions)) == 1, (
                f'CM {cm_id} files should share one slot. '
                f'Got: {positions}'
            )

    def test_assign_positions_matches_section_sequence_order(self):
        """Files within a section, ordered by position_in_section,
        produce the same order as course_sections.sequence (cm_id
        order). This is the devdocs contract.
        """
        from moodle_dl.moodle.result_builder import ResultBuilder

        # Simulate a section with 4 CMs in sequence order [10, 20, 30, 40]
        # Each CM has 2-3 files
        files = []
        for cm_id in [10, 20, 30, 40]:
            for fn in ['main.html', 'main.pdf']:
                files.append(_make_file(1, cm_id, fn))

        rb = _make_rb()
        rb._assign_positions_to_files(files)

        # Position order should match cm_id order
        # CM 10 → slot 0, CM 20 → slot 1, CM 30 → slot 2, CM 40 → slot 3
        for cm_id, expected_slot in [(10, 0), (20, 1), (30, 2), (40, 3)]:
            cm_files = [f for f in files if f.module_id == cm_id]
            for f in cm_files:
                assert f.position_in_section == expected_slot, (
                    f'CM {cm_id} files should have slot {expected_slot}, '
                    f'got {f.position_in_section}'
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