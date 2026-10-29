"""Local frontend and read-only jobs API. Run with python -m frontend.server."""

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import get_args
from urllib.parse import parse_qs, urlsplit

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backend.utils.job_taxonomy import ContractType, JobCategory, Seniority
from frontend.catalogue import JobCatalogue, JobFilters

FRONTEND = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)


def demo_page(filters, cursor=None):
    jobs = [
        (
            "Junior Software Engineer",
            "Example Labs",
            "Paris, France",
            "junior",
            "software_engineer",
            "permanent",
            "Build Python services and help improve our product.\n\n"
            "Work on APIs, automated tests and reliable releases. "
            "We welcome early-career applicants who enjoy learning and collaborating.",
        ),
        (
            "Machine Learning Intern",
            "Example Research",
            "Lyon, France",
            "entry",
            "ml_engineer",
            "internship",
            "Join a research engineering team for a six-month internship.\n\n"
            "Explore model evaluation, prepare datasets and build reproducible "
            "Python experiments alongside a mentor.",
        ),
        (
            "DevOps Apprentice",
            "Example Cloud",
            "Remote · France",
            "entry",
            "devops_engineer",
            "apprenticeship",
            "An apprenticeship focused on cloud infrastructure and developer tools.\n\n"
            "Learn infrastructure as code, observability and CI/CD through practical "
            "work with an experienced platform team.",
        ),
        (
            "Software Engineer",
            "Example Studio",
            "Bordeaux, France",
            "mid",
            "software_engineer",
            "fixed_term",
            "Deliver backend features independently and collaborate on architecture "
            "and code reviews.",
        ),
    ]
    matches = []
    for index, (title, company, location, seniority, category, contract, description) in enumerate(
        jobs
    ):
        if filters.tech != "tech" or (filters.seniorities and seniority not in filters.seniorities):
            continue
        if filters.category and filters.category != category:
            continue
        if filters.contract_type and filters.contract_type != contract:
            continue
        if not all(
            word in f"{title} {company} {location} {description}".casefold()
            for word in filters.search.casefold().split()
        ):
            continue
        matches.append(
            {
                "job_id": str(index),
                "title": title,
                "company": company,
                "location": location,
                "seniority": seniority,
                "category": category,
                "contract_type": contract,
                "description": description,
                "source": "greenhouse",
                "is_tech": True,
                "posting_url": "https://example.com",
                "published_text": None,
            }
        )
    return {"jobs": matches, "next_cursor": None}


def handler_class(read_page, *, demo=False):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlsplit(self.path)
            if request.path == "/api/options":
                self.json_response(
                    {
                        "categories": get_args(JobCategory),
                        "contract_types": get_args(ContractType),
                        "seniorities": get_args(Seniority),
                        "demo": demo,
                    }
                )
                return
            if request.path == "/api/jobs":
                try:
                    params = parse_qs(request.query, keep_blank_values=True)
                    filters = JobFilters(
                        tech=params.get("tech", ["tech"])[0],
                        seniorities=tuple(
                            filter(None, params.get("seniority", ["entry,junior"])[0].split(","))
                        ),
                        category=params.get("category", [""])[0],
                        contract_type=params.get("contract", [""])[0],
                        search=params.get("q", [""])[0].strip(),
                    )
                    self.json_response(read_page(filters, params.get("cursor", [None])[0]))
                except ValueError as exc:
                    self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                except (BotoCoreError, ClientError, RuntimeError):
                    LOG.exception("Could not read the jobs catalogue")
                    self.json_response(
                        {
                            "error": "Could not read jobs. Check your AWS login, region "
                            "and table permissions in the terminal."
                        },
                        HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                return

            assets = {
                "/": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/styles.css": ("styles.css", "text/css"),
            }
            if request.path not in assets:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            filename, content_type = assets[request.path]
            self.respond((FRONTEND / filename).read_bytes(), content_type)

        def json_response(self, data, status=HTTPStatus.OK):
            self.respond(json.dumps(data).encode(), "application/json", status)

        def respond(self, body, content_type, status=HTTPStatus.OK):
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region")
    parser.add_argument("--profile")
    parser.add_argument("--stack", default="JobPipeline")
    parser.add_argument("--jobs-table")
    parser.add_argument("--classifications-table")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo", action="store_true", help="Use clearly labelled example jobs without AWS"
    )
    args = parser.parse_args()
    if bool(args.jobs_table) != bool(args.classifications_table):
        parser.error("Supply both --jobs-table and --classifications-table")

    logging.basicConfig(level=logging.INFO)
    if args.demo:
        read_page = demo_page
    else:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        if not args.jobs_table:
            try:
                stack = session.client("cloudformation").describe_stacks(StackName=args.stack)[
                    "Stacks"
                ][0]
                outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
                args.jobs_table = outputs["JobsTable"]
                args.classifications_table = outputs["ClassificationsTable"]
            except (BotoCoreError, ClientError, KeyError) as exc:
                parser.error(f"Cannot resolve tables from {args.stack}: {exc}")

        def read_page(filters, cursor=None):
            # Each HTTP thread owns its boto3 session and resource objects.
            db = boto3.Session(profile_name=args.profile, region_name=args.region).resource(
                "dynamodb"
            )
            return JobCatalogue(db, args.jobs_table, args.classifications_table).page(
                filters, cursor=cursor
            )

    with ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler_class(read_page, demo=args.demo)
    ) as server:
        print(
            f"Jobs browser: http://127.0.0.1:{args.port}" + (" (demo data)" if args.demo else ""),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
