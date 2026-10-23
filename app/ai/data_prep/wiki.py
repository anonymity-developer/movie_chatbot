"""Load the provided Wikipedia fallback corpus without altering it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from app.ai.data_prep.movies import DataError


def iter_wiki_documents(
    directory: Path, movie_ids: set[str], *, subset: bool = False
) -> Iterator[dict[str, Any]]:
    if not directory.is_dir():
        raise DataError(f"wiki directory is missing: {directory}")
    for path in sorted(directory.glob("*.json")):
        if subset and path.name.split("_", 1)[0] not in movie_ids:
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise DataError(f"invalid wiki document: {path.name}")
        movie_id = str(document.get("movie_id", ""))
        if movie_id not in movie_ids:
            raise DataError(f"unknown movie_id {movie_id} in {path.name}")
        for field in ("page_id", "title", "url", "text"):
            if not str(document.get(field, "")).strip():
                raise DataError(f"missing {field} in {path.name}")
        if not str(document["url"]).startswith("https://ko.wikipedia.org/"):
            raise DataError(f"invalid source URL in {path.name}")
        yield document
