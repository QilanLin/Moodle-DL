# -*- coding: utf-8 -*-
"""
Dummy Moodle course data builder for E2E regression tests.

The unit tests in test_module_folder_prefix.py, test_position_indexing.py,
etc. exercise individual functions in isolation. This file provides a
realistic dummy course that mirrors what the actual Moodle server
returns from core_course_get_contents + mod_*_get_*_by_courses, so we
can run the FULL ResultBuilder pipeline (get_files_in_sections →
_assign_positions → TaskFileOps.gen_path → generate_filename_with_index)
end-to-end and verify the ordering + path + prefix contract.

Real Moodle server response shapes (from the 3 official repos):

  - core_course_get_contents response: section.modules[].{id, name,
    modname, contents[].{filename, filepath, fileurl, type},
    description}
  - mod_*_get_*_by_courses response: courses[].{id, modules[]}.
    Each module has its own files[] array.

The builders below mirror these exact shapes.
"""
import os
import sys
import copy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Section summary helpers
# =========================================================================

def section_summary_html(banner_url=None):
    """Build a section summary HTML block matching what Moodle
    returns from core_course_get_contents for a section's 'summary'
    field.

    Section summary may contain:
      - Plain HTML (p, ul, etc.)
      - Inline images (banner URLs)
      - Inline links (description-url)
    """
    parts = ['<p>Section summary content here.</p>']
    if banner_url:
        parts.append(f'<p><img src="{banner_url}" alt="banner" /></p>')
    return '\n'.join(parts)


# =========================================================================
# Resource module helpers
# =========================================================================

def resource_module_contents(html_filename, pdf_filename, pdf_url):
    """Build a 'contents' array for a resource module with HTML +
    attached PDF, matching what core_course_get_contents returns.

    Real Moodle shape:
      contents: [
        {type: 'file', filename: 'X.html', filepath: '/',
         fileurl: '...', timecreated, timemodified, ...},
        {type: 'file', filename: 'X.pdf', filepath: '/',
         fileurl: '...', ...}
      ]
    """
    return [
        {
            'type': 'file',
            'filename': html_filename,
            'filepath': '/',
            'fileurl': f'https://example.com/{html_filename}',
            'timecreated': 1700000000,
            'timemodified': 1700000100,
            'filesize': 1024,
            'mimetype': 'text/html',
        },
        {
            'type': 'file',
            'filename': pdf_filename,
            'filepath': '/',
            'fileurl': pdf_url,
            'timecreated': 1700000000,
            'timemodified': 1700000100,
            'filesize': 1024 * 1024,
            'mimetype': 'application/pdf',
        },
    ]


def label_module_description(intro_text):
    """Build a 'description' string for a label module. Labels
    don't have separate files; their description is the content.
    """
    return f'<p>{intro_text}</p>'


# =========================================================================
# Dummy course builder
# =========================================================================

