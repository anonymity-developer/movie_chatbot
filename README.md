# Movie Lab

Movie Lab is a movie discovery and question answering service. It searches movie metadata and related documents, then shows the sources used for each answer. Users can search by title or filters, ask follow-up questions, collect more documents, and open a viewing guide.

## Features

- Title search and filter-based discovery
- Source-backed movie chat with per-movie conversation history
- Wikipedia and Wikidata document retrieval with caching
- Retrieval-augmented viewing guides
- Responsive browser interface

## Run locally

Python 3.10 or newer is required. Install dependencies from `requirements.txt`, configure an OpenAI-compatible chat and embedding API, and then run:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m scripts.index_data
.venv\Scripts\python -m uvicorn app.api.main:app
```

Open `http://127.0.0.1:8000/`. Provide API credentials outside the repository. Live Wikimedia collection requires a client identifier with a contact URL or email address.

The included movie metadata and Wikipedia snapshot are indexed on first setup. Index data and conversation state are stored locally under `.local/`. To run tests, use `python -m unittest discover -s tests`.

## Data and source attribution

Movie metadata is based on TMDB. Wikipedia article snapshots are attributed to Korean Wikipedia under CC BY-SA 4.0; Wikidata information is CC0. Source links appear with individual answers. Review TMDB's current terms before republishing or reusing the movie dataset. This product uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.
