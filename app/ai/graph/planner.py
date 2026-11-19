"""Movie planning, deterministic feasibility checks, and bounded replanning."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date as Date, datetime, time, timedelta
import json
import re
from time import perf_counter
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator

from app.ai.planner_tools import registry
from app.ai.planner_calendar import SEOUL
from app.ai.planner_model import complete
from app.ai.planner_review import assess
from app.ai.planner_worker import Task, perform

BUFFER_MINUTES = 15
MAX_REPLANS = 3
TOOLS_FOR_PLAN = ["box_office", "movie_detail", "theaters"]


class Conditions(BaseModel):
    date: Date | None = None
    earliest: time | None = None
    deadline: time | None = None
    region: str | None = Field(default=None, max_length=75)

    @field_validator("earliest", "deadline")
    @classmethod
    def local_time(cls, value):
        if value is not None and (value.tzinfo is not None or value.second or value.microsecond):
            raise ValueError("Use local time at minute precision")
        return value


class Interpretation(BaseModel):
    intent: Literal["plan", "help", "greeting", "rag", "calendar_register", "recall"]
    conditions: Conditions = Field(default_factory=Conditions)
    tools: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=240)
    title: str | None = Field(default=None, max_length=200)
    year: int | None = None


class State(TypedDict, total=False):
    message: str
    previous: dict
    conditions: dict
    intent: str
    requested_tools: list[str]
    special: dict
    plan: list[str]
    missing: list[str]
    recommendations: list[dict]
    excluded: list[dict]
    theaters: list[dict]
    evidence: list[dict]
    errors: list[dict]
    fatal: bool
    replans: int
    answer: str
    status: str
    documents: list[dict]
    calendar_event: dict
    busy: list[dict]
    calendar_checked: bool
    worker_replan: bool
    review_replan: bool


def demo_interpret(message: str, today: Date) -> Interpretation:
    if re.fullmatch(r"(안녕(?:하세요)?|반가워(?:요)?)[.!~ ]*", message.strip()):
        return Interpretation(intent="greeting", reason="인사 요청")
    if re.search(r"사용법|도움말|어떻게 (?:써|사용)", message):
        return Interpretation(intent="help", reason="사용법 요청")
    if re.search(r"(캘린더|일정).*(등록|추가)|(등록|추가).*(캘린더|일정)", message):
        return Interpretation(intent="calendar_register", reason="일정 등록 요청")
    if re.search(r"(직전|방금|아까).*(결과|추천|답변|요청).*(뭐|다시|보여|알려)", message):
        return Interpretation(intent="recall", reason="직전 요청·응답 확인")
    if found := re.search(r'[「"“](.+?)[」"”].*(?:줄거리|배경|제작|촬영)', message):
        return Interpretation(intent="rag", title=found[1], reason="작품 자료 질문")
    values = {}
    explicit = re.search(r"\b\d{4}-\d{2}-\d{2}\b", message)
    if explicit:
        try:
            values["date"] = Date.fromisoformat(explicit[0])
        except ValueError:
            pass
    else:
        relative = re.search(r"모레|내일|오늘|[월화수목금토일]요일", message)
        if relative:
            word = relative[0]
            offset = {"오늘": 0, "내일": 1, "모레": 2}.get(word)
            if offset is None:
                offset = ("월화수목금토일".index(word[0]) - today.weekday()) % 7
            values["date"] = today + timedelta(days=offset)

    pattern = r"(?:(오전|오후|밤|저녁|아침)\s*)?(\d{1,2})(?::(\d{2})|\s*시(?:\s*(\d{1,2})\s*분)?)\s*(이후|부터|이전|전|까지)?"
    for match in re.finditer(pattern, message):
        period, hour, minutes_a, minutes_b, relation = match.groups()
        hour = int(hour)
        end = relation in {"이전", "전", "까지"} or re.match(r"\s*안에", message[match.end():])
        if end and not period and not minutes_a and 1 <= hour < 12 and values.get("earliest", time()).hour >= 12:
            period = "오후"
        if period and 1 <= hour <= 12:
            hour = hour % 12 + (12 if period in {"오후", "저녁", "밤"} else 0)
        minute = int(minutes_a or minutes_b or 0)
        if hour < 24 and minute < 60:
            values["deadline" if end else "earliest"] = time(hour, minute)
    region = re.search(r"([가-힣A-Za-z0-9]+)(?:\s*역)?(?:에서|\s*근처|\s*주변)", message)
    if region and region[1] not in {"영화관", "어디", "조건"}:
        values["region"] = region[1]
    return Interpretation(intent="plan", conditions=Conditions(**values), tools=TOOLS_FOR_PLAN,
                          reason="관람 계획 요청")


def interpret(message, today, previous, last_turn, tools, demo, decide) -> Interpretation:
    quick = demo_interpret(message, today)
    if demo or quick.intent != "plan":
        return quick
    catalog = [{"name": name, "description": tool.description, "input": tool.input.model_json_schema()}
               for name, tool in tools.items()]
    return decide(Interpretation, (
        "영화 관람 요청을 분석하세요. plan은 관람 계획, rag는 줄거리·제작 배경 등 문서 질문입니다. "
        "calendar_register는 사용자가 명시적으로 저장된 계획의 일정 등록을 요청했을 때만 선택하세요. "
        "recall은 직전 답변을 다시 묻는 요청입니다. 문서 질문이면 사용자가 말한 작품의 정확한 제목과 "
        "명시한 개봉 연도를 넣으세요. '그 영화'는 직전 결과가 한 편일 때만 연결하세요. "
        "conditions에는 이번 요청에서 새로 지정한 값만 넣으세요. 시간은 한국 시각 HH:MM입니다. "
        "tools에는 필요한 등록 Tool 이름을 실행 순서로 넣으세요. 기본 관람 후보는 box_office, "
        "movie_detail, theaters입니다. reason은 판단 요약만 작성하고 비밀 값을 반복하지 마세요."
    ), {"today": str(today), "previous_conditions": previous, "previous_turn": last_turn, "request": message, "tools": catalog})


def run_planner(message: str, previous: dict, emit, *, demo: bool, today: Date | None = None,
                last_turn=None, local_tools=None, events=None, worker=False, worker_llm=False) -> State:
    today = today or datetime.now(SEOUL).date()
    tools = {**registry(today, demo), **(local_tools or {})}
    cache: dict[tuple[str, str], dict] = {}

    def model(role, output=emit):
        def decide(schema, instruction, data):
            output("model.called", role=role)
            return complete(schema, instruction, data)
        return decide

    def invoke(name, args, output):
        output("tool.request", tool=name, arguments=args)
        started = perf_counter()
        result = tools[name].run(args)
        output("tool.response", tool=name, arguments=args, result=result, elapsed_ms=round(1000 * (perf_counter() - started)))
        return result

    def call(name: str, args: dict, editable=frozenset()):
        key = (name, json.dumps(args, sort_keys=True))
        if key in cache:
            return cache[key]
        result = perform(Task(name, args, editable), lambda n, a: invoke(n, a, emit), emit,
                         model("worker") if worker_llm and not demo else None, worker=worker)
        cache[key] = result
        return result

    def analyze(state: State):
        analysis = interpret(state["message"], today, state["previous"], last_turn or {}, tools, demo, model("planner"))
        updates = analysis.conditions.model_dump(mode="json", exclude_none=True)
        fields = {**state["previous"], **updates} if analysis.intent == "plan" else state["previous"]
        emit("analysis.decision", intent=analysis.intent, conditions=fields, reason=analysis.reason)
        # A write needs the plan ID from the user's own text, not an ID produced by the model.
        registration = re.fullmatch(
            r"\s*(?:캘린더|일정)(?:에)?\s*등록\s+([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\s*",
            state["message"],
        )
        special = {"title": analysis.title, "year": analysis.year, "plan_id": registration[1] if registration else None}
        return {"intent": analysis.intent, "conditions": fields, "requested_tools": analysis.tools, "special": special}

    def make_plan(state: State):
        if state["intent"] != "plan":
            spec = {"rag": ("rag_search", "title", "영화 제목"),
                    "calendar_register": ("calendar_register", "plan_id", "저장한 계획 ID")}.get(state["intent"])
            plan = [spec[0]] if spec and spec[0] in tools and state["special"][spec[1]] else []
            missing = [spec[2]] if spec and not plan else []
            emit("plan.selected", tools=plan, missing=missing)
            return {"plan": plan, "missing": missing}
        fields = state["conditions"]
        missing = [label for key, label in (("date", "관람 날짜"), ("earliest", "희망 시작 시각"),
                   ("deadline", "종료 마감 시각"), ("region", "지역")) if not fields.get(key)]
        if state["intent"] == "plan" and not missing:
            parsed = Conditions.model_validate(fields)
            if parsed.date < today or parsed.deadline <= parsed.earliest:
                missing = ["오늘 이후의 날짜와 시작 시각보다 늦은 종료 시각"]
        if missing:
            plan = []
        else:
            proposed = list(dict.fromkeys(state["requested_tools"]))
            if set(proposed) == set(TOOLS_FOR_PLAN) and proposed.index("box_office") < proposed.index("movie_detail"):
                plan = proposed
            else:
                plan = TOOLS_FOR_PLAN
            if "calendar_busy" in tools:
                plan = [*plan, "calendar_busy"]
        emit("plan.selected", tools=plan, missing=missing)
        return {"plan": plan, "missing": missing}

    def execute(state: State):
        if state["intent"] != "plan":
            name = state["plan"][0]
            args = ({"title": state["special"]["title"], "year": state["special"]["year"], "question": state["message"]}
                    if name == "rag_search" else {"plan_id": state["special"]["plan_id"]})
            result = call(name, args).result
            if not result["ok"]:
                return {"errors": [{"tool": name, **result["error"]}]}
            return {"documents": result["data"]["documents"]} if name == "rag_search" else {"calendar_event": result["data"]}
        fields = state["conditions"]
        errors = list(state.get("errors", []))
        evidence = list(state.get("evidence", []))
        excluded = list(state.get("excluded", []))
        recommendations = list(state.get("recommendations", []))
        seen = {item["movie_id"] for item in excluded + recommendations}
        theater_list = state.get("theaters", [])
        worker_replan = False

        def find_theaters():
            nonlocal worker_replan
            work = call("theaters", {"region": fields["region"]}, frozenset({"query_hint"}))
            worker_replan |= work.replan
            result = work.result
            if result["ok"]:
                return result["data"]["items"]
            errors.append({"tool": "theaters", **result["error"]})
            return []

        early_theaters = state["plan"].index("theaters") < state["plan"].index("box_office")
        if state["replans"] == 0 and early_theaters:
            theater_list = find_theaters()
        source = "weekly_box_office" if state["replans"] == 1 else "box_office"
        arguments = {"requested_date": fields["date"]}
        if source == "box_office":
            arguments["offset"] = state["replans"]
        work = call(source, arguments)
        worker_replan |= work.replan
        box = work.result
        if state["replans"] == 0 and state["plan"][1] == "theaters":
            theater_list = find_theaters()
        fatal = False
        if not box["ok"]:
            errors.append({"tool": source, **box["error"]})
            fatal = box["error"]["code"] in {"MISSING_KEY", "AUTH_FAILED", "RATE_LIMITED"}
        else:
            evidence.append({"tool": source, "source": "KOBIS", **box["data"]})
            entries = []
            for item in box["data"]["items"]:
                if item["movie_id"] not in seen:
                    entries.append(item)
                    seen.add(item["movie_id"])
            def detail(item):
                args = {"movie_id": item["movie_id"]}
                trace = []

                def record(event, **data):
                    trace.append((event, data))

                outcome = perform(Task("movie_detail", args), lambda n, a: invoke(n, a, record), record,
                                  model("worker", record) if worker_llm and not demo else None, worker=worker)
                return args, outcome, trace
            if entries:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    for item, (args, outcome, trace) in zip(entries, pool.map(detail, entries)):
                        for kind, data in trace:
                            emit(kind, **data)
                        worker_replan |= outcome.replan
                        result = outcome.result
                        if result["ok"]:
                            recommendations.append({**item, **result["data"]})
                        else:
                            errors.append({"tool": "movie_detail", **args, **result["error"]})
                            excluded.append({**item, "reason": result["error"]["message"]})
        if state["replans"] == 0 and state["plan"].index("theaters") > state["plan"].index("movie_detail"):
            theater_list = find_theaters()
        busy = state.get("busy", [])
        calendar_checked = state.get("calendar_checked", False)
        if state["replans"] == 0 and "calendar_busy" in tools:
            result = call("calendar_busy", {"date": fields["date"]}).result
            if result["ok"]:
                busy = result["data"]["busy"]
                calendar_checked = True
            else:
                errors.append({"tool": "calendar_busy", **result["error"]})
        return {"recommendations": recommendations, "excluded": excluded, "theaters": theater_list,
                "evidence": evidence, "errors": errors, "fatal": fatal, "worker_replan": worker_replan,
                "busy": busy, "calendar_checked": calendar_checked}

    def judge(state: State):
        if state["intent"] != "plan":
            return {}
        conditions = Conditions.model_validate(state["conditions"])
        earliest = datetime.combine(conditions.date, conditions.earliest, SEOUL)
        end = datetime.combine(conditions.date, conditions.deadline, SEOUL)
        busy = sorted((datetime.fromisoformat(item["starts_at"].replace("Z", "+00:00")).astimezone(SEOUL),
                       datetime.fromisoformat(item["ends_at"].replace("Z", "+00:00")).astimezone(SEOUL))
                      for item in state["busy"])
        gaps = []
        cursor = earliest
        for start, stop in busy:
            start = start.replace(second=0, microsecond=0)
            if stop.second or stop.microsecond:
                stop = stop.replace(second=0, microsecond=0) + timedelta(minutes=1)
            if cursor < min(start, end):
                gaps.append((cursor, min(start, end)))
            cursor = max(cursor, stop)
        if cursor < end:
            gaps.append((cursor, end))
        fits = []
        excluded = list(state["excluded"])
        for movie in state["recommendations"]:
            required = movie["runtime"] + BUFFER_MINUTES
            duration = timedelta(minutes=required)
            possible = next(((stop - duration, stop) for start, stop in reversed(gaps) if stop - start >= duration), None)
            if possible:
                latest, stop = possible
                fits.append({**movie, "required_minutes": required, "latest_start": latest.strftime("%H:%M"),
                             "latest_end": stop.strftime("%H:%M")})
            else:
                excluded.append({**movie, "reason": f"입장 여유를 포함해 {required}분이 필요하며 이용 가능한 연속 시간이 부족합니다."})
        emit("constraints.checked", matching=len(fits), excluded=len(excluded), buffer_minutes=BUFFER_MINUTES)
        phase_review = {"status": "undetermined", "warnings": []}
        if worker and events is not None:
            index = max((i for i, item in enumerate(events) if item["event"] in {"plan.selected", "replan"}), default=0)
            phase_review = assess(events[index:], MAX_REPLANS)
            emit("review.phase", **phase_review)
        return {"recommendations": fits, "excluded": excluded, "review_replan": phase_review["status"] == "abnormal"}

    def replan(state: State):
        count = state["replans"] + 1
        source = "weekly_box_office" if count == 1 else "box_office"
        reason = "시간을 충족하는 후보 없음. 주간 순위에서 후보 보충" if count == 1 else "시간을 충족하는 후보 없음. 이전 집계일 탐색"
        if state["review_replan"] or state["worker_replan"]:
            reason = "실행 검증 또는 Worker 요청을 반영해 후보 탐색 작업을 다시 구성"
        emit("replan", attempt=count, reason=reason, tools=[source, "movie_detail"])
        return {"replans": count}

    def respond(state: State):
        if state["intent"] in {"greeting", "help"}:
            return {"status": "answered", "answer": "안녕하세요. 관람 날짜, 희망 시작 시각, 종료 마감 시각과 지역을 알려 주세요."}
        if state["missing"]:
            return {"status": "clarify", "answer": "확인할 조건이 있어요. " + ", ".join(state["missing"]) + "을 알려 주세요."}
        if state["intent"] == "recall":
            previous = last_turn or {}
            return {"status": "answered", "answer": (
                f"직전 요청은 ‘{previous['request']}’였어요. {previous['answer']}" if previous else "아직 이전 요청이 없습니다."
            ), "recommendations": previous.get("recommendations", [])}
        if state["intent"] in {"rag", "calendar_register"}:
            if state.get("errors"):
                return {"status": "unavailable", "answer": state["errors"][0]["message"]}
            if state["intent"] == "rag":
                return {"status": "answered", "answer": "작품 질문과 관련된 근거 문서를 찾았습니다."}
            return {"status": "registered", "answer": "저장한 관람 계획을 캘린더에 등록했습니다."}
        if state.get("recommendations"):
            return {"status": "recommended", "answer": (
                f"시간 조건에 맞는 후보 {len(state['recommendations'])}편을 찾았습니다. "
                f"러닝타임과 입장 여유 {BUFFER_MINUTES}분을 반영했어요. "
                + ("등록 일정과 겹치지 않는 시간을 계산했어요. " if state["calendar_checked"] else "캘린더 일정은 확인하지 못했어요. ")
                +
                "영화관 목록은 위치 참고용입니다. 상영 편성과 실제 시각은 영화관에서 확인해 주세요."
            )}
        if state.get("fatal"):
            failure = next(error for error in reversed(state["errors"]) if error["tool"] in {"box_office", "weekly_box_office"})
            return {"status": "unavailable", "answer": failure["message"]}
        return {"status": "no_match", "answer": (
            f"재계획 {state['replans']}회 후에도 시간 조건에 맞는 영화를 찾지 못했습니다. "
            "시작 가능 시각이나 종료 마감 시각을 조정해 주세요."
        )}

    def traced(name, operation):
        def node(state):
            emit("stage.enter", stage=name)
            try:
                return operation(state)
            finally:
                emit("stage.exit", stage=name)
        return node

    graph = StateGraph(State)
    for name, node in (("analyze", analyze), ("plan", make_plan), ("execute", execute),
                       ("judge", judge), ("replan", replan), ("respond", respond)):
        graph.add_node(name, traced(name, node))
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "plan")
    graph.add_conditional_edges("plan", lambda s: "execute" if s["plan"] else "respond", ["execute", "respond"])
    graph.add_edge("execute", "judge")
    graph.add_conditional_edges("judge", lambda s: "respond" if (
        s["intent"] != "plan" or s["fatal"] or s["replans"] >= MAX_REPLANS or
        (s["recommendations"] and not (s["worker_replan"] or s["review_replan"]))
    ) else "replan", ["respond", "replan"])
    graph.add_edge("replan", "execute")
    graph.add_edge("respond", END)
    return graph.compile().invoke({"message": message, "previous": previous, "replans": 0}, {"recursion_limit": 24})
