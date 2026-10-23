import json
import tempfile
import unittest
from pathlib import Path

from app.ai.data_prep.collection import BundleWikiSource, collect_documents
from app.ai.data_prep.movies import load_movies
from app.db.collection_cache import CollectionCache, query_key


class FakeModel:
    def complete_json(self, system, user):
        if '"queries"' in system:
            return {"queries": ["우주 배경", "우주 배경"]}
        return {"selected_ids": []}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.wiki = root / "movielab_wiki" / "wikipedia"
        self.wiki.mkdir(parents=True)
        (self.wiki.parent / "wiki_manifest.json").write_text(
            json.dumps({"11": {"page_ids": [101, 202]}}), encoding="utf-8"
        )
        for page_id, title in ((101, "스타워즈 (영화)"), (202, "다스 베이더")):
            (self.wiki / f"11_{page_id}.json").write_text(
                json.dumps({
                    "page_id": page_id, "title": title,
                    "url": f"https://ko.wikipedia.org/wiki/{page_id}",
                    "text": f"{title}에 관한 본문", "movie_id": "11",
                }, ensure_ascii=False), encoding="utf-8"
            )
        self.movie = {"movie_id": "11", "title": "스타워즈 에피소드 4: 새로운 희망", "original_title": "Star Wars"}
        self.source = BundleWikiSource(self.wiki)
        self.cache = CollectionCache(root / "cache.sqlite3")

    def test_own_document_is_forced_and_repeat_uses_cache(self):
        first = collect_documents(self.movie, "배경을 알려줘", FakeModel(), self.source, self.cache)
        self.assertEqual(len(first.queries), 2)
        self.assertEqual(first.selected_ids, ["101"])
        self.assertEqual(first.excluded_ids, ["202"])
        self.assertEqual(self.source.search_calls, 2)
        self.assertEqual(self.source.fetch_calls, 1)
        second = collect_documents(self.movie, "배경을 알려줘", FakeModel(), self.source, self.cache)
        self.assertEqual(second.cache_hits, 2)
        self.assertEqual(self.source.search_calls, 2)
        self.assertEqual(self.source.fetch_calls, 1)
        self.assertEqual(second.selected_ids, first.selected_ids)

    def test_cache_key_normalizes_spaces_and_case(self):
        self.assertEqual(query_key("wiki", "11", "  Star  Wars "), query_key("wiki", "11", "star wars"))
        self.assertNotEqual(query_key("wiki", "11", "star wars"), query_key("wiki", "12", "star wars"))

    def test_manifest_page_ids_are_resolved_by_movie_title(self):
        root = Path(__file__).resolve().parents[1] / "data"
        movie = load_movies(root / "movies.json")[0]
        source = BundleWikiSource(root / "movielab_wiki" / "wikipedia")
        candidates = source.search(movie, movie["title"])
        own = next(item for item in candidates if item.source_id == source.own_page_ids[movie["movie_id"]])
        self.assertEqual(own.title, movie["title"])
        self.assertNotEqual(own.source_id, "151821")


if __name__ == "__main__":
    unittest.main()
