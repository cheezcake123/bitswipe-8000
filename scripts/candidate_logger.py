
import json

from datetime import datetime, timezone

from pathlib import Path

from typing import Any, Mapping





LOG_PATH = Path("logs/candidates.jsonl")





def _json_default(value: Any) -> Any:

    try:

        import numpy as np



        if isinstance(value, np.integer):

            return int(value)

        if isinstance(value, np.floating):

            return float(value)

        if isinstance(value, np.bool_):

            return bool(value)

    except Exception:

        pass



    if hasattr(value, "isoformat"):

        return value.isoformat()



    return str(value)





def append_candidate_log(candidate: Mapping[str, Any]) -> None:

    """

    Append one BitSwipe candidate decision to logs/candidates.jsonl.



    This function must never break the scanner. If logging fails, it silently

    writes nothing rather than interrupting the trading watch pipeline.

    """

    try:

        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)



        row = dict(candidate)



        if not row.get("ts"):

            row["ts"] = datetime.now(timezone.utc).isoformat()



        with LOG_PATH.open("a", encoding="utf-8") as f:

            f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")

    except Exception:

        return

