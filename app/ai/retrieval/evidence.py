"""Retrieve one guaranteed movie record and related external chunks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    citation_id: str
    text: str
    source_type: str
    title: str
    url_or_file: str
    license: str = ""
    collected_at: str = ""

    def public(self, claim: str = "") -> dict[str, str]:
        return {
            "citation_id": self.citation_id,
            "source_type": self.source_type,
            "title": self.title,
            "url_or_file": self.url_or_file,
            "excerpt": relevant_excerpt(self.text, claim),
            "license": self.license,
            "collected_at": self.collected_at,
        }


def relevant_excerpt(text: str, claim: str, limit: int = 260) -> str:
    """Show a readable passage near the cited claim instead of a chunk's arbitrary start."""
    passages = [part.strip() for part in re.split(r"(?<=[.!?。])\s+|\n+", text) if part.strip()]
    if not passages:
        return ""
    informative = [part for part in passages if not re.match(r"^(제목|원제)\s*:", part)]
    if informative:
        passages = informative
    terms = set(re.findall(r"[가-힣a-z0-9]{2,}", claim.casefold()))
    normalized_claim = claim.casefold()
    grams = {normalized_claim[index:index + 3] for index in range(max(0, len(normalized_claim) - 2)) if normalized_claim[index:index + 3].strip()}

    def score(passage: str) -> tuple[float, int]:
        lowered = passage.casefold()
        word_hits = sum(term in lowered for term in terms)
        gram_hits = sum(gram in lowered for gram in grams)
        return ((word_hits * 3 + gram_hits) / max(1, len(passage) ** 0.35), -len(passage))

    passage = max(passages, key=score) if claim else passages[0]
    if len(passage) <= limit:
        return passage
    matching = [passage.casefold().find(term) for term in terms if term in passage.casefold()]
    position = min(matching) if matching else 0
    start = max(0, min(position - 50, len(passage) - limit))
    end = start + limit
    return ("…" if start else "") + passage[start:end].strip() + ("…" if end < len(passage) else "")


def cosine(left, right) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(value * value for value in left) * sum(value * value for value in right))
    return dot / norm if norm else 0.0


def mmr_indices(query, candidates, limit: int, diversity: float = 0.5) -> list[int]:
    """Choose relevant chunks while penalizing similarity to already chosen chunks."""
    selected: list[int] = []
    remaining = set(range(len(candidates)))
    relevance = [cosine(query, candidate) for candidate in candidates]
    while remaining and len(selected) < limit:
        choice = max(
            remaining,
            key=lambda index: (
                diversity * relevance[index]
                - (1 - diversity) * max(
                    (cosine(candidates[index], candidates[chosen]) for chosen in selected),
                    default=0.0,
                ),
                relevance[index],
                -index,
            ),
        )
        selected.append(choice)
        remaining.remove(choice)
    return selected


def retrieve(
    movie_id: str, question: str, meta_collection, docs_collection, embedder, top_k: int,
    *, strategy: str = "similarity", fetch_k: int = 30,
) -> list[Evidence]:
    if strategy not in {"similarity", "mmr"}:
        raise ValueError("unsupported retrieval strategy")
    if fetch_k < 1:
        raise ValueError("fetch_k must be at least 1")
    metadata = meta_collection.get(ids=[movie_id], include=["documents", "metadatas"])
    if not metadata["ids"]:
        raise LookupError("INDEX_NOT_READY")
    meta = metadata["metadatas"][0]
    items = [
        Evidence(
            citation_id="근거1",
            text=metadata["documents"][0],
            source_type="movie_metadata",
            title=meta["title"],
            url_or_file=meta["source_url"],
            license=meta.get("license", ""),
            collected_at=meta.get("collected_at", ""),
        )
    ]
    if top_k == 1 or docs_collection.count() == 0:
        return items
    query_embedding = embedder.embed([question])[0]
    result = docs_collection.query(
        query_embeddings=[query_embedding],
        n_results=max(fetch_k, top_k - 1) if strategy == "mmr" else top_k - 1,
        where={"movie_id": movie_id},
        include=["documents", "metadatas", "embeddings"] if strategy == "mmr" else ["documents", "metadatas"],
    )
    indices = (
        mmr_indices(query_embedding, result["embeddings"][0], top_k - 1)
        if strategy == "mmr" else range(len(result["ids"][0]))
    )
    seen: set[str] = set()
    for index in indices:
        chunk_id = result["ids"][0][index]
        text = result["documents"][0][index]
        source = result["metadatas"][0][index]
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        items.append(
            Evidence(
                citation_id=f"근거{len(items) + 1}",
                text=text,
                source_type=source["source_type"],
                title=source["title"],
                url_or_file=source["source_url_or_file"],
                license=source.get("license", ""),
                collected_at=source.get("collected_at", ""),
            )
        )
    return items
