# -*- coding: utf-8 -*-
"""
Unit tests for HTML cleaning functions in task.py

Testing the atomized HTML cleaning functions to ensure each one
does exactly one thing and does it well.
"""

import unittest
from unittest.mock import MagicMock

from moodle_dl.downloader.task import Task


class TestHTMLCleaningFunctions(unittest.TestCase):
    """Test each HTML cleaning function independently"""

    def test_convert_line_breaks(self):
        """Test <br> tag conversion"""
        html = "Line 1<br>Line 2<br/>Line 3"
        result = Task._convert_line_breaks(html)
        self.assertEqual(result, "Line 1\nLine 2\nLine 3")

    def test_convert_line_breaks_self_closing(self):
        """Test both <br> and <br/> formats"""
        html = "Start<br>Middle<br/>End"
        result = Task._convert_line_breaks(html)
        self.assertEqual(result, "Start\nMiddle\nEnd")

    def test_convert_paragraphs(self):
        """Test <p> tag conversion"""
        html = "<p>Para 1</p><p>Para 2</p>"
        result = Task._convert_paragraphs(html)
        self.assertEqual(result, "\nPara 1\n\nPara 2\n")

    def test_convert_paragraphs_with_attributes(self):
        """Test <p> conversion with class/style attributes"""
        html = '<p class="intro">Start</p><p style="color: red">Text</p>'
        result = Task._convert_paragraphs(html)
        self.assertEqual(result, "\nStart\n\nText\n")

    def test_convert_lists_unordered(self):
        """Test <ul> and <li> conversion"""
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        result = Task._convert_lists(html)
        self.assertIn("• Item 1", result)
        self.assertIn("• Item 2", result)

    def test_convert_lists_ordered(self):
        """Test <ol> conversion"""
        html = "<ol><li>First</li><li>Second</li></ol>"
        result = Task._convert_lists(html)
        self.assertIn("• First", result)
        self.assertIn("• Second", result)

    def test_convert_formatting_bold(self):
        """Test bold tag conversion"""
        html = "This is <b>bold</b> and <strong>strong</strong>"
        result = Task._convert_formatting(html)
        self.assertEqual(result, "This is **bold** and **strong**")

    def test_convert_formatting_italic(self):
        """Test italic tag conversion"""
        html = "This is <i>italic</i> and <em>emphasized</em>"
        result = Task._convert_formatting(html)
        self.assertEqual(result, "This is *italic* and *emphasized*")

    def test_convert_formatting_mixed(self):
        """Test mixed bold and italic"""
        html = "<b>Bold</b> and <i>italic</i> and <strong>strong</strong> and <em>em</em>"
        result = Task._convert_formatting(html)
        self.assertEqual(result, "**Bold** and *italic* and **strong** and *em*")

    def test_convert_formatting_nested(self):
        """Test nested formatting (via DOTALL flag)"""
        html = "<b>Bold with <i>italic inside</i></b>"
        result = Task._convert_formatting(html)
        self.assertIn("**", result)
        self.assertIn("*", result)

    def test_convert_links(self):
        """Test link conversion"""
        html = 'Click <a href="https://example.com">here</a>'
        result = Task._convert_links(html)
        self.assertEqual(result, "Click [here](https://example.com)")

    def test_convert_links_multiple(self):
        """Test multiple links"""
        html = '<a href="url1">Link 1</a> and <a href="url2">Link 2</a>'
        result = Task._convert_links(html)
        self.assertIn("[Link 1](url1)", result)
        self.assertIn("[Link 2](url2)", result)

    def test_convert_links_with_attributes(self):
        """Test links with extra attributes"""
        html = '<a href="url" title="Title" class="link">Text</a>'
        result = Task._convert_links(html)
        self.assertEqual(result, "[Text](url)")

    def test_remove_html_tags(self):
        """Test removal of remaining HTML tags"""
        html = "<div><span>Text</span></div>"
        result = Task._remove_html_tags(html)
        self.assertEqual(result, "Text")

    def test_remove_html_tags_with_attributes(self):
        """Test tag removal with attributes"""
        html = '<div class="container"><span id="text">Content</span></div>'
        result = Task._remove_html_tags(html)
        self.assertEqual(result, "Content")

    def test_decode_html_entities(self):
        """Test HTML entity decoding"""
        html = "Hello &amp; goodbye &lt;world&gt;"
        result = Task._decode_html_entities(html)
        self.assertEqual(result, "Hello & goodbye <world>")

    def test_decode_html_entities_common(self):
        """Test common HTML entities"""
        html = "&nbsp;&quot;&copy;&reg;&trade;"
        result = Task._decode_html_entities(html)
        self.assertIn("©", result)
        self.assertIn("®", result)
        self.assertIn("™", result)

    def test_clean_whitespace_newlines(self):
        """Test excessive newline cleanup"""
        html = "Line 1\n\n\n\nLine 2"
        result = Task._clean_whitespace(html)
        self.assertEqual(result, "Line 1\n\nLine 2")

    def test_clean_whitespace_spaces(self):
        """Test multiple space cleanup"""
        html = "Word1    Word2     Word3"
        result = Task._clean_whitespace(html)
        self.assertEqual(result, "Word1 Word2 Word3")

    def test_clean_whitespace_trim(self):
        """Test trimming of leading/trailing whitespace"""
        html = "  \n  Text  \n  "
        result = Task._clean_whitespace(html)
        self.assertEqual(result, "Text")

    def test_full_pipeline_simple(self):
        """Test complete pipeline with simple HTML"""
        html = """
        <p>Hello <b>World</b></p>
        <p>Check out <a href="https://example.com">this link</a></p>
        <ul>
            <li>Item 1</li>
            <li>Item 2</li>
        </ul>
        """
        # Use the TaskFileOps helper directly.
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        ops = TaskFileOps(MagicMock())
        # 'structured' mode preserves formatting as markdown.
        result = ops.clean_html(html, mode='structured')

        # Verify key conversions
        self.assertIn("**World**", result)  # Bold preserved
        self.assertIn("[this link](https://example.com)", result)  # Link preserved
        self.assertIn("• Item 1", result)  # List item preserved
        self.assertIn("• Item 2", result)
        self.assertNotIn("<p>", result)  # Tags removed
        self.assertNotIn("<b>", result)
        self.assertNotIn("<a ", result)

    def test_full_pipeline_complex(self):
        """Test complete pipeline with complex HTML"""
        html = """
        <div class="content">
            <p>Welcome to <strong>Python</strong> tutorial!</p>
            <p>Follow these steps:</p>
            <ol>
                <li>Install Python</li>
                <li>Learn <em>basics</em></li>
                <li>Check <a href="https://docs.python.org">official docs</a></li>
            </ol>
            <p>Good luck &amp; have fun!</p>
        </div>
        """
        # Use TaskFileOps directly
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        ops = TaskFileOps(MagicMock())
        result = ops.clean_html(html, mode='structured')

        # Verify structure is preserved
        self.assertIn("**Python**", result)
        self.assertIn("*basics*", result)
        self.assertIn("• Install Python", result)
        self.assertIn("• Learn", result)
        self.assertIn("[official docs](https://docs.python.org)", result)
        self.assertIn("& have fun", result)  # Entity decoded

        # Verify no HTML tags remain
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)

    def test_empty_input(self):
        """Test handling of empty input"""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        ops = TaskFileOps(MagicMock())
        result = ops.clean_html_preserve_structure('')
        self.assertEqual(result, '')

    def test_none_input(self):
        """Test handling of None input"""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        ops = TaskFileOps(MagicMock())
        # None is falsy → empty string
        result = ops.clean_html_preserve_structure(None)  # type: ignore[arg-type]
        self.assertEqual(result, '')


class TestHTMLCleaningOrder(unittest.TestCase):
    """Test that the order of transformations matters"""

    def test_order_matters_entities_before_cleanup(self):
        """Entities should be decoded before final cleanup"""
        # If cleanup happened first, extra spaces might be removed
        html = "&nbsp;&nbsp;&nbsp;Text"
        result = Task._clean_whitespace(Task._decode_html_entities(html))
        # Non-breaking spaces converted to regular spaces, then cleaned
        self.assertNotIn("\xa0", result)

    def test_order_matters_tags_before_entities(self):
        """Tags should be removed before entity decoding"""
        html = "<b>&lt;b&gt;</b>"  # <b>&lt;b&gt;</b>
        
        # Correct order: convert formatting first (converts <b> to **), 
        # then remove tags, then decode entities
        result = Task._remove_html_tags(Task._convert_formatting(html))
        result = Task._decode_html_entities(result)
        
        # After conversion: <b>&lt;b&gt;</b> becomes **&lt;b&gt;**
        # After removing tags: **&lt;b&gt;** stays the same (no tags to remove)
        # After decoding: **<b>**
        self.assertEqual(result.strip(), "**<b>**")


if __name__ == '__main__':
    unittest.main()