class DummyCourseBuilder:
    """Build a realistic dummy Moodle course matching the actual
    server response shape from core_course_get_contents.

    Usage:
      builder = DummyCourseBuilder(course_id=123, course_name='Test')
      builder.add_section('Week 1', section_id=1)
      builder.add_label(1, module_id=10, name='LECTURE SLIDES', text='...')
      builder.add_resource(1, module_id=20, name='Lecture 1',
                          html_name='Lecture 1.html',
                          pdf_name='Lecture 1.pdf',
                          pdf_url='https://...')
      ...
      sections = builder.build_sections()
      fetched_mods = builder.build_fetched_mods()
    """

    def __init__(self, course_id=123, course_name='Test Course'):
        self.course_id = course_id
        self.course_name = course_name
        self.sections_data = {}  # section_id -> dict
        self.fetched_mods = {}   # modname -> {module_id: dict}

    def add_section(self, section_id, name, summary_html=None):
        """Add a section to the course. Sections accumulate modules
        added via subsequent add_* calls.
        """
        self.sections_data[section_id] = {
            'id': section_id,
            'name': name,
            'summary': summary_html or '',
            'modules': [],
        }
        return self

    def add_label(self, section_id, module_id, name, text):
        """Add a label module. Labels have a 'description' field
        with HTML; they don't have separate files.
        """
        self._get_section(section_id)['modules'].append({
            'id': module_id,
            'name': name,
            'modname': 'label',
            'description': label_module_description(text),
            'contents': [],
            'url': '',
        })
        return self

    def add_resource(self, section_id, module_id, name,
                     html_name, pdf_name, pdf_url,
                     description=None):
        """Add a resource module with HTML + attached PDF.
        The HTML goes through the description handler;
        the PDF is fetched as a separate mod_resource entry.
        """
        self._get_section(section_id)['modules'].append({
            'id': module_id,
            'name': name,
            'modname': 'resource',
            'description': description,
            'contents': [],  # resource contents come from fetched_mods
            'url': '',
        })
        # Add to fetched_mods for mod_resource_get_resources_by_courses
        self.fetched_mods.setdefault('resource', {})[module_id] = {
            'id': module_id,
            'course': self.course_id,
            'name': name,
            'files': [{
                'filename': pdf_name,
                'filepath': '/',
                'fileurl': pdf_url,
                'type': 'file',
                'filesize': 1024 * 1024,
                'mimetype': 'application/pdf',
                'timemodified': 1700000100,
            }],
        }
        return self

    def add_section_files(self, section_id, module_id, modname,
                          module_name, files):
        """Add a module with multiple files (general-purpose).

        Each file is a dict that becomes a file in
        fetched_mods[modname][module_id]['files']. The module
        is added to the section with the given modname.

        Use this for resource modules with many files, where
        add_resource is too limited (only one file).
        """
        self._get_section(section_id)['modules'].append({
            'id': module_id,
            'name': module_name,
            'modname': modname,
            'description': '',
            'contents': [],
            'url': '',
        })
        self.fetched_mods.setdefault(modname, {})[module_id] = {
            'id': module_id,
            'course': self.course_id,
            'name': module_name,
            'files': files,
        }
        return self

    def add_url(self, section_id, module_id, name, external_url,
                description=None, introfile_urls=None):
        """Add a URL module (single external URL).
        The external_url is the URL's own URL; introfile_urls
        are description-file attachments (separate from the URL
        itself per mobile app reference contract).

        For E2E simplicity, we put the external URL as a 'file'
        entry in fetched_mods['url'][module_id]['files'], so the
        pipeline can process it like other modules. The real
        mod_url.py handler is more complex (creates a metadata
        file with external URL as fileurl, plus introfiles).
        """
        self._get_section(section_id)['modules'].append({
            'id': module_id,
            'name': name,
            'modname': 'url',
            'description': description,
            'contents': [],
            'url': '',
        })
        # Build files list: include the external URL + introfile URLs
        url_files = []
        # External URL is represented as a file (the URL itself)
        ext_filename = external_url.rsplit('/', 1)[-1] or 'url.html'
        url_files.append({
            'filename': ext_filename,
            'filepath': '/',
            'fileurl': external_url,
            'type': 'url',
            'filesize': 1024,
            'timemodified': 1700000200,
        })
        if introfile_urls:
            for i, url in enumerate(introfile_urls):
                url_files.append({
                    'filename': url.rsplit('/', 1)[-1],
                    'filepath': '/',
                    'fileurl': url,
                    'type': 'file',
                    'filesize': 1024 * 100,
                    'timemodified': 1700000201 + i,
                })
        self.fetched_mods.setdefault('url', {})[module_id] = {
            'id': module_id,
            'course': self.course_id,
            'name': name,
            'externalurl': external_url,
            'introfiles': [],  # Used by real mod_url.py (not in this test)
            'files': url_files,  # Used by ResultBuilder._handle_files
        }
        return self

    def add_page(self, section_id, module_id, name, html_content):
        """Add a page module (HTML content). Pages have a single
        content file (the rendered HTML).
        """
        self._get_section(section_id)['modules'].append({
            'id': module_id,
            'name': name,
            'modname': 'page',
            'description': html_content,
            'contents': [],
            'url': '',
        })
        return self

    def add_assign(self, section_id, module_id, name,
                   description, introfile_urls=None):
        """Add an assignment module with description + introfile
        attachments (typical case: assign with intro PDF).
        """
        self._get_section(section_id)['modules'].append({
            'id': module_id,
            'name': name,
            'modname': 'assign',
            'description': description,
            'contents': [],
            'url': '',
        })
        introfiles = []
        if introfile_urls:
            for url in introfile_urls:
                introfiles.append({
                    'filename': url.rsplit('/', 1)[-1],
                    'filepath': '/',
                    'fileurl': url,
                    'type': 'file',
                    'filesize': 1024,
                    'timemodified': 1700000300,
                })
        self.fetched_mods.setdefault('assign', {})[module_id] = {
            'id': module_id,
            'course': self.course_id,
            'name': name,
            'introfiles': introfiles,
        }
        return self

    def add_book(self, section_id, module_id, name, chapters):
        """Add a book module with chapters. Each chapter is a
        tuple (chapter_title, html_content, subfiles).

        Real Moodle shape: book_export_contents returns structure
        (the book index) + per-chapter index.html + per-chapter
        files all under ONE cm.
        """
        self._get_section(section_id)['modules'].append({
            'id': module_id,
            'name': name,
            'modname': 'book',
            'description': '',
            'contents': [],
            'url': '',
        })
        # Build book files (per-chapter index.html + subfiles)
        book_files = []
        # Book structure / overview file
        book_files.append({
            'filename': 'Book.html',
            'filepath': '/',
            'fileurl': f'https://example.com/book_{module_id}_structure.html',
            'type': 'file',
            'filesize': 1024,
            'timemodified': 1700000400,
        })
        # Per-chapter files
        for ch_idx, (ch_title, ch_html, sub_files) in enumerate(chapters):
            book_files.append({
                'filename': 'index.html',
                'filepath': f'/{ch_idx + 1}/',
                'fileurl': f'https://example.com/book_{module_id}_ch{ch_idx + 1}.html',
                'type': 'file',
                'filesize': 1024 * 50,
                'timemodified': 1700000401 + ch_idx,
            })
            for sf in sub_files:
                book_files.append({
                    'filename': sf,
                    'filepath': f'/{ch_idx + 1}/',
                    'fileurl': f'https://example.com/book_{module_id}_ch{ch_idx + 1}_{sf}',
                    'type': 'file',
                    'filesize': 1024 * 10,
                    'timemodified': 1700000401 + ch_idx,
                })
        self.fetched_mods.setdefault('book', {})[module_id] = {
            'id': module_id,
            'course': self.course_id,
            'name': name,
            'files': book_files,
        }
        return self

    def add_book_kaltura(self, module_id, kaltura_files):
        """Add kaltura video files to an existing book module's
        fetched_mods['book'][module_id]['files'].

        Each kaltura_file is a dict with:
          - entry_id: Kaltura entry_id (e.g. '1_cka79uqg')
          - filename: output filename (e.g. 'Week Overview - Video.mp4')
          - chapter_idx: which chapter this video belongs to (0-based)
          - filepath: chapter folder path (e.g. '/2/')

        This simulates what book.py's _extract_kaltura_videos_from_chapter
        produces — the kaltura video is added as a separate file
        with module_modname='cookie_mod-kalvidres' and the
        same module_id as the book.
        """
        book_entry = self.fetched_mods.setdefault('book', {}).setdefault(module_id, None)
        if book_entry is None:
            raise ValueError(f'Book module {module_id} not found. Call add_book first.')
        for kf in kaltura_files:
            book_entry['files'].append({
                'filename': kf['filename'],
                'filepath': kf.get('filepath', f'/{kf.get("chapter_idx", 0) + 1}/'),
                'fileurl': f'https://kaf.keats.kcl.ac.uk/browseandembed/index/media/entryid/{kf["entry_id"]}/showDescription/false/',
                # 'type' must be CONTENT_TYPE_KALVIDRES_EMBEDDED
                # (='kalvidres_embedded') for result_builder to override
                # module_modname to 'cookie_mod-kalvidres'.
                'type': 'kalvidres_embedded',
                'content_type': 'kalvidres_embedded',
                'isexternalfile': True,
                'mimetype': 'video/mp4',
                'filesize': 1024 * 100,
                'timemodified': 1700000000,
                'kaltura_entry_id': kf['entry_id'],
            })
        return self

    def add_book_url(self, module_id, url_files):
        """Add url-description-book files (weblocs) to an existing
        book module's fetched_mods['book'][module_id]['files'].

        Each url_file is a dict with:
          - external_url: the external URL (e.g. 'https://ebookcentral.proquest.com/...')
          - chapter_idx: which chapter this URL belongs to (0-based)
          - filepath: chapter folder path
        """
        book_entry = self.fetched_mods.setdefault('book', {}).get(module_id, None)
        if book_entry is None:
            raise ValueError(f'Book module {module_id} not found. Call add_book first.')
        for uf in url_files:
            # The URL needs to go through _find_all_urls to be
            # extracted as a separate file with module_modname=
            # 'url-description-book'. _find_all_urls is only called
            # for content_type in ('description', 'html'). We use
            # 'description' with a small HTML containing the URL.
            book_entry['files'].append({
                'filename': uf.get('filename', 'reading link'),
                'filepath': uf.get('filepath', f'/{uf.get("chapter_idx", 0) + 1}/'),
                'fileurl': '',
                'type': 'description',
                'content_type': 'description',
                'isexternalfile': True,
                'mimetype': 'text/html',
                'filesize': 0,
                'timemodified': 1700000000,
                'html': f'<p>See: <a href="{uf["external_url"]}">{uf["external_url"]}</a></p>',
                'no_search_for_moodle_urls': True,
                'description': f'<p>See: <a href="{uf["external_url"]}">{uf["external_url"]}</a></p>',
            })
        return self


    def build_sections(self):
        """Build the sections list in section_id order, mirroring
        the actual array returned by core_course_get_contents.
        """
        return [s for sid, s in sorted(self.sections_data.items())]

    def build_fetched_mods(self):
        """Build the fetched_mods dict in the shape that
        ResultBuilder._get_files_not_on_main_page expects:
        {modname: {module_id: {files, ...}}}

        Note: the actual API response is wrapped by course_id,
        but ResultBuilder expects the already-unwrapped shape.
        """
        return copy.deepcopy(self.fetched_mods)

    def _get_section(self, section_id):
        if section_id not in self.sections_data:
            raise ValueError(f'Section {section_id} not added. '
                             f'Call add_section() first.')
        return self.sections_data[section_id]


