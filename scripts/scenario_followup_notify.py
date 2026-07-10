
#!/usr/bin/env python3

import argparse

import json

import os

from datetime import datetime, timezone

from pathlib import Path



import requests



ROOT = Path(__file__).resolve().parents[1]

ACTIVE_STATE_PATH = ROOT / "logs/active_scenarios.json"

WATCH_LOG = ROOT / "logs/watchlist_scan.log"





def now_utc():

    return datetime.now(timezone.utc)





def now_text():

    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")





def number(value, default=0.0):

    try:

        return float(value)

    except Exception:

        return default





def format_price(value):

    price = number(value)



    if price >= 1000:

        return f"{price:,.2f}"



    if price >= 100:

        return f"{price:,.3f}"



    if price >= 1:

        return f"{price:,.4f}"



    if price >= 0.01:

        return f"{price:.6f}"



    return f"{price:.8f}"





def load_env_file(path):

    if not path.exists():

        return



    for line in path.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines():

        line = line.strip()



        if (

            not line

            or line.startswith("#")

            or "=" not in line

        ):

            continue



        key, value = line.split("=", 1)



        os.environ.setdefault(

            key.strip(),

            value.strip().strip('"').strip("'"),

        )





def load_env():

    load_env_file(ROOT / ".env")

    load_env_file(ROOT / ".env.local")





def load_state():

    try:

        if ACTIVE_STATE_PATH.exists():

            value = json.loads(

                ACTIVE_STATE_PATH.read_text(

                    encoding="utf-8"

                )

            )



            if isinstance(value, dict):

                value.setdefault("active", {})

                value.setdefault("history", [])

                return value

    except Exception:

        pass



    return {

        "active": {},

        "history": [],

    }





def save_state(state):

    ACTIVE_STATE_PATH.parent.mkdir(

        parents=True,

        exist_ok=True,

    )



    ACTIVE_STATE_PATH.write_text(

        json.dumps(

            state,

            ensure_ascii=False,

            indent=2,

        )

        + "\n",

        encoding="utf-8",

    )





def compact(value):

    return json.dumps(

        value,

        ensure_ascii=False,

        separators=(",", ":"),

    )





def log(message):

    WATCH_LOG.parent.mkdir(

        parents=True,

        exist_ok=True,

    )



    with WATCH_LOG.open(

        "a",

        encoding="utf-8",

    ) as handle:

        handle.write(

            f"[{now_text()}] {message}\n"

        )





def direction_text(direction):

    if direction == "SHORT":

        return "숏"



    if direction == "LONG":

        return "롱"



    return str(direction or "알 수 없음")





def candle_summary(record):

    candle = record.get("resolution_candle")



    if not isinstance(candle, dict):

        return []



    lines = []



    if candle.get("open") is not None:

        lines.append(

            f"- 시가: {format_price(candle.get('open'))}"

        )



    if candle.get("high") is not None:

        lines.append(

            f"- 고가: {format_price(candle.get('high'))}"

        )



    if candle.get("low") is not None:

        lines.append(

            f"- 저가: {format_price(candle.get('low'))}"

        )



    if candle.get("close") is not None:

        lines.append(

            f"- 종가: {format_price(candle.get('close'))}"

        )



    return lines





def confirmed_message(record):

    lines = [

        "[BitSwipe 진입조건 확인]",

        "",

        f"종목: {record.get('symbol')}",

        f"관점: 조건부 {direction_text(record.get('direction'))}",

        "",

        "확인 결과:",

        f"- {record.get('resolution')}",

        "- 15분봉 확인 조건이 충족되었습니다.",

        "",

        "검토 진입 구간:",

        (

            f"- {format_price(record.get('entry_zone_low'))}"

            f" ~ {format_price(record.get('entry_zone_high'))}"

        ),

        "",

        "손절 및 관점 무효화:",

        (

            f"- {format_price(record.get('virtual_stop'))}"

        ),

        "",

        "목표:",

        (

            f"- 1차 목표: "

            f"{format_price(record.get('target_1'))}"

        ),

        (

            f"- 2차 목표: "

            f"{format_price(record.get('target_2'))}"

        ),

        (

            f"- 계획 손익비: "

            f"{number(record.get('planned_rr')):.2f}:1"

        ),

        "",

        "위험 관리:",

        (

            f"- 계좌 최대 손실: "

            f"{number(record.get('max_account_risk_pct')):.2f}%"

        ),

        (

            f"- 계산상 최대 명목 포지션: "

            f"계좌의 약 "

            f"{number(record.get('max_position_pct')):.1f}%"

        ),

        (

            f"- 레버리지 상한: "

            f"{number(record.get('max_leverage')):.0f}배"

        ),

        "",

        "최종 판정:",

        "- 초소액 수동 진입 검토 가능",

        "- 현재가가 진입 구간을 벗어났다면 추격 금지",

        "- 손절가를 넓히거나 물타기 금지",

        "- 실제 주문은 사용자가 직접 최종 승인",

    ]



    candle_lines = candle_summary(record)



    if candle_lines:

        lines.extend([

            "",

            "확인된 15분봉:",

            *candle_lines,

        ])



    return "\n".join(lines)





