"""Compare five baseline and RAG answers with the same model and temperature."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

from app.ai.data_prep.movies import load_movies
from app.ai.evaluation import factual_match, percentile, representative_cases, unknown_acknowledged
from app.ai.graph.qa import build_graph
from app.ai.indexing.vector_store import OpenAIEmbedder, VectorStore
from app.config import ROOT, Settings


def evaluate(settings: Settings, client, embedder, case_set: str = "standard") -> dict:
    movies = load_movies(settings.data_dir / "movies.json")
    cases = representative_cases(movies, case_set)
    store = VectorStore(settings.chroma_dir)
    graph = build_graph(
        store.client.get_collection("movie_meta"),
        store.client.get_collection("movie_docs"),
        embedder, client, os.getenv("MOVIELAB_CHAT_MODEL", "gpt-4.1-mini"),
        settings.top_k, retrieval_strategy=settings.retrieval_strategy,
        fetch_k=settings.mmr_fetch_k,
    )
    model = os.getenv("MOVIELAB_CHAT_MODEL", "gpt-4.1-mini")
    rows = []
    for case in cases:
        started = time.perf_counter()
        baseline = client.chat.completions.create(
            model=model, temperature=0,
            messages=[
                {"role": "system", "content": "영화 질문에 간결하게 답하세요. 확인할 수 없으면 모른다고 답하세요."},
                {"role": "user", "content": case["question"]},
            ],
        ).choices[0].message.content or ""
        baseline_ms = round((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        rag = graph.invoke({"movie_id": case["movie_id"], "question": case["question"]})
        rag_ms = round((time.perf_counter() - started) * 1000)
        unknown = case["expected"] is None
        rows.append({
            **case, "baseline_answer": baseline.strip(),
            "baseline_correct": unknown_acknowledged(baseline) if unknown else factual_match(baseline, case["expected"]),
            "baseline_ms": baseline_ms,
            "rag_answer": rag["answer"],
            "rag_correct": (unknown_acknowledged(rag["answer"]) and not rag["citations"]) if unknown
                else factual_match(rag["answer"], case["expected"]),
            "rag_citations": rag["citations"], "rag_ms": rag_ms,
        })
    timings = [row["rag_ms"] for row in rows]
    known = [row for row in rows if row["expected"] is not None]
    unknown_cases = [row for row in rows if row["expected"] is None]
    return {
        "model": model, "temperature": 0, "retrieval_strategy": settings.retrieval_strategy,
        "case_set": case_set,
        "cases": rows,
        "summary": {
            "baseline_correct": sum(row["baseline_correct"] for row in rows),
            "rag_correct": sum(row["rag_correct"] for row in rows),
            "rag_cited": sum(bool(row["rag_citations"]) for row in known),
            "rag_citable": len(known),
            "unknown_answered": sum(row["rag_correct"] for row in unknown_cases),
            "unknown_count": len(unknown_cases),
            "rag_p50_ms": round(statistics.median(timings)),
            "rag_p95_ms": round(percentile(timings, 95)),
        },
    }


def markdown(report: dict) -> str:
    lines = [
        "# RAG 전후 비교 실험", "",
        f"모델: `{report['model']}` · temperature: 0 · 검색: `{report['retrieval_strategy']}` · 문항: `{report.get('case_set', 'standard')}`", "",
        "| 영화 ID | 정답 | 기본 답변 일치 | RAG 일치 | RAG 인용 | 기본 ms | RAG ms |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["cases"]:
        lines.append(
            f"| {row['movie_id']} | {row['expected'] or '문서에서 확인 불가'} | {row['baseline_correct']} | "
            f"{row['rag_correct']} | {len(row['rag_citations'])} | "
            f"{row['baseline_ms']} | {row['rag_ms']} |"
        )
    summary = report["summary"]
    lines.extend([
        "", f"자동 판정 통과: 기본 {summary['baseline_correct']}/{len(report['cases'])}, RAG {summary['rag_correct']}/{len(report['cases'])}.",
        f"근거 필요한 문항의 RAG 인용: {summary['rag_cited']}/{summary['rag_citable']}. RAG p50 {summary['rag_p50_ms']}ms, p95 {summary['rag_p95_ms']}ms.",
    ])
    if summary["unknown_count"]:
        lines.append(f"미지 질문의 RAG 모름 처리: {summary['unknown_answered']}/{summary['unknown_count']}.")
    lines.extend([
        "", "감독명은 문자열 일치, 미지 질문은 명시적인 모름 표현과 인용 0건으로 자동 판정합니다. 복합 사실성과 인용의 실제 뒷받침 여부는 원문 수동 검토가 필요합니다.",
        "", "## 원문 답변과 근거", "",
    ])
    for row in report["cases"]:
        citations = ", ".join(f"[{item['citation_id']}]({item['url_or_file']})" for item in row["rag_citations"])
        lines.extend([
            f"### {row['movie_id']} {row['title']}", "",
            f"질문: {row['question']}", "",
            f"기본 답변: {row['baseline_answer']}", "",
            f"RAG 답변: {row['rag_answer']}", "",
            "인용: " + (citations or "없음"),
            "",
        ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "temp" / "RAG_전후_비교.md")
    parser.add_argument("--case-set", choices=("standard", "obscure", "obscure_unknown"), default="standard")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.chroma_dir.exists():
        parser.error("movie index is missing; run scripts.index_data first")
    from openai import OpenAI
    report = evaluate(settings, OpenAI(timeout=20.0, max_retries=0), OpenAIEmbedder(settings.embedding_model, timeout=8.0, max_retries=0), args.case_set)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown(report), encoding="utf-8")
    json_output = args.output.with_suffix(".json")
    if args.output.parent.resolve() == (ROOT / "docs").resolve():
        json_output = ROOT / "docs" / "temp" / json_output.name
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
