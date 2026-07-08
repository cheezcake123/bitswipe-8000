
import json

from collections import Counter

from pathlib import Path





LOG_PATH = Path("logs/candidates.jsonl")





def load_rows():

    if not LOG_PATH.exists():

        return []



    rows = []

    with LOG_PATH.open("r", encoding="utf-8") as f:

        for line in f:

            line = line.strip()

            if not line:

                continue

            try:

                rows.append(json.loads(line))

            except json.JSONDecodeError:

                continue

    return rows





def print_counter(title, counter):

    print()

    print(f"{title}:")

    if not counter:

        print("- none")

        return



    for key, count in counter.most_common():

        print(f"- {key}: {count}")





def main():

    rows = load_rows()



    print("[BitSwipe Candidate Log Report]")

    print()

    print(f"Log path: {LOG_PATH}")

    print(f"Total candidates: {len(rows)}")



    decision_counter = Counter(row.get("decision") or "UNKNOWN" for row in rows)

    grade_counter = Counter(row.get("grade") or "UNKNOWN" for row in rows)

    direction_counter = Counter(row.get("direction") or "UNKNOWN" for row in rows)

    blocked_counter = Counter(

        row.get("blocked_reason")

        for row in rows

        if row.get("blocked_reason")

    )



    eligible_for_ai = sum(1 for row in rows if row.get("eligible_for_ai") is True)

    ai_called = sum(1 for row in rows if row.get("ai_called") is True)

    alert_sent = sum(1 for row in rows if row.get("alert_sent") is True)



    print_counter("Decision", decision_counter)

    print_counter("Grade", grade_counter)

    print_counter("Direction", direction_counter)



    print()

    print("AI:")

    print(f"- eligible_for_ai: {eligible_for_ai}")

    print(f"- ai_called: {ai_called}")



    print()

    print("Alerts:")

    print(f"- alert_sent: {alert_sent}")



    print_counter("Top blocked reasons", blocked_counter)





if __name__ == "__main__":

    main()