def invalidated_message(record):

    lines = [

        "[BitSwipe 시나리오 폐기]",

        "",

        f"종목: {record.get('symbol')}",

        f"기존 관점: {direction_text(record.get('direction'))}",

        "",

        "폐기 사유:",

        f"- {record.get('resolution')}",

        "",

        "기존 기준:",

        (

            f"- 무효화 가격: "

            f"{format_price(record.get('virtual_stop'))}"

        ),

        (

            f"- 기존 진입 구간: "

            f"{format_price(record.get('entry_zone_low'))}"

            f" ~ {format_price(record.get('entry_zone_high'))}"

        ),

        "",

        "최종 판정:",

        "- 기존 관점은 더 이상 유효하지 않습니다.",

        "- 재진입·물타기·복구 매매 금지",

        "- 새로운 독립 후보가 생성될 때까지 관망",

    ]



    candle_lines = candle_summary(record)



    if candle_lines:

        lines.extend([

            "",

            "무효화된 15분봉:",

            *candle_lines,

        ])



    return "\n".join(lines)





def expired_message(record):

    return "\n".join([

        "[BitSwipe 시나리오 만료]",

        "",

        f"종목: {record.get('symbol')}",

        f"기존 관점: {direction_text(record.get('direction'))}",

        "",

        "만료 사유:",

        f"- {record.get('resolution')}",

        "- 제한 시간 동안 확인 조건이 충족되지 않았습니다.",

        "",

        "최종 판정:",

        "- 기존 진입가·손절가·목표가는 폐기합니다.",

        "- 늦은 진입과 추격 매매 금지",

        "- 새로운 후보가 생성될 때까지 관망",

    ])





def build_message(record):

    resolution = record.get("pending_resolution")



    if resolution == "CONFIRMED":

        return confirmed_message(record)



    if resolution == "INVALIDATED":

        return invalidated_message(record)



    if resolution == "EXPIRED":

        return expired_message(record)



    return None





def send_telegram(text):

    token = os.environ.get(

        "TELEGRAM_BOT_TOKEN",

        "",

    )

    chat_id = os.environ.get(

        "TELEGRAM_CHAT_ID",

        "",

    )



    if not token or not chat_id:

        log(

            "SCENARIO_FOLLOWUP_TELEGRAM_SKIP "

            "missing_token_or_chat_id"

        )

        return False, None



    url = (

        "https://api.telegram.org/"

        f"bot{token}/sendMessage"

    )



    try:

        response = requests.post(

            url,

            json={

                "chat_id": chat_id,

                "text": text,

                "disable_web_page_preview": True,

            },

            timeout=25,

        )



        try:

            payload = response.json()

        except Exception:

            payload = {}



        ok = (

            response.status_code == 200

            and bool(payload.get("ok"))

        )



        message_id = (

            payload.get("result", {}).get("message_id")

            if isinstance(payload, dict)

            else None

        )



        log(

            "SCENARIO_FOLLOWUP_TELEGRAM_RESULT "

            + compact({

                "status": response.status_code,

                "ok": ok,

                "message_id": message_id,

                "description": (

                    payload.get("description")

                    if isinstance(payload, dict)

                    else None

                ),

            })

        )



        return ok, message_id



    except Exception as exc:

        log(

            "SCENARIO_FOLLOWUP_TELEGRAM_ERROR "

            f"{type(exc).__name__}: {exc}"

        )

        return False, None





