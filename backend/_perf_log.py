"""Tiny NDJSON perf probe for this debug session. Not used for product analytics."""
from __future__ import annotations

import json
import time
from pathlib import Path


def perf_log(location: str, message: str, data: dict, hypothesis_id: str = "A") -> None:
    # #region agent log
    try:
        path = Path(__file__).resolve().parents[1] / "debug-1e8c6b.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "1e8c6b",
                        "timestamp": int(time.time() * 1000),
                        "location": location,
                        "message": message,
                        "hypothesisId": hypothesis_id,
                        "data": data,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