# =========================================================================
# Pipeline helpers
# =========================================================================

def run_pipeline(course_sections, fetched_mods):
    """Run the ResultBuilder pipeline on the dummy course data.

    Returns the list of File objects with:
      - module_id, module_modname, position_in_section (set by pipeline)
      - _module_has_attachments (set by pipeline)
      - _in_module_folder (NOT yet set — would be set by gen_path)
    """
    from moodle_dl.moodle.result_builder import ResultBuilder
    from moodle_dl.types import MoodleURL

    # ResultBuilder.__init__ takes (moodle_url, version, token, mod_plurals)
    moodle_url = MoodleURL(use_http=False, domain='example.com', path='')
    rb = ResultBuilder(
        moodle_url=moodle_url,
        version=2024100712,  # Modern Moodle version
        token='test_token',
        mod_plurals={},  # Default empty
    )
    files = rb.get_files_in_sections(course_sections, fetched_mods)
    return files, rb


def files_to_sorted_layout(files, section_id=None):
    """Convert a list of File objects to a sorted (by position)
    layout description, useful for assertion.

    Returns a list of tuples (position, module_id, modname, name,
    in_module_folder) describing the final section layout.
    """
    items = []
    for f in files:
        if section_id is not None and f.section_id != section_id:
            continue
        items.append((
            f.position_in_section,
            f.module_id,
            f.module_modname,
            f.content_filename,
            getattr(f, '_in_module_folder', None),
        ))
    items.sort(key=lambda x: (x[0], x[1] if x[1] is not None else 0, x[3] or ''))
    return items