def demo_record(resolution):

    return {

        "scenario_id": (

            f"AAVEUSDT|SHORT|DEMO|{resolution}"

        ),

        "status": "PENDING_NOTIFY",

        "pending_resolution": resolution,

        "symbol": "AAVEUSDT",

        "direction": "SHORT",

        "resolution": {

            "CONFIRMED": (

                "진입 구간 접촉 후 저항 아래 약세 마감"

            ),

            "INVALIDATED": (

                "15분봉이 숏 무효화 가격 위에서 마감"

            ),

            "EXPIRED": "45분 유효기간 종료",

        }[resolution],

        "entry_zone_low": 93.36,

        "entry_zone_high": 94.00,

        "virtual_stop": 94.376,

        "target_1": 91.92,

        "target_2": 90.48,

        "planned_rr": 2.83,

        "max_account_risk_pct": 0.25,

        "max_position_pct": 23.0,

        "max_leverage": 2.0,

        "resolution_candle": {

            "open": 93.90,

            "high": 94.02,

            "low": 93.20,

            "close": 93.30,

        },

    }





def run_demo():

    for resolution in (

        "CONFIRMED",

        "INVALIDATED",

        "EXPIRED",

    ):

        print("")

        print("=" * 60)

        print("")

        print(build_message(

            demo_record(resolution)

        ))





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument(

        "--demo",

        action="store_true",

    )

    parser.add_argument(

        "--test",

        action="store_true",

    )

    parser.add_argument(

        "--dry-run",

        action="store_true",

    )



    args = parser.parse_args()



    load_env()



    if args.demo:

        run_demo()

        return



    if args.test:

        message = (

            "[후속 알림 테스트]\n\n"

            + build_message(

                demo_record("CONFIRMED")

            )

        )



        if args.dry_run:

            print(message)

            return



        ok, message_id = send_telegram(message)



        if not ok:

            raise SystemExit(

                "후속 알림 테스트 전송 실패"

            )



        print(

            "[Scenario Follow-up Notify] "

            f"TEST_SENT message_id={message_id}"

        )

        return



    state = load_state()

    active = state.setdefault("active", {})

    history = state.setdefault("history", [])



    pending = []



    for key, record in list(active.items()):

        if not isinstance(record, dict):

            continue



        if record.get("status") != "PENDING_NOTIFY":

            continue



        message = build_message(record)



        if not message:

            log(

                "SCENARIO_FOLLOWUP_SKIP "

                + compact({

                    "key": key,

                    "reason": "unknown_resolution",

                    "pending_resolution": (

                        record.get("pending_resolution")

                    ),

                })

            )

            continue



        pending.append((key, record, message))



    if not pending:

        print(

            "[Scenario Follow-up Notify] "

            "SKIP no_pending_results"

        )

        return



    if args.dry_run:

        for key, record, message in pending:

            print("")

            print(f"===== {key} =====")

            print("")

            print(message)



        print("")

        print(

            "[Scenario Follow-up Notify] "

            f"DRY_RUN pending={len(pending)}"

        )

        return



    sent = 0

    failures = 0



    for key, record, message in pending:

        ok, message_id = send_telegram(message)



        if not ok:

            failures += 1



            log(

                "SCENARIO_FOLLOWUP_FAILED "

                + compact({

                    "key": key,

                    "symbol": record.get("symbol"),

                    "resolution": (

                        record.get("pending_resolution")

                    ),

                })

            )

            continue



        final_status = record.get(

            "pending_resolution"

        )



        record["status"] = final_status

        record["followup_sent"] = True

        record["followup_sent_at"] = (

            now_utc().isoformat()

        )

        record["followup_message_id"] = (

            message_id

        )



        history.append(record)

        active.pop(key, None)



        state["updated_at"] = (

            now_utc().isoformat()

        )



        save_state(state)



        sent += 1



        log(

            "SCENARIO_FOLLOWUP_SUCCESS "

            + compact({

                "key": key,

                "symbol": record.get("symbol"),

                "resolution": final_status,

                "message_id": message_id,

            })

        )



    print("[Scenario Follow-up Notify]")

    print(f"pending: {len(pending)}")

    print(f"sent: {sent}")

    print(f"failures: {failures}")



    if failures:

        raise SystemExit(

            "일부 후속 알림 전송 실패"

        )





if __name__ == "__main__":

    main()

