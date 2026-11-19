"""Task contracts and limited recovery for the Planner–Worker workflow."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.planner_model import PlannerError


@dataclass(frozen=True)
class Task:
    tool: str
    arguments: dict
    editable: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class WorkResult:
    result: dict
    replan: bool = False


class Recovery(BaseModel):
    action: Literal["retry", "skip", "replan"]
    changes: dict = Field(default_factory=dict)
    reason: str = Field(max_length=240)


RECOVERABLE = {"NO_RESULT", "NOT_FOUND", "INVALID_INPUT"}
MAX_RETRIES = 1


def perform(task: Task, invoke, emit, decide=None, *, worker=True) -> WorkResult:
    if worker:
        emit("worker.task", tool=task.tool, arguments=task.arguments, editable=sorted(task.editable))
    args = task.arguments
    result = invoke(task.tool, args)
    replan = False
    if worker and decide and not result["ok"] and result["error"]["code"] in RECOVERABLE:
        for _ in range(MAX_RETRIES):
            try:
                decision = decide(Recovery, "실패한 작업을 검토하세요. retry는 editable 필드만 고칠 수 있습니다. 고정 인자는 유지하세요. 재시도할 수 없으면 skip 또는 replan을 고르세요. reason에 비밀 값을 넣지 마세요.", {
                    "tool": task.tool, "arguments": args, "editable": sorted(task.editable), "failure": result["error"],
                })
            except PlannerError as exc:
                emit("worker.decision_error", tool=task.tool, code=exc.code)
                break
            valid = decision.changes and set(decision.changes) <= task.editable
            updated = {**args, **decision.changes}
            action = decision.action
            if action == "retry" and (not valid or updated == args):
                action = "replan"
            emit("worker.decision", tool=task.tool, action=action, reason=decision.reason)
            if action == "retry":
                result = invoke(task.tool, updated)
            replan = action == "replan"
    if worker:
        emit("worker.result", tool=task.tool, ok=result["ok"], replan=replan,
             code=None if result["ok"] else result["error"]["code"])
    return WorkResult(result, replan)
