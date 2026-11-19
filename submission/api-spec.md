# API 명세서

서버의 `/docs`와 `/openapi.json`에 요청·응답 모델이 표시된다. 관람 대화, 계획, 실행 로그는 `movielab_session` 쿠키로 브라우저를 식별한다.

| 메서드 | 경로 | 입력과 응답 |
|---|---|---|
| POST | `/api/planner/chat` | `{message}` → 모드, 상태, 답변, 조건, 추천·제외 후보, 영화관, 근거, 오류, 재계획 횟수, 실행 ID·시간·이벤트 |
| GET | `/api/planner/tools` | 입력 없음 → 외부 Tool별 이름·설명·입력·출력 스키마 |
| GET | `/api/plans` | 입력 없음 → 계획 목록 |
| POST | `/api/plans` | `{title,date,starts_at,ends_at}` → 201, `{plan,overlaps}` |
| POST | `/api/plans/{plan_id}/calendar` | 저장한 UUID → 캘린더 등록 결과와 실행 로그. 제목과 시각은 저장소에서 조회 |
| GET | `/api/plans/{plan_id}` | UUID → 관람 계획 상세 |
| PUT | `/api/plans/{plan_id}` | UUID와 전체 계획 입력 → `{plan,overlaps}` |
| DELETE | `/api/plans/{plan_id}` | UUID → 204 |
| GET | `/api/execution-logs` | 입력 없음 → 실행 ID·시작 시각·이벤트 수 목록 |
| GET | `/api/execution-logs/{run_id}` | UUID → 순번·시각·이벤트 종류·데이터 배열 |
| DELETE | `/api/execution-logs/{run_id}` | UUID → 204 |

계획 날짜는 `YYYY-MM-DD`, 현지 시작·종료는 `HH:MM`이다. 저장된 계획에는 ID와 생성 시각이 추가된다. 없는 레코드나 다른 브라우저의 레코드는 404, 잘못된 형식은 422를 반환한다. 플래너의 공급자 연결 문제는 답변 상태와 Tool 오류 코드로 반환한다.

대화 응답의 `evaluation`에는 규칙 기반 판정과 경고, `model_calls`에는 역할별 호출 수가 들어간다. `documents`는 검색 문서와 출처, `calendar_event`는 일정 백엔드 및 ID를 담는다. 진단 결과는 별도 실행 이벤트로 기록한다.
