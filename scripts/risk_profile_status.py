
#!/usr/bin/env python3

import math

from datetime import datetime, timezone

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

ENV_PATH = ROOT / ".env.local"



RISK_MODES = {

    "PILOT": 0.25,

    "STANDARD": 0.50,

    "HIGH": 0.75,

    "MAX": 1.00,

}





def load_safe_values():

    values = {}



    if not ENV_PATH.exists():

        return values



    allowed = {

        "BITSWIPE_BALANCE_USDT",

        "BITSWIPE_RISK_MODE",

        "BITSWIPE_LEVERAGE",

    }



    for line in ENV_PATH.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines():

        line = line.strip()



        if not line or line.startswith("#") or "=" not in line:

            continue



        key, value = line.split("=", 1)



        if key.strip() in allowed:

            values[key.strip()] = value.strip().strip('"').strip("'")



    return values





def main():

    values = load_safe_values()



    print("[BitSwipe Risk Profile]")



    if not ENV_PATH.exists():

        print("상태: 미설정")

        raise SystemExit(1)



    try:

        balance = float(values.get("BITSWIPE_BALANCE_USDT", ""))

        leverage = float(values.get("BITSWIPE_LEVERAGE", "2"))

    except Exception:

        print("상태: FAIL")

        print("- 잔액 또는 레버리지가 숫자가 아닙니다.")

        raise SystemExit(1)



    mode = values.get(

        "BITSWIPE_RISK_MODE",

        "PILOT",

    ).upper()



    if (

        not math.isfinite(balance)

        or balance <= 0

        or mode not in RISK_MODES

        or not math.isfinite(leverage)

        or leverage <= 0

        or leverage > 3

    ):

        print("상태: FAIL")

        print("- Risk Profile 설정값이 허용 범위를 벗어났습니다.")

        raise SystemExit(1)



    risk_pct = RISK_MODES[mode]

    risk_budget = balance * risk_pct / 100



    modified = datetime.fromtimestamp(

        ENV_PATH.stat().st_mtime,

        tz=timezone.utc,

    )



    age_days = (

        datetime.now(timezone.utc) - modified

    ).total_seconds() / 86400



    print("상태: PASS")

    print(f"- 기준 잔액: {balance:,.2f} USDT")

    print(f"- 위험 모드: {mode} ({risk_pct:.2f}%)")

    print(f"- 거래당 최대 손실: {risk_budget:,.2f} USDT")

    print(f"- 계산 레버리지: {leverage:.1f}배")

    print(f"- 설정 갱신 경과: {age_days:.1f}일")

    print("- 주문 기능: 없음")



    if age_days >= 7:

        print("경고: 잔액 설정이 7일 이상 갱신되지 않았습니다.")





if __name__ == "__main__":

    main()

