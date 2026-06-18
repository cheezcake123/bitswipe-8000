from dataclasses import dataclass

from typing import Optional, Dict, Any





@dataclass

class RiskInput:

    side: str

    entry_price: float

    stop_price: float

    target_price: Optional[float] = None

    leverage: float = 1.0

    risk_percent: float = 1.0

    planned_position_percent: Optional[float] = None





def normalize_side(side: str) -> str:

    s = str(side).strip().lower()



    if s in ["long", "buy", "롱", "매수"]:

        return "long"



    if s in ["short", "sell", "숏", "매도"]:

        return "short"



    return s





def validate_basic_inputs(data: RiskInput) -> list[str]:

    errors = []



    data.side = normalize_side(data.side)



    if data.side not in ["long", "short"]:

        errors.append("방향은 long/short 또는 롱/숏 중 하나여야 합니다.")



    if data.entry_price <= 0:

        errors.append("진입가는 0보다 커야 합니다.")



    if data.stop_price <= 0:

        errors.append("손절가는 0보다 커야 합니다.")



    if data.target_price is not None and data.target_price <= 0:

        errors.append("목표가는 0보다 커야 합니다.")



    if data.leverage <= 0:

        errors.append("레버리지는 0보다 커야 합니다.")



    if data.risk_percent <= 0:

        errors.append("허용 리스크 비율은 0보다 커야 합니다.")



    if data.planned_position_percent is not None and data.planned_position_percent <= 0:

        errors.append("계획 진입 비중은 0보다 커야 합니다.")



    return errors





def validate_stop_direction(data: RiskInput) -> Optional[str]:

    if data.side == "long" and data.stop_price >= data.entry_price:

        return "롱 포지션의 손절가는 진입가보다 낮아야 합니다."



    if data.side == "short" and data.stop_price <= data.entry_price:

        return "숏 포지션의 손절가는 진입가보다 높아야 합니다."



    return None





def validate_target_direction(data: RiskInput) -> Optional[str]:

    if data.target_price is None:

        return None



    if data.side == "long" and data.target_price <= data.entry_price:

        return "롱 포지션의 목표가는 진입가보다 높아야 합니다."



    if data.side == "short" and data.target_price >= data.entry_price:

        return "숏 포지션의 목표가는 진입가보다 낮아야 합니다."



    return None





def calculate_risk(data: RiskInput) -> Dict[str, Any]:

    errors = validate_basic_inputs(data)



    if errors:

        return {

            "valid": False,

            "verdict": "INVALID",

            "errors": errors,

            "warnings": [],

            "hard_blocks": [],

        }



    stop_error = validate_stop_direction(data)

    if stop_error:

        return {

            "valid": False,

            "verdict": "INVALID",

            "errors": [stop_error],

            "warnings": [],

            "hard_blocks": ["손절가 방향 오류"],

        }



    target_error = validate_target_direction(data)

    if target_error:

        return {

            "valid": False,

            "verdict": "INVALID",

            "errors": [target_error],

            "warnings": [],

            "hard_blocks": ["목표가 방향 오류"],

        }



    entry = data.entry_price

    stop = data.stop_price

    target = data.target_price



    stop_distance = abs(entry - stop)

    stop_distance_percent = stop_distance / entry * 100



    leveraged_loss_percent_on_margin = stop_distance_percent * data.leverage



    reward_distance = None

    reward_percent = None

    risk_reward_ratio = None



    if target is not None:

        reward_distance = abs(target - entry)

        reward_percent = reward_distance / entry * 100

        risk_reward_ratio = reward_distance / stop_distance if stop_distance > 0 else None



    max_position_percent_by_account_risk = (

        data.risk_percent / leveraged_loss_percent_on_margin * 100

        if leveraged_loss_percent_on_margin > 0

        else 0

    )



    planned_account_loss_percent = None



    if data.planned_position_percent is not None:

        planned_account_loss_percent = (

            data.planned_position_percent / 100

        ) * leveraged_loss_percent_on_margin



    warnings = []

    hard_blocks = []



    if stop_distance_percent > 10:

        warnings.append("손절폭이 10%를 초과합니다. 포지션 크기를 크게 줄여야 합니다.")



    if leveraged_loss_percent_on_margin > 20:

        warnings.append("레버리지 반영 손실폭이 매우 큽니다.")



    if risk_reward_ratio is not None and risk_reward_ratio < 1.5:

        hard_blocks.append("손익비가 1:1.5 미만입니다.")



    if (

        planned_account_loss_percent is not None

        and planned_account_loss_percent > data.risk_percent

    ):

        hard_blocks.append(

            f"계획 진입 비중 기준 손절 시 계좌 손실이 {planned_account_loss_percent:.2f}%로, 허용치 {data.risk_percent:.2f}%를 초과합니다."

        )



    if hard_blocks:

        verdict = "BLOCK"

    elif warnings:

        verdict = "CAUTION"

    else:

        verdict = "PASS"



    return {

        "valid": True,

        "verdict": verdict,

        "side": data.side,

        "entry_price": entry,

        "stop_price": stop,

        "target_price": target,

        "leverage": data.leverage,

        "risk_percent": data.risk_percent,

        "stop_distance": stop_distance,

        "stop_distance_percent": stop_distance_percent,

        "leveraged_loss_percent_on_margin": leveraged_loss_percent_on_margin,

        "reward_distance": reward_distance,

        "reward_percent": reward_percent,

        "risk_reward_ratio": risk_reward_ratio,

        "max_position_percent_by_account_risk": max_position_percent_by_account_risk,

        "planned_position_percent": data.planned_position_percent,

        "planned_account_loss_percent": planned_account_loss_percent,

        "warnings": warnings,

        "hard_blocks": hard_blocks,

    }

