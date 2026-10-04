"""Tests for configuration and environment loading."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from squadron.config import ConfigError, detect_provider, load_env_file


class EnvLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = dict(os.environ)
        self._tempdir = Path(tempfile.mkdtemp(prefix="sqn-env-"))

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def test_project_env_overrides_inherited_value(self) -> None:
        os.environ["SQUADRON_TEST_KEY"] = "inherited"
        path = self._tempdir / ".env"
        path.write_text("SQUADRON_TEST_KEY=project-file\n", encoding="utf-8")
        load_env_file(path, override=True)
        self.assertEqual(os.environ["SQUADRON_TEST_KEY"], "project-file")

    def test_extra_file_does_not_override_existing_value(self) -> None:
        os.environ["SQUADRON_TEST_KEY"] = "inherited"
        path = self._tempdir / "extra.env"
        path.write_text("SQUADRON_TEST_KEY=extra-file\n", encoding="utf-8")
        load_env_file(path, override=False)
        self.assertEqual(os.environ["SQUADRON_TEST_KEY"], "inherited")

    def test_comments_and_quotes_are_handled(self) -> None:
        path = self._tempdir / "quoted.env"
        path.write_text(
            '# comment\nSQUADRON_QUOTED="has spaces"\n\nEMPTY_VAR=\n',
            encoding="utf-8",
        )
        load_env_file(path, override=True)
        self.assertEqual(os.environ["SQUADRON_QUOTED"], "has spaces")
        self.assertNotIn("EMPTY_VAR", os.environ)

    def test_missing_file_is_ignored(self) -> None:
        load_env_file(self._tempdir / "does-not-exist.env")
        load_env_file(self._tempdir / "does-not-exist.env", override=True)

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            detect_provider("nonexistent")

    def test_blank_provider_falls_back_to_detection(self) -> None:
        os.environ["OPENAI_API_KEY"] = "test-key"
        self.assertEqual(detect_provider("  "), "openai")


if __name__ == "__main__":
    unittest.main()
