
#!/usr/bin/env python3

import json

import subprocess

from datetime import datetime, timezone

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

CONFIG = ROOT / "config/trade_experiment_v1.json"





def git_commit():

    try:

        return subprocess.check_output(

            ["git", "rev-parse", "HEAD"],

            cwd=ROOT,

            text=True,

        ).strip()

    except Exception:

        return "UNKNOWN"





def main():

    if CONFIG.exists():

        data = json.loads(CONFIG.read_text(encoding="utf-8"))

        print("[Forward Experiment]")

        print("이미 시작된 실험입니다.")

        print(json.dumps(data, ensure_ascii=False, indent=2))

        return



    started_at = datetime.now(timezone.utc).isoformat()



    data = {

        "experiment_id": "bitswipe_forward_v1",

        "started_at": started_at,

        "baseline_commit": git_commit(),

        "policy": {

            "market_type": "BINANCE",

            "direction": "SHORT",

            "minimum_rr": 2.0,

            "minimum_score": 30,

            "maximum_score": 59,

            "horizon_min": 240,

            "path_interval": "1m",

            "plan_version": "scanner_rr_v1"

        },

        "cost_model": {

            "round_trip_fee_bps": 10.0,

            "round_trip_slippage_bps": 4.0,

            "total_cost_bps": 14.0

        },

        "promotion_requirements": {

            "minimum_strict_forward_samples": 30,

            "minimum_average_net_r": 0.0,

            "minimum_profit_factor": 1.2,

            "maximum_drawdown_r": 5.0

        },

        "rules": [

            "전진 표본 30건 전까지 전략 조건 변경 금지",

            "버그 수정 외 정책 변경 시 새 experiment_id 사용",

            "자동매매 금지",

            "초소액 수동 검토만 허용"

        ]

    }



    CONFIG.write_text(

        json.dumps(data, ensure_ascii=False, indent=2) + "\n",

        encoding="utf-8",

    )



    print("[Forward Experiment Started]")

    print(json.dumps(data, ensure_ascii=False, indent=2))





if __name__ == "__main__":

    main()

