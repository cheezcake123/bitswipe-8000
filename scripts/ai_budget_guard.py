
import json

import os

import time

from datetime import datetime, timezone

from pathlib import Path





ROOT = Path(__file__).resolve().parents[1]

STATE_PATH = ROOT / "data" / "ai_budget_guard.json"

ENV_PATH = ROOT / ".env"



DEFAULT_MIN_INTERVAL_SECONDS = 21600

DEFAULT_DAILY_LIMIT = 3

DEFAULT_MONTHLY_LIMIT = 70





def _load_env_file():

    env = {}



    try:

        if not ENV_PATH.exists():

            return env



        for line in ENV_PATH.read_text().splitlines():

            line = line.strip()



            if not line or line.startswith("#") or "=" not in line:

                continue



            key, value = line.split("=", 1)

            env[key.strip()] = value.strip()



    except Exception:

        return {}



    return env





def _env_int(name, default):

    file_env = _load_env_file()

    raw = os.getenv(name) or file_env.get(name)



    try:

        if raw:

            return int(raw)

    except Exception:

        pass



    return default





def limits():

    return {

        "min_interval_seconds": _env_int("AI_AUTO_MIN_INTERVAL_SECONDS", DEFAULT_MIN_INTERVAL_SECONDS),

        "daily_limit": _env_int("AI_AUTO_DAILY_LIMIT", DEFAULT_DAILY_LIMIT),

        "monthly_limit": _env_int("AI_AUTO_MONTHLY_LIMIT", DEFAULT_MONTHLY_LIMIT),

    }





def _now_parts():

    now = datetime.now(timezone.utc)

    return now, now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")





def load_state():

    try:

        if not STATE_PATH.exists():

            return {}



        return json.loads(STATE_PATH.read_text())



    except Exception:

        return {}





def save_state(state):

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=True, indent=2))





def can_run_auto_ai():

    state = load_state()

    lim = limits()



    now, day, month = _now_parts()

    now_ts = time.time()



    last_run_at = float(state.get("last_run_at", 0) or 0)

    elapsed = now_ts - last_run_at



    daily_key = f"daily:{day}"

    monthly_key = f"monthly:{month}"



    daily_count = int(state.get(daily_key, 0) or 0)

    monthly_count = int(state.get(monthly_key, 0) or 0)



    if last_run_at > 0 and elapsed < lim["min_interval_seconds"]:

        return {

            "allowed": False,

            "reason": "min_interval_not_elapsed",

            "elapsed_seconds": round(elapsed, 1),

            "required_seconds": lim["min_interval_seconds"],

            "daily_count": daily_count,

            "monthly_count": monthly_count,

            "limits": lim,

        }



    if daily_count >= lim["daily_limit"]:

        return {

            "allowed": False,

            "reason": "daily_limit_reached",

            "daily_count": daily_count,

            "monthly_count": monthly_count,

            "limits": lim,

        }



    if monthly_count >= lim["monthly_limit"]:

        return {

            "allowed": False,

            "reason": "monthly_limit_reached",

            "daily_count": daily_count,

            "monthly_count": monthly_count,

            "limits": lim,

        }



    return {

        "allowed": True,

        "reason": "allowed",

        "daily_count": daily_count,

        "monthly_count": monthly_count,

        "limits": lim,

    }





def mark_auto_ai_run():

    state = load_state()

    now, day, month = _now_parts()



    daily_key = f"daily:{day}"

    monthly_key = f"monthly:{month}"



    state["last_run_at"] = time.time()

    state["last_run_iso"] = now.isoformat()

    state[daily_key] = int(state.get(daily_key, 0) or 0) + 1

    state[monthly_key] = int(state.get(monthly_key, 0) or 0) + 1



    cleaned = {}



    for key, value in state.items():

        if key in ["last_run_at", "last_run_iso"]:

            cleaned[key] = value

        elif key.startswith("daily:") or key.startswith("monthly:"):

            cleaned[key] = value



    save_state(cleaned)



    return {

        "daily_count": cleaned.get(daily_key, 0),

        "monthly_count": cleaned.get(monthly_key, 0),

        "last_run_iso": cleaned.get("last_run_iso"),

    }

