"""Load and validate the supplied movie metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = ("movie_id", "title", "overview", "tmdb_url")


class DataError(ValueError):
    """The supplied data cannot be indexed safely."""


def load_movies(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise DataError("movies.json must contain a list")
    seen: set[str] = set()
    for position, movie in enumerate(data):
        if not isinstance(movie, dict):
            raise DataError(f"movie {position} must be an object")
        for field in REQUIRED_FIELDS:
            if not isinstance(movie.get(field), str) or not movie[field].strip():
                raise DataError(f"movie {position} has no valid {field}")
        movie_id = movie["movie_id"]
        if movie_id in seen:
            raise DataError(f"duplicate movie_id: {movie_id}")
        seen.add(movie_id)
    return data


def movie_index_text(movie: dict[str, Any]) -> str:
    """Produce exactly one searchable text for one movie."""
    fields = (
        ("제목", movie.get("title")),
        ("원제", movie.get("original_title")),
        ("개봉연도", movie.get("year")),
        ("장르", movie.get("genres")),
        ("감독", movie.get("director")),
        ("출연", movie.get("cast")),
        ("줄거리", movie.get("overview")),
    )
    lines = []
    for label, value in fields:
        if isinstance(value, list):
            value = ", ".join(str(item).strip() for item in value if str(item).strip())
        if value is not None and str(value).strip():
            lines.append(f"{label}: {value}")
    return "\n".join(lines)
