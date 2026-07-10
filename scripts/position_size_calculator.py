
#!/usr/bin/env python3

import argparse



RISK_MODES = {

    "PILOT": 0.25,

    "STANDARD": 0.50,

    "HIGH": 0.75,

    "MAX": 1.00,

}



MAX_LEVERAGE = 3.0





def calculate(balance, entry, stop, direction, risk_mode, leverage):

    direction = direction.upper()

    risk_mode = risk_mode.upper()



    if direction not in ("LONG", "SHORT"):

        raise ValueError("direction은 LONG 또는 SHORT여야 합니다.")



    if risk_mode not in RISK_MODES:

        raise ValueError("risk mode 오류")



    if leverage <= 0 or leverage > MAX_LEVERAGE:

        raise ValueError("레버리지는 0 초과 3배 이하여야 합니다.")



    if direction == "LONG" and stop >= entry:

        raise ValueError("LONG 손절가는 진입가보다 낮아야 합니다.")



    if direction == "SHORT" and stop <= entry:

        raise ValueError("SHORT 손절가는 진입가보다 높아야 합니다.")



    risk_pct = RISK_MODES[risk_mode]

    risk_budget = balance * risk_pct / 100

    stop_distance_pct = abs(entry - stop) / entry * 100

    cost_pct = 0.14

    total_loss_pct = stop_distance_pct + cost_pct



    raw_notional = risk_budget / (total_loss_pct / 100)

    max_notional = balance * 0.50 * leverage

    final_notional = min(raw_notional, max_notional)



    quantity = final_notional / entry

    margin = final_notional / leverage

    estimated_loss = final_notional * total_loss_pct / 100



    return {

        "risk_pct": risk_pct,

        "risk_budget": risk_budget,

        "stop_distance_pct": stop_distance_pct,

        "raw_notional": raw_notional,

        "final_notional": final_notional,

        "quantity": quantity,

        "margin": margin,

        "estimated_loss": estimated_loss,

        "capped": final_notional < raw_notional,

    }





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument("--balance", type=float, required=True)

    parser.add_argument("--entry", type=float, required=True)

    parser.add_argument("--stop", type=float, required=True)

    parser.add_argument("--direction", required=True)

    parser.add_argument("--risk-mode", default="PILOT")

    parser.add_argument("--leverage", type=float, default=2.0)



    args = parser.parse_args()



    result = calculate(

        args.balance,

        args.entry,

        args.stop,

        args.direction,

        args.risk_mode,

        args.leverage,

    )



    print("[BitSwipe Risk Sizing V1]")

    print("")

    print(f"위험 모드: {args.risk_mode.upper()} ({result['risk_pct']:.2f}%)")

    print(f"손절 거리: {result['stop_distance_pct']:.3f}%")

    print(f"허용 손실: {result['risk_budget']:,.2f} USDT")

    print(f"권장 명목금액: {result['final_notional']:,.2f} USDT")

    print(f"권장 수량: {result['quantity']:,.8f}")

    print(f"필요 증거금: {result['margin']:,.2f} USDT")

    print(f"예상 총손실: {result['estimated_loss']:,.2f} USDT")

    print(f"증거금 상한 적용: {result['capped']}")

    print("")

    print("주의: 주문은 실행하지 않습니다.")





if __name__ == "__main__":

    main()

