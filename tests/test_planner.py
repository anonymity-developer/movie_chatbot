import os
import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.ai.planner_tools import registry
from app.ai.graph.planner import SEOUL
from app.ai.planner_calendar import GoogleCalendar
from app.ai.planner_events import EventStream
from app.ai.planner_model import PlannerError
from app.ai.planner_review import Diagnosis, assess, diagnose
from app.ai.planner_worker import Recovery, Task, perform
from app.ai.retrieval.evidence import Evidence
from app.api import main
from app.db.planner import PlannerRepository


class PlannerTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(patch.object(main, "settings", replace(main.settings, cache_db=Path(directory) / "planner.sqlite")))
        stack.enter_context(patch.dict(os.environ, {"PLANNER_DEMO": "true"}))
        self.client = stack.enter_context(TestClient(main.app))

    def chat(self, message):
        response = self.client.post("/api/planner/chat", json={"message": message})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_first_pass_and_partial_detail_failure(self):
        result = self.chat("토요일 오후 3시 이후 강남에서 보고 7시 전에 끝나는 영화")
        self.assertEqual(result["status"], "recommended")
        self.assertEqual(result["replans"], 0)
        self.assertEqual(result["recommendations"][0]["latest_start"], "15:55")
        self.assertEqual(result["errors"][0]["code"], "NO_RUNTIME")
        self.assertNotEqual(result["evidence"][0]["requested_date"], result["evidence"][0]["lookup_date"])
        self.assertTrue(result["theaters"])

    def test_replan_reuses_detail_and_remembers_changed_condition(self):
        first = self.chat("내일 15:00 이후 강남에서 보고 16:40 전에 끝나는 영화")
        self.assertEqual(first["replans"], 1)
        self.assertEqual(first["recommendations"][0]["movie_id"], "90000004")
        self.assertEqual(first["evidence"][1]["tool"], "weekly_box_office")
        requests = [event["data"] for event in first["events"] if event["event"] == "tool.request"]
        self.assertEqual(sum(item["arguments"].get("movie_id") == "90000002" for item in requests), 1)
        self.assertEqual(sum(item["tool"] == "theaters" for item in requests), 1)
        followup = self.chat("같은 조건에서 15:30 이후로 바꿔줘")
        self.assertEqual(followup["replans"], 3)
        self.assertEqual(followup["status"], "no_match")
        self.assertEqual(followup["conditions"], {**first["conditions"], "earliest": "15:30:00"})

    def test_no_tool_clarification_and_logger_failure(self):
        with patch.object(PlannerRepository, "save_event", side_effect=OSError("unavailable")):
            greeting = self.chat("안녕하세요")
        self.assertEqual(greeting["status"], "answered")
        self.assertFalse(greeting["log_saved"])
        self.assertFalse(any(event["event"] == "tool.request" for event in greeting["events"]))
        clarification = self.chat("내일 16:40 전에 끝나는 영화")
        self.assertEqual(clarification["status"], "clarify")
        self.assertFalse(clarification["recommendations"])

    def test_ordered_logs_are_browser_scoped_and_deletable(self):
        result = self.chat("안녕하세요")
        path = f"/api/execution-logs/{result['run_id']}"
        self.assertEqual(self.client.get(path).json(), result["events"])
        self.assertEqual(self.client.get("/api/execution-logs").json()[0]["run_id"], result["run_id"])
        with TestClient(main.app) as visitor:
            self.assertEqual(visitor.get(path).status_code, 404)
        self.assertEqual(self.client.put(path, json={}).status_code, 405)
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_plan_crud_overlap_and_browser_scope(self):
        data = {"title": "첫 영화", "date": "2026-11-23", "starts_at": "15:00", "ends_at": "17:00"}
        first = self.client.post("/api/plans", json=data).json()
        second = self.client.post("/api/plans", json={**data, "title": "둘째 영화"}).json()
        self.assertEqual(second["overlaps"], [first["plan"]])
        path = f"/api/plans/{first['plan']['id']}"
        with TestClient(main.app) as visitor:
            self.assertEqual(visitor.get(path).status_code, 404)
        update = self.client.put(path, json={**data, "starts_at": "17:00", "ends_at": "19:00"})
        self.assertEqual(update.json()["overlaps"], [])
        self.assertEqual(self.client.get(path).json()["starts_at"], "17:00")
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(len(self.client.get("/api/plans").json()), 1)

    def test_live_missing_gms_key_and_registered_tool_shapes(self):
        with patch.dict(os.environ, {"PLANNER_DEMO": "false", "GMS_API_KEY": "", "KOBIS_API_KEY": ""}):
            result = self.chat("내일 15:00 이후 강남에서 보고 19:00 전에 끝나는 영화")
            self.assertEqual(result["errors"][0]["code"], "MODEL_CONFIG_MISSING")
            tools = registry(date(2026, 11, 19), demo=False)
            self.assertEqual(tools["box_office"].run({})["error"]["code"], "MISSING_KEY")
            self.assertEqual(tools["movie_detail"].run({"movie_id": "invalid"})["error"]["code"], "INVALID_INPUT")
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/api/planner/chat", schema["paths"])
        tools = self.client.get("/api/planner/tools").json()
        self.assertEqual(sum(tool["kind"] == "external" for tool in tools), 4)
        self.assertEqual(sum(tool["kind"] == "local" for tool in tools), 3)

    def test_live_adapters_pass_candidate_id_and_preserve_model_tool_order(self):
        from app.ai import planner_model

        answer = {"intent": "plan", "conditions": {"date": "2099-11-20", "earliest": "15:00",
                  "deadline": "18:00", "region": "강남"},
                  "tools": ["box_office", "theaters", "movie_detail"], "reason": "시간 제약이 있는 요청"}
        responses = [
            {"boxOfficeResult": {"dailyBoxOfficeList": [{"movieCd": "12345678", "movieNm": "예시 영화"}]}},
            [{"type": "cinema", "osm_type": "node", "osm_id": 123, "name": "예시관", "display_name": "강남"}],
            {"movieInfoResult": {"movieInfo": {"movieNm": "예시 영화", "showTm": "104"}}},
        ]
        environment = {"PLANNER_DEMO": "false", "GMS_API_KEY": "gms-example",
                       "GMS_BASE_URL": "https://example.com/v1", "KOBIS_API_KEY": "kobis-example",
                       "PLANNER_NOMINATIM_URL": "https://example.com/search"}
        with (
            patch.dict(os.environ, environment),
            patch.object(planner_model, "OpenAI") as openai,
            patch("app.ai.planner_tools.requests.get") as request,
        ):
            completion = openai.return_value.__enter__.return_value.chat.completions.create
            completion.return_value.choices = [Mock(message=Mock(content=f"```json\n{json.dumps(answer)}\n```"))]
            request.side_effect = [Mock(status_code=200, **{"json.return_value": data}) for data in responses]
            result = self.chat("내일 15:00 이후 강남에서 보고 18:00 전에 끝나는 영화")
            self.assertEqual(openai.call_args.kwargs["base_url"], "https://gms.ssafy.io/gmsapi/api.anthropic.com/v1")
            self.assertEqual(openai.call_args.kwargs["default_headers"], {"x-api-key": "gms-example"})
            self.assertEqual(completion.call_args.kwargs["model"], "claude-haiku-5-5")
            self.assertNotIn("response_format", completion.call_args.kwargs)
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["recommendations"][0]["movie_id"], "12345678")
        self.assertEqual([event["data"]["tool"] for event in result["events"] if event["event"] == "tool.request"],
                         ["box_office", "theaters", "movie_detail", "calendar_busy"])
        self.assertEqual(request.call_args_list[2].kwargs["params"]["movieCd"], "12345678")
        self.assertNotIn("Authorization", request.call_args_list[1].kwargs["headers"])
        self.assertEqual(request.call_args_list[1].kwargs["params"]["bounded"], 1)
        self.assertEqual(result["theaters"][0]["url"], "https://www.openstreetmap.org/node/123")
        self.assertNotIn("kobis-example", json.dumps(result))

    def test_previous_request_and_answer_are_recalled(self):
        message = "내일 15:00 이후 강남에서 보고 16:40 전에 끝나는 영화"
        first = self.chat(message)
        second = self.chat("방금 추천 결과 뭐였어?")
        self.assertIn(message, second["answer"])
        self.assertIn(first["answer"], second["answer"])
        self.assertEqual(second["recommendations"], first["recommendations"])

    def test_calendar_register_uses_saved_values_and_filters_recommendations(self):
        date_string = (datetime.now(SEOUL).date() + timedelta(days=1)).isoformat()
        plan = {"title": "약속", "date": date_string, "starts_at": "15:00", "ends_at": "16:40"}
        response = self.client.post("/api/plans", json=plan).json()
        plan_id = response["plan"]["id"]
        negated = self.chat(f"캘린더에 등록하지 마 {plan_id}")
        self.assertIsNone(negated["calendar_event"])
        path = f"/api/plans/{plan_id}/calendar"
        result = self.client.post(path).json()
        self.assertEqual(result["calendar_event"], {"source": "local", "event_id": plan_id})
        self.assertEqual(self.client.post(path).json()["calendar_event"], result["calendar_event"])
        result = self.chat("내일 15:00 이후 강남에서 보고 17:00 전에 끝나는 영화")
        self.assertEqual(result["status"], "no_match")
        self.assertTrue(any("연속 시간이 부족" in item["reason"] for item in result["excluded"]))
        self.assertEqual(self.client.get("/api/calendar/local").json(), [{**plan, "id": plan_id}])
        with TestClient(main.app) as visitor:
            self.assertEqual(visitor.post(path).status_code, 404)
            self.assertEqual(visitor.get("/api/calendar/local").json(), [])
            self.assertEqual(visitor.delete(f"/api/calendar/local/{plan_id}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/calendar/local/{plan_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/plans/{plan_id}").status_code, 200)
        self.assertEqual(self.client.post(path).json()["calendar_event"]["event_id"], plan_id)
        self.assertEqual(self.client.delete(f"/api/plans/{plan_id}").status_code, 204)
        self.assertEqual(self.client.get("/api/calendar/local").json(), [])
        self.assertEqual(self.chat("내일 15:00 이후 강남에서 보고 17:00 전에 끝나는 영화")["status"], "recommended")

    def test_calendar_google_adapter_contract(self):
        calendar = GoogleCalendar(Path("unused"), "primary")
        plan = {"id": "056090f5-bc90-4876-86b0-276f0c9cde74", "date": "2026-11-20",
                "title": "영화", "starts_at": "15:00", "ends_at": "17:00"}
        with patch.object(calendar, "service") as service:
            active = service.return_value.__enter__.return_value
            active.freebusy.return_value.query.return_value.execute.return_value = {
                "calendars": {"primary": {"busy": [{"start": "2026-11-20T06:00:00Z", "end": "2026-11-20T08:00:00Z"}]}}
            }
            self.assertEqual(len(calendar.busy(date(2026, 11, 20))), 1)
            event_id = calendar.register(plan)
            payload = active.events.return_value.insert.call_args.kwargs["body"]
            self.assertEqual(payload["id"], event_id)
            self.assertEqual(payload["summary"], plan["title"])
            self.assertEqual(payload["start"]["dateTime"], "2026-11-20T15:00:00+09:00")

    def test_rag_selects_local_tool_returns_sources_or_no_index(self):
        result = self.chat('"인터스텔라" 제작 배경이 뭐야?')
        self.assertEqual(result["errors"][0]["code"], "NO_INDEX")
        from app.ai.graph import planner

        interpretation = planner.Interpretation(intent="rag", title="인터스텔라", tools=["rag_search"])
        document = Evidence("근거1", "자료 본문", "wikipedia", "제작 배경 자료", "https://example.com/source")
        with (
            patch.dict(os.environ, {"PLANNER_DEMO": "false", "GMS_API_KEY": "gms-example"}),
            patch.object(planner, "complete", return_value=interpretation),
            patch("app.ai.planner_rag.VectorStore"),
            patch("app.ai.planner_rag.OpenAIEmbedder"),
            patch("app.ai.planner_rag.retrieve", return_value=[document]),
            patch.object(main, "settings", replace(main.settings, chroma_dir=Path.cwd())),
        ):
            # An unquoted question exercises the model's routing choice.
            result = self.chat("인터스텔라 제작 배경이 뭐야?")
        self.assertEqual(result["documents"][0]["url_or_file"], "https://example.com/source")
        self.assertEqual(result["evaluation"]["status"], "normal")


