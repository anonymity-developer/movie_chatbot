import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.ai.graph.chat_routing import ACKNOWLEDGEMENT_REPLY, OFF_TOPIC_REPLY, local_chat_reply


class ChatRoutingTests(unittest.TestCase):
    def test_recent_question_and_off_topic(self):
        history = [
            {"role": "user", "content": "타노스의 목표는 뭐야?"},
            {"role": "assistant", "content": "인피니티 스톤을 모으는 것입니다."},
        ]
        self.assertIn("타노스의 목표는 뭐야?", local_chat_reply("내가 방금 뭐 물어봤더라", history))
        self.assertEqual(local_chat_reply("ㅇㅇ", history), ACKNOWLEDGEMENT_REPLY)
        self.assertEqual(local_chat_reply("저녁메뉴 추천좀", history), OFF_TOPIC_REPLY)
        self.assertIsNone(local_chat_reply("이 영화에서 저녁 메뉴가 나오나?", history))
        self.assertIsNone(local_chat_reply("감독은 누구야?", history))

    def test_local_reply_needs_no_model_key_or_index(self):
        from fastapi.testclient import TestClient
        import app.api.main as api

        with tempfile.TemporaryDirectory() as directory:
            local_settings = replace(api.settings, cache_db=Path(directory) / "cache.sqlite3")
            with patch.object(api, "settings", local_settings), patch.object(api, "create_qa_graph") as graph, patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                client = TestClient(api.app)
                first = client.post("/api/chat", json={"movie_id": "157336", "question": "저녁메뉴 추천좀"})
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first.json()["answer"], OFF_TOPIC_REPLY)
                self.assertEqual(first.json()["citations"], [])
                self.assertEqual(first.json()["response_type"], "local")
                second = client.post("/api/chat", json={"movie_id": "157336", "question": "내가 방금 뭐 물어봤더라"})
                self.assertIn("저녁메뉴 추천좀", second.json()["answer"])
                graph.assert_not_called()

    def test_runtime_comes_directly_from_movie_metadata_with_a_citation(self):
        from fastapi.testclient import TestClient
        import app.api.main as api

        with tempfile.TemporaryDirectory() as directory:
            local_settings = replace(api.settings, cache_db=Path(directory) / "cache.sqlite3")
            with patch.object(api, "settings", local_settings), patch.object(api, "create_qa_graph") as graph, patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                response = TestClient(api.app).post("/api/chat", json={"movie_id": "157336", "question": "인터스텔라 상영 시간은 몇 분인가요?"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["response_type"], "metadata")
                self.assertIn("169분", response.json()["answer"])
                self.assertEqual(response.json()["citations"][0]["source_type"], "movie_metadata")
                self.assertIn("169분", response.json()["citations"][0]["excerpt"])
                self.assertEqual(response.json()["citations"][0]["citation_id"], "근거1")
                graph.assert_not_called()


if __name__ == "__main__":
    unittest.main()
