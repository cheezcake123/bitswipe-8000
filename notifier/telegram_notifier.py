
from notifier.korean_alerts import localize_alert_text
import json

import os

import urllib.request

from pathlib import Path

from typing import Dict, Any, Mapping, Optional

try:

    from notifier.alert_ledger_registration import (

        prepare_trade_alert_registration,

        register_successful_trade_alert,

    )

except Exception:

    def prepare_trade_alert_registration(text, *, plan=None, env=None):

        return None

    def register_successful_trade_alert(prepared, response):

        return {"ok": False, "registered": False, "code": "HOOK_UNAVAILABLE"}





def _load_env_file(path: str = ".env") -> Dict[str, str]:

    env = {}

    p = Path(path)



    if not p.exists():

        return env



    for line in p.read_text().splitlines():

        line = line.strip()



        if not line or line.startswith("#") or "=" not in line:

            continue



        key, value = line.split("=", 1)

        env[key.strip()] = value.strip()



    return env





def send_telegram_message(text: str, *, registration_plan: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:

    text = localize_alert_text(text)

    prepared = prepare_trade_alert_registration(text, plan=registration_plan)

    outbound_text = prepared.outbound_text if prepared is not None else text

    env = _load_env_file()



    token = os.getenv("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")

    chat_id = os.getenv("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")



    if not token:

        return {

            "ok": False,

            "error": "TELEGRAM_BOT_TOKEN이 설정되어 있지 않습니다.",

        }



    if not chat_id:

        return {

            "ok": False,

            "error": "TELEGRAM_CHAT_ID가 설정되어 있지 않습니다.",

        }



    payload = {

        "chat_id": chat_id,

        "text": outbound_text,

        "disable_web_page_preview": True,

    }



    url = f"https://api.telegram.org/bot{token}/sendMessage"



    req = urllib.request.Request(

        url,

        data=json.dumps(payload).encode("utf-8"),

        headers={"Content-Type": "application/json"},

        method="POST",

    )



    try:

        with urllib.request.urlopen(req, timeout=10) as res:

            body = res.read().decode("utf-8")

            response = json.loads(body)

            if prepared is not None and response.get("ok"):

                response["ledger_registration"] = register_successful_trade_alert(

                    prepared,

                    response,

                )

            return response

    except Exception as exc:

        return {

            "ok": False,

            "error": str(exc),

        }
