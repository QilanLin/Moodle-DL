import unittest

from moodle_dl.utils import is_base_64


class TestIsBase64(unittest.TestCase):
    def test_valid_base64_strings(self):
        assert is_base_64("dGVzdA==")  # "test" with padding
        assert is_base_64("YWJj")  # "abc" without padding

    def test_invalid_base64_strings(self):
        assert not is_base_64("not_base64!")
        assert not is_base_64(b"dGVzdA=")  # incorrect padding


if __name__ == "__main__":
    unittest.main()
