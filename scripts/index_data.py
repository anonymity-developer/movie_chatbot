"""Validate and index the supplied movie and Wikipedia data.

Run `python -m scripts.index_data --validate-only` without API credentials.
Indexing uses OpenAI embeddings and incurs provider charges.
"""

from __future__ import annotations

import argparse

from app.ai.data_prep.movies import load_movies, movie_index_text
from app.ai.data_prep.wiki import iter_wiki_documents
from app.ai.indexing.chunks import Chunk, wiki_chunks
from app.ai.indexing.vector_store import OpenAIEmbedder, VectorStore
from app.config import Settings


def movie_chunks(movies: list[dict]) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=movie["movie_id"],
            text=movie_index_text(movie),
            metadata={
                "movie_id": movie["movie_id"],
                "title": movie["title"],
                "year": int(movie.get("year") or 0),
                "source_type": "movie_metadata",
                "source_url": movie["tmdb_url"],
                "source_name": "TMDB",
                "license": "TMDB API Terms of Use",
            },
        )
        for movie in movies
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--limit-movies", type=int, default=0)
    args = parser.parse_args()
    settings = Settings.from_env()
    movies = load_movies(settings.data_dir / "movies.json")
    if args.limit_movies < 0:
        parser.error("--limit-movies must be non-negative")
    if args.limit_movies:
        movies = movies[: args.limit_movies]
    movie_ids = {movie["movie_id"] for movie in movies}
    wiki_dir = settings.data_dir / "movielab_wiki" / "wikipedia"
    wiki_docs = list(iter_wiki_documents(wiki_dir, movie_ids, subset=bool(args.limit_movies)))
    if not movies or not wiki_docs:
        raise RuntimeError("movie or wiki data is empty")
    print(f"movies={len(movies)} wiki_documents={len(wiki_docs)}")
    print("example_index_text:\n" + movie_index_text(movies[0]))
    if args.validate_only:
        return

    store = VectorStore(settings.chroma_dir)
    embedder = OpenAIEmbedder(settings.embedding_model)
    meta_collection = store.collection("movie_meta", {"embedding_model": settings.embedding_model})
    docs_collection = store.collection(
        "movie_docs",
        {
            "embedding_model": settings.embedding_model,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
        },
    )
    meta_count = store.upsert(meta_collection, movie_chunks(movies), embedder)
    print(f"indexed_meta={meta_count}", flush=True)
    doc_count = store.upsert(
        docs_collection,
        (
            chunk
            for document in wiki_docs
            for chunk in wiki_chunks(document, settings.chunk_size, settings.chunk_overlap)
        ),
        embedder,
        on_progress=lambda count: print(f"indexed_doc_chunks={count}", flush=True) if count % 640 == 0 else None,
    )
    print(
        f"indexed_meta={meta_count} indexed_doc_chunks={doc_count} "
        f"stored_meta={meta_collection.count()} stored_doc_chunks={docs_collection.count()}"
    )


if __name__ == "__main__":
    main()
