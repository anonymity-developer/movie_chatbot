"""Movie planning, deterministic feasibility checks, and bounded replanning."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date as Date, datetime, time, timedelta, timezone
import json
import os
import re
from time import perf_counter
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.ai.planner_tools import registry

SEOUL = timezone(timedelta(hours=9))
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
    intent: Literal["plan", "help", "greeting"]
    conditions: Conditions = Field(default_factory=Conditions)
    tools: list[Literal["box_office", "movie_detail", "theaters"]] = Field(default_factory=list)
    reason: str = Field(default="", max_length=240)


class PlannerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


class State(TypedDict, total=False):
    message: str
    previous: dict
    conditions: dict
    intent: str
    requested_tools: list[str]
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


def demo_interpret(message: str, today: Date) -> Interpretation:
    if re.fullmatch(r"(안녕(?:하세요)?|반가워(?:요)?)[.!~ ]*", message.strip()):
        return Interpretation(intent="greeting", reason="인사 요청")
    if re.search(r"사용법|도움말|어떻게 (?:써|사용)", message):
        return Interpretation(intent="help", reason="사용법 요청")
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


def interpret(message: str, today: Date, previous: dict, tools: dict, demo: bool) -> Interpretation:
    quick = demo_interpret(message, today)
    if demo or quick.intent != "plan":
        return quick
    key = os.getenv("GMS_API_KEY")
    base_url = os.getenv("GMS_BASE_URL")
    model = os.getenv("GMS_CHAT_MODEL")
    if not (key and base_url and model):
        raise PlannerError("MODEL_CONFIG_MISSING", "서버의 GMS_API_KEY, GMS_BASE_URL, GMS_CHAT_MODEL 설정을 확인해 주세요.")
    catalog = [{"name": name, "description": tool.description, "input": tool.input.model_json_schema()}
               for name, tool in tools.items()]
    try:
        with OpenAI(api_key=key, base_url=base_url, timeout=12, max_retries=0) as client:
            result = client.chat.completions.create(
                model=model, response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": (
                        "영화 관람 요청을 분석하여 JSON만 반환하세요. intent는 plan/help/greeting 중 하나입니다. "
                        "conditions에는 이번 요청에서 새로 지정한 값만 넣으세요. date는 YYYY-MM-DD, "
                        "earliest와 deadline은 한국 시각 HH:MM, region은 검색할 지역 이름입니다. "
                        "전날까지의 집계만 있고 영화관 위치는 상영시간이 아닙니다. tools 배열에 필요한 Tool 이름을 "
                        "작업 순서대로 넣으세요. 관람 계획이면 후보 조회, 영화 상세 조회, 지역 영화관 조회가 필요합니다. "
                        "reason은 분류 근거를 짧게 작성하고 비밀 값 또는 사용자 입력 원문을 반복하지 마세요."
                    )},
                    {"role": "user", "content": json.dumps({
                        "today": str(today), "previous_conditions": previous, "request": message, "tools": catalog,
                    }, ensure_ascii=False)},
                ],
            )
            return Interpretation.model_validate_json(result.choices[0].message.content or "")
    except (OpenAIError, ValidationError, IndexError):
        raise PlannerError("MODEL_UNAVAILABLE", "GMS가 관람 조건을 분석하지 못했습니다. 서버 설정을 확인해 주세요.") from None


def run_planner(message: str, previous: dict, emit, *, demo: bool, today: Date | None = None) -> State:
    today = today or datetime.now(SEOUL).date()
    tools = registry(today, demo)
    cache: dict[tuple[str, str], dict] = {}

    def call(name: str, args: dict) -> dict:
        key = (name, json.dumps(args, sort_keys=True))
        if key in cache:
            return cache[key]
        emit("tool.request", tool=name, arguments=args)
        started = perf_counter()
        result = tools[name].run(args)
        emit("tool.response", tool=name, arguments=args, result=result, elapsed_ms=round(1000 * (perf_counter() - started)))
        cache[key] = result
        return result

    def analyze(state: State):
        analysis = interpret(state["message"], today, state["previous"], tools, demo)
        updates = analysis.conditions.model_dump(mode="json", exclude_none=True)
        fields = {**state["previous"], **updates} if analysis.intent == "plan" else state["previous"]
        emit("analysis.decision", intent=analysis.intent, conditions=fields, reason=analysis.reason)
        return {"intent": analysis.intent, "conditions": fields, "requested_tools": analysis.tools}

    def make_plan(state: State):
        fields = state["conditions"]
        missing = [label for key, label in (("date", "관람 날짜"), ("earliest", "희망 시작 시각"),
                   ("deadline", "종료 마감 시각"), ("region", "지역")) if not fields.get(key)]
        if state["intent"] == "plan" and not missing:
            parsed = Conditions.model_validate(fields)
            if parsed.date < today or parsed.deadline <= parsed.earliest:
                missing = ["오늘 이후의 날짜와 시작 시각보다 늦은 종료 시각"]
        if missing or state["intent"] != "plan":
            plan = []
        else:
            proposed = list(dict.fromkeys(state["requested_tools"]))
            if set(proposed) == set(TOOLS_FOR_PLAN) and proposed.index("box_office") < proposed.index("movie_detail"):
                plan = proposed
            else:
                plan = TOOLS_FOR_PLAN
        emit("plan.selected", tools=plan, missing=missing)
        return {"plan": plan, "missing": missing}

    def execute(state: State):
        fields = state["conditions"]
        errors = list(state.get("errors", []))
        evidence = list(state.get("evidence", []))
        excluded = list(state.get("excluded", []))
        recommendations = []
        seen = {item["movie_id"] for item in excluded}
        theater_list = state.get("theaters", [])

        def find_theaters():
            result = call("theaters", {"region": fields["region"]})
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
        box = call(source, arguments)
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
            # Workers only perform I/O. Events and results are emitted in candidate order.
            def detail(item):
                args = {"movie_id": item["movie_id"]}
                started = perf_counter()
                return args, tools["movie_detail"].run(args), round(1000 * (perf_counter() - started))

            for item in entries:
                emit("tool.request", tool="movie_detail", arguments={"movie_id": item["movie_id"]})
            if entries:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    for item, (args, result, elapsed) in zip(entries, pool.map(detail, entries)):
                        emit("tool.response", tool="movie_detail", arguments=args, result=result, elapsed_ms=elapsed)
                        if result["ok"]:
                            recommendations.append({**item, **result["data"]})
                        else:
                            errors.append({"tool": "movie_detail", **args, **result["error"]})
                            excluded.append({**item, "reason": result["error"]["message"]})
        if state["replans"] == 0 and state["plan"][-1] == "theaters":
            theater_list = find_theaters()
        return {"recommendations": recommendations, "excluded": excluded, "theaters": theater_list,
                "evidence": evidence, "errors": errors, "fatal": fatal}

    def judge(state: State):
        conditions = Conditions.model_validate(state["conditions"])
        earliest = datetime.combine(conditions.date, conditions.earliest)
        end = datetime.combine(conditions.date, conditions.deadline)
        fits = []
        excluded = list(state["excluded"])
        for movie in state["recommendations"]:
            required = movie["runtime"] + BUFFER_MINUTES
            latest = end - timedelta(minutes=required)
            if latest >= earliest:
                fits.append({**movie, "required_minutes": required, "latest_start": latest.strftime("%H:%M")})
            else:
                excluded.append({**movie, "reason": f"입장 여유 포함 {required}분이 희망 시간 범위를 초과합니다."})
        emit("constraints.checked", matching=len(fits), excluded=len(excluded), buffer_minutes=BUFFER_MINUTES)
        return {"recommendations": fits, "excluded": excluded}

    def replan(state: State):
        count = state["replans"] + 1
        source = "weekly_box_office" if count == 1 else "box_office"
        reason = "시간을 충족하는 후보 없음. 주간 순위에서 후보 보충" if count == 1 else "시간을 충족하는 후보 없음. 이전 집계일 탐색"
        emit("replan", attempt=count, reason=reason, tools=[source, "movie_detail"])
        return {"replans": count}

    def respond(state: State):
        if state["intent"] in {"greeting", "help"}:
            return {"status": "answered", "answer": "안녕하세요. 관람 날짜, 희망 시작 시각, 종료 마감 시각과 지역을 알려 주세요."}
        if state["missing"]:
            return {"status": "clarify", "answer": "확인할 조건이 있어요. " + ", ".join(state["missing"]) + "을 알려 주세요."}
        if state.get("recommendations"):
            return {"status": "recommended", "answer": (
                f"시간 조건에 맞는 후보 {len(state['recommendations'])}편을 찾았습니다. "
                f"러닝타임과 입장 여유 {BUFFER_MINUTES}분을 반영했어요. "
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
        s["recommendations"] or s["fatal"] or s["replans"] >= MAX_REPLANS
    ) else "replan", ["respond", "replan"])
    graph.add_edge("replan", "execute")
    graph.add_edge("respond", END)
    return graph.compile().invoke({"message": message, "previous": previous, "replans": 0}, {"recursion_limit": 24})
