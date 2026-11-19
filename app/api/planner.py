"""HTTP routes for the planner and its browser-scoped records."""

from datetime import date, datetime
import os
from time import perf_counter
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from app.ai.graph.planner import MAX_REPLANS, SEOUL, run_planner
from app.ai.planner_calendar import calendar_tools
from app.ai.planner_events import EventStream
from app.ai.planner_model import PlannerError, complete
from app.ai.planner_rag import rag_tool
from app.ai.planner_review import assess, diagnose
from app.ai.planner_tools import registry
from app.db.planner import PlannerRepository


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class Event(BaseModel):
    sequence: int
    timestamp: str
    event: str
    data: dict


class ChatResult(BaseModel):
    run_id: str
    mode: str
    status: str
    answer: str
    conditions: dict
    recommendations: list[dict]
    excluded: list[dict]
    theaters: list[dict]
    evidence: list[dict]
    errors: list[dict]
    replans: int
    events: list[Event]
    log_saved: bool
    elapsed_ms: int
    evaluation: dict
    model_calls: dict[str, int]
    documents: list[dict]
    calendar_event: dict | None


class PlanInput(BaseModel):
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    date: date
    starts_at: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    ends_at: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def check_times(self):
        if self.starts_at >= self.ends_at:
            raise ValueError("종료 시각은 시작 시각보다 늦어야 합니다.")
        return self


class Plan(PlanInput):
    id: str
    created_at: str


class PlanWrite(BaseModel):
    plan: Plan
    overlaps: list[Plan]


class ExecutionRun(BaseModel):
    run_id: str
    started_at: str
    event_count: int


def missing_record():
    raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "저장된 항목을 찾을 수 없습니다."})


def create_router(session_for, settings_for):
    router = APIRouter(prefix="/api", tags=["movie planner"])

    def session(request: Request, response: Response):
        return session_for(request, response)

    def repository():
        return PlannerRepository(settings_for().cache_db)

    Session = Annotated[str, Depends(session)]
    Repository = Annotated[PlannerRepository, Depends(repository)]

    @router.get("/planner/tools")
    def tools(sid: Session, repo: Repository):
        registered = {**registry(datetime.now(SEOUL).date(), demo=False),
                      **calendar_tools(repo, sid, demo=True, loopback=True),
                      "rag_search": rag_tool(settings_for(), demo=False)}
        return [{"name": name, "description": tool.description, "kind": tool.kind,
                 "input": tool.input.model_json_schema(), "output": tool.output.model_json_schema()}
                for name, tool in registered.items()]

    @router.post("/planner/chat", response_model=ChatResult)
    def chat(payload: ChatInput, request: Request, sid: Session, repo: Repository):
        demo = os.getenv("PLANNER_DEMO", "").lower() in {"1", "true", "yes"}
        mode = "demo" if demo else "live"
        run_id = str(uuid4())
        started = perf_counter()
        stream = EventStream(lambda event: repo.save_event(sid, run_id, event))
        worker = os.getenv("PLANNER_WORKER", "true").lower() in {"1", "true", "yes"}
        worker_llm = os.getenv("PLANNER_WORKER_LLM", "true").lower() in {"1", "true", "yes"}
        stream.emit("run.start", mode=mode, worker=worker, worker_llm=worker_llm and not demo and worker)
        previous = repo.memory(sid)
        last_turn = repo.previous_turn(sid)
        loopback = bool(request.client and request.client.host in {"127.0.0.1", "::1"}
                        and request.url.hostname in {"127.0.0.1", "::1", "localhost"})
        local_tools = calendar_tools(repo, sid, demo=demo, loopback=loopback)
        local_tools["rag_search"] = rag_tool(settings_for(), demo=demo)
        try:
            result = run_planner(payload.message, previous, stream.emit, demo=demo, last_turn=last_turn,
                                 local_tools=local_tools, events=stream.events, worker=worker, worker_llm=worker_llm)
        except PlannerError as exc:
            stream.emit("run.error", code=exc.code, message=exc.message)
            result = {"status": "unavailable", "answer": exc.message, "conditions": previous,
                      "errors": [{"tool": "GMS", "code": exc.code, "message": exc.message}]}
        turn = stream.redact({
            "request": payload.message, "answer": result["answer"],
            "recommendations": result.get("recommendations", []),
        })
        repo.update_memory(sid, previous, result["conditions"], turn if result.get("intent") != "recall" else None)
        review = assess(stream.events, MAX_REPLANS)
        stream.emit("review.result", **review)

        def diagnose_with_model(schema, instruction, data):
            stream.emit("model.called", role="logger")
            return complete(schema, instruction, data)

        diagnose(review, None if demo else diagnose_with_model, stream.emit, stream.events)
        counts = {role: sum(event["event"] == "model.called" and event["data"]["role"] == role
                            for event in stream.events) for role in ("planner", "worker", "logger")}
        elapsed = round(1000 * (perf_counter() - started))
        stream.emit("run.end", status=result["status"], elapsed_ms=elapsed)
        public = {field: result.get(field, []) for field in ("recommendations", "excluded", "theaters", "evidence", "errors")}
        return stream.redact({
            **public, "run_id": run_id, "mode": mode, "status": result["status"], "answer": result["answer"],
            "conditions": result["conditions"], "replans": result.get("replans", 0), "events": stream.events,
            "log_saved": not stream.failed, "elapsed_ms": elapsed,
            "evaluation": review, "model_calls": counts, "documents": result.get("documents", []),
            "calendar_event": result.get("calendar_event"),
        })

    @router.post("/plans/{plan_id}/calendar", response_model=ChatResult)
    def register_in_calendar(plan_id: UUID, request: Request, sid: Session, repo: Repository):
        if not repo.plan(sid, str(plan_id)):
            missing_record()
        return chat(ChatInput(message=f"캘린더에 등록 {plan_id}"), request, sid, repo)

    @router.get("/execution-logs", response_model=list[ExecutionRun])
    def list_logs(sid: Session, repo: Repository):
        return repo.runs(sid)

    @router.get("/execution-logs/{run_id}", response_model=list[Event])
    def detail_log(run_id: UUID, sid: Session, repo: Repository):
        return repo.events(sid, str(run_id)) or missing_record()

    @router.delete("/execution-logs/{run_id}", status_code=204)
    def delete_log(run_id: UUID, sid: Session, repo: Repository):
        if not repo.delete_run(sid, str(run_id)):
            missing_record()
        return Response(status_code=204)

    @router.get("/plans", response_model=list[Plan])
    def list_plans(sid: Session, repo: Repository):
        return repo.plans(sid)

    @router.get("/plans/{plan_id}", response_model=Plan)
    def detail_plan(plan_id: UUID, sid: Session, repo: Repository):
        return repo.plan(sid, str(plan_id)) or missing_record()

    @router.post("/plans", status_code=201, response_model=PlanWrite)
    def create_plan(payload: PlanInput, sid: Session, repo: Repository):
        return repo.write_plan(sid, payload.model_dump(mode="json"))

    @router.put("/plans/{plan_id}", response_model=PlanWrite)
    def update_plan(plan_id: UUID, payload: PlanInput, sid: Session, repo: Repository):
        return repo.write_plan(sid, payload.model_dump(mode="json"), str(plan_id)) or missing_record()

    @router.delete("/plans/{plan_id}", status_code=204)
    def delete_plan(plan_id: UUID, sid: Session, repo: Repository):
        if not repo.delete_plan(sid, str(plan_id)):
            missing_record()
        return Response(status_code=204)

    return router
