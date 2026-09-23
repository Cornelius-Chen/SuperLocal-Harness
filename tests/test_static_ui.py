import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticUITests(unittest.TestCase):
    def test_javascript_dom_ids_exist(self):
        html = (ROOT / "superlocal_harness" / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "superlocal_harness" / "static" / "app.js").read_text(encoding="utf-8")
        html_ids = set(re.findall(r'\bid="([^"]+)"', html))
        referenced = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', javascript))
        self.assertFalse(referenced - html_ids, f"Missing DOM ids: {sorted(referenced - html_ids)}")

    def test_static_assets_exist(self):
        static = ROOT / "superlocal_harness" / "static"
        self.assertTrue((static / "index.html").is_file())
        self.assertTrue((static / "styles.css").is_file())
        self.assertTrue((static / "app.js").is_file())


if __name__ == "__main__":
    unittest.main()

