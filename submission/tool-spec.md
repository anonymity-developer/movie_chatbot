# Tool 정의서

등록 정보는 `GET /api/planner/tools`에서 조회한다. 박스오피스·영화 상세·영화관 위치는 외부 API이며, 문서 검색과 캘린더는 서비스 기능을 호출하는 로컬 Tool이다.

| 이름 | 사용 시점 | 입력 | 성공 데이터 |
|---|---|---|---|
| `box_office` | 첫 후보 또는 이전 일자 후보 목록 | `requested_date?`, `offset=0` | 요청일, 실제 일별 집계일, 집계 유형, KOBIS ID·제목 목록 |
| `weekly_box_office` | 일별 순위에서 적합 후보가 없을 때 | `requested_date?` | 요청일, 최근 완료 주간 기준일, 집계 유형, KOBIS ID·제목 목록 |
| `movie_detail` | 영화의 제약 판단에 필요한 속성 조회 | 8자리 `movie_id` | 제목, 양의 정수 러닝타임, 관람등급, 장르 |
| `theaters` | 관람 지역의 영화관 조회 | 공개 행정구역 또는 역 이름인 `region`, `query_hint?` | 이름, 등록된 경우 주소, OpenStreetMap 장소 URL |
| `rag_search` | 외부 API로 알기 어려운 작품 배경 검색 | 정확한 `title`, `year?`, `question` | 작품명과 문서 제목·관련 구절·출처·라이선스 |
| `calendar_busy` | 관람 예정일의 일정 조회 | `date` | 백엔드 이름 및 각 바쁜 일정의 시작·종료 |
| `calendar_register` | 명시적으로 요청된 저장 계획 등록 | `plan_id` | 백엔드 이름 및 일정 ID |

성공은 `{ok: true, data: ...}`, 실패는 `{ok: false, error: {code, message}}`다. 코드에는 `INVALID_INPUT`, `NO_RESULT`, `NO_RUNTIME`, `MISSING_KEY`, `AUTH_FAILED`, `RATE_LIMITED`, `TIMEOUT`, `SOURCE_UNAVAILABLE`, `INVALID_RESPONSE`가 있다. 일별 후보의 기본 기준은 전날이다. 미래 요청은 집계 가능한 과거 기준을 사용하고 요청일과 조회일을 함께 전달한다.

Tool은 `app/ai/planner_tools.py`에서 설명, 입력·출력 모델, 호출 함수를 묶어 등록한다. 카탈로그에서 모델에 입력 스키마와 설명을 전달한다. 새 Tool은 레지스트리에 추가한 뒤 호출 가능한 단계와 결과 사용처를 정한다.

문서 색인이 없으면 `NO_INDEX`, 작품 제목이 중복되고 연도를 확정할 수 없으면 `AMBIGUOUS`로 응답한다. Worker는 영화관 조회의 `query_hint`만 변경할 수 있다. 지역, 날짜, 영화 ID와 계획 ID는 고정 인자다.

## 출처와 이용 조건

- KOBIS는 영화진흥위원회에서 발급받은 키를 사용한다. 서비스별 호출량과 키의 적용 한도는 키 발급·관리 및 공식 이용 안내에서 확인한다. 서비스는 후보를 집계당 최대 5편 조회하고 같은 실행의 상세 중복을 제거한다. [제공 서비스·약관](https://www.kobis.or.kr/kobisopenapi/homepg/apiservice/searchServiceInfo.do)
- OpenStreetMap Overpass 공개 인스턴스는 서버에서 공개 행정구역 또는 역 이름을 사용해 `amenity=cinema`를 조회한다. 공개 인스턴스의 부하 제한을 고려해 사용자 요청에서만 검색하고 조회 범위와 응답 수를 제한하며 결과를 캐시한다. 주소가 없는 경우 장소 링크를 제공한다. 데이터 출처 표시와 ODbL 조건을 따른다. [Overpass 사용 안내](https://wiki.openstreetmap.org/wiki/Overpass_API) · [OpenStreetMap 저작권](https://www.openstreetmap.org/copyright)
- GMS에서는 교육 계정에 허용된 모델과 사용량을 확인한다. GMS 키는 서버의 환경변수로 제공한다. [SSAFY GMS](https://gms.ssafy.io/web/)
