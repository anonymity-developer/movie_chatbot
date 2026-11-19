"""Compare baseline and Worker variants on the same simulated provider responses."""

import argparse
from datetime import date
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from app.ai.graph import planner
from app.ai.planner_events import EventStream
from app.ai.planner_review import assess
from app.ai.planner_tools import Tool, ToolFailure, registry
from app.ai.planner_worker import Recovery


def measure(case, worker, worker_llm):
    day = date(2026, 11, 19)
    tools = registry(day, demo=True)
    theater = tools["theaters"]

    def theater_lookup(args):
        if case in {"recoverable", "replan_requested"} and args.query_hint == "영화관":
            raise ToolFailure("NO_RESULT", "합성 응답: 초기 검색 결과 없음")
        if case == "fatal_key":
            raise ToolFailure("MISSING_KEY", "합성 응답: 키 누락")
        return theater.handler(args)

    tools["theaters"] = Tool(theater.description, theater.input, theater.output, theater_lookup)

    def model(schema, instruction, data):
        if schema is Recovery:
            if case == "replan_requested":
                return Recovery(action="replan", reason="다른 후보 탐색 필요")
            return Recovery(action="retry", changes={"query_hint": "극장"}, reason="보조 검색어 변경")
        return planner.Interpretation(intent="plan", conditions=planner.Conditions(
            date="2026-11-20", earliest="15:00", deadline="19:00" if case == "replan_requested" else "16:40", region="강남"
        ), tools=planner.TOOLS_FOR_PLAN, reason="고정된 검증 요청")

    stream = EventStream()
    with patch.object(planner, "registry", return_value=tools), patch.object(planner, "complete", side_effect=model):
        started = perf_counter()
        result = planner.run_planner(
            "내일 15:00 이후 강남에서 보고 16:40 전에 끝나는 영화", {}, stream.emit,
            demo=False, today=day, worker=worker, worker_llm=worker_llm, events=stream.events,
        )
        elapsed = round((perf_counter() - started) * 1000)
    counts = {role: sum(e["event"] == "model.called" and e["data"]["role"] == role for e in stream.events)
              for role in ("planner", "worker", "logger")}
    return {
        "status": result["status"], "replans": result["replans"], "model_calls": counts, "elapsed_ms": elapsed,
        "theater_results": len(result["theaters"]), "evaluation": assess(stream.events, planner.MAX_REPLANS),
        "flow": [e for e in stream.events if e["event"] in {
            "stage.enter", "tool.request", "worker.task", "worker.result", "worker.decision", "replan", "review.phase",
        }],
    }


def compare():
    scenarios = {}
    for state in ("normal", "recoverable", "fatal_key", "replan_requested"):
        outcomes = {"baseline": measure(state, False, False),
                    "worker_no_llm": measure(state, True, False), "worker_llm": measure(state, True, True)}
        assert outcomes["baseline"]["model_calls"]["worker"] == 0 and outcomes["worker_no_llm"]["model_calls"]["worker"] == 0
        assert outcomes["worker_llm"]["model_calls"]["worker"] == int(state in {"recoverable", "replan_requested"})
        assert outcomes["worker_llm"]["theater_results"] == int(state in {"normal", "recoverable"})
        if state == "replan_requested":
            assert outcomes["worker_llm"]["replans"] > outcomes["baseline"]["replans"]
        scenarios[state] = outcomes
    return {"provider": "simulated", "scenarios": scenarios}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    arguments.output.write_text(json.dumps(compare(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(arguments.output)
