import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Request
from openai import APITimeoutError

import app.api.main as api
from app.ai.indexing.vector_store import OpenAIEmbedder


class ServiceTimeoutTests(unittest.TestCase):
    def test_interactive_model_calls_have_bounded_time_and_no_retry(self):
        with patch.object(api, "VectorStore"), patch.object(api, "OpenAIEmbedder") as embedder, patch.object(api, "build_graph"), patch("openai.OpenAI") as client:
            api.create_qa_graph(mode="guide")
        embedder.assert_called_once_with(api.settings.embedding_model, timeout=8.0, max_retries=0)
        client.assert_called_once_with(timeout=20.0, max_retries=0)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), patch("openai.OpenAI") as client:
            OpenAIEmbedder("test-model", timeout=8.0, max_retries=0)
        client.assert_called_once_with(timeout=8.0, max_retries=0)

    def test_model_timeout_is_safe_for_browser(self):
        class TimeoutGraph:
            def invoke(self, _state):
                raise APITimeoutError(request=Request("POST", "https://example.test"))

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(api.settings, cache_db=Path(directory) / "cache.sqlite3")
            with patch.object(api, "settings", settings), patch.object(api, "index_status", return_value={"ready": True}), patch.object(api, "create_qa_graph", return_value=TimeoutGraph()), patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
                response = TestClient(api.app).post("/api/guides", json={"movie_id": "157336"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "MODEL_TIMEOUT")
        self.assertNotIn("example.test", response.text)


if __name__ == "__main__":
    unittest.main()
