# Movie Lab

Movie Lab은 영화를 찾고 작품에 대해 질문할 수 있는 영화 정보 서비스입니다. 영화 메타데이터와 관련 문서를 검색해 답변을 만들고, 답변에 사용한 출처를 함께 보여줍니다.

## 주요 기능

- **영화 찾기:** 제목으로 검색하거나 연도·장르 등의 조건으로 작품을 탐색합니다.
- **영화 질문:** 선택한 영화에 대해 질문하고, 답변의 근거 번호에서 원문 출처를 확인합니다.
- **새 문서 찾기:** 필요한 자료가 부족할 때 관련 문서를 수집하고 색인합니다.
- **관람 가이드:** 작품 소개, 볼거리, 배경 정보를 근거와 함께 보여줍니다.
- **대화 이어보기:** 같은 브라우저에서 영화별 질문과 답변을 다시 확인할 수 있습니다.

화면은 데스크톱과 모바일에 맞춰 표시됩니다. 답변에 필요한 근거를 찾지 못하면 확인되지 않는다고 안내합니다.

## 실행 방법

Python 3.10 이상과 채팅·임베딩을 지원하는 OpenAI 호환 API 키가 필요합니다. 아래 명령은 **Windows PowerShell** 기준입니다.

```powershell
git clone https://github.com/anonymity-developer/movie_chatbot.git
cd movie_chatbot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 본인이 사용하는 API의 키를 현재 터미널에 설정합니다.
$env:OPENAI_API_KEY = "<API 키>"
# 별도의 OpenAI 호환 API를 사용한다면 해당 서비스의 기본 주소도 설정합니다.
# $env:OPENAI_BASE_URL = "<API 기본 주소>"

# 외부 문서 수집을 사용할 때 연락 가능한 프로젝트 주소를 지정합니다.
$env:MOVIELAB_WIKIMEDIA_USER_AGENT = "MovieLab/1.0 (https://github.com/anonymity-developer/movie_chatbot)"

.\.venv\Scripts\python.exe -m scripts.index_data
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app
```

서버가 시작되면 <http://127.0.0.1:8000/>에서 접속합니다. 처음 색인할 때는 포함된 영화 964편과 위키백과 문서 3,725건의 임베딩을 생성하므로 시간이 걸리고 API 사용량이 발생합니다. 색인 전에 데이터 형식만 확인하려면 `.\.venv\Scripts\python.exe -m scripts.index_data --validate-only`를 실행할 수 있습니다.

테스트는 `.\.venv\Scripts\python.exe -m unittest discover -s tests`로 실행합니다. 대화 기록과 검색 색인은 실행 환경의 `.local/`에 저장됩니다.

## 구성

| 경로 | 내용 |
|---|---|
| `app/` | 데이터 수집·색인, 검색·답변, API, 대화 저장 |
| `frontend/` | 웹 화면 |
| `scripts/` | 색인과 검색 비교 등 실행 도구 |
| `tests/` | 자동 테스트 |
| `data/` | 영화 메타데이터와 위키백과 문서 묶음 |

## 데이터 출처

영화 메타데이터는 TMDB 기반이며, 위키백과 문서는 한국어 위키백과(CC BY-SA 4.0), Wikidata 정보는 CC0를 따릅니다. 답변 화면에서 개별 출처의 원문 링크를 확인할 수 있습니다. 데이터 재사용 시 각 출처의 이용 조건을 확인해야 합니다. 이 서비스는 TMDB의 공식 인증이나 승인을 받은 서비스가 아닙니다.
