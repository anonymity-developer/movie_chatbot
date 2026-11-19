"""Capture a reproducible planner verification report without external calls."""

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import main


CASES = (
    ("first_pass", "내일 15:00 이후 강남에서 보고 19:00 전에 끝나는 영화", "recommended", 0),
    ("replan_match", "내일 15:00 이후 강남에서 보고 16:40 전에 끝나는 영화", "recommended", 1),
    ("replan_limit", "내일 15:30 이후 강남에서 보고 16:40 전에 끝나는 영화", "no_match", 3),
    ("detail_failure", "내일 14:00 이후 홍대에서 보고 17:00 전에 끝나는 영화", "recommended", 0),
    ("no_tool", "안녕하세요", "answered", 0),
)


def capture():
    results = {}
    with TemporaryDirectory() as directory:
        settings = replace(main.settings, cache_db=Path(directory) / "verification.sqlite")
        with patch.object(main, "settings", settings), patch.dict(os.environ, {"PLANNER_DEMO": "true"}):
            for name, message, status, replans in CASES:
                with TestClient(main.app) as client:
                    response = client.post("/api/planner/chat", json={"message": message})
                    response.raise_for_status()
                    result = response.json()
                    assert (result["status"], result["replans"]) == (status, replans), result
                    assert result["log_saved"]
                    saved = client.get(f"/api/execution-logs/{result['run_id']}")
                    assert saved.json() == result["events"]
                    if name == "detail_failure":
                        assert any(error["code"] == "NO_RUNTIME" for error in result["errors"])
                    if name == "no_tool":
                        assert not any(event["event"] == "tool.request" for event in result["events"])
                    results[name] = {
                        "request": message, "expected": {"status": status, "replans": replans},
                        "result": result,
                    }
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(capture(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"5 planner cases recorded: {args.output}")
