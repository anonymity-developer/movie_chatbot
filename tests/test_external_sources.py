import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import patch

from app.ai.data_prep.collection import LiveWikiSource
from app.ai.data_prep.wikidata import WikidataSource
from app.ai.data_prep.wikimedia_http import USER_AGENT, WikimediaClient
from app.ai.evaluation import factual_match, percentile, representative_cases, unknown_acknowledged
from app.ai.indexing.chunks import wiki_chunks
from app.ai.retrieval.evidence import retrieve
from app.ai.graph.qa import cited_evidence
from app.ai.data_prep.movies import load_movies


class FakeWikiClient:
    def get_json(self, host, params):
        if params.get("list") == "search":
            return {"query": {"search": [
                {"pageid": 101, "title": "스타워즈 (영화)", "snippet": "<span>영화</span> 설명"},
            ]}}
        return {"query": {"pages": [
            {"pageid": 101, "title": "스타워즈 (영화)", "extract": "원문 설명", "fullurl": "https://ko.wikipedia.org/wiki/스타워즈_(영화)"},
        ]}}


class FakeWikidataClient:
    def __init__(self, tmdb_id="11"):
        self.tmdb_id = tmdb_id

    def get_json(self, host, params):
        if "titles" in params:
            return {"entities": {"Q1": {
                "id": "Q1", "labels": {"ko": {"value": "스타워즈"}},
                "claims": {
                    "P4947": [{"mainsnak": {"datavalue": {"value": self.tmdb_id}}}],
                    "P57": [{"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}],
                },
            }}}
        return {"entities": {"Q2": {"labels": {"ko": {"value": "조지 루카스"}}}}}


class ExternalSourceTests(unittest.TestCase):
    def test_live_wiki_search_and_fetch(self):
        source = LiveWikiSource(FakeWikiClient())
        movie = {"movie_id": "11", "title": "스타워즈", "original_title": "Star Wars"}
        candidates = source.search(movie, "스타워즈")
        self.assertEqual(source.own_page_ids["11"], "101")
        document = source.fetch(movie, candidates[0], "스타워즈")
        self.assertEqual(document["source_type"], "wikipedia")
        self.assertTrue(document["url"].startswith("https://ko.wikipedia.org/"))

    def test_wikidata_requires_exact_tmdb_id_and_has_separate_chunk_id(self):
        movie = {"movie_id": "11", "title": "스타워즈"}
        self.assertIsNone(WikidataSource(FakeWikidataClient("12")).fetch(movie, "스타워즈 (영화)"))
        document = WikidataSource(FakeWikidataClient()).fetch(movie, "스타워즈 (영화)")
        self.assertIn("조지 루카스", document["text"])
        chunk = wiki_chunks(document, 1000, 200)[0]
        self.assertEqual(chunk.chunk_id, "11:wikidata:Q1:0")
        self.assertEqual(chunk.metadata["source_type"], "wikidata")
        self.assertEqual(chunk.metadata["license"], "CC0")

    def test_wikimedia_http_retries_429_and_identifies_client(self):
        calls = []
        delays = []

        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, "rate limited", {"Retry-After": "2"}, None)
            return io.BytesIO(b'{"query":{}}')

        client = WikimediaClient(opener=opener, sleep=delays.append, clock=lambda: 10.0)
        with patch.dict(os.environ, {"MOVIELAB_WIKIMEDIA_USER_AGENT": USER_AGENT}):
            self.assertEqual(client.get_json("ko.wikipedia.org", {"action": "query"}), {"query": {}})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].get_header("User-agent"), USER_AGENT)
        self.assertIn(2.0, delays)
        with self.assertRaises(ValueError):
            client.get_json("example.com", {})

    def test_evaluation_uses_five_cases_and_nearest_rank_percentile(self):
        movies = load_movies(Path(__file__).resolve().parents[1] / "data" / "movies.json")
        self.assertEqual(len(representative_cases(movies)), 5)
        unknown_cases = representative_cases(movies, "obscure_unknown")
        self.assertEqual(len(unknown_cases), 6)
        self.assertIsNone(unknown_cases[-1]["expected"])
        self.assertTrue(factual_match("감독은 크리스토퍼 놀란입니다", "크리스토퍼 놀란"))
        self.assertTrue(unknown_acknowledged("제공된 문서에서 확인되지 않습니다."))
        self.assertTrue(unknown_acknowledged("모릅니다."))
        self.assertFalse(unknown_acknowledged("양말은 파란색입니다."))
        self.assertEqual(percentile([1, 2, 3, 4, 5], 95), 5)

    def test_evaluation_records_five_baseline_and_rag_answers(self):
        import scripts.evaluate_rag as evaluation
        from app.config import Settings

        movies = load_movies(Path(__file__).resolve().parents[1] / "data" / "movies.json")
        cases = representative_cases(movies)
        expected = {case["movie_id"]: case["expected"] for case in cases}

        class Store:
            def __init__(self, path):
                self.client = self

            def get_collection(self, name):
                return object()

        class Graph:
            def invoke(self, state):
                return {"answer": expected[state["movie_id"]], "citations": [{"citation_id": "근거1", "url_or_file": "https://example.com"}]}

        def create(**kwargs):
            title = kwargs["messages"][-1]["content"]
            answer = next(case["expected"] for case in cases if case["title"] in title)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(evaluation, "VectorStore", Store), patch.object(evaluation, "build_graph", return_value=Graph()):
            report = evaluation.evaluate(Settings.from_env(), client, object())
        self.assertEqual(report["summary"]["baseline_correct"], 5)
        self.assertEqual(report["summary"]["rag_correct"], 5)
        self.assertIn("RAG 전후 비교 실험", evaluation.markdown(report))

    def test_collect_indexes_both_source_types_without_network(self):
        from fastapi.testclient import TestClient
        import app.api.main as api

        class Model:
            def __init__(self, model):
                pass

            def complete_json(self, system, user):
                return {"queries": []} if '"queries"' in system else {"selected_ids": []}

        class Second:
            def fetch(self, movie, title):
                return {
                    "movie_id": movie["movie_id"], "page_id": "Q1", "title": movie["title"],
                    "text": "감독: 검증된 감독", "url": "https://www.wikidata.org/wiki/Q1",
                    "source_type": "wikidata", "license": "CC0",
                }

        class Store:
            def __init__(self, path):
                pass

            def collection(self, name, metadata):
                return object()

            def upsert(self, collection, chunks, embedder):
                self.chunks = list(chunks)
                Store.saved = self.chunks
                return len(self.chunks)

        class Embedder:
            def __init__(self, model):
                pass

        with tempfile.TemporaryDirectory() as directory:
            local_settings = replace(api.settings, cache_db=Path(directory) / "cache.sqlite3", enable_wikidata=True)
            with patch.object(api, "settings", local_settings), patch.object(api, "OpenAIChatModel", Model), patch.object(api, "WikidataSource", Second), patch.object(api, "VectorStore", Store), patch.object(api, "OpenAIEmbedder", Embedder), patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
                response = TestClient(api.app).post("/api/collect", json={"movie_id": "157336", "question": "감독은 누구인가요?"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["source_type"] for item in response.json()["selected"]}, {"wikipedia", "wikidata"})
        self.assertEqual({item.metadata["source_type"] for item in Store.saved}, {"wikipedia", "wikidata"})

    def test_retrieval_can_cite_wikipedia_and_wikidata_together(self):
        class Meta:
            def get(self, **kwargs):
                return {"ids": ["11"], "documents": ["movie"], "metadatas": [{"title": "Movie", "source_url": "https://example.com/movie"}]}

        class Docs:
            def count(self):
                return 2

            def query(self, **kwargs):
                return {
                    "ids": [["wiki", "data"]], "documents": [["wiki text", "data facts"]],
                    "metadatas": [[
                        {"source_type": "wikipedia", "title": "Wiki", "source_url_or_file": "https://ko.wikipedia.org/wiki/Wiki", "license": "CC BY-SA 4.0"},
                        {"source_type": "wikidata", "title": "Data", "source_url_or_file": "https://www.wikidata.org/wiki/Q1", "license": "CC0"},
                    ]],
                }

        class Embedder:
            def embed(self, texts):
                return [[1.0, 0.0]]

        evidence = retrieve("11", "question", Meta(), Docs(), Embedder(), 3)
        citations = cited_evidence("wiki [근거2] data [근거3]", evidence)
        self.assertEqual({item["source_type"] for item in citations}, {"wikipedia", "wikidata"})
        self.assertEqual({item["license"] for item in citations}, {"CC BY-SA 4.0", "CC0"})


if __name__ == "__main__":
    unittest.main()
