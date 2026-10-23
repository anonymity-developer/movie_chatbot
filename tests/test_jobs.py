import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.api.main as api
from app.db.jobs import JobRepository


class CollectionJobTests(unittest.TestCase):
    def test_job_stages_result_and_session_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite3"
            local_settings = replace(api.settings, cache_db=path)

            def fake_collect(payload, on_stage=None):
                self.assertEqual(payload.movie_id, "157336")
                on_stage("searching")
                on_stage("selecting")
                on_stage("fetching")
                on_stage("indexing")
                return {"selected": [], "indexed_chunks": 0}

            with patch.object(api, "settings", local_settings), patch.object(api, "collect_impl", side_effect=fake_collect), patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
                with TestClient(api.app) as owner, TestClient(api.app) as stranger:
                    started = owner.post("/api/collection-jobs", json={"movie_id": "157336", "question": "질문"})
                    self.assertEqual(started.status_code, 200)
                    job_id = started.json()["job_id"]
                    found = owner.get(f"/api/jobs/{job_id}")
                    self.assertEqual(found.status_code, 200)
                    self.assertEqual(found.json()["status"], "done")
                    self.assertEqual(found.json()["result"]["indexed_chunks"], 0)
                    self.assertEqual(stranger.get(f"/api/jobs/{job_id}").status_code, 404)
                    self.assertEqual(JobRepository(path).get(job_id, started.cookies.get("movielab_session"))["stage"], "done")

    def test_failed_job_exposes_safe_code(self):
        with tempfile.TemporaryDirectory() as directory:
            local_settings = replace(api.settings, cache_db=Path(directory) / "cache.sqlite3")
            with patch.object(api, "settings", local_settings), patch.object(api, "collect_impl", side_effect=RuntimeError("secret provider body")), patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}):
                with TestClient(api.app) as client:
                    started = client.post("/api/collection-jobs", json={"movie_id": "157336", "question": "질문"})
                    result = client.get(f"/api/jobs/{started.json()['job_id']}")
            self.assertEqual(result.json()["status"], "failed")
            self.assertEqual(result.json()["error_code"], "COLLECTION_FAILED")
            self.assertNotIn("secret provider body", result.text)


if __name__ == "__main__":
    unittest.main()
