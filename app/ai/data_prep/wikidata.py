"""Turn a verified Wikidata film item into a second source document."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.ai.data_prep.wikimedia_http import WikimediaClient


PROPERTIES = {
    "P57": "감독",
    "P161": "출연",
    "P136": "장르",
    "P495": "제작 국가",
    "P577": "개봉일",
    "P2047": "상영 시간",
}


def claim_values(entity: dict[str, Any], property_id: str) -> list[Any]:
    values = []
    for statement in entity.get("claims", {}).get(property_id, []):
        if statement.get("rank") == "deprecated":
            continue
        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
        if value is not None:
            values.append(value)
    return values


class WikidataSource:
    source_type = "wikidata"

    def __init__(self, client: WikimediaClient | None = None):
        self.client = client or WikimediaClient()

    def fetch(self, movie: dict[str, Any], wikipedia_title: str) -> dict[str, Any] | None:
        if not wikipedia_title:
            return None
        payload = self.client.get_json("www.wikidata.org", {
            "action": "wbgetentities", "sites": "kowiki", "titles": wikipedia_title,
            "props": "labels|claims", "languages": "ko|en", "format": "json",
        })
        entities = payload.get("entities", {})
        entity = next((item for item in entities.values() if "missing" not in item), None)
        if not entity:
            return None
        tmdb_ids = {str(value) for value in claim_values(entity, "P4947")}
        if movie["movie_id"] not in tmdb_ids:
            return None
        qid = str(entity.get("id") or "")
        if not qid.startswith("Q") or not qid[1:].isdigit():
            return None
        labels = entity.get("labels", {})
        title = (labels.get("ko") or labels.get("en") or {}).get("value") or movie["title"]
        entity_ids = {
            value["id"]
            for property_id in ("P57", "P161", "P136", "P495")
            for value in claim_values(entity, property_id)
            if isinstance(value, dict) and isinstance(value.get("id"), str)
        }
        related: dict[str, Any] = {}
        if entity_ids:
            related = self.client.get_json("www.wikidata.org", {
                "action": "wbgetentities", "ids": "|".join(sorted(entity_ids)[:50]),
                "props": "labels", "languages": "ko|en", "format": "json",
            }).get("entities", {})
        lines = [f"작품: {title}", f"TMDB 영화 ID: {movie['movie_id']}"]
        for property_id, label in PROPERTIES.items():
            rendered = []
            for value in claim_values(entity, property_id)[:5]:
                if isinstance(value, dict) and "id" in value:
                    names = related.get(value["id"], {}).get("labels", {})
                    name = (names.get("ko") or names.get("en") or {}).get("value")
                    if name:
                        rendered.append(name)
                elif property_id == "P577" and isinstance(value, dict) and "time" in value:
                    rendered.append(str(value["time"]).lstrip("+").split("T")[0])
                elif property_id == "P2047" and isinstance(value, dict) and "amount" in value:
                    rendered.append(f"{str(value['amount']).lstrip('+')}분")
            if rendered:
                lines.append(f"{label}: {', '.join(rendered)}")
        return {
            "movie_id": movie["movie_id"], "page_id": qid,
            "title": str(title), "text": "\n".join(lines),
            "url": f"https://www.wikidata.org/wiki/{qid}",
            "source_type": "wikidata", "license": "CC0",
            "collected_at": datetime.now(timezone.utc).date().isoformat(),
        }
