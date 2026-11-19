import os
import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.ai.planner_tools import registry
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
        self.assertEqual(len(self.client.get("/api/planner/tools").json()), 4)

    def test_live_adapters_pass_candidate_id_and_preserve_model_tool_order(self):
        from app.ai.graph import planner

        answer = {"intent": "plan", "conditions": {"date": "2099-11-20", "earliest": "15:00",
                  "deadline": "18:00", "region": "강남"},
                  "tools": ["box_office", "theaters", "movie_detail"], "reason": "시간 제약이 있는 요청"}
        responses = [
            {"boxOfficeResult": {"dailyBoxOfficeList": [{"movieCd": "12345678", "movieNm": "예시 영화"}]}},
            {"documents": [{"place_name": "예시관", "road_address_name": "강남", "place_url": "https://example.com"}]},
            {"movieInfoResult": {"movieInfo": {"movieNm": "예시 영화", "showTm": "104"}}},
        ]
        environment = {"PLANNER_DEMO": "false", "GMS_API_KEY": "gms-example", "GMS_CHAT_MODEL": "example",
                       "GMS_BASE_URL": "https://example.com/v1", "KOBIS_API_KEY": "kobis-example",
                       "KAKAO_REST_API_KEY": "kakao-example"}
        with (
            patch.dict(os.environ, environment),
            patch.object(planner, "OpenAI") as openai,
            patch("app.ai.planner_tools.requests.get") as request,
        ):
            completion = openai.return_value.__enter__.return_value.chat.completions.create
            completion.return_value.choices = [Mock(message=Mock(content=json.dumps(answer)))]
            request.side_effect = [Mock(status_code=200, **{"json.return_value": data}) for data in responses]
            result = self.chat("내일 15:00 이후 강남에서 보고 18:00 전에 끝나는 영화")
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["recommendations"][0]["movie_id"], "12345678")
        self.assertEqual([event["data"]["tool"] for event in result["events"] if event["event"] == "tool.request"],
                         ["box_office", "theaters", "movie_detail"])
        self.assertEqual(request.call_args_list[2].kwargs["params"]["movieCd"], "12345678")
        self.assertEqual(request.call_args_list[1].kwargs["headers"]["Authorization"], "KakaoAK kakao-example")
        self.assertNotIn("kobis-example", json.dumps(result))
