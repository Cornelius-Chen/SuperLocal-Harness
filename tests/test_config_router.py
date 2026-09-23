import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ironman_harness.config import Settings, expand_env
from ironman_harness.router import StaticRouter


ROOT = Path(__file__).resolve().parents[1]


class ConfigRouterTests(unittest.TestCase):
    def test_environment_expansion(self):
        with patch.dict(os.environ, {"SAMPLE_VALUE": "chosen"}, clear=False):
            self.assertEqual(expand_env("${SAMPLE_VALUE:-fallback}"), "chosen")
            self.assertEqual(expand_env("${MISSING_SAMPLE:-fallback}"), "fallback")

    def test_non_loopback_requires_token(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "HARNESS_BIND": "0.0.0.0",
                "HARNESS_ACCESS_TOKEN": "",
                "HARNESS_DATA_DIR": str(Path(tmp) / "data"),
                "HARNESS_PROJECT_ROOTS": tmp,
            },
            clear=False,
        ):
            with self.assertRaises(ValueError):
                Settings(ROOT)

    def test_local_only_route_removes_cloud(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "HARNESS_BIND": "127.0.0.1",
                "HARNESS_DATA_DIR": str(Path(tmp) / "data"),
                "HARNESS_PROJECT_ROOTS": tmp,
                "DEEPSEEK_API_KEY": "test-only",
            },
            clear=False,
        ):
            settings = Settings(ROOT)
            decision = StaticRouter(settings).decide(
                "auto",
                profile_id="coding",
                role="executor",
                prompt="edit this repository",
                local_only=True,
            )
            self.assertTrue(decision.candidates)
            self.assertTrue(all(settings.get_model(item).locality == "local" for item in decision.candidates))
            with self.assertRaises(ValueError):
                StaticRouter(settings).decide(
                    "deepseek-flash",
                    profile_id="coding",
                    role="executor",
                    prompt="edit",
                    local_only=True,
                )


if __name__ == "__main__":
    unittest.main()

