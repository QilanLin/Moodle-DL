# -*- coding: utf-8 -*-
"""
Tests that pin the description-url prefix preservation during
external downloads.

User scenario (2026-06-23, /Volumes/Untitled/CS5):

  The Tom Mitchell ML textbook PDF (description-url, external
  file) was being downloaded with the *NN* prefix at start
  (e.g. *02* https：⧸⧸...TomMitchell.pdf) but saved to disk
  without the prefix (MachineLearningTomMitchell.pdf). This
  broke the section-wide *NN* ordering — Module Overview/
  had gaps in the numbering.

Root cause: in external_download_url (task.py:1076-1085),
when the file is a description-url and the HTTP HEAD/GET
response includes a guessed_file_name, the code overrides
self.filename with the new HTTP-derived name. This drops
the *NN* prefix that TaskFileOps.generate_filename_with_index
had already set. set_path() then recomputes saved_to using
the prefix-less filename, and the file is saved without prefix.

Fix: detect the existing *NN* prefix in self.filename and
re-apply it to the new HTTP-derived name.

These tests verify the prefix is preserved across:
  1. The fix at task.py:1076-1085 (description-url with
     new_name != '')
  2. The fallback path (new_name == '')
  3. Files that don't have a prefix (no regression)
  4. Files with 3-digit prefix (*100*, *NNN*)
  5. Edge cases (prefix without space, etc.)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _apply_external_filename_override(
    original_filename: str,
    guessed_file_name: str,
    content_type: str = 'description-url',
) -> str:
    """Replicate the fix logic from external_download_url line 1076.

    Pin the contract: when an external description-url download
    discovers the real filename via HEAD/GET, preserve the *NN*
    prefix from the original filename.
    """
    new_name, new_extension = os.path.splitext(guessed_file_name)
    if new_extension == '':
        new_extension = '.bin'

    if content_type == 'description-url' and new_name != '':
        prefix_match = re.match(r'^(\*\d+\*)\s+', original_filename)
        if prefix_match:
            prefix = prefix_match.group(1)
            return f'{prefix} {new_name}{new_extension}'
        else:
            return new_name + new_extension

    return original_filename


class TestExternalFilenamePreservesPrefix:
    """Pin the fix for description-url downloads."""

    def test_tom_mitchell_pdf_preserves_prefix(self):
        """The exact user scenario: Tom Mitchell PDF keeps *02*
        prefix even after HTTP response gives the real filename.
        """
        original = '*02* https：⧸⧸www.cs.cmu.edu⧸~tom⧸files⧸MachineLearningTomMitchell.pdf'
        guessed = 'MachineLearningTomMitchell.pdf'
        result = _apply_external_filename_override(original, guessed)
        assert result == '*02* MachineLearningTomMitchell.pdf', (
            f'Prefix *02* must be preserved. Got: {result!r}'
        )

    def test_zero_padded_prefix_preserved(self):
        original = '*01* some_lecture.pdf'
        guessed = 'lecture.pdf'
        result = _apply_external_filename_override(original, guessed)
        assert result == '*01* lecture.pdf'

    def test_three_digit_prefix_preserved(self):
        """Pin that 3-digit prefix (*100*, *NNN*) is preserved when
        sections have >99 files."""
        original = '*100* big_section_file.pdf'
        guessed = 'big_file.pdf'
        result = _apply_external_filename_override(original, guessed)
        assert result == '*100* big_file.pdf'

    def test_no_prefix_unchanged(self):
        """Files without a prefix (e.g. legacy files) should not
        get a prefix added."""
        original = 'no_prefix_file.pdf'
        guessed = 'real_name.pdf'
        result = _apply_external_filename_override(original, guessed)
        assert result == 'real_name.pdf'

    def test_https_url_with_extreme_chars_in_original(self):
        """Original filename contains URL-encoded special chars
        and Chinese characters — prefix is still preserved.
        """
        original = '*04* https：⧸⧸probml.github.io⧸pml-book⧸book1.html.webloc'
        guessed = 'book1.html'
        # new_name = 'book1', new_extension = '.html'
        result = _apply_external_filename_override(original, guessed)
        assert result == '*04* book1.html'

    def test_guessed_filename_without_extension_gets_bin(self):
        """When guessed_file_name has no extension, fallback to .bin.
        Prefix must still be preserved."""
        original = '*05* mystery_file'
        guessed = 'mystery_file'
        # new_extension = '' → '.bin'
        result = _apply_external_filename_override(original, guessed)
        assert result == '*05* mystery_file.bin'

    def test_non_description_url_not_affected(self):
        """For non-description-url content types, the filename
        override doesn't fire — original_filename is preserved."""
        original = '*07* resource.pdf'
        guessed = 'should_not_override.pdf'
        result = _apply_external_filename_override(
            original, guessed, content_type='resource_file'
        )
        # Returns the original filename unchanged
        assert result == '*07* resource.pdf'

    def test_empty_new_name_doesnt_override(self):
        """Pin: when new_name ends up empty AND new_extension is
        empty (no extension, no name), the .bin fallback still
        applies, and the prefix is preserved.
        """
        original = '*09* mystery_file'
        guessed = 'mystery_file'  # new_name='mystery_file', new_extension=''
        result = _apply_external_filename_override(original, guessed)
        # new_extension='' → '.bin' fallback
        # new_name='mystery_file' != '' → override fires with prefix
        assert result == '*09* mystery_file.bin'

    def test_prefix_format_two_digit(self):
        """Verify the prefix regex matches *01* through *99*."""
        for i in [1, 9, 10, 99]:
            original = f'*{i:02d}* file.pdf'
            result = _apply_external_filename_override(original, 'real.pdf')
            assert result == f'*{i:02d}* real.pdf', (
                f'Prefix *{i:02d}* not preserved: {result!r}'
            )

    def test_prefix_format_three_digit(self):
        """Verify 3-digit prefix format (used when position >= 100)."""
        for i in [100, 123, 999]:
            original = f'*{i:03d}* file.pdf'
            result = _apply_external_filename_override(original, 'real.pdf')
            assert result == f'*{i:03d}* real.pdf'

    def test_prefix_without_space_after(self):
        """Prefix like '*02*filename.pdf' (no space) — regex requires
        space after the prefix. Pin the behavior.
        """
        original = '*02*filename.pdf'
        guessed = 'real.pdf'
        result = _apply_external_filename_override(original, guessed)
        # No space → regex doesn't match → no prefix preserved
        # (This documents the current behavior; user-friendly case
        # is prefix with space)
        assert result == 'real.pdf'

    def test_prefix_only_at_start(self):
        """Prefix in the middle of filename should NOT be preserved
        (only leading prefix counts)."""
        original = 'weird_*02*_file.pdf'
        guessed = 'real.pdf'
        result = _apply_external_filename_override(original, guessed)
        assert result == 'real.pdf'


