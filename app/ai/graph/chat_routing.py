"""Handle simple conversation turns without an unnecessary model request."""

from __future__ import annotations

import re
from typing import Any


OFF_TOPIC_REPLY = "이곳에서는 선택한 영화에 관한 질문에 답할 수 있어요. 영화 내용이나 제작 정보를 물어봐 주세요."
ACKNOWLEDGEMENT_REPLY = "네. 이 영화에 대해 더 궁금한 점이 있으면 물어봐 주세요."


def local_chat_reply(question: str, history: list[dict]) -> str | None:
    cleaned = re.sub(r"[\s.!?~]+", "", question).casefold()
    if re.search(r"(방금|아까|직전|이전에).*(물어|질문|말했|얘기했)", question) or re.search(
        r"(내가|제가).*(뭐|무엇).*(물어|질문|말했)", question
    ):
        previous = next(
            (item["content"] for item in reversed(history)
             if item.get("role") == "user" and item.get("content", "").strip() != "관람 가이드 생성"),
            None,
        )
        return f"바로 앞에 ‘{previous}’라고 물어보셨어요." if previous else "이 영화에 대해 이전에 물어본 질문이 아직 없어요."
    if cleaned in {"ㅇㅇ", "응", "네", "넵", "그래", "좋아", "고마워", "고마워요", "감사", "감사해요", "ㅋㅋ", "ㅎㅎ", "ok", "okay"}:
        return ACKNOWLEDGEMENT_REPLY
    if re.search(r"^(오늘)?(저녁|점심|아침|야식)(메뉴|뭐먹).*(추천|골라|뭐|좀)?", cleaned):
        return OFF_TOPIC_REPLY
    if re.search(r"^(오늘|내일)?날씨", cleaned):
        return OFF_TOPIC_REPLY
    return None


def local_movie_fact_reply(question: str, movie: dict[str, Any]) -> tuple[str, list[dict[str, str]]] | None:
    """Answer a single runtime question from the supplied movie metadata."""
    if not re.search(r"상영\s*시간|러닝\s*타임|런닝\s*타임|몇\s*분", question, re.IGNORECASE):
        return None
    if re.search(r"감독|배우|출연|장르|줄거리|개봉|제작|비교|왜|어떻게|그리고", question):
        return None
    runtime = movie.get("runtime")
    if not isinstance(runtime, int) or runtime <= 0:
        return None
    citation = {
        "citation_id": "근거1", "source_type": "movie_metadata",
        "title": movie["title"], "url_or_file": movie["tmdb_url"],
        "excerpt": f"상영 시간: {runtime}분", "license": "TMDB API Terms of Use",
        "collected_at": "",
    }
    return f"《{movie['title']}》의 상영 시간은 {runtime}분입니다[근거1].", [citation]
