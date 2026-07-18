#!/usr/bin/env python3
"""Offline isolation and safety checks for mobile and Decision Layers previews."""

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
FOUNDATION_PATH = ROOT / "static" / "assets" / "mobile-preview-foundation.js"
MACRO_ADAPTER_PATH = ROOT / "static" / "assets" / "mobile-preview-macro-adapter.js"
DECISION_HTML_PATH = ROOT / "static" / "assets" / "decision-preview.html"
DECISION_CSS_PATH = ROOT / "static" / "assets" / "decision-preview.css"
DECISION_JS_PATH = ROOT / "static" / "assets" / "decision-preview.js"
JOURNAL_HTML_PATH = ROOT / "static" / "assets" / "decision-journal-preview.html"
JOURNAL_CSS_PATH = ROOT / "static" / "assets" / "decision-journal-preview.css"
JOURNAL_JS_PATH = ROOT / "static" / "assets" / "decision-journal-preview.js"

BASE_COMMIT = "6d39043ab121bdbd2900dffe481c86f167946d8c"
JOURNAL_BASE_COMMIT = "0303280a9694f9fd71a90a0dd11a5bd73aac556d"
PUBLIC_SHELL_BASE_COMMIT = "39b106573cb91961ea87947cc8f37ff151ef741e"
ALLOWED_DIFFS = {
    "static/mobile-preview.html",
    "static/assets/mobile-preview.css",
    "static/assets/mobile-preview.js",
    "static/assets/mobile-preview-foundation.js",
    "static/assets/mobile-preview-macro-adapter.js",
    "static/assets/decision-preview.html",
    "static/assets/decision-preview.css",
    "static/assets/decision-preview.js",
    "static/assets/decision-journal-preview.html",
    "static/assets/decision-journal-preview.css",
    "static/assets/decision-journal-preview.js",
    "scripts/mobile_preview_test.py",
}


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


class FrontendPreviewSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server_text = SERVER_PATH.read_text(encoding="utf-8")
        cls.html_text = HTML_PATH.read_text(encoding="utf-8")
        cls.css_text = CSS_PATH.read_text(encoding="utf-8")
        cls.js_text = JS_PATH.read_text(encoding="utf-8")
        cls.foundation_text = FOUNDATION_PATH.read_text(encoding="utf-8")
        cls.macro_adapter_text = MACRO_ADAPTER_PATH.read_text(encoding="utf-8")
        cls.decision_html_text = DECISION_HTML_PATH.read_text(encoding="utf-8")
        cls.decision_css_text = DECISION_CSS_PATH.read_text(encoding="utf-8")
        cls.decision_js_text = DECISION_JS_PATH.read_text(encoding="utf-8")
        cls.journal_html_text = JOURNAL_HTML_PATH.read_text(encoding="utf-8")
        cls.journal_css_text = JOURNAL_CSS_PATH.read_text(encoding="utf-8")
        cls.journal_js_text = JOURNAL_JS_PATH.read_text(encoding="utf-8")

        cls.html = PreviewHTMLParser()
        cls.html.feed(cls.html_text)
        cls.page_text = " ".join(cls.html.text)
        cls.decision_html = PreviewHTMLParser()
        cls.decision_html.feed(cls.decision_html_text)
        cls.decision_page_text = " ".join(cls.decision_html.text)
        cls.journal_html = PreviewHTMLParser()
        cls.journal_html.feed(cls.journal_html_text)
        cls.journal_page_text = " ".join(cls.journal_html.text)
        cls.server_tree = ast.parse(cls.server_text)

    def test_01_only_approved_frontend_files_differ_from_exact_mobile_base(self) -> None:
        self.assertEqual(git_output("rev-parse", BASE_COMMIT).strip().decode(), BASE_COMMIT)
        changed = set(git_output("diff", "--name-only", BASE_COMMIT, "--").decode().splitlines())
        self.assertEqual(changed, ALLOWED_DIFFS)
        for relative_path in ("server.py", "static/index.html", "static/assets/bitswipe.css"):
            base_blob = git_output("rev-parse", f"{BASE_COMMIT}:{relative_path}").strip()
            worktree_blob = git_output("hash-object", relative_path).strip()
            self.assertEqual(base_blob, worktree_blob, f"{relative_path} changed from mobile base")

    def test_02_journal_v3_and_public_shell_v4_are_exactly_isolated(self) -> None:
        self.assertEqual(git_output("rev-parse", JOURNAL_BASE_COMMIT).strip().decode(), JOURNAL_BASE_COMMIT)
        changed = set(git_output("diff", "--name-only", JOURNAL_BASE_COMMIT, "--").decode().splitlines())
        self.assertEqual(
            changed,
            {
                "static/assets/decision-journal-preview.html",
                "static/assets/decision-journal-preview.css",
                "static/assets/decision-journal-preview.js",
                "static/assets/mobile-preview-foundation.js",
                "scripts/mobile_preview_test.py",
            },
        )
        for relative_path in (
            "server.py",
            "static/mobile-preview.html",
            "static/assets/mobile-preview.css",
            "static/assets/mobile-preview.js",
            "static/assets/mobile-preview-macro-adapter.js",
            "static/assets/decision-preview.html",
            "static/assets/decision-preview.css",
            "static/assets/decision-preview.js",
        ):
            base_blob = git_output("rev-parse", f"{JOURNAL_BASE_COMMIT}:{relative_path}").strip()
            worktree_blob = git_output("hash-object", relative_path).strip()
            self.assertEqual(base_blob, worktree_blob, f"{relative_path} changed after Journal base")

        self.assertEqual(
            git_output("rev-parse", PUBLIC_SHELL_BASE_COMMIT).strip().decode(),
            PUBLIC_SHELL_BASE_COMMIT,
        )
        shell_changed = set(
            git_output("diff", "--name-only", PUBLIC_SHELL_BASE_COMMIT, "--").decode().splitlines()
        )
        self.assertEqual(
            shell_changed,
            {
                "static/assets/mobile-preview-foundation.js",
                "scripts/mobile_preview_test.py",
            },
        )
        for relative_path in (
            "server.py",
            "static/mobile-preview.html",
            "static/assets/mobile-preview.css",
            "static/assets/mobile-preview.js",
            "static/assets/mobile-preview-macro-adapter.js",
            "static/assets/decision-preview.html",
            "static/assets/decision-preview.css",
            "static/assets/decision-preview.js",
            "static/assets/decision-journal-preview.html",
            "static/assets/decision-journal-preview.css",
            "static/assets/decision-journal-preview.js",
        ):
            base_blob = git_output("rev-parse", f"{PUBLIC_SHELL_BASE_COMMIT}:{relative_path}").strip()
            worktree_blob = git_output("hash-object", relative_path).strip()
            self.assertEqual(base_blob, worktree_blob, f"{relative_path} changed in Public Shell v4")

    def test_03_root_and_mobile_preview_routes_remain_isolated(self) -> None:
        async_functions = [node for node in self.server_tree.body if isinstance(node, ast.AsyncFunctionDef)]
        get_routes = {
            decorator_path(node, "get"): node
            for node in async_functions
            if decorator_path(node, "get")
        }
        self.assertIn("/", get_routes)
        self.assertIn("/mobile-preview", get_routes)
        preview_source = ast.get_source_segment(self.server_text, get_routes["/mobile-preview"]) or ""
        self.assertIn("mobile-preview.html", preview_source)
        mounts = [
            node for node in ast.walk(self.server_tree)
            if isinstance(node, ast.Call) and call_name(node.func) == "app.mount"
        ]
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0].args[0].value, "/assets")

    def test_04_mobile_preview_uses_only_local_assets_in_expected_order(self) -> None:
        links = self.html.attrs_for("link")
        scripts = self.html.attrs_for("script")
        self.assertEqual(
            [item.get("href") for item in links if item.get("rel") == "stylesheet"],
            ["/assets/mobile-preview.css"],
        )
        self.assertEqual(
            [item.get("src") for item in scripts if item.get("src")],
            [
                "/assets/mobile-preview-foundation.js",
                "/assets/mobile-preview-macro-adapter.js",
                "/assets/mobile-preview.js",
            ],
        )
        self.assertFalse(any(not item.get("src") for item in scripts))

    def test_05_mobile_browser_network_is_read_only(self) -> None:
        combined = "\n".join((self.js_text, self.foundation_text, self.macro_adapter_text))
        self.assertNotRegex(combined, r'method\s*:\s*["\'](?:POST|PUT|PATCH|DELETE)["\']')
        self.assertNotRegex(combined, r"\b(?:XMLHttpRequest|sendBeacon|WebSocket)\s*\(")
        self.assertNotRegex(combined, r"\b(?:placeOrder|place_order|submitOrder|cancelOrder|closePosition|setLeverage)\s*\(")

    def test_06_mobile_eventsource_paths_are_exact(self) -> None:
        event_sources = re.findall(r'new\s+EventSource\(\s*["\']([^"\']+)["\']\s*\)', self.js_text)
        self.assertEqual(event_sources, ["/api/market-stream", "/api/account-stream"])
        self.assertIn('const ALLOWED_STREAMS = new Set(["/api/market-stream", "/api/account-stream"]);', self.js_text)
        self.assertIn("source.close()", self.js_text)

    def test_07_mobile_demo_exits_before_streams_and_live_reads(self) -> None:
        initialize = re.search(r"function initialize\(\) \{(?P<body>.*?)\n  \}\n\n  initialize\(\);", self.js_text, re.DOTALL)
        self.assertIsNotNone(initialize)
        body = initialize.group("body")
        demo_branch = body.index("if (demoMode)")
        demo_return = body.index("return;", demo_branch)
        self.assertLess(demo_return, body.index("connectMarketStream()"))
        self.assertLess(demo_return, body.index("loadLiveReads()"))
        self.assertIn("라이브 API와 스트림을 사용하지 않습니다", self.page_text)

    def test_08_mobile_account_snapshot_is_not_persisted(self) -> None:
        storage_writes = re.findall(r"localStorage\.setItem\(([^,\n]+),\s*([^)]+)\)", self.js_text)
        self.assertTrue(storage_writes)
        for key_expression, value_expression in storage_writes:
            self.assertNotRegex(
                key_expression + " " + value_expression,
                r"state\.account|DEMO_ACCOUNT|wallet|balance|positions|notional|pnl|leverage",
            )
        self.assertNotIn("JSON.stringify(state.account", self.js_text)
        self.assertNotIn("localStorage", self.foundation_text)
        self.assertNotIn("localStorage", self.macro_adapter_text)

    def test_09_mobile_privacy_is_hidden_by_default(self) -> None:
        self.assertIn("return stored === null ? true", self.js_text)
        private_values = [attrs for _, attrs in self.html.tags if attrs.get("data-private") == "true"]
        self.assertGreaterEqual(len(private_values), 5)
        self.assertGreaterEqual(self.page_text.count("••••••"), 5)

    def test_10_scenario_grade_thresholds_and_semantics_are_preserved(self) -> None:
        grade_function = re.search(r"function scoreGrade\(score\) \{(?P<body>.*?)\n  \}", self.js_text, re.DOTALL)
        self.assertIsNotNone(grade_function)
        compact = re.sub(r"\s+", " ", grade_function.group("body"))
        for fragment in ('score >= 85) return "A"', 'score >= 75) return "B+"', 'score >= 65) return "B"', 'score >= 50) return "C"', 'return "D"'):
            self.assertIn(fragment, compact)
        self.assertIn("시나리오 명확도", self.page_text)
        self.assertIn("수익이나 승률을 보장하지 않습니다", self.page_text)
        self.assertNotIn("예상 승률", self.page_text)

    def test_11_grade_risk_and_leverage_concepts_remain_separate(self) -> None:
        ids = {attrs.get("id") for _, attrs in self.html.tags}
        for expected in ("hero-grade-badge", "hero-risk-badge", "detail-grade", "detail-risk"):
            self.assertIn(expected, ids)
        leverage_priority = re.search(r"const leverage = finiteNumber\(([^)]+)\)", self.js_text)
        self.assertIsNotNone(leverage_priority)
        self.assertEqual(
            [item.strip() for item in leverage_priority.group(1).split(",")],
            ["trade.leverage", "raw.claude_leverage", "riskGuard.leverage"],
        )
        self.assertIn("현재 계좌 레버리지를 자동으로 변경하지 않습니다", self.page_text)

    def test_12_automatic_decision_criteria_are_preserved(self) -> None:
        for label in ("분석 신선도", "가격 트리거", "추세 구조", "모멘텀", "거래량 참여", "리스크 가드", "계좌 리스크 적합도"):
            self.assertIn(label, self.js_text)
        self.assertIn("의사결정 기준", self.page_text)

    def test_13_candidate_history_remains_dynamic_and_tradfi_is_context(self) -> None:
        self.assertIn("/api/analysis-history?limit=50", self.js_text)
        self.assertIn("rawCandidates.push.apply(rawCandidates, entries)", self.js_text)
        self.assertNotRegex(self.js_text, r"entries\.(?:filter|find)\([^)]*BTC")
        self.assertIn("전통시장 컨텍스트", self.page_text)
        self.assertIn("매매 후보 아님", self.page_text)

    def test_14_mobile_demo_and_canvas_accessibility_are_preserved(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            self.assertIn(f'symbol: "{symbol}"', self.js_text)
        canvases = self.html.attrs_for("canvas")
        self.assertEqual(len(canvases), 1)
        self.assertEqual(canvases[0].get("role"), "img")
        self.assertTrue(canvases[0].get("aria-label"))
        self.assertIn("ResizeObserver", self.js_text)

    def test_15_mobile_layout_accessibility_contract_is_preserved(self) -> None:
        self.assertRegex(self.css_text, r"@media\s*\(max-width:\s*390px\)")
        self.assertRegex(self.css_text, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
        self.assertIn("env(safe-area-inset-bottom)", self.css_text)
        viewport = next(item for item in self.html.attrs_for("meta") if item.get("name") == "viewport")
        self.assertNotIn("user-scalable=no", viewport.get("content", ""))

    def test_16_macro_adapter_contract_is_preserved(self) -> None:
        self.assertIn('hasOwnProperty.call(raw, "_trad_markets")', self.macro_adapter_text)
        self.assertIn("normalized.trad_markets = asObject(raw._trad_markets)", self.macro_adapter_text)
        self.assertIn("normalized.trad_markets = asObject(raw.trad_markets)", self.macro_adapter_text)
        self.assertIn("ibit.change24h", self.macro_adapter_text)
        self.assertIn('url.pathname !== "/api/macro"', self.macro_adapter_text)
        self.assertNotRegex(self.macro_adapter_text, r'method\s*:\s*["\'](?:POST|PUT|PATCH|DELETE)["\']')

    def test_17_product_identity_and_capability_boundary_are_preserved(self) -> None:
        for field in ("name", "shortName", "tagline", "description"):
            self.assertRegex(self.foundation_text, rf"\b{field}\s*:")
        for capability in (
            "market.data", "macro.context", "analysis", "candidates", "scenarios",
            "account.balances", "account.positions", "account.context",
        ):
            self.assertIn(f'"{capability}"', self.foundation_text)
        self.assertIn('mode: "owner"', self.foundation_text)
        self.assertIn("public: true", self.foundation_text)
        self.assertIn("private: true", self.foundation_text)

    def test_18_foundation_has_no_storage_or_network_side_effects(self) -> None:
        self.assertNotRegex(self.foundation_text, r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b")
        self.assertNotRegex(self.foundation_text, r"\b(?:fetch|EventSource|XMLHttpRequest|sendBeacon|WebSocket)\s*\(")
        self.assertIn('loading: "loading"', self.foundation_text)
        self.assertIn('endpointFailure: "endpoint_failure"', self.foundation_text)
        self.assertIn("zeroIsValue: true", self.foundation_text)

    def test_19_dynamic_content_avoids_inner_html(self) -> None:
        for text in (self.js_text, self.foundation_text, self.macro_adapter_text, self.decision_js_text, self.journal_js_text):
            self.assertNotIn("innerHTML", text)
        self.assertIn("replaceChildren", self.js_text)
        self.assertIn("replaceChildren", self.decision_js_text)
        self.assertIn("replaceChildren", self.journal_js_text)

    def test_20_decision_preview_uses_local_assets_and_view_model(self) -> None:
        links = self.decision_html.attrs_for("link")
        scripts = self.decision_html.attrs_for("script")
        self.assertEqual([item.get("href") for item in links if item.get("rel") == "stylesheet"], ["/assets/decision-preview.css"])
        self.assertEqual(
            [item.get("src") for item in scripts if item.get("src")],
            ["/assets/mobile-preview-foundation.js", "/assets/mobile-preview-macro-adapter.js", "/assets/decision-preview.js"],
        )
        self.assertIn("architecture.buildViewModel", self.decision_js_text)

    def test_21_decision_preview_reads_public_sources_only(self) -> None:
        allowed_reads = re.search(r"const ALLOWED_READS\s*=\s*new Set\(\[(?P<body>.*?)\]\);", self.decision_js_text, re.DOTALL)
        self.assertIsNotNone(allowed_reads)
        self.assertEqual(
            set(re.findall(r'"(/api/[^"\']+)"', allowed_reads.group("body"))),
            {"/api/analyze?include_latest=true", "/api/analysis-history?limit=50", "/api/macro", "/api/symbol"},
        )
        self.assertNotIn("/api/account-stream", self.decision_js_text)
        self.assertRegex(self.decision_js_text, r'method:\s*"GET"')
        self.assertNotRegex(self.decision_js_text, r'method\s*:\s*"(?:POST|PUT|PATCH|DELETE)"')

    def test_22_decision_demo_and_action_language_are_preserved(self) -> None:
        self.assertIn("if (demoMode)", self.decision_js_text)
        self.assertIn("loadDemoData()", self.decision_js_text)
        for action in ("WAIT", "WATCH", "PREPARE", "READY", "MANAGE", "EXIT", "INVALIDATED"):
            self.assertIn(f'"{action}"', self.decision_js_text)
        self.assertIn("Scenario Quality", self.decision_page_text)
        self.assertIn("승률 아님", self.decision_page_text)
        self.assertIn("Risk guard", self.decision_page_text)

    def test_23_decision_navigation_and_responsive_layout_are_preserved(self) -> None:
        for label in ("Market", "Analysis", "Setups", "Journal"):
            self.assertIn(label, self.decision_page_text)
        self.assertNotIn("Account", self.decision_page_text)
        self.assertRegex(self.decision_css_text, r"@media\s*\(max-width:\s*390px\)")
        self.assertRegex(self.decision_css_text, r"@media\s*\(min-width:\s*1200px\)")
        self.assertIn("224px minmax(0, 824px) 280px", self.decision_css_text)

    def test_24_decision_preview_has_no_browser_persistence_or_trading_mutations(self) -> None:
        self.assertNotRegex(self.decision_js_text, r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b")
        self.assertNotRegex(self.decision_js_text, r"\b(?:placeOrder|place_order|submitOrder|cancelOrder|closePosition|setLeverage)\s*\(")

    def test_25_journal_preview_uses_local_assets_and_view_model_contract(self) -> None:
        links = self.journal_html.attrs_for("link")
        scripts = self.journal_html.attrs_for("script")
        self.assertEqual([item.get("href") for item in links if item.get("rel") == "stylesheet"], ["/assets/decision-journal-preview.css"])
        self.assertEqual(
            [item.get("src") for item in scripts if item.get("src")],
            ["/assets/mobile-preview-foundation.js", "/assets/decision-journal-preview.js"],
        )
        self.assertFalse(any(not item.get("src") for item in scripts))
        self.assertIn("architecture.buildViewModel", self.journal_js_text)
        self.assertIn("DECISION JOURNAL", self.journal_page_text)

    def test_26_journal_reads_only_analysis_public_get_endpoints_and_no_streams(self) -> None:
        allowed_reads = re.search(r"const ALLOWED_READS\s*=\s*new Set\(\[(?P<body>.*?)\]\);", self.journal_js_text, re.DOTALL)
        self.assertIsNotNone(allowed_reads)
        self.assertEqual(
            set(re.findall(r'"(/api/[^"\']+)"', allowed_reads.group("body"))),
            {"/api/analyze?include_latest=true", "/api/analysis-history?limit=50"},
        )
        self.assertIn('method: "GET"', self.journal_js_text)
        self.assertNotIn("EventSource", self.journal_js_text)
        self.assertNotIn("/api/account", self.journal_js_text)
        self.assertNotRegex(self.journal_js_text, r'method\s*:\s*"(?:POST|PUT|PATCH|DELETE)"')

    def test_27_journal_demo_exits_before_live_reads(self) -> None:
        initialize = re.search(r"function initialize\(\) \{(?P<body>.*?)\n  \}\n\n  initialize\(\);", self.journal_js_text, re.DOTALL)
        self.assertIsNotNone(initialize)
        body = initialize.group("body")
        demo_branch = body.index("if (demoMode)")
        demo_return = body.index("return;", demo_branch)
        self.assertLess(demo_return, body.index("loadLiveReads()"))
        self.assertIn("화면 검토용이며 라이브 API를 사용하지 않습니다", self.journal_page_text)
        self.assertIn("elements.demoBanner.hidden = false", self.journal_js_text)

    def test_28_journal_never_fabricates_trade_outcomes(self) -> None:
        self.assertIn("결과", self.journal_page_text)
        self.assertIn("미확인", self.journal_page_text)
        self.assertIn("실제 진입 여부", self.journal_page_text)
        self.assertIn("실현 손익", self.journal_page_text)
        self.assertIn("승률", self.journal_page_text)
        self.assertIn("결과를 추정하거나 성공·실패로 분류하지 않습니다", self.journal_page_text)
        self.assertNotRegex(self.journal_js_text, r"(?i)win[_ -]?rate|realized[_ -]?pnl|trade[_ -]?result")

    def test_29_journal_has_no_browser_persistence_private_reads_or_trading_mutations(self) -> None:
        self.assertNotRegex(self.journal_js_text, r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b")
        self.assertNotRegex(self.journal_js_text, r"\b(?:placeOrder|place_order|submitOrder|cancelOrder|closePosition|setLeverage)\s*\(")
        self.assertNotIn("wallet_balance", self.journal_js_text)
        self.assertNotIn("open_positions", self.journal_js_text)

    def test_30_journal_responsive_and_accessible_layout_contract(self) -> None:
        self.assertRegex(self.journal_css_text, r"@media\s*\(max-width:\s*390px\)")
        self.assertRegex(self.journal_css_text, r"@media\s*\(min-width:\s*1200px\)")
        self.assertIn("224px minmax(0, 824px) 280px", self.journal_css_text)
        self.assertIn("min-height: 44px", self.journal_css_text)
        self.assertIn("env(safe-area-inset-bottom)", self.journal_css_text)
        self.assertRegex(self.journal_css_text, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
        viewport = next(item for item in self.journal_html.attrs_for("meta") if item.get("name") == "viewport")
        self.assertNotIn("user-scalable=no", viewport.get("content", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
