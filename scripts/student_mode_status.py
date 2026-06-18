
import json

import subprocess

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

BUDGET_PATH = ROOT / "data" / "ai_budget_guard.json"

WATCH_LOG = ROOT / "logs" / "btc_watch.log"

ALERT_LOG = ROOT / "data" / "telegram_alert_log.jsonl"





def print_section(title):

    print("")

    print("=" * 60)

    print(title)

    print("=" * 60)





def show_budget():

    print_section("AI Budget Guard")



    if not BUDGET_PATH.exists():

        print("No budget state file yet.")

        return



    try:

        print(json.dumps(json.loads(BUDGET_PATH.read_text()), indent=2))

    except Exception as exc:

        print(f"Failed to read budget state: {exc}")





def show_timer():

    print_section("Systemd Timer")



    try:

        result = subprocess.run(

            ["systemctl", "list-timers", "--all"],

            capture_output=True,

            text=True,

            timeout=5,

        )



        lines = [

            line for line in result.stdout.splitlines()

            if "bitswipe-btc-watch" in line

        ]



        if not lines:

            print("bitswipe-btc-watch.timer not found.")

            return



        for line in lines:

            print(line)



    except Exception as exc:

        print(f"Failed to read timer: {exc}")





def show_tail(path, title, limit=10):

    print_section(title)



    if not path.exists():

        print("No log file yet.")

        return



    lines = path.read_text(errors="replace").splitlines()



    for line in lines[-limit:]:

        print(line)





def main():

    show_budget()

    show_timer()

    show_tail(WATCH_LOG, "BTC Watch Log", 10)

    show_tail(ALERT_LOG, "Telegram Alert Log", 10)





if __name__ == "__main__":

    main()

