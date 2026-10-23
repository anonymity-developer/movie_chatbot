"""Find movies by a natural-language condition using indexed candidates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.ai.data_prep.collection import ChatModel


GENRES = (
    "SF", "액션", "모험", "애니메이션", "코미디", "범죄", "드라마", "가족",
    "판타지", "공포", "미스터리", "로맨스", "스릴러", "다큐멘터리", "역사",
    "음악", "전쟁", "서부",
)


@dataclass(frozen=True)
class HardFilters:
    min_year: int | None = None
    max_year: int | None = None
    genre: str | None = None
    korean: bool = False

    def matches(self, movie: dict[str, Any]) -> bool:
        year = movie.get("year")
        if self.min_year is not None and (not isinstance(year, int) or year < self.min_year):
            return False
        if self.max_year is not None and (not isinstance(year, int) or year > self.max_year):
            return False
        if self.genre is not None and self.genre not in movie.get("genres", []):
            return False
        if self.korean and movie.get("original_language") != "ko" and not any(
            country in {"South Korea", "대한민국", "Republic of Korea"}
            for country in movie.get("production_countries", [])
        ):
            return False
        return True


def hard_filters(condition: str) -> HardFilters:
    decade = re.search(r"(?<!\d)(\d{4})년대", condition)
    minimum = re.search(r"(?<!\d)(\d{4})\s*년\s*(?:이후|부터|이상)", condition)
    maximum = re.search(r"(?<!\d)(\d{4})\s*년\s*(?:이전|까지|이하)", condition)
    exact = re.search(r"(?<!\d)(\d{4})\s*년(?!대|\s*(?:이후|부터|이상|이전|까지|이하))", condition)
    min_year = int(decade.group(1)) if decade else int(minimum.group(1)) if minimum else None
    max_year = min_year + 9 if decade else int(maximum.group(1)) if maximum else None
    if exact and min_year is None and max_year is None:
        min_year = max_year = int(exact.group(1))
    named_genres = [genre for genre in GENRES if re.search(rf"(?<![가-힣A-Za-z]){genre}(?![가-힣A-Za-z])", condition, re.IGNORECASE)]
    genre = named_genres[0] if len(named_genres) == 1 else None
    genre_words = "|".join(re.escape(value) for value in GENRES)
    return HardFilters(
        min_year=min_year,
        max_year=max_year,
        genre=genre,
        korean=bool(re.search(rf"(?:한국|국내)(?:\s+(?:{genre_words})){{0,3}}\s*영화", condition)),
    )


@dataclass(frozen=True)
class DiscoveryResult:
    items: list[dict[str, Any]]
    candidate_count: int


def discover_movies(
    condition: str, collection, embedder, model: ChatModel,
    movies_by_id: dict[str, dict[str, Any]], *, candidate_k: int = 80,
    result_limit: int = 12,
) -> DiscoveryResult:
    count = collection.count()
    if count == 0:
        raise LookupError("INDEX_NOT_READY")
    filters = hard_filters(condition)
    # Explicit conditions may be rare; search all indexed movies so a year or
    # country filter cannot accidentally hide every matching item.
    requested = count if filters != HardFilters() else min(candidate_k, count)
    matches = collection.query(
        query_embeddings=embedder.embed([condition]),
        n_results=requested,
        include=["metadatas"],
    )
    candidates = []
    for movie_id in matches["ids"][0]:
        movie = movies_by_id.get(movie_id)
        if movie is not None and filters.matches(movie):
            candidates.append(movie)
        if len(candidates) >= 30:
            break
    if not candidates:
        return DiscoveryResult(items=[], candidate_count=0)
    brief = [
        {
            "movie_id": item["movie_id"], "title": item["title"],
            "year": item.get("year"), "genres": item.get("genres", []),
            "director": item.get("director", []), "cast": item.get("cast", []),
            "overview": str(item.get("overview") or "")[:350],
            "original_language": item.get("original_language"),
            "production_countries": item.get("production_countries", []),
        }
        for item in candidates
    ]
    result = model.complete_json(
        "당신은 영화 조건 탐색 도우미입니다. 후보의 메타데이터만 보고 사용자 조건을 "
        "확실히 만족하는 영화만 고르세요. 모호하거나 조건을 확인할 수 없는 후보는 제외하세요. "
        'JSON 객체 {"selected_ids":["movie_id"]}만 반환하세요.',
        f"사용자 조건: {condition}\n후보: {json.dumps(brief, ensure_ascii=False)}",
    )
    raw_ids = result.get("selected_ids")
    if not isinstance(raw_ids, list):
        raise ValueError("discovery selection must return selected_ids array")
    selected = {str(item) for item in raw_ids}
    items = [
        {
            "movie_id": movie["movie_id"], "title": movie["title"],
            "year": movie.get("year"), "genres": movie.get("genres", []),
        }
        for movie in candidates if movie["movie_id"] in selected
    ][:result_limit]
    return DiscoveryResult(items=items, candidate_count=len(candidates))
