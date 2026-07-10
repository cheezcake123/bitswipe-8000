
#!/usr/bin/env python3

import json

import statistics

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

OUTCOME_PATH = ROOT / "logs/confirmed_path_outcomes.jsonl"

ENV_PATH = ROOT / ".env.local"



MODES = ["PILOT", "STANDARD", "HIGH", "MAX"]





def read_outcomes():

    rows = []



    if not OUTCOME_PATH.exists():

        return rows



    for line in OUTCOME_PATH.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines():

        try:

            row = json.loads(line)



            if (

                isinstance(row, dict)

                and row.get("plan_version") == "confirmed_trigger_v1"

                and row.get("net_r") is not None

            ):

                rows.append(row)

        except Exception:

            continue



    return rows





def requested_mode():

    if not ENV_PATH.exists():

        return "PILOT"



    for line in ENV_PATH.read_text(

        encoding="utf-8",

        errors="ignore",

    ).splitlines():

        if line.strip().startswith("BITSWIPE_RISK_MODE="):

            return line.split("=", 1)[1].strip().upper()



    return "PILOT"





def max_drawdown(values):

    equity = 0.0

    peak = 0.0

    drawdown = 0.0



    for value in values:

        equity += value

        peak = max(peak, equity)

        drawdown = max(drawdown, peak - equity)



    return drawdown





def allowed_mode(rows):

    values = [float(row["net_r"]) for row in rows]

    count = len(values)



    if not values:

        return "PILOT", None, None, 0.0



    average = statistics.mean(values)

    positive = sum(value for value in values if value > 0)

    negative = abs(sum(value for value in values if value < 0))

    profit_factor = positive / negative if negative else float("inf")

    drawdown = max_drawdown(values)



    if (

        count >= 100

        and average >= 0.15

        and profit_factor >= 1.50

        and drawdown <= 6.0

    ):

        allowed = "MAX"

    elif (

        count >= 60

        and average >= 0.10

        and profit_factor >= 1.30

        and drawdown <= 5.0

    ):

        allowed = "HIGH"

    elif (

        count >= 30

        and average > 0

        and profit_factor >= 1.20

        and drawdown <= 4.0

    ):

        allowed = "STANDARD"

    else:

        allowed = "PILOT"



    return allowed, average, profit_factor, drawdown





def effective_risk_mode(requested):

    requested = str(requested or "PILOT").upper()



    if requested not in MODES:

        requested = "PILOT"



    allowed, _, _, _ = allowed_mode(read_outcomes())



    return MODES[

        min(

            MODES.index(requested),

            MODES.index(allowed),

        )

    ]





def main():

    rows = read_outcomes()

    requested = requested_mode()

    allowed, average, pf, drawdown = allowed_mode(rows)

    effective = effective_risk_mode(requested)



    print("[BitSwipe Risk Mode Guard]")

    print(f"- 확정 진입 평가 표본: {len(rows)}")

    print(f"- 사용자 설정 모드: {requested}")

    print(f"- 현재 최대 허용 모드: {allowed}")

    print(f"- 실제 적용 모드: {effective}")



    if average is None:

        print("- 평균 순손익: -")

        print("- Profit Factor: -")

    else:

        print(f"- 평균 순손익: {average:+.3f}R")

        print(f"- Profit Factor: {pf:.2f}")



    print(f"- 최대 누적 낙폭: {drawdown:.3f}R")



    if requested != effective:

        print("상태: 제한 적용")

        print("- 검증 기준을 충족하지 못해 위험률이 하향되었습니다.")

    else:

        print("상태: PASS")



    print("- 위험 모드는 자동으로 상향되지 않습니다.")





if __name__ == "__main__":

    main()

