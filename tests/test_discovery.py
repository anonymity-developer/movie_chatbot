import importlib.util
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.ai.retrieval.discovery import discover_movies, hard_filters


class FakeCollection:
    def __init__(self, ids):
        self.ids = ids
        self.queried = False

    def count(self):
        return len(self.ids)

    def query(self, **kwargs):
        self.queried = True
        return {"ids": [self.ids]}


class FakeEmbedder:
    def __init__(self, model=None):
        pass

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeModel:
    def __init__(self, model=None):
        self.called = False

    def complete_json(self, system, user):
        self.called = True
        return {"selected_ids": ["1", "2", "unknown"]}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.movies = {
            "1": {"movie_id": "1", "title": "한국 SF", "year": 2014, "genres": ["SF"], "original_language": "ko", "production_countries": ["South Korea"], "overview": "우주 이야기"},
            "2": {"movie_id": "2", "title": "해외 SF", "year": 2014, "genres": ["SF"], "original_language": "en", "production_countries": ["United States"], "overview": "우주 이야기"},
            "3": {"movie_id": "3", "title": "오래된 영화", "year": 2009, "genres": ["SF"], "original_language": "ko", "overview": "미래 이야기"},
            "4": {"movie_id": "4", "title": "한국 드라마", "year": 2014, "genres": ["드라마"], "original_language": "ko", "overview": "가족 이야기"},
        }

    def test_explicit_filters_prevent_wrong_matches_even_if_model_selects_them(self):
        collection = FakeCollection(["2", "3", "4", "1"])
        result = discover_movies("2014년 한국 SF 영화", collection, FakeEmbedder(), FakeModel(), self.movies)
        self.assertEqual([item["movie_id"] for item in result.items], ["1"])
        self.assertEqual(result.candidate_count, 1)
        self.assertTrue(collection.queried)

    def test_year_range_and_empty_index(self):
        self.assertTrue(hard_filters("2010년대 영화").matches(self.movies["1"]))
        self.assertFalse(hard_filters("2010년대 영화").matches(self.movies["3"]))
        self.assertFalse(hard_filters("2014년 이후 영화").matches(self.movies["3"]))
        with self.assertRaises(LookupError):
            discover_movies("한국 SF", FakeCollection([]), FakeEmbedder(), FakeModel(), self.movies)


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "FastAPI is not installed")
class DiscoveryApiTests(unittest.TestCase):
    def test_model_auth_error_is_safe_for_browser(self):
        from fastapi.testclient import TestClient
        from httpx import Request, Response
        from openai import AuthenticationError
        import app.api.main as api

        error = AuthenticationError(
            "private provider detail", response=Response(401, request=Request("POST", "https://api.openai.com")), body=None
        )

        class Store:
            def __init__(self, path):
                self.client = self

            def get_collection(self, name):
                return FakeCollection(["157336"])

        with tempfile.TemporaryDirectory() as directory:
            local_settings = replace(api.settings, chroma_dir=Path(directory))
            with patch.object(api, "settings", local_settings), patch.object(api, "VectorStore", Store), patch.object(api, "discover_movies", side_effect=error), patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
                response = TestClient(api.app).post("/api/discover", json={"condition": "SF 영화"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "MODEL_AUTH_FAILED")
        self.assertNotIn("private provider detail", response.text)

    def test_discovery_response_contains_only_selected_real_movies(self):
        from fastapi.testclient import TestClient
        import app.api.main as api

        class Store:
            def __init__(self, path):
                self.client = self

            def get_collection(self, name):
                return FakeCollection(["157336", "11"])

        class Model:
            def __init__(self, model):
                pass

            def complete_json(self, system, user):
                return {"selected_ids": ["157336", "11", "not-a-movie"]}

        with tempfile.TemporaryDirectory() as directory:
            local_settings = replace(api.settings, chroma_dir=Path(directory))
            with patch.object(api, "settings", local_settings), patch.object(api, "VectorStore", Store), patch.object(api, "OpenAIEmbedder", FakeEmbedder), patch.object(api, "OpenAIChatModel", Model), patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
                response = TestClient(api.app).post("/api/discover", json={"condition": "2014년 SF 영화"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["movie_id"] for item in response.json()["items"]], ["157336"])


if __name__ == "__main__":
    unittest.main()
