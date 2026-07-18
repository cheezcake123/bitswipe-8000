#!/usr/bin/env python3
"""Offline and import-level checks for Public Beta owner-session protection."""

from __future__ import annotations

import hmac
from pathlib import Path
import os
import subprocess
import sys
import time
import unittest

import owner_auth


ROOT = Path(__file__).resolve().parents[1]
SECURITY_BASE_COMMIT = "1b63d76eacb2e3216200168c9988bf1b534638a7"
SERVER_PATH = ROOT / "server.py"
CONFIG_PATH = ROOT / "config.py"
AUTH_PATH = ROOT / "owner_auth.py"
ACCOUNT_HTML_PATH = ROOT / "static" / "assets" / "decision-account-preview.html"
DECISION_HTML_PATH = ROOT / "static" / "assets" / "decision-preview.html"
JOURNAL_HTML_PATH = ROOT / "static" / "assets" / "decision-journal-preview.html"


class FakeConfig:
    OWNER_PASSWORD = "owner-test-secret"

    @classmethod
    def owner_password_configured(cls) -> bool:
        return bool(cls.OWNER_PASSWORD) and cls.OWNER_PASSWORD != "changeme"

    @classmethod
    def verify_owner_password(cls, supplied: object) -> bool:
        if not cls.owner_password_configured() or not isinstance(supplied, str):
            return False
        return hmac.compare_digest(supplied.encode(), cls.OWNER_PASSWORD.encode())


class PublicBetaSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server_text = SERVER_PATH.read_text(encoding="utf-8")
        cls.config_text = CONFIG_PATH.read_text(encoding="utf-8")
        cls.auth_text = AUTH_PATH.read_text(encoding="utf-8")
        cls.account_html = ACCOUNT_HTML_PATH.read_text(encoding="utf-8")
        cls.decision_html = DECISION_HTML_PATH.read_text(encoding="utf-8")
        cls.journal_html = JOURNAL_HTML_PATH.read_text(encoding="utf-8")

    def test_01_protected_owner_paths_are_exact(self) -> None:
        self.assertEqual(
            owner_auth.PROTECTED_OWNER_PATHS,
            frozenset({
                "/assets/decision-account-preview.html",
                "/api/account-stream",
                "/api/account",
                "/api/performance",
            }),
        )

    def test_02_session_token_round_trip_and_tamper_rejection(self) -> None:
        token, ttl = owner_auth.issue_owner_session(FakeConfig)
        self.assertGreaterEqual(ttl, 300)
        self.assertTrue(owner_auth.verify_owner_session(token, FakeConfig))
        version, expiry, signature = token.split(".", 2)
        tampered = f"{version}.{expiry}.{signature[:-1]}{'0' if signature[-1] != '0' else '1'}"
        self.assertFalse(owner_auth.verify_owner_session(tampered, FakeConfig))

    def test_03_expired_or_wrong_password_tokens_are_rejected(self) -> None:
        expired_at = int(time.time()) - 1
        expired = f"v1.{expired_at}.{owner_auth._token_signature(expired_at, FakeConfig.OWNER_PASSWORD)}"
        self.assertFalse(owner_auth.verify_owner_session(expired, FakeConfig))

        token, _ = owner_auth.issue_owner_session(FakeConfig)

        class ChangedConfig(FakeConfig):
            OWNER_PASSWORD = "different-owner-secret"

        self.assertFalse(owner_auth.verify_owner_session(token, ChangedConfig))

    def test_04_unconfigured_password_fails_closed(self) -> None:
        class Unconfigured(FakeConfig):
            OWNER_PASSWORD = "changeme"

        self.assertFalse(Unconfigured.owner_password_configured())
        self.assertFalse(owner_auth.verify_owner_session("v1.0.invalid", Unconfigured))
        with self.assertRaises(RuntimeError):
            owner_auth.issue_owner_session(Unconfigured)

    def test_05_redirect_target_is_allowlisted(self) -> None:
        private_path = "/assets/decision-account-preview.html"
        self.assertEqual(owner_auth._safe_next(private_path), private_path)
        self.assertEqual(owner_auth._safe_next("https://example.com/steal"), private_path)
        self.assertEqual(owner_auth._safe_next("//example.com"), private_path)
        self.assertEqual(owner_auth._safe_next("/api/account"), private_path)

    def test_06_cookie_and_cache_security_contract(self) -> None:
        for fragment in (
            "httponly=True",
            'samesite="strict"',
            '"Cache-Control": "no-store"',
            "secure=_secure_cookie(request)",
            "response.delete_cookie",
        ):
            self.assertIn(fragment, self.auth_text)
        self.assertNotIn("WWW-Authenticate", self.auth_text)
        self.assertNotIn("password=", self.auth_text)

    def test_07_login_uses_post_body_and_constant_time_verification(self) -> None:
        self.assertIn('@app.post(OWNER_LOGIN_PATH', self.auth_text)
        self.assertIn("await request.body()", self.auth_text)
        self.assertIn("parse_qs", self.auth_text)
        self.assertIn("runtime_config.verify_owner_password(password)", self.auth_text)
        self.assertIn("hmac.compare_digest", self.auth_text)
        self.assertIn("MAX_LOGIN_BODY_BYTES", self.auth_text)
        self.assertIn("LOGIN_FAILURE_LIMIT", self.auth_text)

    def test_08_security_bootstrap_order_matches_server_import_order(self) -> None:
        fastapi_import = self.server_text.index("from fastapi import FastAPI")
        config_import = self.server_text.index("import config as runtime_config")
        app_create = self.server_text.index("app = FastAPI()")
        self.assertLess(fastapi_import, config_import)
        self.assertLess(config_import, app_create)
        self.assertIn("arm_fastapi_owner_security", self.config_text)
        self.assertIn("_arm_owner_http_security()", self.config_text)
        self.assertIn("_bitswipe_owner_security_armed", self.auth_text)
        self.assertIn("bitswipe_owner_security_installed", self.auth_text)

    def test_09_server_body_and_public_shell_files_are_unchanged(self) -> None:
        expected_server_blob = subprocess.check_output(
            ["git", "rev-parse", f"{SECURITY_BASE_COMMIT}:server.py"], cwd=ROOT
        ).strip()
        current_server_blob = subprocess.check_output(["git", "hash-object", "server.py"], cwd=ROOT).strip()
        self.assertEqual(expected_server_blob, current_server_blob)
        self.assertNotIn("decision-account-preview", self.decision_html)
        self.assertNotIn("decision-account-preview", self.journal_html)

    def test_10_private_page_copy_matches_active_auth_boundary(self) -> None:
        for text in (
            "OWNER SESSION PROTECTED",
            "OWNER_PASSWORD",
            "OWNER 세션 보호 활성",
            "HttpOnly",
            "/api/account-stream",
            "/api/account",
            "/api/performance",
            "HTTPS 전송 보호",
        ):
            self.assertIn(text, self.account_html)
        self.assertNotIn("인증이 아직 없습니다", self.account_html)

    def test_11_auth_module_has_no_trading_or_scenario_ledger_side_effects(self) -> None:
        for forbidden in (
            "placeOrder",
            "place_order",
            "submitOrder",
            "cancelOrder",
            "closePosition",
            "setLeverage",
            "SCENARIO_LEDGER_ALERT_REGISTRATION_ENABLED=1",
        ):
            self.assertNotIn(forbidden, self.auth_text)

    def test_12_server_import_installs_owner_routes_without_starting_lifespan(self) -> None:
        env = os.environ.copy()
        env["OWNER_PASSWORD"] = "security-import-test-secret"
        env["SCENARIO_LEDGER_ALERT_REGISTRATION_ENABLED"] = "0"
        code = (
            "import server; "
            "paths={getattr(r,'path',None) for r in server.app.routes}; "
            "assert '/owner/login' in paths; "
            "assert '/owner/logout' in paths; "
            "assert server.app.state.bitswipe_owner_security_installed is True; "
            "print('OWNER AUTH BOOTSTRAP: OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OWNER AUTH BOOTSTRAP: OK", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
