import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.jobs.__main__ import main
from backend.jobs.collector import BoardConfig, collect_jobs, load_boards, save_report
from backend.jobs.job_fetchers import GreenhouseJobFetcher, LeverJobFetcher


def greenhouse_job(job_id):
    return {
        "id": job_id,
        "title": "Software Engineer",
        "content": "<p>Build Python tools</p>",
        "location": {"name": "Paris"},
        "absolute_url": f"https://example.com/jobs/{job_id}",
    }


def lever_job(job_id):
    return {
        "id": str(job_id),
        "text": "AI Engineer",
        "descriptionPlain": "Build AI tools",
        "hostedUrl": f"https://jobs.lever.co/example/{job_id}",
        "applyUrl": f"https://jobs.lever.co/example/{job_id}/apply",
    }


class CollectorTests(unittest.TestCase):
    def test_greenhouse_all_jobs_is_not_capped_at_100(self):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json={"jobs": [greenhouse_job(index) for index in range(150)]}
                )
            )
        ) as client:
            jobs = GreenhouseJobFetcher("example", client=client).fetch(limit=None)
        self.assertEqual(len(jobs), 150)

    def test_lever_all_jobs_paginates_beyond_100(self):
        offsets = []

        def handler(request):
            offset = int(request.url.params["skip"])
            offsets.append(offset)
            return httpx.Response(
                200, json=[lever_job(index) for index in range(offset, min(offset + 100, 105))]
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            jobs = LeverJobFetcher("example", client=client, request_delay=0).fetch(limit=None)
        self.assertEqual(len(jobs), 105)
        self.assertEqual(offsets, [0, 100])

    def test_collects_distinct_boards_continues_on_errors_and_preserves_identity(self):
        requests = []

        def handler(request):
            requests.append(request)
            if "/broken/" in request.url.path:
                return httpx.Response(429, headers={"Retry-After": "120"})
            if request.url.host == "api.eu.lever.co":
                return httpx.Response(200, json=[lever_job(123)])
            return httpx.Response(200, json={"jobs": [greenhouse_job(123)]})

        boards = [
            BoardConfig(source="greenhouse", board="broken"),
            BoardConfig(source="greenhouse", board="one"),
            BoardConfig(source="greenhouse", board="two"),
            BoardConfig(source="lever", board="three", region="eu"),
            BoardConfig(source="greenhouse", board="one"),
        ]
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = collect_jobs(boards, client=client, request_delay=0)
            self.assertFalse(client.is_closed)
        self.assertEqual(len(requests), 4)
        self.assertEqual(
            result["summary"],
            {
                "total_boards": 4,
                "successful_boards": 3,
                "failed_boards": 1,
                "pending_boards": 0,
                "total_jobs": 3,
            },
        )
        self.assertEqual([row["board"] for row in result["jobs"]], ["one", "two", "three"])
        self.assertEqual(result["jobs"][-1]["region"], "eu")
        self.assertIn("Retry-After: 120 seconds", result["boards"][0]["error"])
        self.assertEqual(len({row["source_id"] for row in result["jobs"]}), 1)

    def test_failed_lever_board_does_not_export_its_first_page(self):
        def handler(request):
            if request.url.params["skip"] == "0":
                return httpx.Response(200, json=[lever_job(index) for index in range(100)])
            return httpx.Response(500)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = collect_jobs(
                [BoardConfig(source="lever", board="example")], client=client, request_delay=0
            )
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["summary"]["failed_boards"], 1)

    def test_empty_board_is_successful_not_a_fetch_failure(self):
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"jobs": []}))
        ) as client:
            result = collect_jobs(
                [BoardConfig(source="greenhouse", board="example")], client=client
            )
        self.assertEqual(result["summary"]["successful_boards"], 1)
        self.assertEqual(result["summary"]["total_jobs"], 0)

    def test_total_limit_stops_across_boards_without_requesting_the_next(self):
        requested = []

        def handler(request):
            board = request.url.path.split("/")[3]
            requested.append(board)
            return httpx.Response(200, json={"jobs": [greenhouse_job(i) for i in range(2)]})

        boards = [BoardConfig(source="greenhouse", board=name) for name in ("one", "two", "three")]
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = collect_jobs(boards, total_limit=3, client=client, request_delay=0)
        self.assertEqual(requested, ["one", "two"])
        self.assertEqual([row["job_count"] for row in report["boards"]], [2, 1])
        self.assertEqual(report["summary"]["total_jobs"], 3)
        self.assertEqual(report["summary"]["pending_boards"], 1)
        self.assertEqual(report["summary"]["failed_boards"], 0)
        self.assertEqual(report["status"], "limit_reached")

    def test_total_limit_above_100_stops_lever_pagination(self):
        offsets = []

        def handler(request):
            offset = int(request.url.params["skip"])
            offsets.append(offset)
            return httpx.Response(200, json=[lever_job(i) for i in range(offset, offset + 100)])

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = collect_jobs(
                [BoardConfig(source="lever", board="example")],
                total_limit=125,
                client=client,
                request_delay=0,
            )
        self.assertEqual(offsets, [0, 100])
        self.assertEqual(len(report["jobs"]), 125)

    def test_json_is_updated_after_each_success_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jobs.json"

            def handler(request):
                board = request.url.path.split("/")[3]
                saved = json.loads(output.read_text())
                if board == "one":
                    self.assertEqual(saved["summary"]["total_jobs"], 0)
                elif board == "broken":
                    self.assertEqual(saved["summary"]["total_jobs"], 1)
                    self.assertEqual(saved["status"], "running")
                    return httpx.Response(404)
                else:
                    self.assertEqual(saved["summary"]["failed_boards"], 1)
                    self.assertEqual(len(saved["boards"]), 2)
                return httpx.Response(200, json={"jobs": [greenhouse_job(123)]})

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                collect_jobs(
                    [
                        BoardConfig(source="greenhouse", board=name)
                        for name in ("one", "broken", "two")
                    ],
                    client=client,
                    request_delay=0,
                    on_update=lambda snapshot: save_report(snapshot, output),
                )
            saved = json.loads(output.read_text())
            self.assertEqual(saved["status"], "complete")
            self.assertEqual(saved["summary"]["total_jobs"], 2)

    def test_interrupt_preserves_completed_company_results(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jobs.json"

            def handler(request):
                if "/two/" in request.url.path:
                    raise KeyboardInterrupt
                return httpx.Response(200, json={"jobs": [greenhouse_job(123)]})

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(KeyboardInterrupt):
                    collect_jobs(
                        [BoardConfig(source="greenhouse", board=name) for name in ("one", "two")],
                        client=client,
                        request_delay=0,
                        on_update=lambda snapshot: save_report(snapshot, output),
                    )
            saved = json.loads(output.read_text())
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["jobs"][0]["board"], "one")
            self.assertEqual(saved["summary"]["pending_boards"], 1)

    def test_failed_snapshot_write_keeps_previous_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jobs.json"
            save_report({"jobs": []}, output)
            with self.assertRaises(TypeError):
                save_report({"jobs": [object()]}, output)
            self.assertEqual(json.loads(output.read_text()), {"jobs": []})
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_registry_validation_happens_before_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boards.json"
            for payload in (
                [],
                {},
                [{"source": "linkedin", "board": "example"}],
                [
                    {"source": "greenhouse", "board": "../bad"},
                ],
                [{"source": "greenhouse", "board": "example", "region": "eu"}],
            ):
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload))
                    with self.assertRaises(ValueError):
                        load_boards(path)

    def test_batch_command_writes_catalogue_and_failure_report(self):
        def handler(request):
            if "/broken/" in request.url.path:
                return httpx.Response(404)
            return httpx.Response(200, json={"jobs": [greenhouse_job(123)]})

        boards = [
            BoardConfig(source="greenhouse", board="example"),
            BoardConfig(source="greenhouse", board="broken"),
        ]
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = collect_jobs(boards, client=client, request_delay=0)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "boards.json"
            config.write_text(json.dumps([board.model_dump() for board in boards]))
            output = Path(directory) / "nested" / "jobs.json"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "jobs",
                        "--boards-file",
                        str(config),
                        "--output",
                        str(output),
                    ],
                ),
                patch("backend.jobs.__main__.collect_jobs", return_value=report),
            ):
                self.assertEqual(main(), 2)
            saved = json.loads(output.read_text())
            self.assertEqual(saved["jobs"][0]["board"], "example")
            self.assertEqual(saved["summary"]["failed_boards"], 1)

    def test_csv_selects_only_supported_platforms_and_maps_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boards.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=[
                        "platform",
                        "token",
                        "company",
                        "region",
                        "board_url",
                        "api_url",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"platform": "not_implemented", "token": "invalid/for/our/fetchers"},
                        {"platform": "greenhouse", "token": "example", "company": "Example, Inc."},
                        {"platform": "lever", "token": "usboard", "region": "US"},
                        {"platform": "lever", "token": "euboard", "region": "EU"},
                        {
                            "platform": " Lever ",
                            "token": " inferred ",
                            "api_url": "https://api.eu.lever.co/v0/postings/inferred?mode=json",
                        },
                        {"platform": "not_implemented", "token": "other"},
                    ]
                )
            messages = []
            boards = load_boards(path, progress=messages.append)
        self.assertEqual(
            [board.board for board in boards], ["example", "usboard", "euboard", "inferred"]
        )
        self.assertEqual([board.region for board in boards], ["global", "global", "eu", "eu"])
        self.assertEqual(boards[0].company, "Example, Inc.")
        self.assertIn("skipped 2 unsupported rows", messages[0])

    def test_csv_rejects_invalid_supported_boards_before_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boards.csv"
            for content in (
                "platform,token,region\ngreenhouse,../bad,\n",
                "platform,token,region\nlever,example,other\n",
                "platform,token\nnot_implemented,something\n",
                "wrong,headers\nexample,value\n",
            ):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        load_boards(path)


if __name__ == "__main__":
    unittest.main()
