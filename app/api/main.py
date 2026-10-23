"""Initial Movie Lab API and HTML frontend."""

from __future__ import annotations

import os
import time
from functools import lru_cache
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from openai import APIConnectionError, APITimeoutError, AuthenticationError, OpenAIError, RateLimitError
from pydantic import BaseModel, Field

from app.ai.data_prep.movies import load_movies
from app.ai.data_prep.collection import BundleWikiSource, LiveWikiSource, OpenAIChatModel, collect_documents, title_key
from app.ai.data_prep.wikidata import WikidataSource
from app.ai.graph.qa import build_graph
from app.ai.indexing.chunks import wiki_chunks
from app.ai.indexing.vector_store import OpenAIEmbedder, VectorStore
from app.ai.retrieval.discovery import discover_movies
from app.ai.graph.chat_routing import local_chat_reply, local_movie_fact_reply
from app.config import ROOT, Settings
from app.db.collection_cache import CollectionCache
from app.db.conversations import ConversationRepository
from app.db.jobs import JobRepository


app = FastAPI(title="Movie Lab")
settings = Settings.from_env()


@app.exception_handler(OpenAIError)
def model_error(_request: Request, exc: OpenAIError):
    if isinstance(exc, AuthenticationError):
        code, message = "MODEL_AUTH_FAILED", "모델 API 키가 거절되었습니다. 서버 설정을 확인해 주세요."
    elif isinstance(exc, RateLimitError):
        code, message = "MODEL_RATE_LIMITED", "AI 서비스가 요청을 제한했습니다. 질문 내용과는 관계없는 서비스 오류입니다."
    elif isinstance(exc, APITimeoutError):
        code, message = "MODEL_TIMEOUT", "AI 서비스 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
    elif isinstance(exc, APIConnectionError):
        code, message = "MODEL_UNAVAILABLE", "모델 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요."
    else:
        code, message = "MODEL_UNAVAILABLE", "모델 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
    return JSONResponse(status_code=503, content={"detail": {"code": code, "message": message}})


@lru_cache(maxsize=1)
def movies() -> list[dict]:
    return load_movies(settings.data_dir / "movies.json")


class ChatRequest(BaseModel):
    movie_id: str
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class GuideRequest(BaseModel):
    movie_id: str


class CollectRequest(BaseModel):
    movie_id: str
    question: str = Field(min_length=1, max_length=2000)


class DiscoverRequest(BaseModel):
    condition: str = Field(min_length=2, max_length=500)


@app.get("/")
def home():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/app.css")
def stylesheet():
    return FileResponse(ROOT / "frontend" / "app.css", media_type="text/css")


@app.get("/app.js")
def javascript():
    return FileResponse(ROOT / "frontend" / "app.js", media_type="application/javascript")


@app.get("/api/movies")
def list_movies(query: str = "", page: int = Query(1, ge=1)):
    needle = query.strip().casefold()
    matching = [
        item for item in movies()
        if needle in item["title"].casefold() or needle in item.get("original_title", "").casefold()
    ]
    page_size = 24
    start = (page - 1) * page_size
    return {
        "items": [
            {"movie_id": item["movie_id"], "title": item["title"], "year": item.get("year")}
            for item in matching[start : start + page_size]
        ],
        "total": len(matching),
        "page": page,
    }


@app.get("/api/movies/{movie_id}")
def get_movie(movie_id: str):
    movie = next((item for item in movies() if item["movie_id"] == movie_id), None)
    if movie is None:
        raise HTTPException(status_code=404, detail={"code": "MOVIE_NOT_FOUND", "message": "영화를 찾을 수 없습니다."})
    return movie


def browser_session(request: Request, response: Response) -> str:
    cookie = request.cookies.get("movielab_session")
    try:
        session_id = str(UUID(cookie)) if cookie else None
    except ValueError:
        session_id = None
    if session_id is None:
        session_id = str(uuid4())
        response.set_cookie(
            "movielab_session", session_id, max_age=60 * 60 * 24 * 30,
            httponly=True, samesite="lax", secure=request.url.scheme == "https",
        )
    return session_id


