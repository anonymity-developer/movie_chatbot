"""Compare similarity and MMR evidence for one movie and question."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.ai.indexing.vector_store import OpenAIEmbedder, VectorStore
from app.ai.retrieval.evidence import retrieve
from app.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("movie_id")
    parser.add_argument("question")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.chroma_dir.exists():
        parser.error("movie index is missing; run scripts.index_data first")
    store = VectorStore(settings.chroma_dir)
    meta = store.client.get_collection("movie_meta")
    docs = store.client.get_collection("movie_docs")
    embedder = OpenAIEmbedder(settings.embedding_model)
    output = {
        "movie_id": args.movie_id,
        "question": args.question,
        "top_k": settings.top_k,
        "similarity": [
            item.public() for item in retrieve(
                args.movie_id, args.question, meta, docs, embedder, settings.top_k,
                strategy="similarity",
            )
        ],
        "mmr": [
            item.public() for item in retrieve(
                args.movie_id, args.question, meta, docs, embedder, settings.top_k,
                strategy="mmr", fetch_k=settings.mmr_fetch_k,
            )
        ],
    }
    serialized = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output is None:
        print(serialized)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
        print(f"similarity={len(output['similarity'])} mmr={len(output['mmr'])}")


if __name__ == "__main__":
    main()
