#!/usr/bin/env python3
"""Offline isolation, safety, and v2 contract checks for /mobile-preview."""

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
BASE_COMMIT = "6d39043ab121bdbd2900dffe481c86f167946d8c"
ALLOWED_DIFFS = {
    "static/mobile-preview.html",
    "static/assets/mobile-preview.css",
    "static/assets/mobile-preview.js",
    "static/assets/mobile-preview-foundation.js",
    "static/assets/mobile-preview-macro-adapter.js",
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


class MobilePreviewV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server_text = SERVER_PATH.read_text(encoding="utf-8")
        cls.html_text = HTML_PATH.read_text(encoding="utf-8")
        cls.css_text = CSS_PATH.read_text(encoding="utf-8")
        cls.js_text = JS_PATH.read_text(encoding="utf-8")
        cls.foundation_text = FOUNDATION_PATH.read_text(encoding="utf-8")
        cls.macro_adapter_text = MACRO_ADAPTER_PATH.read_text(encoding="utf-8")
        cls.combined_preview = "\n".join(
            (
                cls.html_text,
                cls.css_text,
                cls.foundation_text,
                cls.js_text,
                cls.macro_adapter_text,
            )
        )
        cls.server_tree = ast.parse(cls.server_text)
        cls.html = PreviewHTMLParser()
        cls.html.feed(cls.html_text)
        cls.page_text = " ".join(cls.html.text)

    def test_01_only_allowed_preview_files_differ_from_exact_base(self) -> None:
        self.assertEqual(git_output("rev-parse", BASE_COMMIT).strip().decode(), BASE_COMMIT)
        changed = set(
            git_output("diff", "--name-only", BASE_COMMIT, "--")
            .decode("utf-8")
            .splitlines()
        )
        self.assertEqual(changed, ALLOWED_DIFFS)
        for relative_path in ("server.py", "static/index.html", "static/assets/bitswipe.css"):
            base_blob = git_output("rev-parse", f"{BASE_COMMIT}:{relative_path}").strip()
            worktree_blob = git_output("hash-object", relative_path).strip()
            self.assertEqual(base_blob, worktree_blob, f"{relative_path} changed from the exact base")

    def test_02_preview_uses_local_css_and_javascript_only(self) -> None:
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
        self.assertFalse(any(not item.get("src") for item in scripts), "inline scripts are not allowed")

    def test_03_preview_contains_no_external_urls_or_third_party_requests(self) -> None:
        self.assertNotRegex(self.combined_preview, r"https?://|//[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        csp = [
            item.get("content", "")
            for item in self.html.attrs_for("meta")
            if item.get("http-equiv", "").lower() == "content-security-policy"
        ]
        self.assertEqual(len(csp), 1)
        self.assertIn("connect-src 'self'", csp[0])
        self.assertIn("default-src 'self'", csp[0])

    def test_04_browser_network_code_is_read_only(self) -> None:
        network_code = self.js_text + "\n" + self.foundation_text + "\n" + self.macro_adapter_text
        self.assertNotRegex(
            network_code,
            r'method\s*:\s*["\'](?:POST|PUT|PATCH|DELETE)["\']',
        )
        self.assertNotRegex(network_code, r"\b(?:XMLHttpRequest|sendBeacon|WebSocket)\s*\(")
        self.assertNotRegex(
            network_code,
            r"\b(?:placeOrder|place_order|submitOrder|cancelOrder|closePosition|setLeverage)\s*\(",
        )

    def test_05_eventsource_uses_exactly_two_approved_paths(self) -> None:
        event_sources = re.findall(r'new\s+EventSource\(\s*["\']([^"\']+)["\']\s*\)', self.js_text)
        self.assertEqual(event_sources, ["/api/market-stream", "/api/account-stream"])
        allowed = re.search(r"const ALLOWED_STREAMS\s*=\s*new Set\(\[([^\]]+)\]\)", self.js_text)
        self.assertIsNotNone(allowed)
        self.assertEqual(
            set(re.findall(r'["\']([^"\']+)["\']', allowed.group(1))),
            {"/api/market-stream", "/api/account-stream"},
        )
        self.assertIn("sameOriginStreamPath", self.js_text)
        self.assertIn("source.close()", self.js_text)
        self.assertIn('window.addEventListener("beforeunload", closeStreams)', self.js_text)

    def test_06_demo_mode_exits_before_live_reads_and_streams(self) -> None:
        initialize = re.search(
            r"function initialize\(\) \{(?P<body>.*?)\n  \}\n\n  initialize\(\);",
            self.js_text,
            re.DOTALL,
        )
        self.assertIsNotNone(initialize)
        body = initialize.group("body")
        demo_branch = body.index("if (demoMode)")
        demo_return = body.index("return;", demo_branch)
        first_stream = body.index("connectMarketStream()")
        first_fetch = body.index("loadLiveReads()")
        self.assertLess(demo_return, first_stream)
        self.assertLess(demo_return, first_fetch)
        self.assertIn("라이브 API와 스트림을 사용하지 않습니다", self.page_text)

    def test_07_fetch_adapter_allows_same_origin_get_only(self) -> None:
        self.assertEqual(len(re.findall(r"\bwindow\.fetch\s*\(", self.js_text)), 1)
        self.assertIn("url.origin !== window.location.origin", self.js_text)
        self.assertRegex(self.js_text, r'method:\s*["\']GET["\']')
        paths = set(
            re.findall(
                r'^\s{6}["\'](/api/[^"\']+)["\'],?$',
                self.js_text,
                re.MULTILINE,
            )
        )
        self.assertEqual(
            paths,
            {
                "/api/analyze?include_latest=true",
                "/api/analysis-history?limit=50",
                "/api/macro",
                "/api/symbol",
            },
        )

    def test_08_grade_thresholds_are_exact(self) -> None:
        grade_function = re.search(
            r"function scoreGrade\(score\) \{(?P<body>.*?)\n  \}",
            self.js_text,
            re.DOTALL,
        )
        self.assertIsNotNone(grade_function)
        compact = re.sub(r"\s+", " ", grade_function.group("body"))
        self.assertIn('score >= 85) return "A"', compact)
        self.assertIn('score >= 75) return "B+"', compact)
        self.assertIn('score >= 65) return "B"', compact)
        self.assertIn('score >= 50) return "C"', compact)
        self.assertIn('return "D"', compact)

    def test_09_score_is_scenario_clarity_not_win_rate(self) -> None:
        self.assertGreaterEqual(self.page_text.count("시나리오 명확도"), 1)
        self.assertIn("구조적 명확도", self.page_text)
        self.assertIn("수익이나 승률을 보장하지 않습니다", self.page_text)
        self.assertNotIn("예상 승률", self.page_text)

    def test_10_grade_and_risk_are_separate_badges(self) -> None:
        ids = {item.get("id") for _, item in self.html.tags}
        self.assertIn("hero-grade-badge", ids)
        self.assertIn("hero-risk-badge", ids)
        self.assertIn("detail-grade", ids)
        self.assertIn("detail-risk", ids)
        self.assertIn('candidate.risk.verdict === "BLOCK"', self.js_text)
        self.assertIn("높은 등급이어도 실행하지 않습니다", self.js_text)

    def test_11_recommended_and_actual_leverage_are_separate(self) -> None:
        for text in ("권장 레버리지", "유효 레버리지", "설정 레버리지", "실제 레버리지"):
            self.assertIn(text, self.page_text)
        self.assertIn("현재 계좌 레버리지를 자동으로 변경하지 않습니다", self.page_text)
        self.assertIn("시나리오의 권장 레버리지와 별도", self.page_text)
        leverage_priority = re.search(
            r"const leverage = finiteNumber\(([^)]+)\)",
            self.js_text,
        )
        self.assertIsNotNone(leverage_priority)
        self.assertEqual(
            [item.strip() for item in leverage_priority.group(1).split(",")],
            ["trade.leverage", "raw.claude_leverage", "riskGuard.leverage"],
        )

    def test_12_manual_five_checkbox_checklist_is_removed(self) -> None:
        inputs = self.html.attrs_for("input")
        self.assertFalse(any(item.get("type", "").lower() == "checkbox" for item in inputs))
        self.assertNotIn("data-checklist", self.html_text)
        self.assertNotIn("체크 완료하고", self.page_text)

    def test_13_seven_automatic_decision_criteria_exist(self) -> None:
        criteria = (
            "분석 신선도",
            "가격 트리거",
            "추세 구조",
            "모멘텀",
            "거래량 참여",
            "리스크 가드",
            "계좌 리스크 적합도",
        )
        for label in criteria:
            self.assertIn(label, self.js_text)
        self.assertIn("의사결정 기준", self.page_text)
        self.assertIn("실측 데이터 · 읽기 전용", self.page_text)
        self.assertIn('status: "supporting"', self.js_text)
        self.assertIn('status: "unavailable"', self.js_text)

    def test_14_account_snapshot_is_never_written_to_local_storage(self) -> None:
        storage_writes = re.findall(
            r"localStorage\.setItem\(([^,\n]+),\s*([^)]+)\)",
            self.js_text,
        )
        self.assertTrue(storage_writes)
        for key_expression, value_expression in storage_writes:
            combined = key_expression + " " + value_expression
            self.assertNotRegex(
                combined,
                r"state\.account|DEMO_ACCOUNT|wallet|balance|positions|notional|pnl|leverage",
            )
        self.assertNotIn("JSON.stringify(state.account", self.js_text)
        self.assertNotIn("localStorage", self.foundation_text)
        self.assertNotIn("localStorage", self.macro_adapter_text)
        self.assertIn("계좌 스냅샷은 저장하지 않습니다", self.page_text)

    def test_15_balance_privacy_is_hidden_by_default(self) -> None:
        self.assertIn("return stored === null ? true", self.js_text)
        private_values = [
            item for _, item in self.html.tags
            if item.get("data-private") == "true"
        ]
        self.assertGreaterEqual(len(private_values), 5)
        self.assertGreaterEqual(self.page_text.count("••••••"), 5)
        toggle = next(item for item in self.html.attrs_for("button") if item.get("id") == "privacy-toggle")
        self.assertEqual(toggle.get("aria-pressed"), "true")
        self.assertIn("잔고 표시", self.page_text)

    def test_16_no_secret_or_trading_action_code_is_present(self) -> None:
        combined_js = self.js_text + "\n" + self.foundation_text + "\n" + self.macro_adapter_text
        self.assertNotRegex(
            combined_js,
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|listen[_-]?key|order[_-]?id)\b",
        )
        self.assertNotRegex(
            combined_js,
            r"(?i)\b(?:buy|sell|long|short)[A-Za-z_]*(?:Order|Position)\s*\(",
        )
        buttons = self.html.attrs_for("button")
        action_labels = " ".join(item.get("aria-label", "") for item in buttons)
        self.assertNotRegex(action_labels, r"주문|취소|청산|레버리지 변경")
        self.assertIn("주문·취소·청산 기능이 없는 읽기 전용 화면", self.page_text)

    def test_17_navigation_has_aria_current_and_non_color_active_cue(self) -> None:
        nav_buttons = [
            attrs
            for tag, attrs in self.html.tags
            if tag == "button" and attrs.get("data-route")
        ]
        self.assertEqual(len(nav_buttons), 3)
        self.assertEqual(sum(item.get("aria-current") == "page" for item in nav_buttons), 1)
        active_rule = re.search(
            r"\.bottom-nav button\[aria-current=\"page\"\]\s*\{([^}]+)\}",
            self.css_text,
            re.DOTALL,
        )
        self.assertIsNotNone(active_rule)
        self.assertRegex(active_rule.group(1), r"box-shadow|border|text-decoration|font-weight")
        self.assertIn('candidate-card[data-active="true"]', self.css_text)

    def test_18_candidate_history_is_dynamic_and_not_btc_filtered(self) -> None:
        self.assertIn("/api/analysis-history?limit=50", self.js_text)
        self.assertIn("rawCandidates.push.apply(rawCandidates, entries)", self.js_text)
        self.assertNotRegex(
            self.js_text,
            r"entries\.(?:filter|find)\([^)]*BTC",
        )
        self.assertIn("다중 심볼 자동 스캔은 별도 백엔드 단계", self.page_text)
        self.assertIn("card.dataset.active", self.js_text)

    def test_19_tradfi_is_clearly_context_not_candidates(self) -> None:
        self.assertIn("전통시장 컨텍스트", self.page_text)
        self.assertIn("매매 후보 아님", self.page_text)
        self.assertIn("분석된 거래 후보가 아닙니다", self.page_text)
        for ticker in ("SPX", "NDX", "VIX", "GOLD", "IBIT"):
            self.assertIn(f'"{ticker}"', self.js_text)

    def test_20_demo_has_multiple_symbols_and_permanent_sample_banner(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            self.assertIn(f'symbol: "{symbol}"', self.js_text)
        banner = next(item for item in self.html.attrs_for("div") if item.get("id") == "demo-banner")
        self.assertIn("hidden", banner)
        self.assertIn("샘플 데이터", self.page_text)
        self.assertIn("elements.demoBanner.hidden = false", self.js_text)
        self.assertNotIn("elements.demoBanner.hidden = true", self.js_text)

    def test_21_canvas_has_fallback_and_accessible_description(self) -> None:
        canvases = self.html.attrs_for("canvas")
        self.assertEqual(len(canvases), 1)
        self.assertEqual(canvases[0].get("role"), "img")
        self.assertTrue(canvases[0].get("aria-label"))
        self.assertIn("브라우저가 canvas를 지원하지 않아", self.page_text)
        self.assertIn("Canvas를 지원하지 않는 브라우저입니다", self.js_text)
        self.assertIn("ResizeObserver", self.js_text)

    def test_22_mobile_layout_prevents_horizontal_overflow(self) -> None:
        self.assertRegex(self.css_text, r"html\s*\{[^}]*min-width:\s*320px", re.DOTALL)
        self.assertGreaterEqual(len(re.findall(r"overflow-x:\s*hidden", self.css_text)), 2)
        self.assertIn("minmax(0, 1fr)", self.css_text)
        self.assertIn("min-width: 0", self.css_text)
        self.assertRegex(self.css_text, r"@media\s*\(max-width:\s*390px\)")
        self.assertRegex(self.css_text, r"min-height:\s*44px")

    def test_23_reduced_motion_safe_areas_and_zoom_semantics_exist(self) -> None:
        self.assertRegex(self.css_text, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
        self.assertIn("env(safe-area-inset-top)", self.css_text)
        self.assertIn("env(safe-area-inset-bottom)", self.css_text)
        viewport = next(item for item in self.html.attrs_for("meta") if item.get("name") == "viewport")
        self.assertNotIn("user-scalable=no", viewport.get("content", ""))
        self.assertNotIn("maximum-scale=1", viewport.get("content", ""))
        self.assertTrue(self.html.attrs_for("main"))
        self.assertGreaterEqual(
            sum(bool(attrs.get("aria-live")) for _, attrs in self.html.tags),
            5,
        )

    def test_24_macro_adapter_matches_live_backend_contract(self) -> None:
        scripts = [item.get("src") for item in self.html.attrs_for("script") if item.get("src")]
        self.assertLess(
            scripts.index("/assets/mobile-preview-foundation.js"),
            scripts.index("/assets/mobile-preview-macro-adapter.js"),
        )
        self.assertLess(
            scripts.index("/assets/mobile-preview-macro-adapter.js"),
            scripts.index("/assets/mobile-preview.js"),
        )
        self.assertIn('hasOwnProperty.call(raw, "_trad_markets")', self.macro_adapter_text)
        self.assertIn("normalized.trad_markets = asObject(raw._trad_markets)", self.macro_adapter_text)
        self.assertIn("normalized.trad_markets = asObject(raw.trad_markets)", self.macro_adapter_text)
        self.assertIn("const ibit = asObject(raw.IBIT_PX)", self.macro_adapter_text)
        self.assertIn("ibit.change24h", self.macro_adapter_text)
        self.assertIn('url.pathname !== "/api/macro"', self.macro_adapter_text)
        self.assertIn("url.origin !== window.location.origin", self.macro_adapter_text)
        self.assertIn("response.clone().json()", self.macro_adapter_text)
        self.assertIn('if (typeof value === "number" && Number.isFinite(value)) return value;', self.macro_adapter_text)
        self.assertNotIn("localStorage", self.macro_adapter_text)
        self.assertNotRegex(
            self.macro_adapter_text,
            r'method\s*:\s*["\'](?:POST|PUT|PATCH|DELETE)["\']',
        )

    def test_25_product_identity_is_centralized(self) -> None:
        for field in ("name", "shortName", "tagline", "description"):
            self.assertRegex(self.foundation_text, rf"\b{field}\s*:")
        self.assertIn('name: "BitSwipe"', self.foundation_text)
        self.assertIn('shortName: "BitSwipe"', self.foundation_text)
        self.assertIn('tagline: "Command Center"', self.foundation_text)
        self.assertNotIn("BitSwipe", self.html_text)
        self.assertNotIn("BitSwipe", self.js_text)
        self.assertNotIn("BitSwipe", self.macro_adapter_text)
        self.assertIn("data-product-name", self.html_text)
        self.assertIn("data-product-tagline", self.html_text)
        self.assertIn("document.title = product.name", self.foundation_text)

    def test_26_public_private_capability_boundary_exists(self) -> None:
        for capability in (
            "market.data",
            "macro.context",
            "analysis",
            "candidates",
            "scenarios",
            "account.balances",
            "account.positions",
            "account.context",
        ):
            self.assertIn(f'"{capability}"', self.foundation_text)
        self.assertIn('mode: "owner"', self.foundation_text)
        self.assertIn("public: true", self.foundation_text)
        self.assertIn("private: true", self.foundation_text)
        screen_scopes = {
            attrs.get("data-screen"): attrs.get("data-capability-scope")
            for tag, attrs in self.html.tags
            if tag == "section" and attrs.get("data-screen")
        }
        self.assertEqual(screen_scopes.get("scenario"), "public")
        self.assertEqual(screen_scopes.get("candidates"), "public")
        self.assertEqual(screen_scopes.get("detail"), "public")
        self.assertEqual(screen_scopes.get("account"), "private")
        account_nav = next(
            attrs
            for tag, attrs in self.html.tags
            if tag == "button" and attrs.get("data-route") == "account"
        )
        self.assertEqual(account_nav.get("data-capability-scope"), "private")

    def test_27_foundation_has_no_storage_or_network_side_effects(self) -> None:
        self.assertNotRegex(
            self.foundation_text,
            r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b",
        )
        self.assertNotRegex(
            self.foundation_text,
            r"\b(?:fetch|EventSource|XMLHttpRequest|sendBeacon|WebSocket)\s*\(",
        )
        self.assertNotRegex(
            self.foundation_text,
            r'method\s*:\s*["\'](?:POST|PUT|PATCH|DELETE)["\']',
        )

    def test_28_honest_data_state_semantics_exist(self) -> None:
        for state_name in (
            'loading: "loading"',
            'unavailable: "unavailable"',
            'endpointFailure: "endpoint_failure"',
            'stale: "stale"',
            'ready: "ready"',
            'demo: "demo"',
        ):
            self.assertIn(state_name, self.foundation_text)
        self.assertIn("zeroIsValue: true", self.foundation_text)
        self.assertIn("missingValue: null", self.foundation_text)

    def test_preview_route_and_static_mount_remain_isolated(self) -> None:
        async_functions = [
            node for node in self.server_tree.body
            if isinstance(node, ast.AsyncFunctionDef)
        ]
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
            node
            for node in ast.walk(self.server_tree)
            if isinstance(node, ast.Call) and call_name(node.func) == "app.mount"
        ]
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0].args[0].value, "/assets")

    def test_dynamic_content_uses_text_content_not_inner_html(self) -> None:
        self.assertNotIn("innerHTML", self.js_text)
        self.assertNotIn("innerHTML", self.foundation_text)
        self.assertNotIn("innerHTML", self.macro_adapter_text)
        self.assertGreater(self.js_text.count(".textContent"), 50)
        self.assertIn("replaceChildren", self.js_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