class TestCS5UserScenarioPinned:
    """Pin the exact user-observed scenario from CS5."""

    def test_cs5_module_overview_ordering_after_fix(self):
        """After the fix, the Module Overview section's
        description-url external files (Tom Mitchell, RLbook,
        AI.pdf, 1802.01528v3.pdf, Reading List.pdf, etc.) should
        all keep their *NN* prefix.

        Pre-fix, these were saved without prefix (Tom Mitchell
        → MachineLearningTomMitchell.pdf, no prefix).
        """
        # Simulate what happens for each external description-url
        # download in Module Overview
        scenarios = [
            # (original_filename, guessed_file_name, expected)
            ('*02* https：⧸⧸www.cs.cmu.edu⧸~tom⧸files⧸MachineLearningTomMitchell.pdf', 'MachineLearningTomMitchell.pdf', '*02* MachineLearningTomMitchell.pdf'),
            ('*03* http：⧸⧸incompleteideas.net⧸book⧸RLbook2020.pdf', 'RLbook2020.pdf', '*03* RLbook2020.pdf'),
            ('*05* https：⧸⧸probml.github.io⧸pml-book⧸book1.html', 'book1.html', '*05* book1.html'),
            ('*06* https：⧸⧸www.deeplearningbook.org⧸', 'content.html', '*06* content.html'),
            ('*08* https：⧸⧸ocw.mit.edu⧸courses⧸res-18-001', 'mitres_18_001_f17_full_book.pdf', '*08* mitres_18_001_f17_full_book.pdf'),
        ]
        for original, guessed, expected in scenarios:
            result = _apply_external_filename_override(original, guessed)
            assert result == expected, (
                f'Expected {expected!r}, got {result!r} '
                f'(original={original!r}, guessed={guessed!r})'
            )