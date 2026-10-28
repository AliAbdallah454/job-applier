import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.jobs.cli.collect import main
from backend.jobs.job_fetchers import FetchError, GreenhouseJobFetcher, LeverJobFetcher
from backend.jobs.models import JobSource


def greenhouse_job(job_id=123, **changes):
    return {
        "id": job_id,
        "title": "Junior AI Engineer",
        "content": "&lt;p&gt;Build Python &amp;amp; AI tools.&lt;/p&gt;",
        "location": {"name": "Paris, France"},
        "absolute_url": f"https://example.com/jobs/{job_id}",
        "first_published": "2026-10-26T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        **changes,
    }


def lever_job(job_id="abc", **changes):
    return {
        "id": job_id,
        "text": "Python Engineer",
        "descriptionPlain": "Build AI tools.",
        "lists": [{"text": "Requirements", "content": "<ul><li>Python experience</li></ul>"}],
        "additionalPlain": "Apply today.",
        "categories": {"location": "Paris"},
        "hostedUrl": f"https://jobs.lever.co/example/{job_id}",
        "applyUrl": f"https://jobs.lever.co/example/{job_id}/apply",
        **changes,
    }


class BoardFetcherTests(unittest.TestCase):
    def test_greenhouse_request_normalization_filtering_and_deduplication(self):
        def handler(request):
            self.assertEqual(request.url.path, "/v1/boards/example/jobs")
            self.assertEqual(request.url.params["content"], "true")
            self.assertEqual(request.headers["Accept"], "application/json")
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        greenhouse_job(),
                        greenhouse_job(),
                        greenhouse_job(456, title="Accountant", content=""),
                    ],
                    "meta": {"total": 3},
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            jobs = GreenhouseJobFetcher("example", "Example Inc", client).fetch(
                "AI Python", "PARIS"
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, JobSource.GREENHOUSE)
        self.assertEqual(jobs[0].company, "Example Inc")
        self.assertEqual(jobs[0].description, "Build Python & AI tools.")
        self.assertEqual(jobs[0].published_at.day, 26)
        self.assertEqual(jobs[0].updated_at.day, 27)

    def test_greenhouse_empty_query_and_empty_board(self):
        for rows in ([], [greenhouse_job()]):
            with (
                self.subTest(rows=rows),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, rows=rows: httpx.Response(200, json={"jobs": rows})
                    )
                ) as client,
            ):
                self.assertEqual(
                    len(GreenhouseJobFetcher("example", client=client).fetch()), len(rows)
                )

    def test_greenhouse_update_timestamp_is_not_used_as_publication(self):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"jobs": [greenhouse_job(first_published=None)]})
            )
        ) as client:
            job = GreenhouseJobFetcher("example", client=client).fetch()[0]
        self.assertIsNone(job.published_at)
        self.assertIsNotNone(job.updated_at)

    def test_lever_paginates_and_uses_eu_host(self):
        offsets = []

        def handler(request):
            self.assertEqual(request.url.host, "api.eu.lever.co")
            skip = int(request.url.params["skip"])
            offsets.append(skip)
            rows = [lever_job("1", categories={"location": "Lyon"}), lever_job("2")]
            return httpx.Response(200, json=rows if skip == 0 else [lever_job("3")])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            jobs = LeverJobFetcher(
                "example", client=client, region="eu", page_size=2, request_delay=0
            ).fetch(
                "python",
                "Paris",
                limit=2,
            )
        self.assertEqual(offsets, [0, 2])
        self.assertEqual([job.source_id for job in jobs], ["2", "3"])
        self.assertEqual(jobs[0].source, JobSource.LEVER)
        self.assertIn("Requirements\n\nPython experience", jobs[0].description)
        self.assertTrue(str(jobs[0].application_url).endswith("/apply"))

    def test_lever_stops_once_limit_is_met(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=[lever_job("1"), lever_job("2")])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            jobs = LeverJobFetcher("example", client=client, page_size=2).fetch(limit=1)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(requests), 1)

    def test_invalid_payloads_raise_explicit_errors(self):
        cases = [
            (GreenhouseJobFetcher, {"jobs": None}),
            (GreenhouseJobFetcher, {"jobs": [], "meta": {"total": 3}}),
            (GreenhouseJobFetcher, {"jobs": [greenhouse_job(id=None)]}),
            (LeverJobFetcher, {"error": "invalid board"}),
            (LeverJobFetcher, [lever_job(id=None)]),
        ]
        for fetcher_type, payload in cases:
            with self.subTest(source=fetcher_type.__name__, payload=payload):
                with httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, payload=payload: httpx.Response(200, json=payload)
                    )
                ) as client:
                    with self.assertRaises(FetchError):
                        fetcher_type("example", client=client).fetch()

    def test_lever_repeated_page_does_not_loop_forever(self):
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[lever_job()]))
        ) as client:
            with self.assertRaisesRegex(FetchError, "Repeated pagination"):
                LeverJobFetcher("example", client=client, page_size=1, request_delay=0).fetch(
                    limit=2
                )

    def test_board_token_is_validated_before_creating_client(self):
        for fetcher_type in (GreenhouseJobFetcher, LeverJobFetcher):
            with self.subTest(source=fetcher_type.__name__), self.assertRaises(ValueError):
                fetcher_type("../jobs?redirect=example")

    def test_cli_exports_normalized_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "jobs.json"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "jobs",
                        "AI",
                        "--source",
                        "greenhouse",
                        "--board",
                        "example",
                        "--output",
                        str(output),
                    ],
                ),
                patch("backend.jobs.cli.collect.create_fetcher") as factory,
            ):
                with httpx.Client() as client:
                    job = GreenhouseJobFetcher("example", client=client)._normalize(
                        greenhouse_job()
                    )
                factory.return_value.fetch.return_value = [job]
                self.assertEqual(main(), 0)
            saved = json.loads(output.read_text())
            self.assertEqual(saved[0]["source"], "greenhouse")
            self.assertEqual(saved[0]["published_at"], "2026-10-26T10:00:00Z")


if __name__ == "__main__":
    unittest.main()
