"""Runtime settings; secrets are read only from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
except ImportError:
    # Data validation can run before optional service dependencies are installed.
    pass
else:
    load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    chroma_dir: Path
    cache_db: Path
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    discovery_candidates: int
    retrieval_strategy: str
    mmr_fetch_k: int
    wiki_source: str
    enable_wikidata: bool

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            data_dir=Path(os.getenv("MOVIELAB_DATA_DIR", str(ROOT / "data"))),
            chroma_dir=Path(os.getenv("MOVIELAB_CHROMA_DIR", str(ROOT / ".local" / "chroma"))),
            cache_db=Path(os.getenv("MOVIELAB_CACHE_DB", str(ROOT / ".local" / "cache.sqlite3"))),
            embedding_model=os.getenv("MOVIELAB_EMBEDDING_MODEL", "text-embedding-3-small"),
            chunk_size=int(os.getenv("MOVIELAB_CHUNK_SIZE", "1000")),
            chunk_overlap=int(os.getenv("MOVIELAB_CHUNK_OVERLAP", "200")),
            top_k=int(os.getenv("MOVIELAB_TOP_K", "6")),
            discovery_candidates=int(os.getenv("MOVIELAB_DISCOVERY_CANDIDATES", "80")),
            retrieval_strategy=os.getenv("MOVIELAB_RETRIEVAL_STRATEGY", "similarity").lower(),
            mmr_fetch_k=int(os.getenv("MOVIELAB_MMR_FETCH_K", "30")),
            wiki_source=os.getenv("MOVIELAB_WIKI_SOURCE", "bundle").lower(),
            enable_wikidata=os.getenv("MOVIELAB_ENABLE_WIKIDATA", "true").lower() in {"1", "true", "yes"},
        )
        if settings.chunk_size <= 0 or not 0 <= settings.chunk_overlap < settings.chunk_size:
            raise ValueError("invalid chunk size or overlap")
        if settings.top_k < 1:
            raise ValueError("MOVIELAB_TOP_K must be at least 1")
        if settings.discovery_candidates < 1:
            raise ValueError("MOVIELAB_DISCOVERY_CANDIDATES must be at least 1")
        if settings.retrieval_strategy not in {"similarity", "mmr"}:
            raise ValueError("MOVIELAB_RETRIEVAL_STRATEGY must be similarity or mmr")
        if settings.mmr_fetch_k < 1:
            raise ValueError("MOVIELAB_MMR_FETCH_K must be at least 1")
        if settings.wiki_source not in {"bundle", "live"}:
            raise ValueError("MOVIELAB_WIKI_SOURCE must be bundle or live")
        return settings
