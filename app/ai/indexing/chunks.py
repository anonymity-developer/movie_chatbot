"""Stable chunks for source documents."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    metadata: dict[str, str | int]


def split_text(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("chunk size must be positive and overlap smaller than size")
    text = text.strip()
    if not text:
        return []
    step = size - overlap
    parts = []
    for start in range(0, len(text), step):
        parts.append(text[start : start + size])
        if start + size >= len(text):
            break
    return parts


def wiki_chunks(document: dict[str, Any], size: int, overlap: int) -> list[Chunk]:
    movie_id = str(document["movie_id"])
    page_id = str(document["page_id"])
    source_type = str(document.get("source_type") or "wikipedia")
    if source_type not in {"wikipedia", "wikidata"}:
        raise ValueError("unsupported document source")
    text_hash = hashlib.sha256(document["text"].encode("utf-8")).hexdigest()
    result = []
    for index, text in enumerate(split_text(document["text"], size, overlap)):
        result.append(
            Chunk(
                chunk_id=f"{movie_id}:{source_type}:{page_id}:{index}",
                text=text,
                metadata={
                    "movie_id": movie_id,
                    "source_type": source_type,
                    "source_id": page_id,
                    "title": str(document["title"]),
                    "source_url_or_file": str(document["url"]),
                    "license": str(document.get("license") or ("CC0" if source_type == "wikidata" else "CC BY-SA 4.0")),
                    "collected_at": str(document.get("collected_at") or ""),
                    "chunk_index": index,
                    "text_hash": text_hash,
                },
            )
        )
    return result
