"""Check planner integrations with small read-only requests and print only status."""

import argparse
from datetime import datetime
import os
from pathlib import Path
from time import perf_counter

from app.ai.graph.planner import Interpretation, SEOUL
from app.ai.indexing.vector_store import OpenAIEmbedder
from app.ai.planner_calendar import GoogleCalendar
from app.ai.planner_model import PlannerError, complete
from app.ai.planner_rag import rag_tool
from app.ai.planner_tools import ToolFailure, registry
from app.config import ROOT, Settings, openai_options


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--google", action="store_true", help="also read Google's free/busy API")
    parser.add_argument("--rag", action="store_true", help="also query the existing Chroma index")
    parser.add_argument("--embedding", action="store_true", help="test one embedding before indexing")
    args = parser.parse_args()
    today = datetime.now(SEOUL).date()
    failed = False

    def probe(name, operation):
        nonlocal failed
        start = perf_counter()
        try:
            outcome = operation()
            status = "OK" if outcome["ok"] else outcome["error"]["code"]
        except (PlannerError, ToolFailure) as exc:
            status = exc.code
        except Exception:
            status = "SOURCE_UNAVAILABLE"
        failed |= status != "OK"
        print(f"{name}: {status} ({round(1000 * (perf_counter() - start))} ms)")
        return outcome if status == "OK" else None

    def verify_model():
        result = complete(Interpretation, "영화 플래너의 인사 요청입니다. intent=greeting을 반환하세요.", {"request": "안녕하세요"})
        return {"ok": result.intent == "greeting", "error": {"code": "UNEXPECTED_MODEL_RESPONSE"}}

    probe("GMS", verify_model)
    tools = registry(today, demo=False)
    daily = probe("KOBIS daily", lambda: tools["box_office"].run({}))
    probe("KOBIS weekly", lambda: tools["weekly_box_office"].run({}))
    if daily:
        movie_id = daily["data"]["items"][0]["movie_id"]
        probe("KOBIS detail", lambda: tools["movie_detail"].run({"movie_id": movie_id}))
    probe("OpenStreetMap", lambda: tools["theaters"].run({"region": "강남역"}))
    if args.embedding:
        def verify_embedding():
            if not openai_options():
                return {"ok": False, "error": {"code": "MISSING_KEY"}}
            try:
                vectors = OpenAIEmbedder(Settings.from_env().embedding_model, timeout=10, max_retries=0).embed(["영화 관람"])
                return {"ok": bool(vectors[0]), "error": {"code": "EMPTY_EMBEDDING"}}
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                return {"ok": False, "error": {"code": f"HTTP_{status}" if status else "EMBEDDING_UNAVAILABLE"}}
        probe("Embedding", verify_embedding)
    if args.google:
        token = ROOT / Path(os.getenv("GOOGLE_CALENDAR_TOKEN", ".local/google-calendar-token.json"))
        calendar = GoogleCalendar(token, os.getenv("GOOGLE_CALENDAR_ID", "primary"))

        def verify_google():
            calendar.busy(today)
            return {"ok": True}

        probe("Google Calendar", verify_google)
    if args.rag:
        probe("Chroma RAG", lambda: rag_tool(Settings.from_env(), False).run(
            {"title": "인터스텔라", "question": "제작 배경"}))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
