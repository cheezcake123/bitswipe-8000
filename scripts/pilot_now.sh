
#!/usr/bin/env bash

set -euo pipefail



cd /opt/bitswipe



echo "============================================================"

echo "[BitSwipe Pilot Now]"

echo "============================================================"

date -u

echo ""



echo "===== 1) 타이머 상태 ====="

systemctl status bitswipe-btc-watch.timer --no-pager -l || true

echo ""



echo "===== 2) 다음 자동 실행 시각 ====="

systemctl list-timers --all | grep bitswipe || true

echo ""



echo "===== 3) 감시 상태 요약 ====="

python3 scripts/watch_status_ko.py || true

echo ""



echo "===== 4) 실전 파일럿 보고서 ====="

python3 scripts/live_pilot_report.py || true

echo ""



echo "===== 5) Prediction Watch ====="

python3 scripts/prediction_watch_report.py || true

echo ""



echo "===== 6) Prediction Scoreboard ====="

python3 scripts/prediction_scoreboard.py || true

echo ""



echo "===== 6-1) Prediction Scoreboard V2 ====="

python3 scripts/prediction_scoreboard_v2.py || true

echo ""

echo "===== 6-2) Trade Path Scoreboard ====="

python3 scripts/trade_path_scoreboard.py || true

echo ""

echo "===== 6-3A) Experiment Integrity Guard ====="

python3 scripts/experiment_integrity_guard.py || true

echo ""

echo "===== 6-3) Forward Validation V1 ====="

python3 scripts/forward_validation_scoreboard.py || true

echo ""

echo "===== 7) Prediction Rule Recommender ====="

python3 scripts/prediction_rule_recommender.py || true

echo ""



echo "===== 8) 엄격 파일럿 정책 ====="

python3 scripts/pilot_policy_notify.py --dry-run || true

echo ""



echo "===== 8-1) Strict Policy Scoreboard ====="

python3 scripts/policy_scoreboard.py || true

echo ""

echo "===== 8-2) Prediction Detail Report ====="

python3 scripts/prediction_detail_report.py || true

echo ""

echo "===== 9) 최신 자동 루프 로그 ====="

grep -E "SCAN_DONE|PREDICTION_STORE|PREDICTION_EVAL|PREDICTION_BACKFILL|POLICY_SCAN|POLICY_NOTIFY|POLICY_TELEGRAM|PILOT_SCAN|PILOT_NOTIFY|NO_CANDIDATE_STATUS" logs/watchlist_scan.log | tail -n 80 || true

echo ""



echo "============================================================"

echo "운영 원칙:"

echo "- PILOT_ELIGIBLE 또는 PILOT_ELIGIBLE_STRICT 없으면 매매 금지"

echo "- 후보가 있어도 자동진입 금지"

echo "- 초소액 수동 검토만 가능"

echo "- 레버리지 1~2배 이하"

echo "- 하루 2연패 시 중단"

echo "============================================================"

