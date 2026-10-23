"""Three-stage, question-driven document collection using the local wiki corpus."""

from __future__ import annotations

import json
import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from app.db.collection_cache import CollectionCache
from app.ai.data_prep.wikimedia_http import WikimediaClient


@dataclass(frozen=True)
class Candidate:
    source_id: str
    title: str
    summary: str
    path: Path | None


class ChatModel(Protocol):
    def complete_json(self, system: str, user: str) -> dict[str, Any]: ...


class OpenAIChatModel:
    def __init__(self, model: str):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt before collecting documents") from exc
        self.client = OpenAI()
        self.model = model

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        result = json.loads(response.choices[0].message.content or "{}")
        if not isinstance(result, dict):
            raise ValueError("model response must be a JSON object")
        return result


def title_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s*\((영화|film)\)\s*$", "", value).strip()


def is_movie_document(candidate: Candidate, movie: dict[str, Any]) -> bool:
    expected = {title_key(str(movie.get(key, ""))) for key in ("title", "original_title")}
    expected.discard("")
    return title_key(candidate.title) in expected


class BundleWikiSource:
    """Search the supplied offline corpus by movie, then read selected files."""

    source_type = "wikipedia_bundle_v2"

    def __init__(self, directory: Path):
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        self.directory = directory
        manifest_path = directory.parent / "wiki_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Titles preserve collection order, but page_ids are sorted separately.
        # Resolve the first (movie) title against the actual document content.
        self.own_titles = {
            movie_id: str(entry["titles"][0])
            for movie_id, entry in manifest.items() if entry.get("titles")
        }
        self.own_page_ids = {
            movie_id: str(entry["page_ids"][0])
            for movie_id, entry in manifest.items()
            if not entry.get("titles") and entry.get("page_ids")
        }
        self.search_calls = 0
        self.fetch_calls = 0

    def is_own_document(self, candidate: Candidate, movie: dict[str, Any]) -> bool:
        return candidate.source_id == self.own_page_ids.get(movie["movie_id"]) or is_movie_document(candidate, movie)

    def search(self, movie: dict[str, Any], term: str, limit: int = 10) -> list[Candidate]:
        self.search_calls += 1
        movie_id = movie["movie_id"]
        words = [word.casefold() for word in term.split() if len(word) > 1]
        found: list[tuple[Candidate, str]] = []
        for path in self.directory.glob(f"{movie_id}_*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            title = str(document.get("title", ""))
            summary = str(document.get("text", ""))[:400]
            if not title or not summary or not document.get("page_id"):
                continue
            candidate = Candidate(str(document["page_id"]), title, summary, path)
            found.append((candidate, summary.casefold()))
        own_title = title_key(self.own_titles.get(movie_id, ""))
        if own_title:
            own = next((candidate for candidate, _ in found if title_key(candidate.title) == own_title), None)
            if own is not None:
                self.own_page_ids[movie_id] = own.source_id
        ranked: list[tuple[int, Candidate]] = []
        for candidate, summary_l in found:
            title = candidate.title
            title_l = title.casefold()
            score = sum(3 for word in words if word in title_l) + sum(
                1 for word in words if word in summary_l
            )
            if self.is_own_document(candidate, movie):
                score += 100
            ranked.append((score, candidate))
        ranked.sort(key=lambda entry: (-entry[0], entry[1].title, entry[1].source_id))
        return [candidate for _, candidate in ranked[:limit]]

    def fetch(self, movie: dict[str, Any], candidate: Candidate, term: str) -> dict[str, Any]:
        self.fetch_calls += 1
        if candidate.path is None:
            raise ValueError("bundle candidate has no path")
        path = candidate.path.resolve()
        if self.directory.resolve() not in path.parents:
            raise ValueError("candidate path is outside wiki data directory")
        document = json.loads(path.read_text(encoding="utf-8"))
        if str(document.get("movie_id")) != movie["movie_id"]:
            raise ValueError("candidate belongs to another movie")
        if str(document.get("page_id")) != candidate.source_id:
            raise ValueError("candidate page ID changed")
        if not str(document.get("url", "")).startswith("https://ko.wikipedia.org/"):
            raise ValueError("candidate has no valid original URL")
        if not str(document.get("text", "")).strip():
            raise ValueError("candidate has no body")
        document["search_query"] = term
        document["source_type"] = "wikipedia"
        return document


class LiveWikiSource:
    """Search and fetch Korean Wikipedia pages through its Action API."""

    source_type = "wikipedia_live"

    def __init__(self, client: WikimediaClient | None = None, manifest_path: Path | None = None):
        self.client = client or WikimediaClient()
        self.own_page_ids: dict[str, str] = {}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path and manifest_path.exists() else {}
        self.own_titles = {
            movie_id: str(entry["titles"][0])
            for movie_id, entry in manifest.items() if entry.get("titles")
        }

    def search(self, movie: dict[str, Any], term: str, limit: int = 10) -> list[Candidate]:
        payload = self.client.get_json("ko.wikipedia.org", {
            "action": "query", "list": "search", "srsearch": term,
            "srnamespace": 0, "srlimit": limit, "format": "json", "formatversion": 2,
        })
        candidates = []
        for item in payload.get("query", {}).get("search", []):
            page_id = item.get("pageid")
            title = str(item.get("title") or "")
            if not page_id or not title:
                continue
            summary = html.unescape(re.sub(r"<[^>]+>", "", str(item.get("snippet") or "")))
            candidate = Candidate(str(page_id), title, summary, None)
            if is_movie_document(candidate, movie) or title_key(title) == title_key(self.own_titles.get(movie["movie_id"], "")):
                self.own_page_ids[movie["movie_id"]] = candidate.source_id
            candidates.append(candidate)
        return candidates

    def fetch(self, movie: dict[str, Any], candidate: Candidate, term: str) -> dict[str, Any]:
        payload = self.client.get_json("ko.wikipedia.org", {
            "action": "query", "prop": "extracts|info", "explaintext": 1,
            "inprop": "url", "pageids": candidate.source_id,
            "format": "json", "formatversion": 2,
        })
        pages = payload.get("query", {}).get("pages", [])
        page = pages[0] if pages else {}
        if str(page.get("pageid")) != candidate.source_id:
            raise ValueError("Wikipedia page ID changed")
        text = str(page.get("extract") or "").strip()
        url = str(page.get("fullurl") or "")
        if not text or not url.startswith("https://ko.wikipedia.org/"):
            raise ValueError("Wikipedia page has no usable body or URL")
        return {
            "movie_id": movie["movie_id"], "page_id": candidate.source_id,
            "title": str(page.get("title") or candidate.title), "text": text,
            "url": url, "source_type": "wikipedia", "search_query": term,
            "collected_at": datetime.now(timezone.utc).date().isoformat(),
        }


def extract_queries(model: ChatModel, movie: dict[str, Any], question: str) -> list[str]:
    result = model.complete_json(
        "당신은 영화 분야 검색 전문가입니다. 질문에 필요한 한국어 위키백과 검색어를 추출하세요. "
        'JSON 객체 {"queries":["검색어"]}만 반환하세요. 최대 4개입니다.',
        f"영화: {movie['title']}\n원제: {movie.get('original_title', '')}\n질문: {question}",
    )
    raw = result.get("queries")
    if not isinstance(raw, list):
        raise ValueError("query extraction must return a queries array")
    queries = [movie["title"]]
    seen = {movie["title"].casefold()}
    for value in raw:
        if not isinstance(value, str):
            continue
        term = " ".join(value.split())[:100]
        if term and term.casefold() not in seen:
            queries.append(term)
            seen.add(term.casefold())
        if len(queries) >= 4:
            break
    return queries


def select_candidates(
    model: ChatModel, movie: dict[str, Any], question: str, candidates: list[Candidate],
    own_page_id: str | None = None,
) -> list[Candidate]:
    if not candidates:
        return []
    payload = [
        {"id": item.source_id, "title": item.title, "summary": item.summary[:300]}
        for item in candidates
    ]
    result = model.complete_json(
        "당신은 영화 분야 문서 선별자입니다. 질문과 관련된 후보 문서만 고르세요. "
        'JSON 객체 {"selected_ids":["문서ID"]}만 반환하세요. 본문은 아직 보지 못했습니다.',
        f"영화: {movie['title']}\n질문: {question}\n후보: {json.dumps(payload, ensure_ascii=False)}",
    )
    raw = result.get("selected_ids")
    if not isinstance(raw, list):
        raise ValueError("candidate selection must return selected_ids array")
    selected_ids = {str(item) for item in raw}
    return [
        item for item in candidates
        if item.source_id in selected_ids or item.source_id == own_page_id or is_movie_document(item, movie)
    ]


@dataclass(frozen=True)
class CollectionResult:
    queries: list[str]
    documents: list[dict[str, Any]]
    cache_hits: int
    selected_ids: list[str]
    excluded_ids: list[str]


def collect_documents(
    movie: dict[str, Any], question: str, model: ChatModel,
    source: BundleWikiSource, cache: CollectionCache,
    on_stage: Callable[[str], None] | None = None,
) -> CollectionResult:
    if on_stage:
        on_stage("searching")
    queries = extract_queries(model, movie, question)
    cache_hits = 0
    documents: dict[str, dict[str, Any]] = {}
    excluded_ids: set[str] = set()
    for term in queries:
        cached = cache.get(source.source_type, movie["movie_id"], term)
        if cached is not None:
            cache_hits += 1
            selected = cached
            if on_stage:
                on_stage("loading_cache")
        else:
            if on_stage:
                on_stage("searching")
            candidates = source.search(movie, term)
            if on_stage:
                on_stage("selecting")
            chosen = select_candidates(
                model, movie, question, candidates, source.own_page_ids.get(movie["movie_id"])
            )
            chosen_ids = {item.source_id for item in chosen}
            excluded_ids.update(item.source_id for item in candidates if item.source_id not in chosen_ids)
            if on_stage:
                on_stage("fetching")
            selected = [
                documents.get(item.source_id) or source.fetch(movie, item, term)
                for item in chosen
            ]
            cache.put(source.source_type, movie["movie_id"], term, selected)
        for document in selected:
            documents[str(document["page_id"])] = document
    ordered = list(documents.values())
    return CollectionResult(
        queries=queries,
        documents=ordered,
        cache_hits=cache_hits,
        selected_ids=[str(document["page_id"]) for document in ordered],
        excluded_ids=sorted(excluded_ids - set(documents)),
    )
