# -*- coding: utf-8 -*-
import unittest

from moodle_dl.moodle.mods.book import BookMod


class TestBookAuthDetection(unittest.TestCase):
    def test_book_title_with_microsoft_is_not_false_positive(self):
        html = """
        <!DOCTYPE html>
        <html>
            <head><title>Introduction to Microsoft Teams Meetings</title></head>
            <body>
                <main>
                    <h1>Introduction to Microsoft Teams Meetings</h1>
                    <article>Actual book content.</article>
                </main>
            </body>
        </html>
        """

        self.assertEqual(BookMod._detect_interactive_auth_page(html), '')
        self.assertEqual(
            BookMod._detect_interactive_auth_requirement(
                'https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=7564464',
                html,
            ),
            '',
        )

    def test_microsoft_sign_in_page_is_detected(self):
        html = """
        <!DOCTYPE html>
        <html>
            <head><title>Sign in to your account</title></head>
            <body>
                <form action="https://login.microsoftonline.com/common/login">
                    <input id="i0116" name="loginfmt" />
                </form>
            </body>
        </html>
        """

        self.assertEqual(BookMod._detect_interactive_auth_page(html), 'microsoft_sign_in')

    def test_microsoft_account_picker_is_detected(self):
        html = """
        <!DOCTYPE html>
        <html>
            <head><title>Pick an account</title></head>
            <body>
                <div>Microsoft</div>
                <h1>Pick an account</h1>
                <div>Qilan Lin</div>
                <div>Use another account</div>
            </body>
        </html>
        """

        self.assertEqual(BookMod._detect_interactive_auth_page(html), 'microsoft_account_picker')

    def test_auth_redirect_urls_are_detected(self):
        self.assertEqual(
            BookMod._detect_interactive_auth_requirement(
                'https://login.microsoftonline.com/emckclac.onmicrosoft.com/oauth2/authorize',
            ),
            'redirected_to_auth_url',
        )
        self.assertEqual(
            BookMod._detect_interactive_auth_requirement(
                'https://keats.kcl.ac.uk/auth/oidc/',
            ),
            'redirected_to_auth_url',
        )
        self.assertEqual(
            BookMod._detect_interactive_auth_requirement(
                'https://keats.kcl.ac.uk/mod/book/tool/print/index.php?id=7564464',
            ),
            '',
        )


if __name__ == '__main__':
    unittest.main()
