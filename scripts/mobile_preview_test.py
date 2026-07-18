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
MACRO_PATH = ROOT / "static" / "assets" / "mobile-preview-macro-adapter.js"
DECISION_HTML_PATH = ROOT / "static" / "assets" / "decision-preview.html"
DECISION_CSS_PATH = ROOT / "static" / "assets" / "decision-preview.css"
DECISION_JS_PATH = ROOT / "static" / "assets" / "decision-preview.js"
JOURNAL_HTML_PATH = ROOT / "static" / "assets" / "decision-journal-preview.html"
JOURNAL_CSS_PATH = ROOT / "static" / "assets" / "decision-journal-preview.css"
JOURNAL_JS_PATH = ROOT / "static" / "assets" / "decision-journal-preview.js"
ACCOUNT_HTML_PATH = ROOT / "static" / "assets" / "decision-account-preview.html"
ACCOUNT_CSS_PATH = ROOT / "static" / "assets" / "decision-account-preview.css"
ACCOUNT_JS_PATH = ROOT / "static" / "assets" / "decision-account-preview.js"

BASE_COMMIT = "6d39043ab121bdbd2900dffe481c86f167946d8c"
JOURNAL_BASE = "0303280a9694f9fd71a90a0dd11a5bd73aac556d"
PUBLIC_SHELL_BASE = "39b106573cb91961ea87947cc8f37ff151ef741e"
PERSONAL_CONTEXT_BASE = "0ee1f51d23e7d4c754aea9e65248ac7d6dc15384"
SECURITY_BASE = "1b63d76eacb2e3216200168c9988bf1b534638a7"

