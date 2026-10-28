import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.jobs.cli.collect import main
from backend.jobs.collector import BoardConfig, collect_jobs, load_boards
from backend.jobs.job_fetchers import (
    FETCHERS,
    AshbyJobFetcher,
    FetchError,
    PersonioJobFetcher,
    RecruiteeJobFetcher,
    RipplingJobFetcher,
    WorkableJobFetcher,
    WorkdayJobFetcher,
)


class AdditionalFetcherTests(unittest.TestCase):
    def test_ashby_filters_unlisted_and_preserves_date_and_secondary_locations(self):
        job = {
            "title": "ML Engineer",
            "location": "London",
            "secondaryLocations": [{"location": "Paris"}],
            "descriptionHtml": "<p>Build ML models</p>",
            "publishedAt": "2026-10-27T10:00:00Z",
            "jobUrl": "https://jobs.ashbyhq.com/example/123",
            "applyUrl": "https://jobs.ashbyhq.com/example/123/apply",
            "isListed": True,
        }

        def handler(request):
            self.assertEqual(request.url.path, "/posting-api/job-board/example.ai")
            return httpx.Response(200, json={"jobs": [job, {**job, "isListed": False}]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            jobs = AshbyJobFetcher("example.ai", "Example", client).fetch("ML", "Paris", None)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].description, "Build ML models")
        self.assertEqual(jobs[0].published_at.day, 27)
        self.assertEqual(jobs[0].location, "London; Paris")

    def test_recruitee_extracts_requirements_and_uses_optional_token(self):
        def handler(request):
            self.assertEqual(request.headers["X-Careers-Sites-Token"], "test-token")
            self.assertEqual(request.url.host, "example.recruitee.com")
            return httpx.Response(
                200,
                json={
                    "offers": [
                        {
                            "id": 1,
                            "slug": "software-engineer",
                            "title": "Software Engineer",
                            "description": "<p>Build tools</p>",
                            "requirements": "<p>Python</p>",
                            "locations": [{"city": "Paris", "country": "France"}],
                            "published_at": "2026-10-27T10:00:00Z",
                        }
                    ]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            job = RecruiteeJobFetcher("example", client=client, careers_token="test-token").fetch()[
                0
            ]
        self.assertEqual(job.description, "Build tools\n\nPython")
        self.assertEqual(job.location, "Paris, France")
        self.assertEqual(str(job.posting_url), "https://example.recruitee.com/o/software-engineer")

    def test_personio_xml_cdata_and_creation_date_is_not_publication(self):
        xml = """<workzag-jobs><position><id>123</id><name>DevOps Engineer</name>
        <office>Berlin</office><additionalOffices><office>Paris</office></additionalOffices>
        <jobDescriptions><jobDescription><name>Your role</name><value><![CDATA[
        <p>Build infrastructure &amp; tools</p>]]></value></jobDescription></jobDescriptions>
        <createdAt>2026-10-27T10:00:00Z</createdAt></position></workzag-jobs>"""
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=xml))
        ) as client:
            job = PersonioJobFetcher("example", client=client).fetch("DevOps", "Paris")[0]
        self.assertEqual(job.description, "Your role\n\nBuild infrastructure & tools")
        self.assertEqual(job.location, "Berlin; Paris")
        self.assertIsNone(job.published_at)
        self.assertTrue(str(job.posting_url).endswith("/job/123"))

    def test_personio_rejects_entity_declarations(self):
        xml = '<!DOCTYPE x [<!ENTITY x "unexpected">]><workzag-jobs/>'
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=xml))
        ) as client:
            with self.assertRaises(FetchError):
                PersonioJobFetcher("example", client=client).fetch()

    def test_personio_allows_only_same_board_personio_domain_redirect(self):
        def handler(request):
            if request.url.host.endswith(".de"):
                return httpx.Response(
                    302, headers={"Location": "https://example.jobs.personio.com/xml"}
                )
            return httpx.Response(200, text="<workzag-jobs/>")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(
                PersonioJobFetcher("example", client=client, request_delay=0).fetch(), []
            )

    def test_rippling_accepts_thin_array_without_inventing_description(self):
        data = [
            {
                "uuid": "123",
                "name": "DevOps Engineer",
                "workLocation": {"name": "Paris"},
                "url": "https://ats.rippling.com/example/jobs/123",
            }
        ]
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
        ) as client:
            job = RipplingJobFetcher("example", client=client).fetch()[0]
        self.assertEqual(job.source_id, "123")
        self.assertEqual(job.location, "Paris")
        self.assertEqual(job.description, "")
        self.assertIsNone(job.published_at)

    def test_workable_follows_widget_pages_and_loads_details(self):
        requested = []

        def handler(request):
            requested.append(str(request.url))
            if "/widget/" in request.url.path:
                second = "page=2" in str(request.url)
                return httpx.Response(
                    200,
                    json={
                        "jobs": [{"shortcode": "B" if second else "A", "title": "Engineer"}],
                        "paging": {} if second else {"next": "?page=2"},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "title": "Software Engineer",
                    "description": "<p>Build tools</p>",
                    "requirements": "<p>Python</p>",
                    "location": {"city": "Paris"},
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            jobs = WorkableJobFetcher("example", client=client, request_delay=0).fetch(limit=None)
        self.assertEqual([job.source_id for job in jobs], ["A", "B"])
        self.assertEqual(jobs[0].description, "Build tools\n\nPython")
        self.assertEqual(len(requested), 4)

    def test_workable_rejects_external_pagination(self):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "jobs": [],
                        "paging": {"next": "https://unexpected.example/jobs"},
                    },
                )
            )
        ) as client:
            with self.assertRaises(FetchError):
                WorkableJobFetcher("example", client=client).fetch(limit=None)

    def test_workday_post_search_pagination_details_and_relative_date(self):
        offsets = []

        def handler(request):
            self.assertEqual(request.url.host, "example.wd3.myworkdayjobs.com")
            if request.method == "POST":
                body = json.loads(request.content)
                self.assertEqual(body["limit"], 20)
                offset = body["offset"]
                offsets.append(offset)
                ids = range(20) if offset == 0 else [20]
                return httpx.Response(
                    200,
                    json={
                        "total": 21,
                        "jobPostings": [
                            {"title": "Engineer", "externalPath": f"/job/Paris/Engineer_{i}"}
                            for i in ids
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "title": "Software Engineer",
                        "jobDescription": "<p>Build Python tools</p>",
                        "location": "Paris",
                        "postedOn": "Posted 3 Days Ago",
                    }
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            jobs = WorkdayJobFetcher(
                "wd3/example/External_Careers", client=client, request_delay=0
            ).fetch(limit=None)
        self.assertEqual(len(jobs), 21)
        self.assertEqual(offsets, [0, 20])
        self.assertEqual(jobs[0].published_text, "Posted 3 Days Ago")
        self.assertIsNone(jobs[0].published_at)
        self.assertEqual(jobs[0].description, "Build Python tools")
        self.assertIn("/en-US/External_Careers/job/", str(jobs[0].posting_url))

    def test_workday_empty_page_before_total_is_an_error(self):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "total": 10,
                        "jobPostings": [],
                    },
                )
            )
        ) as client:
            with self.assertRaisesRegex(FetchError, "Empty page"):
                WorkdayJobFetcher("wd1/example/Careers", client=client).fetch()

    def test_new_sources_reject_malformed_payloads_and_auth_failures(self):
        cases = [
            (AshbyJobFetcher, "example"),
            (RecruiteeJobFetcher, "example"),
            (RipplingJobFetcher, "example"),
            (WorkableJobFetcher, "example"),
            (WorkdayJobFetcher, "wd1/example/Careers"),
            (PersonioJobFetcher, "example"),
        ]
        for cls, board in cases:
            for status, content in [
                (200, "<html>Unexpected response</html>"),
                (401, "Unauthorized"),
            ]:
                with self.subTest(source=cls.__name__, status=status):
                    with httpx.Client(
                        transport=httpx.MockTransport(
                            lambda _, status=status, content=content: httpx.Response(
                                status, text=content
                            )
                        )
                    ) as client:
                        with self.assertRaises(FetchError):
                            cls(board, client=client).fetch()

    def test_new_sources_empty_boards_are_valid(self):
        cases = [
            (AshbyJobFetcher, "example", {"jobs": []}),
            (RecruiteeJobFetcher, "example", {"offers": []}),
            (RipplingJobFetcher, "example", []),
            (WorkableJobFetcher, "example", {"jobs": []}),
            (WorkdayJobFetcher, "wd1/example/Careers", {"jobPostings": [], "total": 0}),
        ]
        for cls, board, payload in cases:
            with (
                self.subTest(source=cls.__name__),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, payload=payload: httpx.Response(200, json=payload)
                    )
                ) as client,
            ):
                self.assertEqual(cls(board, client=client).fetch(), [])

    def test_csv_supports_all_eight_platforms_and_workday_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boards.csv"
            path.write_text(
                "platform,token,company\n"
                + "\n".join(
                    f"{source},"
                    f"{'wd1/example/Careers' if source == 'workday' else 'example'},Example"
                    for source in FETCHERS
                )
            )
            boards = load_boards(path)
        self.assertEqual({board.source for board in boards}, set(FETCHERS))
        self.assertEqual(len(boards), 8)

    def test_collector_dispatches_each_new_source(self):
        boards = [
            BoardConfig(source=source, board="example")
            for source in (
                "ashby",
                "recruitee",
                "rippling",
                "workable",
                "personio",
            )
        ]

        def handler(request):
            host = request.url.host
            if host == "api.ashbyhq.com":
                payload = {"jobs": []}
            elif host.endswith("recruitee.com"):
                payload = {"offers": []}
            elif host.endswith("personio.de"):
                return httpx.Response(200, text="<workzag-jobs/>")
            elif host == "api.rippling.com":
                payload = []
            else:
                payload = {"jobs": []}
            return httpx.Response(200, json=payload)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = collect_jobs(boards, client=client, request_delay=0)
        self.assertEqual(report["summary"]["successful_boards"], 5)

    def test_cli_platform_filter_precedes_board_limit(self):
        report = {
            "jobs": [],
            "boards": [],
            "summary": {"total_jobs": 0, "successful_boards": 1, "failed_boards": 0},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boards.csv"
            path.write_text(
                "platform,token\nworkday,wd1/example/Careers\nashby,first\nashby,second\n"
            )
            output = Path(directory) / "jobs.json"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "jobs",
                        "--boards-file",
                        str(path),
                        "--platforms",
                        "ashby",
                        "--max-boards",
                        "1",
                        "--output",
                        str(output),
                    ],
                ),
                patch("backend.jobs.cli.collect.collect_jobs", return_value=report) as collect,
            ):
                self.assertEqual(main(), 0)
            selected = collect.call_args.args[0]
            self.assertEqual(
                [(board.source, board.board) for board in selected], [("ashby", "first")]
            )
            self.assertEqual(json.loads(output.read_text())["input"]["loaded_supported_rows"], 3)


if __name__ == "__main__":
    unittest.main()
