"""Run one live API journey with the configured model and local index."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.main import app
from app.config import ROOT


def checked(client: TestClient, method: str, path: str, **kwargs) -> dict:
    response = client.request(method, path, **kwargs)
    if response.status_code != 200:
        detail = response.json().get("detail", {})
        raise RuntimeError(f"{path}: HTTP {response.status_code} {detail.get('code', '')}")
    return response.json()


def main() -> None:
    movie_id = "157336"
    with TestClient(app) as client:
        before = checked(client, "GET", "/api/index/status")
        if not before["ready"]:
            raise RuntimeError("movie index is not ready")
        started = checked(client, "POST", "/api/collection-jobs", json={
            "movie_id": movie_id,
            "question": "인터스텔라의 과학 자문과 개봉 정보는 무엇인가요?",
        })
        job = checked(client, "GET", f"/api/jobs/{started['job_id']}")
        if job["status"] != "done":
            raise RuntimeError(f"collection job did not finish: {job['error_code']}")
        collected = job["result"]
        chat = checked(client, "POST", "/api/chat", json={
            "movie_id": movie_id, "question": "인터스텔라의 과학 자문은 누구인가요?",
        })
        mixed_chat = checked(client, "POST", "/api/chat", json={
            "movie_id": movie_id, "question": "인터스텔라의 감독과 상영 시간은 각각 무엇인가요?",
            "conversation_id": chat["conversation_id"],
        })
        guide = checked(client, "POST", "/api/guides", json={"movie_id": movie_id})
        discovery = checked(client, "POST", "/api/discover", json={"condition": "2014년 SF 영화"})
        history = checked(client, "GET", f"/api/conversations/{movie_id}")
        after = checked(client, "GET", "/api/index/status")
    report = {
        "movie_id": movie_id,
        "index_before": before,
        "index_after": after,
        "collect": {
            "job_status": job["status"],
            "source_types": sorted({item["source_type"] for item in collected["selected"]}),
            "selected_count": len(collected["selected"]),
            "cache_hits": collected["cache_hits"],
            "indexed_chunks": collected["indexed_chunks"],
            "warnings": collected["warnings"],
            "elapsed_ms": collected["elapsed_ms"],
        },
        "chat": {"citation_count": len(chat["citations"]), "elapsed_ms": chat["elapsed_ms"]},
        "mixed_chat": {
            "citation_count": len(mixed_chat["citations"]),
            "source_types": sorted({item["source_type"] for item in mixed_chat["citations"]}),
            "elapsed_ms": mixed_chat["elapsed_ms"],
        },
        "guide": {"citation_count": len(guide["citations"]), "elapsed_ms": guide["elapsed_ms"]},
        "discovery": {"result_count": len(discovery["items"]), "candidate_count": discovery["candidate_count"]},
        "history_messages": len(history["messages"]),
    }
    path = ROOT / "docs" / "temp" / "실행검증.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(path), **report}, ensure_ascii=True))


if __name__ == "__main__":
    main()
