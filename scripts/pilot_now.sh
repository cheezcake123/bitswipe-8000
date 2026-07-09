
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



echo "===== 7) 최신 자동 루프 로그 ====="

grep -E "SCAN_DONE|PREDICTION_STORE|PREDICTION_EVAL|PREDICTION_BACKFILL|PILOT_SCAN|PILOT_NOTIFY|PILOT_TELEGRAM|NO_CANDIDATE_STATUS" logs/watchlist_scan.log | tail -n 60 || true

echo ""



echo "============================================================"

echo "운영 원칙:"

echo "- PILOT_ELIGIBLE 없으면 매매 금지"

echo "- PILOT_ELIGIBLE 있어도 자동진입 금지"

echo "- 초소액 수동 검토만 가능"

echo "- 레버리지 1~2배 이하"

echo "- 하루 2연패 시 중단"

echo "============================================================"