def create_qa_graph(*, mode: str = "chat"):
    from openai import OpenAI

    store = VectorStore(settings.chroma_dir)
    return build_graph(
        store.client.get_collection("movie_meta"),
        store.client.get_collection("movie_docs"),
        OpenAIEmbedder(settings.embedding_model, timeout=8.0, max_retries=0),
        OpenAI(timeout=20.0, max_retries=0),
        os.getenv("MOVIELAB_CHAT_MODEL", "gpt-4.1-mini"),
        settings.top_k,
        mode=mode,
        retrieval_strategy=settings.retrieval_strategy,
        fetch_k=settings.mmr_fetch_k,
    )


@app.get("/api/index/status")
def index_status():
    if not settings.chroma_dir.exists():
        return {"ready": False, "movie_meta": 0, "movie_docs": 0}
    try:
        store = VectorStore(settings.chroma_dir)
        meta = store.client.get_collection("movie_meta").count()
    except Exception:
        return {"ready": False, "movie_meta": 0, "movie_docs": 0}
    try:
        docs = store.client.get_collection("movie_docs").count()
    except Exception:
        docs = 0
    return {"ready": meta > 0 and docs > 0, "movie_meta": meta, "movie_docs": docs}


@app.post("/api/discover")
def discover(payload: DiscoverRequest):
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail={"code": "MODEL_CONFIG_MISSING", "message": "모델 API 키가 설정되지 않았습니다."})
    if not settings.chroma_dir.exists():
        raise HTTPException(status_code=503, detail={"code": "INDEX_NOT_READY", "message": "영화 색인을 먼저 생성해 주세요."})
    store = VectorStore(settings.chroma_dir)
    try:
        collection = store.client.get_collection("movie_meta")
        if collection.count() == 0:
            raise LookupError("INDEX_NOT_READY")
    except Exception:
        raise HTTPException(status_code=503, detail={"code": "INDEX_NOT_READY", "message": "영화 색인을 먼저 생성해 주세요."})
    result = discover_movies(
        payload.condition,
        collection,
        OpenAIEmbedder(settings.embedding_model),
        OpenAIChatModel(os.getenv("MOVIELAB_CHAT_MODEL", "gpt-4.1-mini")),
        {movie["movie_id"]: movie for movie in movies()},
        candidate_k=settings.discovery_candidates,
    )
    return {"condition": payload.condition, "items": result.items, "candidate_count": result.candidate_count}


@app.get("/api/conversations/{movie_id}")
def conversation_history(movie_id: str, request: Request, response: Response):
    if not any(item["movie_id"] == movie_id for item in movies()):
        raise HTTPException(status_code=404, detail={"code": "MOVIE_NOT_FOUND", "message": "영화를 찾을 수 없습니다."})
    session_id = browser_session(request, response)
    conversation_id, messages = ConversationRepository(settings.cache_db).history(session_id, movie_id)
    return {"movie_id": movie_id, "conversation_id": conversation_id, "messages": messages}


@app.post("/api/chat")
def chat(payload: ChatRequest, request: Request, response: Response):
    movie = next((item for item in movies() if item["movie_id"] == payload.movie_id), None)
    if movie is None:
        raise HTTPException(status_code=404, detail={"code": "MOVIE_NOT_FOUND", "message": "영화를 찾을 수 없습니다."})
    session_id = browser_session(request, response)
    repository = ConversationRepository(settings.cache_db)
    existing_id, history = repository.history(session_id, payload.movie_id)
    if payload.conversation_id is not None and payload.conversation_id != existing_id:
        raise HTTPException(status_code=409, detail={"code": "CONVERSATION_MISMATCH", "message": "이 영화의 대화 기록을 다시 불러와 주세요."})
    local_answer = local_chat_reply(payload.question, history)
    if local_answer is not None:
        conversation_id = repository.append_exchange(
            session_id, payload.movie_id, payload.question, local_answer, []
        )
        return {
            "movie_id": payload.movie_id, "conversation_id": conversation_id,
            "answer": local_answer, "citations": [], "status": "done",
            "response_type": "local",
            "elapsed_ms": 0, "retrieval_ms": 0, "generation_ms": 0,
        }
    fact = local_movie_fact_reply(payload.question, movie)
    if fact is not None:
        answer, citations = fact
        conversation_id = repository.append_exchange(
            session_id, payload.movie_id, payload.question, answer, citations
        )
        return {
            "movie_id": payload.movie_id, "conversation_id": conversation_id,
            "answer": answer, "citations": citations, "status": "done",
            "response_type": "metadata",
            "elapsed_ms": 0, "retrieval_ms": 0, "generation_ms": 0,
        }
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail={"code": "MODEL_CONFIG_MISSING", "message": "모델 API 키가 설정되지 않았습니다."})
    if not index_status()["ready"]:
        raise HTTPException(status_code=503, detail={"code": "INDEX_NOT_READY", "message": "영화 색인을 먼저 생성해 주세요."})
    started = time.perf_counter()
    try:
        result = create_qa_graph().invoke({
            "movie_id": payload.movie_id, "question": payload.question, "history": history,
        })
    except LookupError:
        raise HTTPException(status_code=503, detail={"code": "INDEX_NOT_READY", "message": "선택한 영화의 색인이 없습니다."})
    conversation_id = repository.append_exchange(
        session_id, payload.movie_id, payload.question, result["answer"], result["citations"]
    )
    return {
        "movie_id": payload.movie_id,
        "conversation_id": conversation_id,
        "answer": result["answer"],
        "citations": result["citations"],
        "status": "done",
        "response_type": "rag",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "retrieval_ms": result.get("retrieval_ms", 0),
        "generation_ms": result.get("generation_ms", 0),
    }


