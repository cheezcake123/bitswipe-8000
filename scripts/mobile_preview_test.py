#!/usr/bin/env python3
"""Offline safety and isolation checks for the BitSwipe mobile preview."""

from __future__ import annotations

import ast
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server.py"
HTML_PATH = ROOT / "static" / "mobile-preview.html"
CSS_PATH = ROOT / "static" / "assets" / "mobile-preview.css"
JS_PATH = ROOT / "static" / "assets" / "mobile-preview.js"
BASE_COMMIT = "0dc773f3369ec6f325dc26f89fd6d08cd27936a8"


class PreviewHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text.append(data.strip())

    def attrs_for(self, tag: str) -> list[dict[str, str]]:
        return [attrs for found_tag, attrs in self.tags if found_tag == tag]


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        prefix = call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def decorator_path(node: ast.AsyncFunctionDef, method: str) -> str | None:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or call_name(decorator.func) != f"app.{method}":
            continue
        if decorator.args and isinstance(decorator.args[0], ast.Constant):
            return decorator.args[0].value
    return None


def git_output(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


class MobilePreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server_text = SERVER_PATH.read_text(encoding="utf-8")
        cls.html_text = HTML_PATH.read_text(encoding="utf-8")
        cls.css_text = CSS_PATH.read_text(encoding="utf-8")
        cls.js_text = JS_PATH.read_text(encoding="utf-8")
        cls.server_tree = ast.parse(cls.server_text)
        cls.html = PreviewHTMLParser()
        cls.html.feed(cls.html_text)

    def test_preview_files_exist_and_are_non_empty(self) -> None:
        for path in (HTML_PATH, CSS_PATH, JS_PATH):
            self.assertTrue(path.is_file(), f"missing preview file: {path}")
            self.assertGreater(path.stat().st_size, 100, f"preview file is unexpectedly empty: {path}")

    def test_server_exposes_isolated_preview_and_keeps_root(self) -> None:
        async_functions = [node for node in self.server_tree.body if isinstance(node, ast.AsyncFunctionDef)]
        get_routes = {decorator_path(node, "get"): node for node in async_functions if decorator_path(node, "get")}
        self.assertIn("/", get_routes)
        self.assertIn("/mobile-preview", get_routes)

        preview_node = get_routes["/mobile-preview"]
        source = ast.get_source_segment(self.server_text, preview_node) or ""
        self.assertIn("HTMLResponse", source)
        self.assertIn("_read_static_text", source)
        self.assertRegex(source, r'["\']static["\']\s*,\s*["\']mobile-preview\.html["\']')

        preview_decorator = next(
            decorator
            for decorator in preview_node.decorator_list
            if isinstance(decorator, ast.Call) and call_name(decorator.func) == "app.get"
        )
        keywords = {keyword.arg: keyword.value for keyword in preview_decorator.keywords}
        self.assertIn("include_in_schema", keywords)
        self.assertIsInstance(keywords["include_in_schema"], ast.Constant)
        self.assertFalse(keywords["include_in_schema"].value)

    def test_assets_mount_remains_single_and_unchanged(self) -> None:
        mounts: list[ast.Call] = []
        for node in ast.walk(self.server_tree):
            if isinstance(node, ast.Call) and call_name(node.func) == "app.mount":
                mounts.append(node)
        self.assertEqual(len(mounts), 1, "server.py must retain exactly one static mount")
        self.assertIsInstance(mounts[0].args[0], ast.Constant)
        self.assertEqual(mounts[0].args[0].value, "/assets")
        self.assertIn("StaticFiles", ast.get_source_segment(self.server_text, mounts[0]) or "")

    def test_html_references_assets_and_is_not_indexed(self) -> None:
        links = self.html.attrs_for("link")
        scripts = self.html.attrs_for("script")
        metas = self.html.attrs_for("meta")
        self.assertTrue(any(item.get("href") == "/assets/mobile-preview.css" for item in links))
        self.assertTrue(any(item.get("src") == "/assets/mobile-preview.js" for item in scripts))
        robots = [item.get("content", "").replace(" ", "").lower() for item in metas if item.get("name") == "robots"]
        self.assertIn("noindex,nofollow", robots)

    def test_html_has_no_password_input_and_has_accessibility_markers(self) -> None:
        inputs = self.html.attrs_for("input")
        self.assertFalse(any(item.get("type", "text").lower() == "password" for item in inputs))
        self.assertGreaterEqual(sum(item.get("type") == "checkbox" for item in inputs), 5)
        self.assertTrue(any(item.get("lang") == "ko" for item in self.html.attrs_for("html")))
        self.assertTrue(self.html.attrs_for("main"))
        self.assertTrue(any(item.get("aria-label") for item in self.html.attrs_for("nav")))
        self.assertGreaterEqual(sum(bool(item.get("aria-live")) for _, item in self.html.tags), 3)
        icon_buttons = [
            item for item in self.html.attrs_for("button")
            if "icon-button" in item.get("class", "").split()
        ]
        self.assertTrue(icon_buttons)
        self.assertTrue(all(item.get("aria-label") for item in icon_buttons))
        page_text = " ".join(self.html.text)
        self.assertIn("아직 저장된 분석", self.js_text)
        self.assertIn("교육용 읽기 전용", page_text)

    def test_all_browser_requests_are_same_origin_gets(self) -> None:
        fetch_calls = re.findall(r"\bfetch\s*\(", self.js_text)
        self.assertEqual(len(fetch_calls), 1, "all network reads must pass through one guarded adapter")
        self.assertIn('url.origin !== window.location.origin', self.js_text)
        self.assertRegex(self.js_text, r'method:\s*["\']GET["\']')
        self.assertNotRegex(self.js_text, r'https?://')

        requested_paths = re.findall(r'requestJson\(\s*["\']([^"\']+)["\']', self.js_text)
        self.assertEqual(
            set(requested_paths),
            {"/api/analyze?include_latest=true", "/api/analysis-history?limit=20"},
        )
        self.assertTrue(all(path.startswith("/api/") and not path.startswith("//") for path in requested_paths))

    def test_preview_javascript_has_no_mutating_or_external_actions(self) -> None:
        forbidden_methods = re.compile(r'method\s*:\s*["\'](?:POST|PUT|PATCH|DELETE)["\']', re.IGNORECASE)
        self.assertIsNone(forbidden_methods.search(self.js_text))
        forbidden_calls = (
            "sendTelegram",
            "send_telegram",
            "placeOrder",
            "place_order",
            "activateFeature",
            "activate_feature",
        )
        for name in forbidden_calls:
            self.assertNotRegex(self.js_text, rf"\b{re.escape(name)}\s*\(")
        self.assertNotIn("WebSocket(", self.js_text)
        self.assertNotIn("EventSource(", self.js_text)

    def test_demo_mode_is_opt_in_and_visibly_labeled(self) -> None:
        self.assertRegex(
            self.js_text,
            r'URLSearchParams\(window\.location\.search\)\.get\(["\']demo["\']\)\s*===\s*["\']1["\']',
        )
        demo_banners = [item for item in self.html.attrs_for("div") if item.get("id") == "demo-banner"]
        self.assertEqual(len(demo_banners), 1)
        self.assertIn("hidden", demo_banners[0])
        self.assertIn("샘플 데이터", " ".join(self.html.text))
        self.assertIn("실시간 시장 정보가 아닙니다", " ".join(self.html.text))

    def test_local_storage_keys_are_preview_specific(self) -> None:
        match = re.search(r'const STORAGE_PREFIX\s*=\s*["\']([^"\']+)["\']', self.js_text)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertTrue(match.group(1).startswith("bitswipe_mobile_preview_"))
        storage_calls = re.findall(r'localStorage\.(?:getItem|setItem)\(([^)]+)\)', self.js_text)
        self.assertTrue(storage_calls)
        self.assertTrue(all("key" in expression for expression in storage_calls))

    def test_mobile_css_safety_and_responsiveness(self) -> None:
        self.assertIn("env(safe-area-inset-top)", self.css_text)
        self.assertIn("env(safe-area-inset-bottom)", self.css_text)
        self.assertRegex(self.css_text, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
        self.assertRegex(self.css_text, r"@media\s*\(min-width:\s*\d+px\)")
        self.assertRegex(self.css_text, r"overflow-x:\s*hidden")
        self.assertRegex(self.css_text, r"min-width:\s*320px")
        self.assertRegex(self.css_text, r"min-height:\s*44px")

    def test_existing_homepage_assets_match_base_commit(self) -> None:
        for relative_path in ("static/index.html", "static/assets/bitswipe.css"):
            base_blob = git_output("rev-parse", f"{BASE_COMMIT}:{relative_path}").strip()
            worktree_blob = git_output("hash-object", relative_path).strip()
            self.assertEqual(base_blob, worktree_blob, f"{relative_path} differs from the requested base")


if __name__ == "__main__":
    unittest.main(verbosity=2)
