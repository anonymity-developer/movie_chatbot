"""Registered, typed external tools for the movie planner."""

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
import json
import os
import re
from threading import Lock
from time import monotonic, sleep, time
from typing import Callable

from pydantic import BaseModel, Field, ValidationError
import requests


KOBIS = "https://www.kobis.or.kr/kobisopenapi/webservice/rest"
OVERPASS = "https://overpass-api.de/api/interpreter"
# Approximate bounding box for the Gangnam Station area (south, west, north, east).
GANGNAM_STATION_REGIONS = {"강남", "강남역", "서울 강남", "서울 강남역", "서울특별시 강남역"}
NOMINATIM = "https://nominatim.openstreetmap.org/search"
_nominatim_lock = Lock()
_last_nominatim_request = 0.0


class CandidateInput(BaseModel):
    requested_date: date | None = None
    offset: int = Field(default=0, ge=0, le=3)


class DetailInput(BaseModel):
    movie_id: str = Field(pattern=r"^\d{8}$")


class WeeklyInput(BaseModel):
    requested_date: date | None = None


class TheaterInput(BaseModel):
    region: str = Field(min_length=1, max_length=75)
    query_hint: str = Field(default="영화관", min_length=1, max_length=30)


class Candidate(BaseModel):
    movie_id: str
    title: str


class Candidates(BaseModel):
    requested_date: date | None
    lookup_date: date
    period: str
    items: list[Candidate]


class Detail(BaseModel):
    title: str
    runtime: int = Field(gt=0)
    rating: str | None = None
    genres: list[str] = Field(default_factory=list)


class Theater(BaseModel):
    name: str
    address: str
    url: str


class Theaters(BaseModel):
    items: list[Theater]
    source: str


