
#!/usr/bin/env python3

import argparse

import hashlib

import json

import subprocess

from datetime import datetime, timezone

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]



CONFIG = ROOT / "config/trade_experiment_v1.json"

LOCK = ROOT / "config/trade_experiment_v1.lock.json"



CRITICAL_FILES = [

    "scripts/watchlist_scan_once.py",

    "scripts/prediction_watch_store.py",

    "scripts/trade_path_evaluate.py",

    "scripts/pilot_policy_notify.py",

    "config/trade_experiment_v1.json",

]





def sha256(path):

    digest = hashlib.sha256()



    with path.open("rb") as handle:

        while True:

            chunk = handle.read(1024 * 1024)



            if not chunk:

                break



            digest.update(chunk)



    return digest.hexdigest()





def git_head():

    try:

        return subprocess.check_output(

            ["git", "rev-parse", "HEAD"],

            cwd=ROOT,

            text=True,

        ).strip()

    except Exception:

        return "UNKNOWN"





def create_lock():

    if not CONFIG.exists():

        raise SystemExit(

            "trade_experiment_v1.json이 없습니다."

        )



    missing = [

        name

        for name in CRITICAL_FILES

        if not (ROOT / name).exists()

    ]



    if missing:

        print("LOCK_CREATE_FAILED")

        print("누락 파일:")



        for name in missing:

            print("-", name)



        raise SystemExit(1)



    experiment = json.loads(

        CONFIG.read_text(encoding="utf-8")

    )



    data = {

        "experiment_id": experiment.get("experiment_id"),

        "locked_at": datetime.now(

            timezone.utc

        ).isoformat(),

        "git_commit_at_lock": git_head(),

        "critical_files": {

            name: sha256(ROOT / name)

            for name in CRITICAL_FILES

        },

    }



    LOCK.write_text(

        json.dumps(

            data,

            ensure_ascii=False,

            indent=2,

        )

        + "\n",

        encoding="utf-8",

    )



    print("[Experiment Integrity Lock Created]")

    print("experiment_id:", data["experiment_id"])

    print("locked_at:", data["locked_at"])

    print("git_commit:", data["git_commit_at_lock"])

    print("critical_files:", len(data["critical_files"]))





def verify_lock():

    if not LOCK.exists():

        print("[Experiment Integrity Guard]")

        print("상태: LOCK_NOT_FOUND")

        print(

            "먼저 --create-lock 옵션으로 잠금을 생성하세요."

        )

        raise SystemExit(1)



    lock = json.loads(

        LOCK.read_text(encoding="utf-8")

    )



    changed = []

    missing = []



    for name, expected_hash in lock[

        "critical_files"

    ].items():

        path = ROOT / name



        if not path.exists():

            missing.append(name)

            continue



        current_hash = sha256(path)



        if current_hash != expected_hash:

            changed.append(name)



    print("[Experiment Integrity Guard]")

    print("experiment_id:", lock.get("experiment_id"))

    print("locked_at:", lock.get("locked_at"))

    print(

        "locked_commit:",

        lock.get("git_commit_at_lock"),

    )

    print("current_commit:", git_head())

    print("")



    if missing:

        print("누락된 핵심 파일:")



        for name in missing:

            print("-", name)



    if changed:

        print("변경된 핵심 파일:")



        for name in changed:

            print("-", name)



    if not missing and not changed:

        print("상태: PASS")

        print("전략 핵심 파일 변경 없음.")

        print("Forward V1 실험 무결성 유지.")

        return



    print("")

    print("상태: FAIL")

    print(

        "정책 또는 평가 코드가 실험 시작 후 변경되었습니다."

    )

    print(

        "버그 수정이 아니라면 V1 결과와 섞지 마세요."

    )



    raise SystemExit(1)





def main():

    parser = argparse.ArgumentParser()



    parser.add_argument(

        "--create-lock",

        action="store_true",

    )



    args = parser.parse_args()



    if args.create_lock:

        create_lock()

    else:

        verify_lock()





if __name__ == "__main__":

    main()