@app.post("/api/guides")
def guide(payload: GuideRequest, request: Request, response: Response):
    if not any(item["movie_id"] == payload.movie_id for item in movies()):
        raise HTTPException(status_code=404, detail={"code": "MOVIE_NOT_FOUND", "message": "영화를 찾을 수 없습니다."})
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail={"code": "MODEL_CONFIG_MISSING", "message": "모델 API 키가 설정되지 않았습니다."})
    if not index_status()["ready"]:
        raise HTTPException(status_code=503, detail={"code": "INDEX_NOT_READY", "message": "영화 색인을 먼저 생성해 주세요."})
    session_id = browser_session(request, response)
    started = time.perf_counter()
    try:
        result = create_qa_graph(mode="guide").invoke({
            "movie_id": payload.movie_id,
            "question": "이 영화의 작품 소개, 볼거리, 이해를 돕는 배경을 근거와 함께 알려줘.",
        })
    except LookupError:
        raise HTTPException(status_code=503, detail={"code": "INDEX_NOT_READY", "message": "선택한 영화의 색인이 없습니다."})
    conversation_id = ConversationRepository(settings.cache_db).append_exchange(
        session_id, payload.movie_id, "관람 가이드 생성", result["answer"], result["citations"]
    )
    return {
        "movie_id": payload.movie_id, "conversation_id": conversation_id,
        "answer": result["answer"],
        "citations": result["citations"], "status": "done",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "retrieval_ms": result.get("retrieval_ms", 0),
        "generation_ms": result.get("generation_ms", 0),
    }


