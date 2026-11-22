"""Offline contract tests for the embedded, default-branch CI metadata observer.

No GitHub API, runner, or untrusted PR code is executed by these tests.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci-completion-metadata.yml"
BASE = "/actions/runs/111"
JOBS_PAGE = BASE + "/attempts/1/jobs?per_page=100&page={}"
JOBS = JOBS_PAGE.format(1)
SOURCE = BASE + "/attempts/1"
ARTIFACTS = BASE + "/artifacts?per_page=100&page={}"


def embedded_python() -> str:
    """Extract the actual inline Python block without requiring a YAML dependency."""
    text = WORKFLOW.read_text(encoding="utf-8")
    opener = "          python - <<'PY'\n"
    closer = "\n          PY"
    assert text.count(opener) == 1
    block = text.split(opener, 1)[1]
    assert closer in block
    return textwrap.dedent(block.split(closer, 1)[0]) + "\n"


def item(n: int) -> dict:
    return {"id": n, "name": f"probe-{n}", "size_in_bytes": n, "expired": False}


class CompletionMetadataObserverTests(unittest.TestCase):
    def execute(
        self,
        pages: dict,
        job_pages: dict | None = None,
        *,
        elapsed_per_request: float = 0.0,
    ) -> tuple[dict, SystemExit | None, list[str]]:
        payloads = {
            SOURCE: {
                "id": 111,
                "name": "CI",
                "run_attempt": 1,
                "head_sha": "abcd",
                "status": "completed",
                "conclusion": "failure",
            },
            JOBS: {
                "total_count": 1,
                "jobs": [
                    {
                        "id": 9,
                        "name": "Windows Python 3.14",
                        "steps": [
                            {
                                "number": 1,
                                "name": "Tests",
                                "status": "in_progress",
                                "conclusion": None,
                            },
                        ],
                    }
                ],
            },
            **(job_pages or {}),
            **pages,
        }
        calls = []
        clock = [0.0]

        def fake_urlopen(request, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 20)
            self.assertLessEqual(timeout, 180 - clock[0])
            url = request.full_url
            prefix = "https://api.github.com/repos/drevendev/HungryCrab"
            self.assertTrue(url.startswith(prefix))
            path = url[len(prefix) :]
            calls.append(path)
            answer = payloads[path]
            clock[0] += elapsed_per_request
            if isinstance(answer, Exception):
                raise answer
            return io.BytesIO(json.dumps(answer).encode("utf-8"))

        env = {
            "GITHUB_REPOSITORY": "drevendev/HungryCrab",
            "SOURCE_RUN_ID": "111",
            "SOURCE_ATTEMPT": "1",
            "GH_TOKEN": "fixture",
        }
        original_dir = Path.cwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.chdir(temp_dir)
                with (
                    mock.patch.dict(os.environ, env),
                    mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
                    mock.patch("time.monotonic", side_effect=lambda: clock[0]),
                ):
                    stopped = None
                    try:
                        exec(
                            compile(embedded_python(), str(WORKFLOW), "exec"),
                            {"__name__": "__main__"},
                        )
                    except SystemExit as error:
                        stopped = error
                result = json.loads(Path("ci-source-metadata.json").read_text(encoding="utf-8"))
            finally:
                os.chdir(original_dir)
        return result, stopped, calls

    def test_collection_budget_exhaustion_keeps_partial_metadata(self):
        snapshot, stopped, calls = self.execute(
            {ARTIFACTS.format(1): {"total_count": 0, "artifacts": []}},
            elapsed_per_request=95.0,
        )
        self.assertIsNotNone(stopped)
        self.assertEqual(snapshot["source_run_id"], 111)
        self.assertEqual(len(snapshot["jobs"]), 1)
        self.assertEqual(snapshot["run_artifacts_not_attempt_scoped"], [])
        self.assertEqual(snapshot["collection_budget_seconds"], 180)
        self.assertEqual(snapshot["collection_elapsed_seconds"], 190.0)
        self.assertIn(SOURCE, calls)
        self.assertIn(JOBS, calls)
        self.assertNotIn(ARTIFACTS.format(1), calls)
        self.assertTrue(
            any(
                error.get("error") == "COLLECTION_DEADLINE_EXCEEDED" for error in snapshot["errors"]
            )
        )

    def test_invalid_artifact_ids_fail_closed_with_saved_snapshot(self):
        for artifact_id in (True, 0, -1):
            with self.subTest(artifact_id=artifact_id):
                snapshot, stopped, _ = self.execute(
                    {
                        ARTIFACTS.format(1): {
                            "total_count": 1,
                            "artifacts": [
                                {
                                    "id": artifact_id,
                                    "name": "invalid-id",
                                    "size_in_bytes": 1,
                                    "expired": False,
                                }
                            ],
                        }
                    }
                )
                self.assertIsNotNone(stopped)
                self.assertEqual(snapshot["run_artifacts_not_attempt_scoped"], [])
                self.assertTrue(
                    any(
                        error.get("error") == "INVALID_OR_DUPLICATE_ARTIFACT_ID"
                        for error in snapshot["errors"]
                    )
                )
                self.assertTrue(
                    any(
                        error.get("error") == "ARTIFACT_COUNT_MISMATCH"
                        for error in snapshot["errors"]
                    )
                )

    def test_101_artifacts_are_collected_across_two_pages(self):
        snapshot, stopped, calls = self.execute(
            {
                ARTIFACTS.format(1): {
                    "total_count": 101,
                    "artifacts": [item(i) for i in range(1, 101)],
                },
                ARTIFACTS.format(2): {"total_count": 101, "artifacts": [item(101)]},
            }
        )
        self.assertIsNone(stopped)
        self.assertEqual(snapshot["errors"], [])
        self.assertEqual(len(snapshot["run_artifacts_not_attempt_scoped"]), 101)
        self.assertEqual(snapshot["run_artifacts_not_attempt_scoped"][-1]["id"], 101)
        self.assertEqual(calls.count(ARTIFACTS.format(2)), 1)

    def test_failed_second_page_keeps_partial_snapshot_but_fails(self):
        snapshot, stopped, calls = self.execute(
            {
                ARTIFACTS.format(1): {
                    "total_count": 101,
                    "artifacts": [item(i) for i in range(1, 101)],
                },
                ARTIFACTS.format(2): URLError("synthetic second-page outage"),
            }
        )
        self.assertIsNotNone(stopped)
        self.assertEqual(len(snapshot["run_artifacts_not_attempt_scoped"]), 100)
        self.assertTrue(
            any(error.get("error") == "ARTIFACT_COUNT_MISMATCH" for error in snapshot["errors"])
        )
        self.assertTrue(
            any(error.get("path") == ARTIFACTS.format(2) for error in snapshot["errors"])
        )
        self.assertIn(ARTIFACTS.format(2), calls)

    def test_single_empty_page_is_complete(self):
        snapshot, stopped, calls = self.execute(
            {
                ARTIFACTS.format(1): {"total_count": 0, "artifacts": []},
            }
        )
        self.assertIsNone(stopped)
        self.assertEqual(snapshot["run_artifacts_not_attempt_scoped"], [])
        self.assertNotIn(ARTIFACTS.format(2), calls)

    def test_source_attempt_identity_mismatch_fails_closed(self):
        snapshot, stopped, _ = self.execute(
            {
                SOURCE: {
                    "id": 111,
                    "name": "CI",
                    "run_attempt": 2,
                    "head_sha": "abcd",
                    "status": "completed",
                    "conclusion": "failure",
                },
                ARTIFACTS.format(1): {"total_count": 0, "artifacts": []},
            }
        )
        self.assertIsNotNone(stopped)
        self.assertEqual(snapshot["source_attempt"], 1)
        self.assertTrue(
            any(
                error.get("error") == "SOURCE_ATTEMPT_IDENTITY_MISMATCH"
                for error in snapshot["errors"]
            )
        )

    def test_non_object_source_response_preserves_partial_snapshot(self):
        snapshot, stopped, calls = self.execute(
            {
                SOURCE: [],
                ARTIFACTS.format(1): {"total_count": 0, "artifacts": []},
            }
        )
        self.assertIsNotNone(stopped)
        self.assertIn(SOURCE, calls)
        self.assertIn(
            {"path": SOURCE, "error": "NON_OBJECT_JSON_RESPONSE", "type": "list"},
            snapshot["errors"],
        )

    def test_non_object_jobs_page_preserves_partial_snapshot(self):
        snapshot, stopped, calls = self.execute(
            {ARTIFACTS.format(1): {"total_count": 0, "artifacts": []}},
            job_pages={JOBS: None},
        )
        self.assertIsNotNone(stopped)
        self.assertIn(JOBS, calls)
        self.assertIn(
            {"path": JOBS, "error": "NON_OBJECT_JSON_RESPONSE", "type": "NoneType"},
            snapshot["errors"],
        )
        self.assertTrue(
            any(error.get("error") == "INVALID_JOBS_PAGE" for error in snapshot["errors"])
        )

    def test_101_attempt_jobs_are_collected_across_two_pages(self):
        first_page = [{"id": i, "name": f"job-{i}", "steps": []} for i in range(1, 101)]
        snapshot, stopped, calls = self.execute(
            {ARTIFACTS.format(1): {"total_count": 0, "artifacts": []}},
            job_pages={
                JOBS: {"total_count": 101, "jobs": first_page},
                JOBS_PAGE.format(2): {
                    "total_count": 101,
                    "jobs": [{"id": 101, "name": "job-101", "steps": []}],
                },
            },
        )
        self.assertIsNone(stopped)
        self.assertEqual(snapshot["errors"], [])
        self.assertEqual(len(snapshot["jobs"]), 101)
        self.assertEqual(snapshot["jobs"][-1]["id"], 101)
        self.assertEqual(calls.count(JOBS_PAGE.format(2)), 1)

    def test_contradictory_job_total_fails_closed(self):
        snapshot, stopped, calls = self.execute(
            {ARTIFACTS.format(1): {"total_count": 0, "artifacts": []}},
            job_pages={
                JOBS: {
                    "total_count": 2,
                    "jobs": [{"id": 9, "name": "only-job", "steps": []}],
                },
            },
        )
        self.assertIsNotNone(stopped)
        self.assertEqual(len(snapshot["jobs"]), 1)
        self.assertTrue(
            any(error.get("error") == "JOB_COUNT_MISMATCH" for error in snapshot["errors"])
        )
        self.assertTrue(
            any(error.get("error") == "TRUNCATED_JOB_PAGE" for error in snapshot["errors"])
        )
        self.assertNotIn(JOBS_PAGE.format(2), calls)

    def test_duplicate_job_id_on_second_page_fails_closed(self):
        first_page = [{"id": i, "name": f"job-{i}", "steps": []} for i in range(1, 101)]
        snapshot, stopped, calls = self.execute(
            {ARTIFACTS.format(1): {"total_count": 0, "artifacts": []}},
            job_pages={
                JOBS: {"total_count": 101, "jobs": first_page},
                JOBS_PAGE.format(2): {
                    "total_count": 101,
                    "jobs": [{"id": 100, "name": "duplicate", "steps": []}],
                },
            },
        )
        self.assertIsNotNone(stopped)
        self.assertEqual(len(snapshot["jobs"]), 100)
        self.assertTrue(
            any(error.get("error") == "INVALID_OR_DUPLICATE_JOB_ID" for error in snapshot["errors"])
        )
        self.assertTrue(
            any(error.get("error") == "JOB_COUNT_MISMATCH" for error in snapshot["errors"])
        )
        self.assertIn(JOBS_PAGE.format(2), calls)


if __name__ == "__main__":
    unittest.main()
