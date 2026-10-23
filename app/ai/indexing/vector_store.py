"""Chroma persistence with explicit OpenAI embeddings and stable IDs."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from app.ai.indexing.chunks import Chunk


def batches(items: Iterable[Chunk], size: int = 64) -> Iterable[list[Chunk]]:
    batch: list[Chunk] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


class OpenAIEmbedder:
    def __init__(self, model: str, *, timeout: float | None = None, max_retries: int | None = None):
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is missing")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt before indexing") from exc
        options = {}
        if timeout is not None:
            options["timeout"] = timeout
        if max_retries is not None:
            options["max_retries"] = max_retries
        self.client = OpenAI(**options)
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model, input=list(texts))
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(texts):
            raise RuntimeError("embedding response count does not match input")
        return [item.embedding for item in ordered]


class VectorStore:
    def __init__(self, directory: Path):
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt before indexing") from exc
        directory.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(directory))

    def collection(self, name: str, metadata: dict[str, str | int]):
        collection = self.client.get_or_create_collection(name=name, metadata=metadata)
        for key, value in metadata.items():
            if collection.metadata.get(key) != value:
                raise RuntimeError(
                    f"{name} has incompatible {key}; rebuild the collection before indexing"
                )
        return collection

    @staticmethod
    def upsert(
        collection, chunks: Iterable[Chunk], embedder: OpenAIEmbedder,
        on_progress: Callable[[int], None] | None = None,
    ) -> int:
        total = 0
        for batch in batches(chunks):
            existing = collection.get(
                ids=[item.chunk_id for item in batch], include=["documents", "metadatas"]
            )
            saved = {
                chunk_id: (document, metadata)
                for chunk_id, document, metadata in zip(
                    existing["ids"], existing["documents"], existing["metadatas"]
                )
            }
            changed = [
                item for item in batch
                if saved.get(item.chunk_id) != (item.text, item.metadata)
            ]
            if changed:
                texts = [item.text for item in changed]
                collection.upsert(
                    ids=[item.chunk_id for item in changed],
                    documents=texts,
                    metadatas=[item.metadata for item in changed],
                    embeddings=embedder.embed(texts),
                )
            total += len(batch)
            if on_progress is not None:
                on_progress(total)
        return total
