from moodle_dl.utils import determine_ext


def test_determine_ext_prefers_query_filename_extension():
    url = 'https://example.com/pluginfile.php?filename=lecture_video.mp4&token=abc123'

    assert determine_ext(url, default_ext='bin') == 'mp4'
