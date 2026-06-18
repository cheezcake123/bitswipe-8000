
import json

import sys

import time

import urllib.error

import urllib.request

from pathlib import Path





ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))



from scripts.ai_budget_guard import can_run_auto_ai, mark_auto_ai_run

from scripts.btc_cheap_scanner import scan_btc





LOG_PATH = ROOT / "logs" / "btc_watch.log"

URL = "http://127.0.0.1:8000/api/analyze?symbol=BTCUSDT"





def write_log(message):

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y-%m-%d %H:%M:%S")



    with LOG_PATH.open("a") as f:

        f.write(f"[{ts}] {message}\n")





def compact(data):

    return json.dumps(data, ensure_ascii=True)[:1200]





def main():

    guard = can_run_auto_ai()



    if not guard.get("allowed"):

        write_log("SKIP_BUDGET " + compact(guard))

        return



    scanner = scan_btc()



    if not scanner.get("ok"):

        write_log("SKIP_SCANNER_ERROR " + compact(scanner))

        return



    if not scanner.get("should_run_ai"):

        write_log("SKIP_SCANNER_LOW_SCORE " + compact(scanner))

        return



    req = urllib.request.Request(

        URL,

        data=b"",

        method="POST",

        headers={"Content-Type": "application/json"},

    )



    try:

        with urllib.request.urlopen(req, timeout=30) as res:

            body = res.read().decode("utf-8", errors="replace")



            try:

                data = json.loads(body)

                summary = compact(data)

            except Exception:

                data = {}

                summary = body[:1000]



            started = bool(data.get("started"))



            if started:

                marked = mark_auto_ai_run()

                write_log(

                    "AI_REQUEST_OK "

                    + f"status={res.status} "

                    + "scanner="

                    + compact(scanner)

                    + " marked="

                    + compact(marked)

                    + " response="

                    + summary

                )

            else:

                write_log(

                    "AI_REQUEST_NO_START "

                    + f"status={res.status} "

                    + "scanner="

                    + compact(scanner)

                    + " response="

                    + summary

                )



    except urllib.error.HTTPError as exc:

        body = exc.read().decode("utf-8", errors="replace")



        if "cooldown" in body.lower():

            write_log(f"SERVER_COOLDOWN status={exc.code} response={body[:1000]}")

            return



        write_log(f"HTTP_ERROR status={exc.code} response={body[:1000]}")



    except Exception as exc:

        write_log(f"ERROR {type(exc).__name__}: {exc}")





if __name__ == "__main__":

    main()

