from moodle_dl.downloader.leganto_print import (
    build_lti_launch_form,
    cookies_text_to_playwright,
    is_leganto_lti_launch_url,
    is_leganto_reading_list_url,
)


def test_leganto_url_detection_is_specific_to_kcl_reading_lists():
    assert is_leganto_reading_list_url('https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881?auth=SAML')
    assert is_leganto_lti_launch_url('https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1')

    assert not is_leganto_reading_list_url('https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1')
    assert not is_leganto_lti_launch_url('https://rl.kcl.ac.uk/leganto/nui/lists/15085102330006881')
    assert not is_leganto_reading_list_url('https://example.com/leganto/nui/lists/15085102330006881')


def test_lti_launch_form_escapes_endpoint_and_parameters():
    form = build_lti_launch_form(
        'https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1?x=<bad>',
        [
            {'name': 'id_token', 'value': 'abc&def'},
            {'name': '<ignored>', 'value': '"quoted"'},
        ],
    )

    assert 'action="https://rl.kcl.ac.uk/lti/v3/launch/44KCL_INST/LMS_MOODLE_1?x=&lt;bad&gt;"' in form
    assert 'name="id_token" value="abc&amp;def"' in form
    assert 'name="&lt;ignored&gt;" value="&quot;quoted&quot;"' in form
    assert 'document.getElementById("launchForm").submit()' in form


def test_cookies_text_to_playwright_converts_netscape_cookies():
    cookies_text = '\n'.join(
        [
            '# Netscape HTTP Cookie File',
            '.rl.kcl.ac.uk\tTRUE\t/\tTRUE\t1893456000\tSESSION\tabc123',
            'rl.kcl.ac.uk\tFALSE\t/leganto\tFALSE\t0\tPREF\txyz',
        ]
    )

    cookies = cookies_text_to_playwright(cookies_text)

    assert cookies[0] == {
        'name': 'SESSION',
        'value': 'abc123',
        'domain': '.rl.kcl.ac.uk',
        'path': '/',
        'secure': True,
        'httpOnly': False,
        'expires': 1893456000,
    }
    assert cookies[1] == {
        'name': 'PREF',
        'value': 'xyz',
        'domain': 'rl.kcl.ac.uk',
        'path': '/leganto',
        'secure': False,
        'httpOnly': False,
    }


def test_cookies_text_to_playwright_ignores_empty_input():
    assert cookies_text_to_playwright(None) == []
    assert cookies_text_to_playwright('') == []