ALLOWED_DIFFS = {
    "config.py", "owner_auth.py", "scripts/mobile_preview_test.py", "scripts/public_beta_security_test.py",
    "static/mobile-preview.html", "static/assets/mobile-preview.css", "static/assets/mobile-preview.js",
    "static/assets/mobile-preview-foundation.js", "static/assets/mobile-preview-macro-adapter.js",
    "static/assets/decision-preview.html", "static/assets/decision-preview.css", "static/assets/decision-preview.js",
    "static/assets/decision-journal-preview.html", "static/assets/decision-journal-preview.css", "static/assets/decision-journal-preview.js",
    "static/assets/decision-account-preview.html", "static/assets/decision-account-preview.css", "static/assets/decision-account-preview.js",
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
    return node.id if isinstance(node, ast.Name) else ""


def route_path(node: ast.AsyncFunctionDef, method: str) -> str | None:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and call_name(decorator.func) == f"app.{method}":
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                return decorator.args[0].value
    return None


def git_output(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def changed_since(commit: str) -> set[str]:
    return set(git_output("diff", "--name-only", commit, "--").decode().splitlines())


class FrontendPreviewSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = SERVER_PATH.read_text(encoding="utf-8")
        cls.mobile_html = HTML_PATH.read_text(encoding="utf-8")
        cls.mobile_css = CSS_PATH.read_text(encoding="utf-8")
        cls.mobile_js = JS_PATH.read_text(encoding="utf-8")
        cls.foundation = FOUNDATION_PATH.read_text(encoding="utf-8")
        cls.macro = MACRO_PATH.read_text(encoding="utf-8")
        cls.decision_html = DECISION_HTML_PATH.read_text(encoding="utf-8")
        cls.decision_css = DECISION_CSS_PATH.read_text(encoding="utf-8")
        cls.decision_js = DECISION_JS_PATH.read_text(encoding="utf-8")
        cls.journal_html = JOURNAL_HTML_PATH.read_text(encoding="utf-8")
        cls.journal_css = JOURNAL_CSS_PATH.read_text(encoding="utf-8")
        cls.journal_js = JOURNAL_JS_PATH.read_text(encoding="utf-8")
        cls.account_html = ACCOUNT_HTML_PATH.read_text(encoding="utf-8")
        cls.account_css = ACCOUNT_CSS_PATH.read_text(encoding="utf-8")
        cls.account_js = ACCOUNT_JS_PATH.read_text(encoding="utf-8")
        cls.server_tree = ast.parse(cls.server)
        cls.parsers = {}
        for key, text in {
            "mobile": cls.mobile_html, "decision": cls.decision_html,
            "journal": cls.journal_html, "account": cls.account_html,
        }.items():
            parser = PreviewHTMLParser(); parser.feed(text); cls.parsers[key] = parser
        cls.page_text = {key: " ".join(parser.text) for key, parser in cls.parsers.items()}

    def test_01_exact_version_boundaries_and_unchanged_server(self) -> None:
        self.assertEqual(changed_since(BASE_COMMIT), ALLOWED_DIFFS)
        self.assertEqual(changed_since(SECURITY_BASE), {
            "config.py", "owner_auth.py", "scripts/mobile_preview_test.py",
            "scripts/public_beta_security_test.py", "static/assets/decision-account-preview.html",
        })
        self.assertEqual(changed_since(PERSONAL_CONTEXT_BASE), {
            "config.py", "owner_auth.py", "scripts/mobile_preview_test.py", "scripts/public_beta_security_test.py",
            "static/assets/decision-account-preview.html", "static/assets/decision-account-preview.css",
            "static/assets/decision-account-preview.js",
        })
        self.assertEqual(changed_since(PUBLIC_SHELL_BASE), {
            "config.py", "owner_auth.py", "scripts/mobile_preview_test.py", "scripts/public_beta_security_test.py",
            "static/assets/mobile-preview-foundation.js", "static/assets/decision-account-preview.html",
            "static/assets/decision-account-preview.css", "static/assets/decision-account-preview.js",
        })
        self.assertEqual(changed_since(JOURNAL_BASE), {
            "config.py", "owner_auth.py", "scripts/mobile_preview_test.py", "scripts/public_beta_security_test.py",
            "static/assets/mobile-preview-foundation.js", "static/assets/decision-journal-preview.html",
            "static/assets/decision-journal-preview.css", "static/assets/decision-journal-preview.js",
            "static/assets/decision-account-preview.html", "static/assets/decision-account-preview.css",
            "static/assets/decision-account-preview.js",
        })
        for path in ("server.py", "static/index.html", "static/assets/bitswipe.css"):
            self.assertEqual(
                git_output("rev-parse", f"{SECURITY_BASE}:{path}").strip(),
                git_output("hash-object", path).strip(),
                f"{path} changed in security v1",
            )

    def test_02_root_mobile_and_static_mount_contract(self) -> None:
        functions = [n for n in self.server_tree.body if isinstance(n, ast.AsyncFunctionDef)]
        routes = {route_path(n, "get"): n for n in functions if route_path(n, "get")}
        self.assertIn("/", routes); self.assertIn("/mobile-preview", routes)
        self.assertIn("mobile-preview.html", ast.get_source_segment(self.server, routes["/mobile-preview"]) or "")
        mounts = [n for n in ast.walk(self.server_tree) if isinstance(n, ast.Call) and call_name(n.func) == "app.mount"]
        self.assertEqual(len(mounts), 1); self.assertEqual(mounts[0].args[0].value, "/assets")

    def test_03_mobile_read_only_demo_privacy_and_stream_contract(self) -> None:
        parser = self.parsers["mobile"]
        self.assertEqual([x.get("href") for x in parser.attrs_for("link") if x.get("rel") == "stylesheet"], ["/assets/mobile-preview.css"])
        self.assertEqual([x.get("src") for x in parser.attrs_for("script") if x.get("src")], [
            "/assets/mobile-preview-foundation.js", "/assets/mobile-preview-macro-adapter.js", "/assets/mobile-preview.js",
        ])
        self.assertEqual(re.findall(r'new\s+EventSource\(\s*["\']([^"\']+)["\']\s*\)', self.mobile_js), ["/api/market-stream", "/api/account-stream"])
        self.assertNotRegex(self.mobile_js, r'method\s*:\s*["\'](?:POST|PUT|PATCH|DELETE)["\']')
        init = re.search(r"function initialize\(\) \{(?P<body>.*?)\n  \}\n\n  initialize\(\);", self.mobile_js, re.DOTALL)
        self.assertIsNotNone(init); body = init.group("body"); branch = body.index("if (demoMode)"); ret = body.index("return;", branch)
        self.assertLess(ret, body.index("connectMarketStream()")); self.assertLess(ret, body.index("loadLiveReads()"))
        self.assertNotIn("JSON.stringify(state.account", self.mobile_js)
        self.assertGreaterEqual(self.page_text["mobile"].count("••••••"), 5)

    def test_04_mobile_decision_semantics_and_accessibility(self) -> None:
        grade = re.search(r"function scoreGrade\(score\) \{(?P<body>.*?)\n  \}", self.mobile_js, re.DOTALL)
        self.assertIsNotNone(grade); compact = re.sub(r"\s+", " ", grade.group("body"))
        for fragment in ('score >= 85) return "A"', 'score >= 75) return "B+"', 'score >= 65) return "B"', 'score >= 50) return "C"'):
            self.assertIn(fragment, compact)
        leverage = re.search(r"const leverage = finiteNumber\(([^)]+)\)", self.mobile_js)
        self.assertEqual([x.strip() for x in leverage.group(1).split(",")], ["trade.leverage", "raw.claude_leverage", "riskGuard.leverage"])
        for label in ("분석 신선도", "가격 트리거", "추세 구조", "모멘텀", "거래량 참여", "리스크 가드", "계좌 리스크 적합도"):
            self.assertIn(label, self.mobile_js)
        self.assertIn("/api/analysis-history?limit=50", self.mobile_js)
        self.assertNotRegex(self.mobile_js, r"entries\.(?:filter|find)\([^)]*BTC")
        self.assertRegex(self.mobile_css, r"@media\s*\(max-width:\s*390px\)")
        self.assertIn("env(safe-area-inset-bottom)", self.mobile_css)
        self.assertTrue(self.parsers["mobile"].attrs_for("canvas")[0].get("aria-label"))

    def test_05_macro_and_foundation_contracts(self) -> None:
        for fragment in ('hasOwnProperty.call(raw, "_trad_markets")', "normalized.trad_markets = asObject(raw._trad_markets)", "ibit.change24h"):
            self.assertIn(fragment, self.macro)
        self.assertNotRegex(self.macro, r'method\s*:\s*["\'](?:POST|PUT|PATCH|DELETE)["\']')
        for capability in ("market.data", "analysis", "candidates", "scenarios", "account.balances", "account.positions", "account.context"):
            self.assertIn(f'"{capability}"', self.foundation)
        self.assertIn("zeroIsValue: true", self.foundation)
        self.assertNotRegex(self.foundation, r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b")
        self.assertNotRegex(self.foundation, r"\b(?:fetch|EventSource|XMLHttpRequest|sendBeacon|WebSocket)\s*\(")

    def test_06_dynamic_renderers_avoid_inner_html(self) -> None:
        for text in (self.mobile_js, self.foundation, self.macro, self.decision_js, self.journal_js, self.account_js):
            self.assertNotIn("innerHTML", text)
        for text in (self.mobile_js, self.decision_js, self.journal_js, self.account_js):
            self.assertIn("replaceChildren", text)

    def test_07_public_decision_network_demo_navigation_and_layout(self) -> None:
        parser = self.parsers["decision"]
        self.assertEqual([x.get("src") for x in parser.attrs_for("script") if x.get("src")], [
            "/assets/mobile-preview-foundation.js", "/assets/mobile-preview-macro-adapter.js", "/assets/decision-preview.js",
        ])
        allowed = re.search(r"const ALLOWED_READS\s*=\s*new Set\(\[(?P<body>.*?)\]\);", self.decision_js, re.DOTALL)
        self.assertEqual(set(re.findall(r'"(/api/[^"\']+)"', allowed.group("body"))), {
            "/api/analyze?include_latest=true", "/api/analysis-history?limit=50", "/api/macro", "/api/symbol",
        })
        self.assertNotIn("/api/account-stream", self.decision_js)
        for action in ("WAIT", "WATCH", "PREPARE", "READY", "MANAGE", "EXIT", "INVALIDATED"):
            self.assertIn(f'"{action}"', self.decision_js)
        for label in ("Market", "Analysis", "Setups", "Journal"):
            self.assertIn(label, self.page_text["decision"])
        self.assertNotIn("Account", self.page_text["decision"])
        self.assertIn("224px minmax(0, 824px) 280px", self.decision_css)
        self.assertNotRegex(self.decision_js, r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b")
        self.assertNotRegex(self.decision_js, r"\b(?:placeOrder|submitOrder|cancelOrder|closePosition|setLeverage)\s*\(")

    def test_08_journal_public_read_only_truthfulness_and_layout(self) -> None:
        parser = self.parsers["journal"]
        self.assertEqual([x.get("src") for x in parser.attrs_for("script") if x.get("src")], [
            "/assets/mobile-preview-foundation.js", "/assets/decision-journal-preview.js",
        ])
        allowed = re.search(r"const ALLOWED_READS\s*=\s*new Set\(\[(?P<body>.*?)\]\);", self.journal_js, re.DOTALL)
        self.assertEqual(set(re.findall(r'"(/api/[^"\']+)"', allowed.group("body"))), {
            "/api/analyze?include_latest=true", "/api/analysis-history?limit=50",
        })
        self.assertNotIn("EventSource", self.journal_js); self.assertNotIn("/api/account", self.journal_js)
        for text in ("결과", "미확인", "실제 진입 여부", "실현 손익", "승률", "결과를 추정하거나 성공·실패로 분류하지 않습니다"):
            self.assertIn(text, self.page_text["journal"])
        self.assertNotRegex(self.journal_js, r"(?i)win[_ -]?rate|realized[_ -]?pnl|trade[_ -]?result")
        self.assertNotRegex(self.journal_js, r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b")
        self.assertIn("224px minmax(0, 824px) 280px", self.journal_css)

    def test_09_private_account_scope_and_public_separation(self) -> None:
        parser = self.parsers["account"]
        self.assertEqual([x.get("src") for x in parser.attrs_for("script") if x.get("src")], [
            "/assets/mobile-preview-foundation.js", "/assets/decision-account-preview.js",
        ])
        self.assertTrue([attrs for _, attrs in parser.tags if attrs.get("data-capability-scope") == "private"])
        self.assertIn('foundation.capabilities.canAccess("private")', self.account_js)
        for public_text in (self.decision_html, self.journal_html, self.foundation):
            self.assertNotIn("decision-account-preview", public_text)

    def test_10_private_account_network_demo_and_storage_boundary(self) -> None:
        reads = re.search(r"const ALLOWED_READS\s*=\s*new Set\(\[(?P<body>.*?)\]\);", self.account_js, re.DOTALL)
        streams = re.search(r"const ALLOWED_STREAMS\s*=\s*new Set\(\[(?P<body>.*?)\]\);", self.account_js, re.DOTALL)
        self.assertEqual(set(re.findall(r'"(/api/[^"\']+)"', reads.group("body"))), {
            "/api/analyze?include_latest=true", "/api/analysis-history?limit=50",
        })
        self.assertEqual(set(re.findall(r'"(/api/[^"\']+)"', streams.group("body"))), {"/api/account-stream"})
        self.assertNotIn("/api/market-stream", self.account_js)
        init = re.search(r"function initialize\(\) \{(?P<body>.*?)\n  \}\n\n  window\.addEventListener", self.account_js, re.DOTALL)
        self.assertIsNotNone(init); body = init.group("body"); branch = body.index("if (demoMode)"); ret = body.index("return;", branch)
        self.assertLess(ret, body.index("connectAccountStream()")); self.assertLess(ret, body.index("loadAnalysisReads()"))
        self.assertIn("privacyHidden: true", self.account_js)
        self.assertNotRegex(self.account_js, r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b")
        self.assertNotRegex(self.account_js, r'method\s*:\s*"(?:POST|PUT|PATCH|DELETE)"')
        self.assertNotRegex(self.account_js, r"\b(?:placeOrder|submitOrder|cancelOrder|closePosition|setLeverage)\s*\(")

    def test_11_private_account_scenario_link_and_auth_copy(self) -> None:
        for fragment in ("architecture.buildViewModel", "viewModel.private", "latestScenarioForPosition", "positionAction", 'return "EXIT"', 'return "MANAGE"'):
            self.assertIn(fragment, self.account_js)
        for text in ("POSITION → SCENARIO", "동일 심볼의 최신 분석", "OWNER SESSION PROTECTED", "OWNER_PASSWORD", "OWNER 세션 보호 활성", "HttpOnly", "/api/account-stream", "/api/account", "/api/performance", "HTTPS 전송 보호"):
            self.assertIn(text, self.page_text["account"])
        self.assertNotIn("인증이 아직 없습니다", self.page_text["account"])
        self.assertGreaterEqual(self.page_text["account"].count("••••••"), 5)

    def test_12_private_account_responsive_accessibility(self) -> None:
        self.assertRegex(self.account_css, r"@media\s*\(max-width:\s*390px\)")
        self.assertRegex(self.account_css, r"@media\s*\(min-width:\s*1200px\)")
        self.assertIn("224px minmax(0, 824px) 280px", self.account_css)
        self.assertIn("min-height: 44px", self.account_css)
        self.assertIn("env(safe-area-inset-bottom)", self.account_css)
        self.assertRegex(self.account_css, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
        viewport = next(x for x in self.parsers["account"].attrs_for("meta") if x.get("name") == "viewport")
        self.assertNotIn("user-scalable=no", viewport.get("content", ""))

    def test_13_tradfi_remains_context_not_candidate(self) -> None:
        self.assertIn("전통시장 컨텍스트", self.page_text["mobile"])
        self.assertIn("매매 후보 아님", self.page_text["mobile"])
        self.assertIn("rawCandidates.push.apply(rawCandidates, entries)", self.mobile_js)

    def test_14_scenario_grade_is_not_win_rate(self) -> None:
        self.assertIn("시나리오 명확도", self.page_text["mobile"])
        self.assertIn("수익이나 승률을 보장하지 않습니다", self.page_text["mobile"])
        self.assertIn("Scenario Quality", self.page_text["decision"])
        self.assertIn("승률 아님", self.page_text["decision"])
        self.assertNotIn("예상 승률", self.page_text["mobile"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