def collect_impl(payload: CollectRequest, on_stage=None):
    movie = next((item for item in movies() if item["movie_id"] == payload.movie_id), None)
    if movie is None:
        raise HTTPException(status_code=404, detail={"code": "MOVIE_NOT_FOUND", "message": "영화를 찾을 수 없습니다."})
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail={"code": "MODEL_CONFIG_MISSING", "message": "모델 API 키가 설정되지 않았습니다."})
    started = time.perf_counter()
    source = (
        LiveWikiSource(manifest_path=settings.data_dir / "movielab_wiki" / "wiki_manifest.json") if settings.wiki_source == "live"
        else BundleWikiSource(settings.data_dir / "movielab_wiki" / "wikipedia")
    )
    cache = CollectionCache(settings.cache_db)
    model = OpenAIChatModel(os.getenv("MOVIELAB_CHAT_MODEL", "gpt-4.1-mini"))
    try:
        result = collect_documents(movie, payload.question, model, source, cache, on_stage=on_stage)
    except (OSError, ValueError, RuntimeError) as exc:
        if settings.wiki_source != "live":
            raise
        raise HTTPException(status_code=503, detail={"code": "SOURCE_UNAVAILABLE", "message": "위키백과 수집에 실패했습니다. 잠시 후 다시 시도하거나 로컬 문서 묶음을 사용해 주세요."}) from exc
    documents = list(result.documents)
    warnings = []
    extra_cache_hits = 0
    if settings.enable_wikidata:
        if on_stage:
            on_stage("fetching")
        own_id = source.own_page_ids.get(movie["movie_id"])
        own_title = title_key(source.own_titles.get(movie["movie_id"], ""))
        own = next((
            item for item in documents
            if str(item["page_id"]) == own_id or (own_title and title_key(item["title"]) == own_title)
        ), None)
        if own is not None:
            cached = cache.get("wikidata", movie["movie_id"], own["title"])
            if cached is not None:
                extra_cache_hits = 1
                documents.extend(cached)
            else:
                try:
                    second = WikidataSource().fetch(movie, own["title"])
                except (OSError, ValueError, RuntimeError):
                    warnings.append("Wikidata 정보를 가져오지 못해 위키백과 문서만 사용했습니다.")
                else:
                    selected = [second] if second is not None else []
                    cache.put("wikidata", movie["movie_id"], own["title"], selected)
                    documents.extend(selected)
        else:
            warnings.append("영화 자체 문서를 찾지 못해 Wikidata 연결을 확인할 수 없었습니다.")
    if not documents:
        return {
            "queries": result.queries, "selected": [], "excluded_ids": result.excluded_ids,
            "cache_hits": result.cache_hits + extra_cache_hits, "indexed_chunks": 0,
            "warnings": warnings,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    if on_stage:
        on_stage("indexing")
    store = VectorStore(settings.chroma_dir)
    docs_collection = store.collection(
        "movie_docs",
        {
            "embedding_model": settings.embedding_model,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
        },
    )
    embedder = OpenAIEmbedder(settings.embedding_model)
    indexed = store.upsert(
        docs_collection,
        (
            chunk for document in documents
            for chunk in wiki_chunks(document, settings.chunk_size, settings.chunk_overlap)
        ),
        embedder,
    )
    return {
        "queries": result.queries,
        "selected": [
            {"page_id": str(document["page_id"]), "title": document["title"], "url": document["url"], "source_type": document["source_type"]}
            for document in documents
        ],
        "excluded_ids": result.excluded_ids,
        "cache_hits": result.cache_hits + extra_cache_hits,
        "warnings": warnings,
        "indexed_chunks": indexed,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


@app.post("/api/collect")
def collect(payload: CollectRequest):
    return collect_impl(payload)


def run_collect_job(job_id: str, payload: CollectRequest) -> None:
    repository = JobRepository(settings.cache_db)
    try:
        result = collect_impl(payload, on_stage=lambda stage: repository.update(job_id, stage))
    except AuthenticationError:
        repository.update(job_id, "failed", "failed", error_code="MODEL_AUTH_FAILED")
    except RateLimitError:
        repository.update(job_id, "failed", "failed", error_code="MODEL_RATE_LIMITED")
    except OpenAIError:
        repository.update(job_id, "failed", "failed", error_code="MODEL_UNAVAILABLE")
    except HTTPException as exc:
        code = exc.detail.get("code", "COLLECTION_FAILED") if isinstance(exc.detail, dict) else "COLLECTION_FAILED"
        repository.update(job_id, "failed", "failed", error_code=code)
    except Exception:
        repository.update(job_id, "failed", "failed", error_code="COLLECTION_FAILED")
    else:
        repository.update(job_id, "done", "done", result=result)


@app.post("/api/collection-jobs")
def start_collection_job(
    payload: CollectRequest, request: Request, response: Response, background_tasks: BackgroundTasks,
):
    if not any(item["movie_id"] == payload.movie_id for item in movies()):
        raise HTTPException(status_code=404, detail={"code": "MOVIE_NOT_FOUND", "message": "영화를 찾을 수 없습니다."})
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail={"code": "MODEL_CONFIG_MISSING", "message": "모델 API 키가 설정되지 않았습니다."})
    session_id = browser_session(request, response)
    job_id = JobRepository(settings.cache_db).create(session_id, payload.movie_id)
    background_tasks.add_task(run_collect_job, job_id, payload)
    return {"job_id": job_id, "movie_id": payload.movie_id, "stage": "queued", "status": "pending"}


@app.get("/api/jobs/{job_id}")
def get_collection_job(job_id: str, request: Request, response: Response):
    session_id = browser_session(request, response)
    job = JobRepository(settings.cache_db).get(job_id, session_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "작업을 찾을 수 없습니다."})
    if job["error_code"]:
        messages = {
            "MODEL_AUTH_FAILED": "모델 API 키가 거절되었습니다. 서버 설정을 확인해 주세요.",
            "MODEL_RATE_LIMITED": "모델 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.",
            "MODEL_UNAVAILABLE": "모델 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            "SOURCE_UNAVAILABLE": "문서 출처에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        }
        job["error_message"] = messages.get(job["error_code"], "문서 수집에 실패했습니다. 다시 시도해 주세요.")
    return job
