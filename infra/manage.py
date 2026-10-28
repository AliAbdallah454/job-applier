"""Small deployment helper: securely supply the API key, launch a run, or backfill results."""

import argparse
import getpass
import json

import boto3

from backend.jobs.collection_config import collection_environment, load_collection_config
from backend.jobs.job_fetchers import FETCHERS


def stack_outputs(session, stack_name):
    response = session.client("cloudformation").describe_stacks(StackName=stack_name)

    return {item["OutputKey"]: item["OutputValue"] for item in response["Stacks"][0]["Outputs"]}


def launch(
    session,
    outputs,
    *,
    mode,
    config=None,
    max_boards=None,
    max_jobs_per_board=None,
    platforms=None,
):
    command = ["-m", "backend.jobs.workers.seed", "--mode", mode]

    if max_boards is not None:
        command += ["--max-boards", str(max_boards)]

    if max_jobs_per_board is not None:
        command += ["--max-jobs-per-board", str(max_jobs_per_board)]

    if platforms:
        command += ["--platforms", *platforms]

    container_override = {"name": "worker", "command": command}
    if config is not None:
        container_override["environment"] = [
            {"name": name, "value": value}
            for name, value in collection_environment(
                config, outputs["JobSourceBucketName"]
            ).items()
        ]

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
        overrides={"containerOverrides": [container_override]},
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
    parser.add_argument("--config", help="Local collection YAML path; collect only")
    parser.add_argument("--max-boards", type=int, help="Maximum company boards; default all")
    parser.add_argument(
        "--max-jobs-per-board", type=int, help="Maximum jobs per board; default all"
    )
    parser.add_argument("--platforms", nargs="+", choices=list(FETCHERS))

    args = parser.parse_args()

    if args.config and args.action != "collect":
        parser.error("--config is available only for collect")

    if args.max_boards is not None and args.max_boards < 1:
        parser.error("--max-boards must be positive")

    if args.max_jobs_per_board is not None and args.max_jobs_per_board < 1:
        parser.error("--max-jobs-per-board must be positive")

    config = None
    if args.config:
        config = load_collection_config(args.config)

    session = boto3.Session(
        profile_name=args.profile, region_name=args.region or (config.region if config else None)
    )

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
            config=config,
            max_boards=args.max_boards,
            max_jobs_per_board=args.max_jobs_per_board,
            platforms=args.platforms,
        )

        print("Feeder task launched; this is not a completion confirmation:")
        print("\n".join(tasks))


if __name__ == "__main__":
    main()
