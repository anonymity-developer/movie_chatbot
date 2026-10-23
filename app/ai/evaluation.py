"""Small reproducible factual QA evaluation helpers."""

from __future__ import annotations

import math
import re
from typing import Any


def representative_cases(movies: list[dict[str, Any]], case_set: str = "standard") -> list[dict[str, Any]]:
    """Select fixed director questions and optionally one unknowable detail."""
    selected = []
    if case_set == "standard":
        candidates = [movies[index] for index in (0, 100, 300, 500, 800) if index < len(movies)]
    elif case_set in {"obscure", "obscure_unknown"}:
        by_id = {movie["movie_id"]: movie for movie in movies}
        candidates = [by_id[movie_id] for movie_id in (
            "1321179", "1461254", "1137179", "1233199", "797109"
        ) if movie_id in by_id]
    else:
        raise ValueError("case_set must be standard, obscure, or obscure_unknown")
    for movie in candidates:
        directors = movie.get("director") or []
        if directors:
            selected.append({
                "movie_id": movie["movie_id"],
                "title": movie["title"],
                "question": f"영화 {movie['title']}({movie.get('year') or '연도 미상'})의 감독은 누구인가요?",
                "expected": directors[0],
            })
    if len(selected) != 5:
        raise ValueError("five representative movies with directors are required")
    if case_set == "obscure_unknown":
        by_id = {movie["movie_id"]: movie for movie in movies}
        movie = by_id["157336"]
        selected.append({
            "movie_id": movie["movie_id"], "title": movie["title"],
            "question": "인터스텔라의 쿠퍼가 오늘 신고 있는 양말 색깔은 무엇인가요?",
            "expected": None,
        })
    return selected


def factual_match(answer: str, expected: str) -> bool:
    normalize = lambda value: re.sub(r"[\s·\-.]", "", value).casefold()
    return normalize(expected) in normalize(answer)


def unknown_acknowledged(answer: str) -> bool:
    """Credit an explicit refusal to invent a detail absent from the corpus."""
    return bool(re.search(r"모르|모릅|모른|모를|알 수 없|확인(?:할 수 없|되지 않)|나오지 않|언급되지 않|기록되지 않", answer))


def percentile(values: list[float], percentage: float) -> float:
    if not values or not 0 < percentage <= 100:
        raise ValueError("nonempty values and a valid percentile are required")
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * percentage / 100) - 1]
