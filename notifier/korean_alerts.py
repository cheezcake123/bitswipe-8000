from __future__ import annotations



import re





_PHRASES = (

    ("[BitSwipe AI Watchlist]", "[BitSwipe AI \uad00\ucc30 \uc54c\ub9bc]"),

    ("BitSwipe A-grade candidate", "BitSwipe A\ub4f1\uae09 \uc9c4\uc785 \ud6c4\ubcf4"),

    ("BitSwipe B-grade WATCH", "BitSwipe B\ub4f1\uae09 \uad00\ucc30 \uc54c\ub9bc"),

    ("NOT ENTRY - confirmation needed", "\ud604\uc7ac \uc9c4\uc785 \uae08\uc9c0 \u00b7 \ucd94\uac00 \ud655\uc778 \ud544\uc694"),

    ("Verdict: Risk Guard PASS", "\ucd5c\uc885 \ud310\uc815: \ub9ac\uc2a4\ud06c \uc2ec\uc0ac \ud1b5\uacfc"),

    ("Risk Guard PASS", "\ub9ac\uc2a4\ud06c \uc2ec\uc0ac \ud1b5\uacfc"),

    (

        "Do not chase immediately. Re-check candle close and stop location.",

        "\uc989\uc2dc \ucd94\uaca9 \uc9c4\uc785\ud558\uc9c0 \ub9c8\uc138\uc694. "

        "\uce94\ub4e4 \uc885\uac00\uc640 \uc190\uc808 \uc704\uce58\ub97c \ub2e4\uc2dc \ud655\uc778\ud558\uc138\uc694.",

    ),

    ("[Confirmed Research Health]", "[\ud655\uc815 \uc5f0\uad6c \uc0c1\ud0dc]"),

    ("[Confirmed Research Alert]", "[\ud655\uc815 \uc5f0\uad6c \uc54c\ub9bc]"),

    ("Confirmed Trigger", "\ud655\uc815 \uc870\uac74"),

    ("Forward V1", "\uc804\uc9c4 \uac80\uc99d 1\ub2e8\uacc4"),

    ("Watch only", "\uad00\ucc30 \uc804\uc6a9"),

    ("No immediate entry", "\uc989\uc2dc \uc9c4\uc785 \uae08\uc9c0"),

    ("confirmation needed", "\ucd94\uac00 \ud655\uc778 \ud544\uc694"),

    ("A-grade", "A\ub4f1\uae09"),

    ("B-grade", "B\ub4f1\uae09"),

)



_PREFIXES = (

    ("Symbol: ", "\uc885\ubaa9: "),

    ("Asset: ", "\uc790\uc0b0: "),

    ("Event: ", "\uad00\ucc30 \uc720\ud615: "),

    ("Direction: ", "\ubc29\ud5a5: "),

    ("Confidence: ", "\uc2e0\ub8b0\ub3c4: "),

    ("Rule score: ", "\uaddc\uce59 \uc810\uc218: "),

    ("Current price: ", "\ud604\uc7ac\uac00: "),

    ("Entry: ", "\uc9c4\uc785\uac00: "),

    ("Stop / invalidation: ", "\uc190\uc808 / \ubb34\ud6a8\ud654: "),

    ("Stop: ", "\uc190\uc808\uac00: "),

    ("Target: ", "\ubaa9\ud45c\uac00: "),

    ("Risk reward: ", "\uc190\uc775\ube44: "),

    ("Stop distance: ", "\uc190\uc808 \uac70\ub9ac: "),

    ("Leverage: ", "\ub808\ubc84\ub9ac\uc9c0: "),

    ("Leveraged loss: ", "\ub808\ubc84\ub9ac\uc9c0 \ubc18\uc601 \uc608\uc0c1 \uc190\uc2e4: "),

    (

        "Max position for 1pct account risk: ",

        "\uacc4\uc88c 1% \uc704\ud5d8 \uae30\uc900 \ucd5c\ub300 \ud3ec\uc9c0\uc158: ",

    ),

    ("Support: ", "\uc9c0\uc9c0\uc120: "),

    ("Resistance: ", "\uc800\ud56d\uc120: "),

    ("Trend: ", "\ucd94\uc138: "),

    ("Volume ratio: ", "\uac70\ub798\ub7c9 \ube44\uc728: "),

    ("Why watch: ", "\uad00\ucc30 \uc774\uc720: "),

    ("Confirmation: ", "\ud655\uc778 \uc870\uac74: "),

    ("Warning: ", "\uc8fc\uc758: "),

    ("Verdict: ", "\ucd5c\uc885 \ud310\uc815: "),

    ("Summary: ", "\uc694\uc57d: "),

    ("Scenario: ", "\uc2dc\ub098\ub9ac\uc624: "),

    ("- health: ", "- \uc0c1\ud0dc: "),

    ("- notification: ", "- \uc54c\ub9bc \uc804\uc1a1: "),

    ("- dry_run: ", "- \uc2dc\ud5d8 \uc2e4\ud589: "),

    ("- timer: ", "- \ud0c0\uc774\uba38: "),

    ("- service_result: ", "- \uc11c\ube44\uc2a4 \uacb0\uacfc: "),

    ("- stalled: ", "- \ud3c9\uac00 \uc9c0\uc5f0: "),

    ("- recent_failures: ", "- \ucd5c\uadfc \uc2e4\ud328: "),

)



