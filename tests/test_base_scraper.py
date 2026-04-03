import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

if "tqdm" not in sys.modules:
    tqdm_stub = types.ModuleType("tqdm")
    tqdm_stub.tqdm = lambda iterable, *args, **kwargs: iterable
    sys.modules["tqdm"] = tqdm_stub

from base_scraper import robust_get, safe_output_path, sanitize_filename, validate_url


class TestBaseScraperHelpers(unittest.TestCase):
    def test_sanitize_filename_blocks_traversal_patterns(self):
        self.assertEqual(sanitize_filename("../../../etc/passwd"), "etc_passwd")
        self.assertEqual(sanitize_filename("..\\..\\secret.txt"), "secret.txt")

    def test_safe_output_path_stays_inside_base_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            out = safe_output_path(base, "../unsafe", filename="../../doc.pdf")
            resolved = out.resolve()
            self.assertTrue(str(resolved).startswith(str(base.resolve())))
            self.assertEqual(out.name, "doc.pdf")

    def test_validate_url(self):
        self.assertTrue(validate_url("https://example.com/doc.pdf"))
        self.assertTrue(validate_url("http://example.com"))
        self.assertFalse(validate_url("ftp://example.com/file"))
        self.assertFalse(validate_url("/relative/path"))
        self.assertFalse(validate_url(""))

    def test_robust_get_retries_and_returns_response(self):
        session = MagicMock()
        ok_response = MagicMock()
        ok_response.raise_for_status.return_value = None

        session.get.side_effect = [
            requests.exceptions.Timeout("timeout"),
            ok_response,
        ]

        with patch("base_scraper.time.sleep") as sleep_mock:
            resp = robust_get(session, "https://example.com", retries=3, timeout=5, delay_factor=1.0)

        self.assertIs(resp, ok_response)
        self.assertEqual(session.get.call_count, 2)
        sleep_mock.assert_called_once_with(1.0)

    def test_robust_get_returns_none_after_last_failure(self):
        session = MagicMock()
        session.get.side_effect = requests.exceptions.ConnectionError("boom")

        with patch("base_scraper.time.sleep") as sleep_mock:
            resp = robust_get(session, "https://example.com", retries=3, timeout=5, delay_factor=1.0)

        self.assertIsNone(resp)
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
