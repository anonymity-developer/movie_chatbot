import unittest

from app.ai.retrieval.evidence import mmr_indices, retrieve


class MmrTests(unittest.TestCase):
    def test_mmr_prefers_diverse_second_chunk(self):
        query = [1.0, 0.0]
        vectors = [[0.9, 0.436], [0.89, 0.456], [0.7, -0.714]]
        self.assertEqual(mmr_indices(query, vectors, 2), [0, 2])

    def test_metadata_is_fixed_and_strategies_return_different_documents(self):
        class Meta:
            def get(self, **kwargs):
                return {
                    "ids": ["movie-1"],
                    "documents": ["Movie metadata"],
                    "metadatas": [{"title": "Movie", "source_url": "https://example.com/movie"}],
                }

        class Docs:
            def count(self):
                return 3

            def query(self, **kwargs):
                self.last_query = kwargs
                count = kwargs["n_results"]
                return {
                    "ids": [["a", "b", "c"][:count]],
                    "documents": [["A", "B", "C"][:count]],
                    "metadatas": [[
                        {"source_type": "wiki", "title": title, "source_url_or_file": "https://example.com"}
                        for title in "ABC"[:count]
                    ]],
                    "embeddings": [[[0.9, 0.436], [0.89, 0.456], [0.7, -0.714]][:count]],
                }

        class Embedder:
            def embed(self, texts):
                return [[1.0, 0.0]]

        docs = Docs()
        args = ("movie-1", "question", Meta(), docs, Embedder(), 3)
        similar = retrieve(*args)
        diverse = retrieve(*args, strategy="mmr", fetch_k=3)
        self.assertEqual([item.text for item in similar], ["Movie metadata", "A", "B"])
        self.assertEqual([item.text for item in diverse], ["Movie metadata", "A", "C"])
        self.assertEqual(docs.last_query["where"], {"movie_id": "movie-1"})
        self.assertIn("embeddings", docs.last_query["include"])


if __name__ == "__main__":
    unittest.main()