_TOKENS = (

    ("PILOT_ELIGIBLE_STRICT", "\uc5c4\uaca9 \uc870\uac74 \ucda9\uc871"),

    ("WATCH_STRONG_RR_NOT_STRICT", "\uc190\uc775\ube44 \uc591\ud638 \uad00\ucc30 \ub300\uc0c1"),

    ("NO_CLEAR_DIRECTION", "\ubc29\ud5a5 \ubd88\uba85\ud655"),

    ("LOW_ESTIMATED_RR", "\uc608\uc0c1 \uc190\uc775\ube44 \ubd80\uc871"),

    ("SCORE_BELOW_THRESHOLD", "\uc810\uc218 \uae30\uc900 \ubbf8\ub2ec"),

    ("GRADE_BELOW_B", "\ub4f1\uae09 \uae30\uc900 \ubbf8\ub2ec"),

    ("AI_BUDGET_EXHAUSTED", "AI \ud638\ucd9c \ud55c\ub3c4 \uc18c\uc9c4"),

    ("NO_AI_CANDIDATE", "AI \ubd84\uc11d \ud6c4\ubcf4 \uc5c6\uc74c"),

    ("DUPLICATE", "\uc911\ubcf5 \uc54c\ub9bc"),

    ("COOLDOWN", "\uc7ac\uc54c\ub9bc \ub300\uae30"),

    ("CONFIRMED", "\uc9c4\uc785 \uc870\uac74 \ucda9\uc871"),

    ("INVALIDATED", "\uc2dc\ub098\ub9ac\uc624 \ubb34\ud6a8\ud654"),

    ("EXPIRED", "\uc2dc\ub098\ub9ac\uc624 \ub9cc\ub8cc"),

    ("TWO_WAY", "\uc591\ubc29\ud5a5"),

    ("LONG", "\ub871"),

    ("SHORT", "\uc20f"),

    ("WAIT", "\uad00\ub9dd"),

    ("BLOCK", "\ucc28\ub2e8"),

    ("ALLOW", "\ud5c8\uc6a9"),

    ("WATCH", "\uad00\ucc30"),

    ("BINANCE", "\ubc14\uc774\ub0b8\uc2a4"),

    ("PASS", "\uc815\uc0c1"),

    ("FAIL", "\uc774\uc0c1"),

    ("YES", "\uc804\uc1a1"),

    ("NO", "\ubbf8\uc804\uc1a1"),

    ("active", "\uc815\uc0c1 \uc791\ub3d9"),

    ("inactive", "\uc911\uc9c0"),

    ("success", "\uc815\uc0c1"),

    ("failed", "\uc2e4\ud328"),

    ("unknown", "\ud655\uc778 \ubd88\uac00"),

)





def localize_alert_text(text: str) -> str:

    value = str(text or "")



    for source, target in _PHRASES:

        value = value.replace(source, target)



    lines = value.splitlines()



    for index, line in enumerate(lines):

        stripped = line.lstrip()

        indent = line[: len(line) - len(stripped)]



        for source, target in _PREFIXES:

            if stripped.startswith(source):

                stripped = target + stripped[len(source):]

                break



        lines[index] = indent + stripped



    value = "\n".join(lines)



    for source, target in _TOKENS:

        pattern = (

            r"(?<![A-Za-z0-9_])"

            + re.escape(source)

            + r"(?![A-Za-z0-9_])"

        )

        value = re.sub(pattern, target, value)



    return value

