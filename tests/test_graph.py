import importlib.util
import unittest
from types import SimpleNamespace

from app.ai.graph.qa import build_graph
from app.ai.graph.chat_routing import OFF_TOPIC_REPLY


@unittest.skipUnless(importlib.util.find_spec("langgraph"), "LangGraph is not installed")
class GraphTests(unittest.TestCase):
    def test_guide_uses_two_nodes_and_returns_only_cited_source(self):
        class MetaCollection:
            def get(self, **kwargs):
                return {
                    "ids": ["1"],
                    "documents": ["제목: 영화\n장르: SF"],
                    "metadatas": [{"title": "영화", "source_url": "https://example.com/1"}],
                }

        class DocCollection:
            def count(self):
                return 0

        captured = []

        def create(**kwargs):
            captured.append(kwargs["messages"])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="SF 영화입니다. [근거1]"))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        graph = build_graph(MetaCollection(), DocCollection(), None, client, "test-model", 2, mode="guide")
        result = graph.invoke({"movie_id": "1", "question": "관람 가이드"})
        self.assertEqual([item["citation_id"] for item in result["citations"]], ["근거1"])
        self.assertIn("관람 가이드", captured[0][1]["content"])
        self.assertIn("retrieve", graph.get_graph().nodes)
        self.assertIn("generate", graph.get_graph().nodes)

    def test_model_off_topic_redirect_needs_no_citation(self):
        class MetaCollection:
            def get(self, **kwargs):
                return {"ids": ["1"], "documents": ["제목: 영화"], "metadatas": [{"title": "영화", "source_url": "https://example.com"}]}

        class DocCollection:
            def count(self):
                return 0

        def create(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=OFF_TOPIC_REPLY))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        graph = build_graph(MetaCollection(), DocCollection(), None, client, "test-model", 1)
        result = graph.invoke({"movie_id": "1", "question": "로또 번호 추천해줘"})
        self.assertEqual(result["answer"], OFF_TOPIC_REPLY)
        self.assertEqual(result["citations"], [])


if __name__ == "__main__":
    unittest.main()
