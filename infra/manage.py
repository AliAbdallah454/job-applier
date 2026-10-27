"""Small deployment helper: securely supply the API key, launch a run, or backfill results."""

import argparse
import getpass
import json

import boto3

from backend.jobs.job_fetchers import FETCHERS


def stack_outputs(session, stack_name):
    response = session.client("cloudformation").describe_stacks(StackName=stack_name)
    return {item["OutputKey"]: item["OutputValue"] for item in response["Stacks"][0]["Outputs"]}


def launch(session, outputs, *, mode, max_boards=None, limit=None, platforms=None):
    command = ["-m", "backend.pipeline.seed", "--mode", mode]
    if max_boards is not None:
        command += ["--max-boards", str(max_boards)]
    if limit is not None:
        command += ["--limit", str(limit)]
    if platforms:
        command += ["--platforms", *platforms]
    response = session.client("ecs").run_task(
        cluster=outputs["ClusterName"],
        taskDefinition=outputs["SeedTaskDefinition"],
        launchType="FARGATE",
        platformVersion="1.4.0",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": outputs["PublicSubnets"].split(","),
                "securityGroups": [outputs["SecurityGroupId"]],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={"containerOverrides": [{"name": "worker", "command": command}]},
    )
    if response.get("failures") or not response.get("tasks"):
        raise RuntimeError(
            "ECS could not launch the feeder; check ECS task failures and IAM access"
        )
    return [task["taskArn"] for task in response["tasks"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["set-key", "collect", "backfill", "outputs"])
    parser.add_argument("--stack", default="JobPipeline")
    parser.add_argument("--region")
    parser.add_argument("--profile")
    parser.add_argument("--max-boards", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--platforms", nargs="+", choices=list(FETCHERS))
    args = parser.parse_args()
    if args.max_boards is not None and args.max_boards < 1:
        parser.error("--max-boards must be positive")
    if args.limit is not None and not 1 <= args.limit <= 100:
        parser.error("--limit must be 1–100")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    outputs = stack_outputs(session, args.stack)
    if args.action == "set-key":
        key = getpass.getpass("TypeSafe API key (hidden): ").strip()
        if not key:
            parser.error("API key cannot be empty")
        session.client("secretsmanager").put_secret_value(
            SecretId=outputs["TypesafeSecretArn"], SecretString=key
        )
        print("API key saved to Secrets Manager; never written to a local file.")
    elif args.action == "outputs":
        print(json.dumps(outputs, indent=2))
    else:
        # Verify a populated secret exists without retrieving or printing its value.
        secret = session.client("secretsmanager").describe_secret(
            SecretId=outputs["TypesafeSecretArn"]
        )
        if not any(
            "AWSCURRENT" in stages for stages in secret.get("VersionIdsToStages", {}).values()
        ):
            parser.error("Populate the API key first using set-key")
        tasks = launch(
            session,
            outputs,
            mode="scrape" if args.action == "collect" else "classify",
            max_boards=args.max_boards,
            limit=args.limit,
            platforms=args.platforms,
        )
        print("Feeder task launched; this is not a completion confirmation:")
        print("\n".join(tasks))


if __name__ == "__main__":
    main()
