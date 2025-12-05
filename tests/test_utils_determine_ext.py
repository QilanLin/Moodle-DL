from moodle_dl.utils import KNOWN_EXTENSIONS, determine_ext


def test_determine_ext_prefers_query_filename_extension():
    url = 'https://example.com/pluginfile.php?filename=lecture_video.mp4&token=abc123'

    assert determine_ext(url, default_ext='bin') == 'mp4'


def test_determine_ext_query_only_extension():
    url = 'https://example.com/?file_name=slides.pptx'

    assert determine_ext(url, default_ext='bin') == 'pptx'


def test_determine_ext_with_trailing_slash_after_extension():
    url = 'https://example.com/content/video.mp4/'

    assert determine_ext(url, default_ext='bin') == 'mp4'


def test_determine_ext_preserves_mixed_case_extension():
    url = 'https://example.com/files/HANDOUT.PdF'

    assert determine_ext(url, default_ext='bin') == 'PdF'


def test_determine_ext_invalid_extension_falls_back_to_default():
    url = 'https://example.com/?filename=lecture_video.mp4!'

    assert determine_ext(url, default_ext='bin') == 'bin'


def test_determine_ext_handles_excel_extension():
    url = 'https://example.com/files/report.xlsx'

    assert determine_ext(url, default_ext='bin') == 'xlsx'


def test_known_extensions_are_sorted_and_deduplicated():
    assert KNOWN_EXTENSIONS == tuple(sorted(set(KNOWN_EXTENSIONS)))
    assert 'xlsx' in KNOWN_EXTENSIONS
    assert 'xls' in KNOWN_EXTENSIONS
    assert 'excl' not in KNOWN_EXTENSIONS
