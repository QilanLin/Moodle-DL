# -*- coding: utf-8 -*-
from pathlib import Path

from moodle_dl.downloader.html_localizer import (
    build_local_resource_map,
    canonical_resource_url,
    rewrite_html_links_to_local_paths,
)
from moodle_dl.types import File


def make_file(url: str, saved_to: str) -> File:
    return File(
        module_id=1,
        section_name='Section',
        section_id=1,
        module_name='Book',
        content_filepath='/',
        content_filename=Path(saved_to).name,
        content_fileurl=url,
        content_filesize=1,
        content_timemodified=1,
        module_modname='book',
        content_type='file',
        content_isexternalfile=False,
        saved_to=saved_to,
    )


def test_canonical_resource_url_matches_moodle_pluginfile_variants():
    standard = 'https://keats.kcl.ac.uk/pluginfile.php/11275714/mod_book/chapter/785143/student-vms-01.png'
    webservice = (
        'https://keats.kcl.ac.uk/webservice/pluginfile.php/11275714/mod_book/chapter/785143/'
        'student-vms-01.png?token=secret&offline=1'
    )
    token_plugin = (
        'https://keats.kcl.ac.uk/tokenpluginfile.php/secret/11275714/mod_book/chapter/785143/'
        'student-vms-01.png?offline=1#image'
    )

    assert canonical_resource_url(standard) == canonical_resource_url(webservice)
    assert canonical_resource_url(standard) == canonical_resource_url(token_plugin)
    assert canonical_resource_url('#local-anchor') == ''
    assert canonical_resource_url('student-vms-01.png') == ''


def test_build_local_resource_map_uses_downloaded_files_not_shortcuts(tmp_path):
    image_path = tmp_path / 'chapter' / 'student-vms-01.png'
    image_path.parent.mkdir()
    image_path.write_bytes(b'image')
    shortcut_path = tmp_path / 'chapter' / 'student-vms-01.png.webloc'
    shortcut_path.write_text('shortcut', encoding='utf-8')

    real_url = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/1/mod_book/chapter/2/student-vms-01.png?token=x'
    shortcut_url = 'https://keats.kcl.ac.uk/pluginfile.php/1/mod_book/chapter/2/student-vms-01.png'

    local_map = build_local_resource_map([
        make_file(shortcut_url, str(shortcut_path)),
        make_file(real_url, str(image_path)),
    ])

    assert local_map == {
        canonical_resource_url(shortcut_url): str(image_path),
    }


def test_rewrite_html_links_to_local_paths_rewrites_only_downloaded_resources(tmp_path):
    html_path = tmp_path / 'book' / 'Print Book.html'
    image_path = tmp_path / 'book' / '02 - Faculty student VM' / '*39* student-vms-01.png'
    pdf_path = tmp_path / 'book' / '03 - Linux' / 'guide.pdf'
    image_path.parent.mkdir(parents=True)
    pdf_path.parent.mkdir(parents=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b'image')
    pdf_path.write_bytes(b'pdf')

    image_url = 'https://keats.kcl.ac.uk/pluginfile.php/112/mod_book/chapter/785/student-vms-01.png'
    pdf_url = 'https://keats.kcl.ac.uk/pluginfile.php/112/mod_book/chapter/786/guide.pdf'
    html_content = (
        f'<img src="{image_url}" alt="VM Control panel">'
        f'<a href="{pdf_url}?forcedownload=1">Guide</a>'
        '<img src="https://external.example/missing.png">'
        '<a href="#toc">TOC</a>'
    )

    rewritten, count = rewrite_html_links_to_local_paths(
        html_content,
        str(html_path),
        {
            canonical_resource_url(image_url): str(image_path),
            canonical_resource_url(pdf_url): str(pdf_path),
        },
    )

    assert count == 2
    assert 'src="02 - Faculty student VM/*39* student-vms-01.png"' in rewritten
    assert 'href="03 - Linux/guide.pdf"' in rewritten
    assert 'https://external.example/missing.png' in rewritten
    assert 'href="#toc"' in rewritten
