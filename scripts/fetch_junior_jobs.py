"""Export classified tech jobs at entry or junior level that are not internships."""

import argparse
import csv
import sys
import time

import boto3
from boto3.dynamodb.conditions import Attr, Key

from backend.jobs.classification.models import classification_input, input_hash

SENIORITIES = ("entry", "junior")
COLUMNS = (
    "job_id",
    "title",
    "company",
    "location",
    "seniority",
    "contract_type",
    "category",
    "published_at",
    "posting_url",
)


def stack_outputs(session, stack_name):
    stack = session.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"][0]
    return {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}


def matching_classifications(table):
    # contract_type is null when unknown; only an explicit internship is excluded.
    not_internship = Attr("contract_type").ne("internship")
    for seniority in SENIORITIES:
        arguments = {
            "IndexName": "ByTechSeniority",
            "KeyConditionExpression": Key("tech_status").eq("tech")
            & Key("seniority_job").begins_with(seniority + "#"),
            "FilterExpression": not_internship,
        }
        while True:
            page = table.query(**arguments)
            yield from page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                break
            arguments["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def postings(dynamodb, jobs_table, job_ids):
    found = {}
    for start in range(0, len(job_ids), 100):
        pending = {
            jobs_table: {"Keys": [{"job_id": job_id} for job_id in job_ids[start : start + 100]]}
        }
        for attempt in range(5):
            response = dynamodb.batch_get_item(RequestItems=pending)
            for item in response.get("Responses", {}).get(jobs_table, []):
                found[item["job_id"]] = item
            pending = response.get("UnprocessedKeys", {})
            if not pending:
                break
            time.sleep(0.1 * 2**attempt)
        else:
            raise RuntimeError("DynamoDB could not read all postings; please retry")
    return found


def junior_jobs(dynamodb, jobs_table, classifications_table):
    results = list(matching_classifications(dynamodb.Table(classifications_table)))
    jobs = postings(dynamodb, jobs_table, list(dict.fromkeys(r["job_id"] for r in results)))
    rows = []
    for result in results:
        job = jobs.get(result["job_id"])
        if not job:
            continue
        posting = job if "title" in job else job.get("job", {})
        try:
            # A changed posting keeps its old classification until it is reclassified.
            if result.get("input_hash") != input_hash(classification_input(posting)):
                continue
        except ValueError:
            continue
        rows.append(
            {
                **{field: posting.get(field) for field in COLUMNS},
                "job_id": result["job_id"],
                "seniority": result.get("seniority"),
                "contract_type": result.get("contract_type") or "unknown",
                "category": result.get("category"),
            }
        )
    return sorted(rows, key=lambda row: str(row.get("published_at") or ""), reverse=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="JobPipeline")
    parser.add_argument("--region")
    parser.add_argument("--profile")
    parser.add_argument("--output", help="CSV file to write (default: standard output)")
    args = parser.parse_args(argv)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    outputs = stack_outputs(session, args.stack)
    rows = junior_jobs(
        session.resource("dynamodb"), outputs["JobsTable"], outputs["ClassificationsTable"]
    )

    out = open(args.output, "w", newline="", encoding="utf-8") if args.output else sys.stdout
    try:
        writer = csv.DictWriter(out, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if args.output:
            out.close()
    print(f"{len(rows)} jobs", file=sys.stderr)


if __name__ == "__main__":
    main()
