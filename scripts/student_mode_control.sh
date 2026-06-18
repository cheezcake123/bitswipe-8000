
#!/usr/bin/env bash

set -e



case "$1" in

  start)

    sudo systemctl start bitswipe-btc-watch.timer

    echo "Student Mode timer started."

    ;;

  stop)

    sudo systemctl stop bitswipe-btc-watch.timer

    echo "Student Mode timer stopped."

    ;;

  restart)

    sudo systemctl restart bitswipe-btc-watch.timer

    echo "Student Mode timer restarted."

    ;;

  disable)

    sudo systemctl disable --now bitswipe-btc-watch.timer

    echo "Student Mode timer disabled."

    ;;

  enable)

    sudo systemctl enable --now bitswipe-btc-watch.timer

    echo "Student Mode timer enabled."

    ;;

  status)

    systemctl list-timers | grep bitswipe || true

    echo ""

    sudo systemctl status bitswipe-btc-watch.timer --no-pager

    ;;

  log)

    tail -30 /opt/bitswipe/logs/btc_watch.log

    ;;

  budget)

    cat /opt/bitswipe/data/ai_budget_guard.json

    ;;

  *)

    echo "Usage: bash scripts/student_mode_control.sh {start|stop|restart|disable|enable|status|log|budget}"

    exit 1

    ;;

esac

