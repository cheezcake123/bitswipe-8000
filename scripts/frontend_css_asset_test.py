#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "static" / "index.html"
STYLESHEET_PATH = ROOT / "static" / "assets" / "bitswipe.css"
SERVER_PATH = ROOT / "server.py"
STYLESHEET_LINK = b'<link rel="stylesheet" href="/assets/bitswipe.css">'
ORIGINAL_INDEX_SHA256 = "1996d67e89b5f742c3370200622105b71d29e336af48b036bafe6a702e3aa8e8"
ORIGINAL_STYLESHEET_SHA256 = "fb5b09180562592268c089364e3e687d201e3b89b742ae645cb6e5a764d16cd7"
ORIGINAL_SCRIPT_COUNT = 5
ORIGINAL_SCRIPTS_SHA256 = "f6bf37413a7cd804d8be7f8482943933875fd28a3f0b96ea9776b370d15a3528"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FrontendCssAssetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_bytes = INDEX_PATH.read_bytes()
        cls.index = cls.index_bytes.decode("utf-8")
        cls.stylesheet_bytes = STYLESHEET_PATH.read_bytes()
        cls.stylesheet = cls.stylesheet_bytes.decode("utf-8")
        cls.server = SERVER_PATH.read_text(encoding="utf-8")
        cls.server_tree = ast.parse(cls.server, filename=str(SERVER_PATH))

    def test_stylesheet_is_referenced_once_and_embedded_block_is_gone(self):
        self.assertEqual(self.index_bytes.count(STYLESHEET_LINK), 1)
        self.assertEqual(self.index.count("/assets/bitswipe.css"), 1)
        self.assertIsNone(re.search(r"<style\b", self.index, re.IGNORECASE))
        self.assertNotIn("--bg-canvas", self.index)

    def test_stylesheet_exists_and_preserves_critical_css(self):
        self.assertTrue(STYLESHEET_PATH.is_file())
        self.assertGreater(len(self.stylesheet_bytes), 0)
        self.assertEqual(sha256(self.stylesheet_bytes), ORIGINAL_STYLESHEET_SHA256)
        for content in (
            "--bg-canvas",
            "--accent",
            "@media (max-width: 768px)",
            "@media (prefers-reduced-motion: reduce)",
            "@media print",
            ".btn-analyze",
            "#chart-plot",
        ):
            self.assertIn(content, self.stylesheet)

    def test_html_is_byte_identical_after_reconstructing_original_style_block(self):
        reconstructed = self.index_bytes.replace(
            STYLESHEET_LINK,
            b"<style>" + self.stylesheet_bytes + b"</style>",
        )
        self.assertEqual(sha256(reconstructed), ORIGINAL_INDEX_SHA256)

    def test_application_javascript_was_not_removed_or_moved(self):
        scripts = re.findall(
            br"<script\b.*?</script>",
            self.index_bytes,
            flags=re.IGNORECASE | re.DOTALL,
        )
        self.assertEqual(len(scripts), ORIGINAL_SCRIPT_COUNT)
        self.assertEqual(sha256(b"\0".join(scripts)), ORIGINAL_SCRIPTS_SHA256)

    def test_important_html_identifiers_and_external_references_remain(self):
        for identifier in (
            "setup-overlay",
            "symbol-select",
            "btn-analyze",
            "chart-plot",
            "report-box",
            "gpt-question",
            "agents-card",
            "account-panel-body",
            "bottom-tab-history",
            "perf-equity-chart",
        ):
            self.assertIn(f'id="{identifier}"', self.index)

        for reference in (
            "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js",
            "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
            "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap",
            '<script type="application/ld+json">',
            '<link rel="canonical" href="https://bitswipe.xyz/">',
            '<meta property="og:image" content="https://bitswipe.xyz/og-image.png">',
        ):
            self.assertIn(reference, self.index)

    def test_server_mounts_only_the_asset_directory(self):
        staticfiles_imports = [
            node
            for node in ast.walk(self.server_tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "fastapi.staticfiles"
            and any(alias.name == "StaticFiles" for alias in node.names)
        ]
        self.assertEqual(len(staticfiles_imports), 1)

        mounts = [
            node
            for node in ast.walk(self.server_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "mount"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "app"
        ]
        self.assertEqual(len(mounts), 1)
        mount = mounts[0]
        self.assertGreaterEqual(len(mount.args), 2)
        self.assertIsInstance(mount.args[0], ast.Constant)
        self.assertEqual(mount.args[0].value, "/assets")

        staticfiles = mount.args[1]
        self.assertIsInstance(staticfiles, ast.Call)
        self.assertIsInstance(staticfiles.func, ast.Name)
        self.assertEqual(staticfiles.func.id, "StaticFiles")
        directory = next(
            keyword.value for keyword in staticfiles.keywords if keyword.arg == "directory"
        )
        self.assertIsInstance(directory, ast.Call)
        self.assertIsInstance(directory.func, ast.Attribute)
        self.assertEqual(directory.func.attr, "join")
        self.assertEqual(len(directory.args), 3)
        self.assertIsInstance(directory.args[0], ast.Name)
        self.assertEqual(directory.args[0].id, "BASE_DIR")
        self.assertEqual([arg.value for arg in directory.args[1:]], ["static", "assets"])

        mount_name = next(keyword.value for keyword in mount.keywords if keyword.arg == "name")
        self.assertIsInstance(mount_name, ast.Constant)
        self.assertEqual(mount_name.value, "assets")

        base_assignment = next(
            node
            for node in self.server_tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "BASE_DIR" for target in node.targets)
        )
        self.assertLess(base_assignment.lineno, mount.lineno)

    def test_no_sensitive_path_is_exposed_by_a_static_mount_or_route(self):
        route_paths = []
        for node in ast.walk(self.server_tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "app"
                    and decorator.func.attr in {"get", "post", "put", "patch", "delete", "mount"}
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    route_paths.append(decorator.args[0].value)

        for sensitive_route in ("/data", "/.env", "/repository", "/scripts"):
            self.assertNotIn(sensitive_route, route_paths)
        self.assertNotIn('StaticFiles(directory=BASE_DIR', self.server)
        self.assertNotIn('StaticFiles(directory=os.path.join(BASE_DIR, "data")', self.server)


if __name__ == "__main__":
    unittest.main()
