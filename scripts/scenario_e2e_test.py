
#!/usr/bin/env python3

import json

import sys

import tempfile

from datetime import datetime, timedelta, timezone

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "scripts"))



import scenario_followup_notify as followup

import scenario_tracker as tracker





def now_utc():

    return datetime.now(timezone.utc)





def save_state(path, record):

    path.write_text(

        json.dumps(

            {

                "active": {

                    "AAVEUSDT|SHORT": record,

                },

                "history": [],

            },

            ensure_ascii=False,

            indent=2,

        )

        + "\n",

        encoding="utf-8",

    )





def load_state(path):

    return json.loads(

        path.read_text(encoding="utf-8")

    )





def base_record(expired=False):

    now = now_utc()

    sent_at = now - timedelta(minutes=20)



    expires_at = (

        now - timedelta(minutes=1)

        if expired

        else now + timedelta(minutes=25)

    )



    return {

        "scenario_id": "AAVEUSDT|SHORT|E2E",

        "status": "WATCHING",

        "symbol": "AAVEUSDT",

        "market_type": "BINANCE",

        "direction": "SHORT",

        "classification": "PILOT_ELIGIBLE_STRICT",

        "grade": "C",

        "score": 54,

        "sent_at": sent_at.isoformat(),

        "registered_at": sent_at.isoformat(),

        "expires_at": expires_at.isoformat(),

        "entry_reference": 93.36,

        "entry_zone_low": 93.36,

        "entry_zone_high": 94.00,

        "support": 90.48,

        "resistance": 94.00,

        "virtual_stop": 94.376,

        "target_1": 91.92,

        "target_2": 90.48,

        "risk_pct": 1.088,

        "reward_pct": 3.085,

        "planned_rr": 2.83,

        "max_account_risk_pct": 0.25,

        "max_position_pct": 23.0,

        "max_leverage": 2.0,

        "followup_sent": False,

        "resolution": None,

        "resolved_at": None,

    }





def confirmed_candle():

    close_time = now_utc() - timedelta(minutes=5)



    return [{

        "open_time": int(

            (

                close_time

                - timedelta(minutes=15)

            ).timestamp() * 1000

        ),

        "close_time": int(

            close_time.timestamp() * 1000

        ),

        "open": 93.90,

        "high": 94.02,

        "low": 93.20,

        "close": 93.30,

    }]





def invalidated_candle():

    close_time = now_utc() - timedelta(minutes=5)



    return [{

        "open_time": int(

            (

                close_time

                - timedelta(minutes=15)

            ).timestamp() * 1000

        ),

        "close_time": int(

            close_time.timestamp() * 1000

        ),

        "open": 94.10,

        "high": 94.70,

        "low": 94.00,

        "close": 94.50,

    }]





def run_main(module):

    original_argv = sys.argv[:]



    try:

        sys.argv = [module.__file__]

        module.main()

    finally:

        sys.argv = original_argv





def run_tracker_case(

    state_path,

    expected,

    candles,

    expired=False,

):

    save_state(

        state_path,

        base_record(expired=expired),

    )



    tracker.ACTIVE_STATE_PATH = state_path

    tracker.fetch_closed_candles = (

        lambda symbol: candles

    )



    run_main(tracker)



    state = load_state(state_path)

    record = state["active"]["AAVEUSDT|SHORT"]



    assert record["status"] == "PENDING_NOTIFY"

    assert record["pending_resolution"] == expected

    assert record["followup_sent"] is False



    message = followup.build_message(record)



    assert message

    assert expected in (

        "CONFIRMED",

        "INVALIDATED",

        "EXPIRED",

    )



    return record





def run_successful_followup(

    state_path,

    log_path,

    expected,

    message_id,

):

    followup.ACTIVE_STATE_PATH = state_path

    followup.WATCH_LOG = log_path

    followup.send_telegram = (

        lambda text: (True, message_id)

    )



    run_main(followup)



    state = load_state(state_path)



    assert state["active"] == {}

    assert len(state["history"]) == 1



    record = state["history"][0]



    assert record["status"] == expected

    assert record["followup_sent"] is True

    assert record["followup_message_id"] == message_id





def test_success_case(

    directory,

    name,

    expected,

    candles,

    expired=False,

):

    state_path = directory / f"{name}.json"

    log_path = directory / f"{name}.log"



    run_tracker_case(

        state_path=state_path,

        expected=expected,

        candles=candles,

        expired=expired,

    )



    run_successful_followup(

        state_path=state_path,

        log_path=log_path,

        expected=expected,

        message_id=900,

    )



    print(f"PASS {name}: {expected}")





def test_failure_retry(directory):

    state_path = directory / "retry.json"

    log_path = directory / "retry.log"



    run_tracker_case(

        state_path=state_path,

        expected="CONFIRMED",

        candles=confirmed_candle(),

    )



    followup.ACTIVE_STATE_PATH = state_path

    followup.WATCH_LOG = log_path

    followup.send_telegram = (

        lambda text: (False, None)

    )



    failed_as_expected = False



    try:

        run_main(followup)

    except SystemExit:

        failed_as_expected = True



    assert failed_as_expected



    failed_state = load_state(state_path)

    failed_record = (

        failed_state["active"]["AAVEUSDT|SHORT"]

    )



    assert failed_record["status"] == "PENDING_NOTIFY"

    assert failed_record["followup_sent"] is False

    assert failed_state["history"] == []



    followup.send_telegram = (

        lambda text: (True, 901)

    )



    run_main(followup)



    retried_state = load_state(state_path)



    assert retried_state["active"] == {}

    assert len(retried_state["history"]) == 1

    assert (

        retried_state["history"][0]["status"]

        == "CONFIRMED"

    )

    assert (

        retried_state["history"][0]

        ["followup_message_id"]

        == 901

    )



    print(

        "PASS failure_retry: "

        "PENDING 유지 후 재전송 성공"

    )





def main():

    with tempfile.TemporaryDirectory(

        prefix="bitswipe-scenario-e2e-"

    ) as temp_directory:

        directory = Path(temp_directory)



        test_success_case(

            directory=directory,

            name="confirmed",

            expected="CONFIRMED",

            candles=confirmed_candle(),

        )



        test_success_case(

            directory=directory,

            name="invalidated",

            expected="INVALIDATED",

            candles=invalidated_candle(),

        )



        test_success_case(

            directory=directory,

            name="expired",

            expected="EXPIRED",

            candles=[],

            expired=True,

        )



        test_failure_retry(directory)



    print("")

    print("[Scenario E2E Test]")

    print("ALL_PASS")

    print(

        "운영 장부와 실제 텔레그램은 "

        "변경되지 않았습니다."

    )





if __name__ == "__main__":

    main()

