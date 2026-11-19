# Tool 정의서

등록 정보는 `GET /api/planner/tools`에서 조회한다. 아래 네 Tool은 외부 API로 분류한다.

| 이름 | 사용 시점 | 입력 | 성공 데이터 |
|---|---|---|---|
| `box_office` | 첫 후보 또는 이전 일자 후보 목록 | `requested_date?`, `offset=0` | 요청일, 실제 일별 집계일, 집계 유형, KOBIS ID·제목 목록 |
| `weekly_box_office` | 일별 순위에서 적합 후보가 없을 때 | `requested_date?` | 요청일, 최근 완료 주간 기준일, 집계 유형, KOBIS ID·제목 목록 |
| `movie_detail` | 영화의 제약 판단에 필요한 속성 조회 | 8자리 `movie_id` | 제목, 양의 정수 러닝타임, 관람등급, 장르 |
| `theaters` | 관람 지역의 영화관 조회 | `region` | 이름, 도로명 또는 지번 주소, 장소 URL |

성공은 `{ok: true, data: ...}`, 실패는 `{ok: false, error: {code, message}}`다. 코드에는 `INVALID_INPUT`, `NO_RESULT`, `NO_RUNTIME`, `MISSING_KEY`, `AUTH_FAILED`, `RATE_LIMITED`, `TIMEOUT`, `SOURCE_UNAVAILABLE`, `INVALID_RESPONSE`가 있다. 일별 후보의 기본 기준은 전날이다. 미래 요청은 집계 가능한 과거 기준을 사용하고 요청일과 조회일을 함께 전달한다.

Tool은 `app/ai/planner_tools.py`에서 설명, 입력·출력 모델, 호출 함수를 묶어 등록한다. 카탈로그에서 모델에 입력 스키마와 설명을 전달한다. 새 Tool은 레지스트리에 추가한 뒤 호출 가능한 단계와 결과 사용처를 정한다.

## 출처와 이용 조건

- KOBIS는 영화진흥위원회에서 발급받은 키를 사용한다. 서비스별 호출량과 키의 적용 한도는 키 발급·관리 및 공식 이용 안내에서 확인한다. 서비스는 후보를 집계당 최대 5편 조회하고 같은 실행의 상세 중복을 제거한다. [제공 서비스·약관](https://www.kobis.or.kr/kobisopenapi/homepg/apiservice/searchServiceInfo.do)
- 카카오는 로컬 키워드 검색에 앱 REST API 키를 사용한다. 무료 쿼터 적용 조건과 일간·월간 한도는 앱과 공식 쿼터 표에서 확인한다. 응답의 장소 URL을 제공하고 상영 편성 추정에는 장소 데이터를 쓰지 않는다. [로컬 API](https://developers.kakao.com/docs/ko/local/dev-guide) · [쿼터 및 서비스 약관](https://developers.kakao.com/docs/ko/getting-started/quota)
- GMS에서는 교육 계정에 허용된 모델과 사용량을 확인한다. GMS 키는 서버의 환경변수로 제공한다. [SSAFY GMS](https://gms.ssafy.io/web/)
