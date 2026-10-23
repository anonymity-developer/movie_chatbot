import json
import tempfile
import unittest
from pathlib import Path

from app.ai.data_prep.movies import DataError, load_movies, movie_index_text
from app.ai.data_prep.wiki import iter_wiki_documents
from app.ai.graph.qa import cited_evidence
from app.ai.indexing.chunks import split_text, wiki_chunks
from app.ai.indexing.vector_store import VectorStore
from app.ai.retrieval.evidence import Evidence
from scripts.index_data import movie_chunks


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[float(len(text))] for text in texts]


class FakeCollection:
    def __init__(self):
        self.items = {}

    def upsert(self, *, ids, documents, metadatas, embeddings):
        for chunk_id, text, metadata, embedding in zip(ids, documents, metadatas, embeddings):
            self.items[chunk_id] = (text, metadata, embedding)

    def get(self, *, ids, include):
        found = [chunk_id for chunk_id in ids if chunk_id in self.items]
        return {
            "ids": found,
            "documents": [self.items[chunk_id][0] for chunk_id in found],
            "metadatas": [self.items[chunk_id][1] for chunk_id in found],
        }


class DataPipelineTests(unittest.TestCase):
    def test_movies_have_unique_ids_and_searchable_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "movies.json"
            movie = {
                "movie_id": "1", "title": "영화", "original_title": "Movie",
                "year": 2020, "genres": ["드라마"], "director": ["감독"],
                "cast": ["배우"], "overview": "줄거리", "tmdb_url": "https://example.com/1",
            }
            path.write_text(json.dumps([movie], ensure_ascii=False), encoding="utf-8")
            self.assertIn("줄거리: 줄거리", movie_index_text(load_movies(path)[0]))
            path.write_text(json.dumps([movie, movie], ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(DataError):
                load_movies(path)

    def test_chunks_keep_overlap_without_extra_tail(self):
        self.assertEqual(split_text("abcdefghij", 4, 1), ["abcd", "defg", "ghij"])
        self.assertEqual(split_text("abcd", 4, 1), ["abcd"])
        with self.assertRaises(ValueError):
            split_text("abc", 4, 4)

    def test_shared_wiki_page_ids_are_movie_scoped(self):
        doc = {"movie_id": "1", "page_id": 12, "title": "문서", "url": "https://ko.wikipedia.org/wiki/X", "text": "abcdefghij"}
        a = wiki_chunks(doc, 4, 1)
        doc["movie_id"] = "2"
        b = wiki_chunks(doc, 4, 1)
        self.assertEqual(len(a), 3)
        self.assertNotEqual(a[0].chunk_id, b[0].chunk_id)
        self.assertEqual(a[0].metadata["source_url_or_file"], doc["url"])

    def test_repeated_upsert_does_not_increase_count(self):
        movie = {"movie_id": "1", "title": "영화", "overview": "줄거리", "tmdb_url": "https://example.com", "year": 2020}
        collection = FakeCollection()
        chunks = movie_chunks([movie])
        embedder = FakeEmbedder()
        VectorStore.upsert(collection, chunks, embedder)
        VectorStore.upsert(collection, chunks, embedder)
        self.assertEqual(len(collection.items), 1)
        self.assertEqual(embedder.calls, 1)

    def test_invalid_citation_is_rejected(self):
        evidence = [Evidence("근거1", "본문", "wikipedia", "제목", "https://ko.wikipedia.org/wiki/X")]
        self.assertEqual(len(cited_evidence("설명 [근거1]", evidence)), 1)
        self.assertEqual(cited_evidence("설명 [근거1] [근거9]", evidence), [])

    def test_provided_corpus_is_readable(self):
        root = Path(__file__).resolve().parent.parent / "data"
        movies = load_movies(root / "movies.json")
        movie_ids = {movie["movie_id"] for movie in movies}
        documents = list(iter_wiki_documents(root / "movielab_wiki" / "wikipedia", movie_ids))
        self.assertEqual(len(movies), 964)
        self.assertEqual(len(documents), 3725)


if __name__ == "__main__":
    unittest.main()
