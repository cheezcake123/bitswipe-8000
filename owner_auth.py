from __future__ import annotations

import hashlib
import hmac
import html
import os
import time
from collections import defaultdict, deque
from urllib.parse import parse_qs, quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


OWNER_SESSION_COOKIE = "bitswipe_owner_session"
OWNER_LOGIN_PATH = "/owner/login"
OWNER_LOGOUT_PATH = "/owner/logout"
PRIVATE_ACCOUNT_PATH = "/assets/decision-account-preview.html"
PROTECTED_OWNER_PATHS = frozenset({
    PRIVATE_ACCOUNT_PATH,
    "/api/account-stream",
    "/api/account",
    "/api/performance",
})
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_SESSION_TTL_SECONDS = 24 * 60 * 60
MAX_LOGIN_BODY_BYTES = 4096
LOGIN_FAILURE_WINDOW_SECONDS = 5 * 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_SECONDS = 10 * 60
_TOKEN_VERSION = "v1"
_TOKEN_CONTEXT = "bitswipe-owner-session"

_failed_logins: dict[str, deque[float]] = defaultdict(deque)
_locked_until: dict[str, float] = {}


def _session_ttl_seconds() -> int:
    raw = os.getenv("BITSWIPE_OWNER_SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL_SECONDS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_SESSION_TTL_SECONDS
    return max(5 * 60, min(MAX_SESSION_TTL_SECONDS, value))


def _client_key(request: Request) -> str:
    # Do not trust X-Forwarded-For here. A reverse proxy can normalize request.client
    # when configured; spoofable forwarding headers must not control the lock bucket.
    return (request.client.host if request.client else "unknown") or "unknown"


def _prune_failures(client_key: str, now: float) -> deque[float]:
    bucket = _failed_logins[client_key]
    cutoff = now - LOGIN_FAILURE_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if not bucket:
        _failed_logins.pop(client_key, None)
        bucket = _failed_logins[client_key]
    return bucket


def _login_locked(request: Request) -> int:
    now = time.time()
    key = _client_key(request)
    until = _locked_until.get(key, 0.0)
    if until <= now:
        _locked_until.pop(key, None)
        return 0
    return max(1, int(until - now))


def _record_login_failure(request: Request) -> None:
    now = time.time()
    key = _client_key(request)
    bucket = _prune_failures(key, now)
    bucket.append(now)
    if len(bucket) >= LOGIN_FAILURE_LIMIT:
        _locked_until[key] = now + LOGIN_LOCK_SECONDS
        bucket.clear()


def _clear_login_failures(request: Request) -> None:
    key = _client_key(request)
    _failed_logins.pop(key, None)
    _locked_until.pop(key, None)


def _token_signature(expires_at: int, owner_password: str) -> str:
    payload = f"{_TOKEN_CONTEXT}:{_TOKEN_VERSION}:{expires_at}".encode("utf-8")
    return hmac.new(owner_password.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def issue_owner_session(runtime_config) -> tuple[str, int]:
    if not runtime_config.owner_password_configured():
        raise RuntimeError("OWNER_PASSWORD is not configured")
    ttl = _session_ttl_seconds()
    expires_at = int(time.time()) + ttl
    signature = _token_signature(expires_at, runtime_config.OWNER_PASSWORD)
    return f"{_TOKEN_VERSION}.{expires_at}.{signature}", ttl


def verify_owner_session(token: object, runtime_config) -> bool:
    if not runtime_config.owner_password_configured() or not isinstance(token, str):
        return False
    try:
        version, raw_expiry, supplied_signature = token.split(".", 2)
        expires_at = int(raw_expiry)
    except (TypeError, ValueError):
        return False
    if version != _TOKEN_VERSION or expires_at < int(time.time()):
        return False
    # Reject implausibly long-lived tokens even if a future implementation mistake
    # signs one. Current sessions are capped at 24 hours.
    if expires_at > int(time.time()) + MAX_SESSION_TTL_SECONDS + 60:
        return False
    expected = _token_signature(expires_at, runtime_config.OWNER_PASSWORD)
    return hmac.compare_digest(supplied_signature.encode("ascii", "ignore"), expected.encode("ascii"))


def _safe_next(raw: object) -> str:
    value = str(raw or "").strip()
    return PRIVATE_ACCOUNT_PATH if value != PRIVATE_ACCOUNT_PATH else value


def _secure_cookie(request: Request) -> bool:
    if request.url.scheme.lower() == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return forwarded == "https"


def _login_page(next_path: str, *, error: str = "", status_code: int = 200) -> HTMLResponse:
    safe_next = html.escape(_safe_next(next_path), quote=True)
    safe_error = html.escape(error, quote=False)
    error_block = f'<p class="error" role="alert">{safe_error}</p>' if safe_error else ""
    body = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>Owner Login</title>
<style>
:root{{color-scheme:light;font-family:Inter,Pretendard,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f5f7;color:#1b2028;padding:24px}}
main{{width:min(100%,420px);background:#fff;border:1px solid #dde1e7;border-radius:18px;padding:28px;box-shadow:0 16px 48px rgba(32,36,45,.08)}}
.kicker{{font-size:12px;letter-spacing:.08em;color:#626a77;font-weight:700}}h1{{margin:8px 0 10px;font-size:25px}}p{{color:#626a77;line-height:1.55}}label{{display:grid;gap:8px;margin-top:22px;font-weight:700}}input{{width:100%;min-height:46px;border:1px solid #c9ced6;border-radius:10px;padding:10px 12px;font:inherit}}button{{width:100%;min-height:46px;margin-top:14px;border:0;border-radius:10px;background:#485cc7;color:#fff;font:inherit;font-weight:800;cursor:pointer}}.error{{background:#fff4f2;border:1px solid #e7b8b0;color:#8b3026;padding:10px 12px;border-radius:10px}}small{{display:block;margin-top:16px;color:#8b929d;line-height:1.5}}
</style>
</head>
<body><main>
<div class="kicker">OWNER AUTHENTICATION</div>
<h1>Private Account 로그인</h1>
<p>계좌·포지션·손익 데이터는 소유자 세션이 있어야 열립니다.</p>
{error_block}
<form method="post" action="{OWNER_LOGIN_PATH}">
<input type="hidden" name="next" value="{safe_next}">
<label>OWNER_PASSWORD
<input type="password" name="password" autocomplete="current-password" required autofocus maxlength="512">
</label>
<button type="submit">소유자 세션 시작</button>
</form>
<small>비밀번호는 URL에 넣지 않으며, 성공 시 비밀번호 대신 서명된 HttpOnly 세션 쿠키만 저장합니다.</small>
</main></body></html>"""
    return HTMLResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})


def install_owner_security(app, runtime_config) -> None:
    """Install owner-only session gates without changing public Decision routes."""

    @app.middleware("http")
    async def owner_session_guard(request: Request, call_next):
        path = request.url.path
        if path not in PROTECTED_OWNER_PATHS:
            return await call_next(request)

        if not runtime_config.owner_password_configured():
            if path == PRIVATE_ACCOUNT_PATH:
                return _login_page(
                    PRIVATE_ACCOUNT_PATH,
                    error="서버에 OWNER_PASSWORD가 설정되지 않아 Private Account가 잠겨 있습니다.",
                    status_code=503,
                )
            return JSONResponse(
                {"detail": "OWNER_PASSWORD is not configured; private account access is disabled."},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        token = request.cookies.get(OWNER_SESSION_COOKIE)
        if not verify_owner_session(token, runtime_config):
            if path == PRIVATE_ACCOUNT_PATH:
                target = quote(PRIVATE_ACCOUNT_PATH, safe="/")
                return RedirectResponse(f"{OWNER_LOGIN_PATH}?next={target}", status_code=303)
            return JSONResponse(
                {"detail": "Owner authentication required."},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get(OWNER_LOGIN_PATH, include_in_schema=False)
    async def owner_login(request: Request):
        next_path = _safe_next(request.query_params.get("next"))
        if verify_owner_session(request.cookies.get(OWNER_SESSION_COOKIE), runtime_config):
            return RedirectResponse(next_path, status_code=303)
        if not runtime_config.owner_password_configured():
            return _login_page(
                next_path,
                error="서버에 OWNER_PASSWORD가 설정되지 않아 로그인할 수 없습니다.",
                status_code=503,
            )
        locked = _login_locked(request)
        if locked:
            return _login_page(next_path, error=f"로그인 시도가 잠겼습니다. 약 {locked}초 뒤 다시 시도하세요.", status_code=429)
        return _login_page(next_path)

    @app.post(OWNER_LOGIN_PATH, include_in_schema=False)
    async def owner_login_submit(request: Request):
        next_path = PRIVATE_ACCOUNT_PATH
        content_length = request.headers.get("content-length")
        try:
            if content_length and int(content_length) > MAX_LOGIN_BODY_BYTES:
                return _login_page(next_path, error="로그인 요청이 너무 큽니다.", status_code=413)
        except ValueError:
            return _login_page(next_path, error="잘못된 로그인 요청입니다.", status_code=400)

        body = await request.body()
        if len(body) > MAX_LOGIN_BODY_BYTES:
            return _login_page(next_path, error="로그인 요청이 너무 큽니다.", status_code=413)
        fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        password = (fields.get("password") or [""])[0]
        next_path = _safe_next((fields.get("next") or [PRIVATE_ACCOUNT_PATH])[0])

        if not runtime_config.owner_password_configured():
            return _login_page(next_path, error="서버에 OWNER_PASSWORD가 설정되지 않아 로그인할 수 없습니다.", status_code=503)

        locked = _login_locked(request)
        if locked:
            return _login_page(next_path, error=f"로그인 시도가 잠겼습니다. 약 {locked}초 뒤 다시 시도하세요.", status_code=429)

        if not runtime_config.verify_owner_password(password):
            _record_login_failure(request)
            return _login_page(next_path, error="OWNER_PASSWORD가 일치하지 않습니다.", status_code=401)

        _clear_login_failures(request)
        token, ttl = issue_owner_session(runtime_config)
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            OWNER_SESSION_COOKIE,
            token,
            max_age=ttl,
            httponly=True,
            secure=_secure_cookie(request),
            samesite="strict",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post(OWNER_LOGOUT_PATH, include_in_schema=False)
    async def owner_logout():
        response = RedirectResponse(OWNER_LOGIN_PATH, status_code=303)
        response.delete_cookie(OWNER_SESSION_COOKIE, path="/")
        response.headers["Cache-Control"] = "no-store"
        return response


def arm_fastapi_owner_security(runtime_config) -> None:
    """Attach owner security to the next FastAPI app created after config import.

    server.py imports FastAPI before config and creates its app only after config has
    loaded. Patching the already-imported class constructor here keeps server.py byte-
    identical while installing the middleware exactly once per FastAPI instance.
    """
    from fastapi import FastAPI

    if getattr(FastAPI, "_bitswipe_owner_security_armed", False):
        return

    original_init = FastAPI.__init__

    def secured_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if not getattr(self.state, "bitswipe_owner_security_installed", False):
            install_owner_security(self, runtime_config)
            self.state.bitswipe_owner_security_installed = True

    FastAPI.__init__ = secured_init
    FastAPI._bitswipe_owner_security_armed = True
