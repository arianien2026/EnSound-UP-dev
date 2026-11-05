"""Development handoff URL stays pinned to the expected frontend."""

import os
import unittest
from unittest.mock import patch

from app.dev_frontend import dev_frontend_url


class DevelopmentFrontendUrlTests(unittest.TestCase):
    def test_missing_configuration_disables_handoff(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(dev_frontend_url())

    def test_exact_development_destinations(self):
        for url in ("https://arianien2026.github.io/EnSound-UP-dev/",
                    "http://localhost:5173/EnSound-UP-dev/"):
            with self.subTest(url=url), patch.dict(os.environ, {"ENSOUND_DEV_FRONTEND_URL": url}):
                self.assertEqual(dev_frontend_url(), url)

    def test_unexpected_origin_path_or_query_fails_closed(self):
        for url in ("https://arianien2026.github.io/-EnSound-UP/",
                    "https://attacker.example/EnSound-UP-dev/",
                    "http://localhost:5173/other/", "http://localhost:5173/EnSound-UP-dev/?to=bad",
                    "https://arianien2026.github.io/EnSound-UP-dev/#token"):
            with self.subTest(url=url), patch.dict(os.environ, {"ENSOUND_DEV_FRONTEND_URL": url}):
                with self.assertRaises(ValueError):
                    dev_frontend_url()


if __name__ == "__main__":
    unittest.main()