class ToolFailure(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Tool:
    description: str
    input: type[BaseModel]
    output: type[BaseModel]
    handler: Callable
    kind: str = "external"

    def run(self, arguments: dict) -> dict:
        try:
            payload = self.input.model_validate(arguments)
        except ValidationError:
            return {"ok": False, "error": {"code": "INVALID_INPUT", "message": "조회 조건을 확인해 주세요."}}
        try:
            result = self.output.model_validate(self.handler(payload))
            return {"ok": True, "data": result.model_dump(mode="json")}
        except ToolFailure as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        except (TypeError, KeyError, ValueError, AttributeError):
            return {"ok": False, "error": {"code": "INVALID_RESPONSE", "message": "외부 서비스의 응답을 읽을 수 없습니다."}}


def request_json(url, params, *, timeout=(2, 4)):
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": "AgenttripMoviePlanner/1.0 (https://github.com/anonymity-developer/movie_chatbot)"},
            timeout=timeout,
        )
        if response.status_code in (401, 403):
            raise ToolFailure("AUTH_FAILED", "외부 서비스 인증 설정을 확인해 주세요.")
        if response.status_code == 429:
            raise ToolFailure("RATE_LIMITED", "외부 서비스 호출 한도에 도달했습니다.")
        response.raise_for_status()
    except requests.Timeout:
        raise ToolFailure("TIMEOUT", "외부 조회 시간이 초과되었습니다.") from None
    except requests.RequestException:
        raise ToolFailure("SOURCE_UNAVAILABLE", "외부 서비스에 연결할 수 없습니다.") from None
    return response.json()


def fetch(url: str, params: dict, key_name: str) -> dict:
    key = os.getenv(key_name)
    if not key:
        raise ToolFailure("MISSING_KEY", f"서버의 {key_name} 설정을 확인해 주세요.")
    body = request_json(url, {**params, "key": key})
    if "faultInfo" in body:
        code = str(body["faultInfo"].get("errorCode", ""))
        if code in {"320010", "320011"}:
            raise ToolFailure("AUTH_FAILED", "KOBIS 인증 설정을 확인해 주세요.")
        raise ToolFailure("SOURCE_UNAVAILABLE", "KOBIS에서 조회를 완료하지 못했습니다.")
    return body


def osm_names(region):
    name = region.split()[-1]
    city_names = {
        "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
        "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    }
    if name in city_names:
        return [city_names[name]]
    return [name, name + "구"] if len(name) >= 2 and not name.endswith(("역", "구", "시", "군", "동", "읍", "면")) else [name]


@lru_cache(maxsize=128)
def osm_cinemas(endpoint, region, radius, hour):
    query = osm_cinema_region_query(region, radius)
    body = request_json(endpoint, {"data": query}, timeout=(3, 8))
    if body.get("remark"):
        raise ToolFailure("SOURCE_UNAVAILABLE", "지도 서비스에서 영화관 조회를 완료하지 못했습니다.")
    return osm_cinema_items(body["elements"])


@lru_cache(maxsize=8)
def gangnam_cinemas(endpoint, hour):
    global _last_nominatim_request
    with _nominatim_lock:
        sleep(max(0.0, 1.1 - (monotonic() - _last_nominatim_request)))
        _last_nominatim_request = monotonic()
        body = request_json(endpoint, {
            "q": "[cinema] Gangnam-gu, Seoul", "format": "jsonv2", "limit": 5,
            "countrycodes": "kr", "accept-language": "ko",
            "viewbox": "127.003,37.520,127.052,37.475", "bounded": 1,
        }, timeout=(3, 6))
    return [
        {"name": row["name"], "address": row.get("display_name") or "",
         "url": f"https://www.openstreetmap.org/{row['osm_type']}/{int(row['osm_id'])}"}
        for row in body if row.get("name") and row.get("type") == "cinema"
        and row.get("osm_type") in {"node", "way", "relation"}
    ][:5]


def osm_cinema_region_query(region, radius):
    bounds = "(33,124,39,132)"
    local_names = osm_names(region)
    stem = region.split()[-1].removesuffix("역")
    # The request contains only validated public place names. Exact tag filters keep the query bounded.
    def quoted(name):
        return json.dumps(name, ensure_ascii=False)

    station_query = "".join(
        f'nwr["public_transport"="station"]["name"={quoted(name)}]{bounds};'
        for name in (stem, stem + "역")
    )
    area_query = "".join(f'area["boundary"="administrative"]["name"={quoted(name)}];' for name in local_names)
    return "[out:json][timeout:5];" + f"({station_query})->.station;({area_query})->.district;" + (
        f'(nwr(around.station:{radius})["amenity"="cinema"];'
        f'nwr(area.district)["amenity"="cinema"]{bounds};);out center 15;'
    )


def osm_cinema_items(elements):
    items = []
    seen = set()
    for row in elements:
        tags = row.get("tags", {})
        name = tags.get("name:ko") or tags.get("name")
        if not name or name in seen or row["type"] not in {"node", "way", "relation"}:
            continue
        seen.add(name)
        address = tags.get("addr:full") or " ".join(tags[key] for key in (
            "addr:city", "addr:district", "addr:street", "addr:housenumber"
        ) if tags.get(key))
        items.append({"name": name, "address": address, "url": f"https://www.openstreetmap.org/{row['type']}/{int(row['id'])}"})
    return items[:5]


def lookup_day(requested: date | None, today: date, offset: int) -> date:
    latest = today - timedelta(days=1)
    return min(requested or latest, latest) - timedelta(days=offset)


def registry(today: date, demo: bool) -> dict[str, Tool]:
    def candidate_response(requested, lookup, offset, *, weekly=False):
        if demo:
            batches = [
                [("90000001", "데모 · 긴 여정"), ("90000002", "데모 · 도시의 밤"), ("90000003", "데모 · 상세 누락")],
                [("90000002", "데모 · 도시의 밤"), ("90000004", "데모 · 짧은 산책")],
                [("90000005", "데모 · 새벽"), ("90000006", "데모 · 그림자")],
                [("90000007", "데모 · 바다"), ("90000008", "데모 · 노을")],
            ]
            rows = batches[offset]
        else:
            period = "weekly" if weekly else "daily"
            params = {"targetDt": lookup.strftime("%Y%m%d"), "itemPerPage": 5}
            if weekly:
                params["weekGb"] = "0"
            body = fetch(f"{KOBIS}/boxoffice/search{period.title()}BoxOfficeList.json", params, "KOBIS_API_KEY")
            rows = [(row["movieCd"], row["movieNm"]) for row in body["boxOfficeResult"][f"{period}BoxOfficeList"]]
        if not rows:
            raise ToolFailure("NO_RESULT", "해당 집계일에 후보 영화가 없습니다.")
        return {"requested_date": requested, "lookup_date": lookup, "period": "weekly" if weekly else "daily",
                "items": [{"movie_id": key, "title": title} for key, title in rows]}

    def candidates(args: CandidateInput):
        return candidate_response(args.requested_date, lookup_day(args.requested_date, today, args.offset), args.offset)

    def weekly(args: WeeklyInput):
        base = lookup_day(args.requested_date, today, 0)
        sunday = base - timedelta(days=(base.weekday() + 1) % 7)
        return candidate_response(args.requested_date, sunday, 1, weekly=True)

    def detail(args: DetailInput):
        if demo:
            runtimes = [170, 100, None, 72, 105, 110, 115, 120]
            titles = ["긴 여정", "도시의 밤", "상세 누락", "짧은 산책", "새벽", "그림자", "바다", "노을"]
            position = int(args.movie_id) - 90000001
            if not 0 <= position < len(runtimes):
                raise ToolFailure("NO_RESULT", "영화 상세 정보를 찾을 수 없습니다.")
            runtime = runtimes[position]
            show_time = str(runtime) if runtime is not None else ""
            movie = {"movieNm": f"데모 · {titles[position]}", "showTm": show_time,
                     "audits": [{"watchGradeNm": "12세이상관람가"}], "genres": [{"genreNm": "드라마"}]}
        else:
            body = fetch(f"{KOBIS}/movie/searchMovieInfo.json", {"movieCd": args.movie_id}, "KOBIS_API_KEY")
            movie = body["movieInfoResult"].get("movieInfo")
        if not movie:
            raise ToolFailure("NO_RESULT", "영화 상세 정보를 찾을 수 없습니다.")
        runtime = str(movie.get("showTm") or "")
        if not runtime.isdecimal() or int(runtime) <= 0:
            raise ToolFailure("NO_RUNTIME", "러닝타임을 확인할 수 없습니다.")
        return {"title": movie["movieNm"], "runtime": int(runtime),
                "rating": next((item["watchGradeNm"] for item in movie.get("audits", []) if item.get("watchGradeNm")), None),
                "genres": [item["genreNm"] for item in movie.get("genres", []) if item.get("genreNm")]}

    def theaters(args: TheaterInput):
        if demo:
            return {"source": "demo", "items": [{"name": "데모 · 영화관", "address": args.region, "url": ""}]}
        region = " ".join(args.region.split())
        if not re.fullmatch(r"[가-힣A-Za-z0-9 -]{2,75}", region):
            raise ToolFailure("INVALID_INPUT", "공개 행정구역 또는 역 이름을 입력해 주세요.")
        radius = 2200 if args.query_hint == "영화관" else 3500
        hour = int(time() // 3600)
        location_search = os.getenv("PLANNER_NOMINATIM_URL", NOMINATIM).strip()
        if region in GANGNAM_STATION_REGIONS and location_search:
            items = gangnam_cinemas(location_search, hour)
        else:
            items = osm_cinemas(os.getenv("PLANNER_OVERPASS_URL", OVERPASS), region, radius, hour)
        if not items:
            raise ToolFailure("NO_RESULT", "해당 지역의 OpenStreetMap 영화관 검색 결과가 없습니다. 구 또는 역 이름을 확인해 주세요.")
        return {"source": "OpenStreetMap", "items": items}

    return {
        "box_office": Tool("후보 영화가 필요할 때 KOBIS 일별 순위 조회", CandidateInput, Candidates, candidates),
        "weekly_box_office": Tool("일별 순위에서 적합한 후보가 없을 때 주간 순위로 후보 보충", WeeklyInput, Candidates, weekly),
        "movie_detail": Tool("후보 ID로 러닝타임, 장르, 관람등급을 확인할 때", DetailInput, Detail, detail),
        "theaters": Tool("공개 행정구역·역 주변 영화관이 필요할 때 OpenStreetMap 검색. 결과 없음에서 query_hint를 극장으로 바꾸면 역 주변 범위 확대", TheaterInput, Theaters, theaters),
    }
