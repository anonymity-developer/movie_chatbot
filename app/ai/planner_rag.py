"""Adapt the second-round Chroma corpus to the planner's local tool contract."""

from openai import OpenAIError
from pydantic import BaseModel, Field

from app.ai.data_prep.movies import load_movies
from app.ai.indexing.vector_store import OpenAIEmbedder, VectorStore
from app.ai.planner_tools import Tool, ToolFailure
from app.ai.retrieval.evidence import retrieve
from app.config import openai_options


class RagInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    year: int | None = None
    question: str = Field(min_length=1, max_length=2000)


class RagResult(BaseModel):
    title: str
    documents: list[dict]


def rag_tool(settings, demo):
    def search(args):
        if demo or not settings.chroma_dir.is_dir():
            raise ToolFailure("NO_INDEX", "영화 문서 색인이 없습니다. 영화 자료를 먼저 색인해 주세요.")
        if not openai_options():
            raise ToolFailure("MISSING_KEY", "문서 검색에 사용할 GMS 키를 설정해 주세요.")
        matches = [
            movie for movie in load_movies(settings.data_dir / "movies.json")
            if args.title.strip().casefold() in (movie["title"].casefold(), str(movie.get("original_title") or "").casefold())
            and (args.year is None or args.year == movie.get("year"))
        ]
        if len(matches) > 1:
            raise ToolFailure("AMBIGUOUS", "제목에 해당하는 작품이 여러 편입니다. 개봉 연도를 알려 주세요.")
        if not matches:
            raise ToolFailure("NO_RESULT", "색인 대상 영화에서 제목을 찾지 못했습니다.")
        try:
            store = VectorStore(settings.chroma_dir)
            meta = store.client.get_collection("movie_meta")
            docs = store.client.get_collection("movie_docs")
        except Exception:
            raise ToolFailure("NO_INDEX", "영화 문서 색인이 없습니다. 영화 자료를 먼저 색인해 주세요.") from None
        try:
            embedder = OpenAIEmbedder(settings.embedding_model, timeout=8.0, max_retries=0)
            items = retrieve(matches[0]["movie_id"], args.question, meta, docs, embedder,
                             max(2, settings.top_k), strategy=settings.retrieval_strategy, fetch_k=settings.mmr_fetch_k)
        except LookupError:
            raise ToolFailure("NO_INDEX", "이 영화의 색인이 없습니다.") from None
        except OpenAIError:
            raise ToolFailure("SOURCE_UNAVAILABLE", "문서 검색 모델 연결을 확인해 주세요.") from None
        documents = [item.public(claim=args.question) for item in items if item.source_type != "movie_metadata"]
        if not documents:
            raise ToolFailure("NO_RESULT", "관련 근거 문서를 찾을 수 없습니다.")
        return {"title": matches[0]["title"], "documents": documents}

    return Tool("줄거리·제작 배경처럼 외부 영화 API로 답하기 어려운 작품 정보를 근거와 함께 확인할 때",
                RagInput, RagResult, search, "local")