class PlannerAdvancedUnitTests(unittest.TestCase):
    def test_gms_key_is_only_used_with_gms_base(self):
        from app.config import GMS_OPENAI_URL, openai_options
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "GMS_API_KEY": "gms-example", "GMS_BASE_URL": "",
                                      "OPENAI_BASE_URL": "https://other.example/v1"}):
            self.assertEqual(openai_options(), {"api_key": "gms-example", "base_url": GMS_OPENAI_URL})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-example", "GMS_API_KEY": "gms-example",
                                      "OPENAI_BASE_URL": "https://other.example/v1"}):
            self.assertEqual(openai_options(), {"api_key": "openai-example", "base_url": "https://other.example/v1"})

    def test_abnormal_execution_causes_planner_replan(self):
        from app.ai.graph import planner

        stream = EventStream()

        def with_anomaly(task, invoke, emit, decide=None, *, worker=True):
            result = perform(task, invoke, emit, decide, worker=worker)
            if task.tool == "theaters":
                emit("tool.request", tool="unregistered", arguments={})
            return result

        with patch.object(planner, "perform", side_effect=with_anomaly):
            result = planner.run_planner("내일 15:00 이후 강남에서 보고 19:00 전에 끝나는 영화", {},
                                         stream.emit, demo=True, worker=True, events=stream.events)
        self.assertEqual(result["status"], "recommended")
        self.assertEqual(result["replans"], 1)
        self.assertTrue(any(e["event"] == "review.phase" and e["data"]["status"] == "abnormal" for e in stream.events))

    def test_review_rule_codes_and_model_diagnosis_are_separate(self):
        stream = EventStream()
        self.assertEqual(assess([], 1)["status"], "undetermined")
        stream.emit("plan.selected", tools=["theaters"])
        for _ in range(2):
            stream.emit("tool.request", tool="movie_detail", arguments={"movie_id": "20200001"})
            stream.emit("replan", tools=[])
        outcome = assess(stream.events, 1)
        self.assertEqual({warning["code"] for warning in outcome["warnings"]},
                         {"DUPLICATE_CALL", "UNPLANNED_CALL", "EXCESSIVE_REPLAN"})
        model = Mock(return_value=Diagnosis(summary="Tool 계획과 호출 경로를 확인하세요."))
        diagnose(outcome, model, stream.emit, stream.events)
        self.assertEqual(stream.events[-1]["event"], "review.diagnosis")
        model.reset_mock()
        diagnose({"status": "normal", "warnings": []}, model, stream.emit)
        model.assert_not_called()
        model.side_effect = PlannerError("MODEL_UNAVAILABLE", "unavailable")
        diagnose(outcome, model, stream.emit)
        self.assertEqual(stream.events[-1]["event"], "review.diagnosis_error")

    def test_worker_retries_same_tool_and_keeps_fixed_arguments(self):
        fail = {"ok": False, "error": {"code": "NO_RESULT", "message": "없음"}}
        succeed = {"ok": True, "data": {"items": []}}
        stream = EventStream()
        task = Task("theaters", {"region": "강남"}, frozenset({"query_hint"}))
        invoke = Mock(side_effect=[fail, succeed])
        model = Mock(return_value=Recovery(action="retry", changes={"query_hint": "극장"}, reason="검색어 조정"))
        self.assertTrue(perform(task, invoke, stream.emit, model).result["ok"])
        invoke.assert_called_with("theaters", {"region": "강남", "query_hint": "극장"})

        invoke = Mock(return_value=fail)
        model.return_value = Recovery(action="retry", changes={"region": "다른 지역"}, reason="조건 변경")
        self.assertTrue(perform(task, invoke, stream.emit, model).replan)
        invoke.assert_called_once_with("theaters", {"region": "강남"})

        invoke = Mock(return_value={"ok": False, "error": {"code": "MISSING_KEY", "message": "없음"}})
        model.reset_mock()
        self.assertFalse(perform(task, invoke, stream.emit, model).replan)
        model.assert_not_called()
