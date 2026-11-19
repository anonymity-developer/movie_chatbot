"""Rule-based log evaluation and optional diagnosis of abnormal executions."""

from collections import Counter
import json

from pydantic import BaseModel, Field

from app.ai.planner_model import PlannerError


class Diagnosis(BaseModel):
    summary: str = Field(max_length=500)


def assess(events, limit):
    plans = [e for e in events if e["event"] in {"plan.selected", "replan"} and "tools" in e["data"]]
    requests = [e for e in events if e["event"] == "tool.request"]
    if not plans or (not requests and any(e["data"]["tools"] for e in plans)):
        return {"status": "undetermined", "warnings": []}
    counts = Counter((e["data"]["tool"], json.dumps(e["data"]["arguments"], sort_keys=True)) for e in requests)
    warnings = [{"code": "DUPLICATE_CALL", "tool": tool, "count": count}
                for (tool, _), count in counts.items() if count > 1]
    replans = sum(e["event"] == "replan" for e in plans)
    if replans > limit:
        warnings.append({"code": "EXCESSIVE_REPLAN", "count": replans})
    allowed = set()
    for event in events:
        if event["event"] in {"plan.selected", "replan"} and "tools" in event["data"]:
            allowed = set(event["data"]["tools"])
        if event["event"] == "tool.request" and event["data"]["tool"] not in allowed:
            warnings.append({"code": "UNPLANNED_CALL", "tool": event["data"]["tool"]})
    return {"status": "abnormal" if warnings else "normal", "warnings": warnings}


def diagnose(review, complete, emit, events=()):
    if review["status"] != "abnormal" or complete is None:
        return
    try:
        trace = [{"event": event["event"], "sequence": event["sequence"],
                  "tool": event["data"].get("tool"), "action": event["data"].get("action")}
                 for event in events if event["event"] in {"tool.request", "replan", "worker.decision"}]
        result = complete(Diagnosis, "규칙 기반 경고를 참고해 가능한 원인과 수정 조치를 세 문장 이내로 작성하세요.",
                          {"assessment": review, "trace": trace[-40:]})
        emit("review.diagnosis", summary=result.summary)
    except PlannerError as exc:
        emit("review.diagnosis_error", code=exc.code)
