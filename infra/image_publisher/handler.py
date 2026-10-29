"""CloudFormation waits for CodeBuild to copy each image into its worker repository."""

import hashlib
import os

import boto3


def on_event(event, context):
    physical_id = event.get("PhysicalResourceId") or event["LogicalResourceId"]
    if event["RequestType"] == "Delete":
        return {"PhysicalResourceId": physical_id}

    properties = event["ResourceProperties"]
    variables = {
        "SOURCE_IMAGE": properties["SourceImage"],
        "DESTINATION_IMAGE": properties["DestinationImage"],
        "DESTINATION_REPOSITORY": properties["RepositoryName"],
        "IMAGE_TAG": properties["ImageTag"],
        "REGISTRY": properties["Registry"],
    }
    response = boto3.client("codebuild").start_build(
        projectName=os.environ["PROJECT_NAME"],
        idempotencyToken=hashlib.sha256(event["RequestId"].encode()).hexdigest(),
        environmentVariablesOverride=[
            {"name": name, "value": value, "type": "PLAINTEXT"} for name, value in variables.items()
        ],
    )
    return {
        "PhysicalResourceId": physical_id,
        "Data": {"BuildId": response["build"]["id"], "ImageTag": properties["ImageTag"]},
    }


def is_complete(event, context):
    if event["RequestType"] == "Delete":
        return {"IsComplete": True}

    build_id = event["Data"]["BuildId"]
    response = boto3.client("codebuild").batch_get_builds(ids=[build_id])
    builds = response.get("builds", [])
    if not builds:
        raise RuntimeError(f"Image publication build not found: {build_id}")
    status = builds[0]["buildStatus"]
    if status == "IN_PROGRESS":
        return {"IsComplete": False}
    if status != "SUCCEEDED":
        raise RuntimeError(f"Image publication {status}: inspect CodeBuild build {build_id}")
    return {"IsComplete": True, "Data": {"ImageTag": event["ResourceProperties"]["ImageTag"]}}
