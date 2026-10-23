import importlib.util
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.db.conversations import ConversationRepository


class ConversationRepositoryTests(unittest.TestCase):
    def test_history_is_scoped_by_browser_and_movie(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ConversationRepository(Path(directory) / "app.sqlite3")
            first = repository.append_exchange("browser-a", "movie-1", "질문1", "답변1", [{"citation_id": "근거1"}])
            self.assertEqual(first, repository.append_exchange("browser-a", "movie-1", "질문2", "답변2", []))
            self.assertIsNone(repository.history("browser-a", "movie-2")[0])
            self.assertIsNone(repository.history("browser-b", "movie-1")[0])
            identity, messages = repository.history("browser-a", "movie-1")
            self.assertEqual(identity, first)
            self.assertEqual([item["role"] for item in messages], ["user", "assistant", "user", "assistant"])
            self.assertEqual(messages[1]["citations"], [{"citation_id": "근거1"}])


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "FastAPI is not installed")
class ConversationApiTests(unittest.TestCase):
    def test_cookie_isolation_movie_history_and_guide(self):
        from fastapi.testclient import TestClient
        import app.api.main as api

        class FakeGraph:
            def __init__(self, mode):
                self.mode = mode

            def invoke(self, state):
                content = "관람 가이드 [근거1]" if self.mode == "guide" else "답변 [근거1]"
                return {"answer": content, "citations": [{"citation_id": "근거1", "title": "문서"}]}

        with tempfile.TemporaryDirectory() as directory:
            local_settings = replace(api.settings, cache_db=Path(directory) / "app.sqlite3")
            with patch.object(api, "settings", local_settings), patch.object(api, "index_status", return_value={"ready": True}), patch.object(api, "create_qa_graph", side_effect=lambda *, mode="chat": FakeGraph(mode)), patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
                first = TestClient(api.app)
                second = TestClient(api.app)
                sent = first.post("/api/chat", json={"movie_id": "157336", "question": "배경은?"})
                self.assertEqual(sent.status_code, 200)
                self.assertIn("httponly", sent.headers["set-cookie"].lower())
                conversation_id = sent.json()["conversation_id"]
                self.assertEqual(len(first.get("/api/conversations/157336").json()["messages"]), 2)
                self.assertEqual(second.get("/api/conversations/157336").json()["messages"], [])
                self.assertEqual(first.get("/api/conversations/11").json()["messages"], [])
                wrong = second.post("/api/chat", json={"movie_id": "157336", "question": "다른 질문", "conversation_id": conversation_id})
                self.assertEqual(wrong.status_code, 409)
                guide = first.post("/api/guides", json={"movie_id": "157336"})
                self.assertEqual(guide.status_code, 200)
                self.assertEqual(guide.json()["conversation_id"], conversation_id)
                self.assertEqual(len(first.get("/api/conversations/157336").json()["messages"]), 4)


if __name__ == "__main__":
    unittest.main()
