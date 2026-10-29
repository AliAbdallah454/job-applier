"""Stack-owned worker repositories, populated before ECS uses a new image tag."""

import os
import re
from pathlib import Path

from aws_cdk import CfnOutput, CustomResource, Duration, Fn, RemovalPolicy, Stack, Token
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecr_assets as assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import custom_resources as resources
from constructs import Construct

ROOT = Path(__file__).resolve().parents[1]

COPY_COMMANDS = """set -euo pipefail
if aws ecr describe-images --repository-name "$DESTINATION_REPOSITORY" \\
    --image-ids "imageTag=$IMAGE_TAG" >/dev/null 2>&1; then
    echo "Image tag already published"
else
    aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | \\
        docker login --username AWS --password-stdin "$REGISTRY"
    docker pull "$SOURCE_IMAGE"
    docker tag "$SOURCE_IMAGE" "$DESTINATION_IMAGE"
    docker push "$DESTINATION_IMAGE"
fi
"""


class WorkerImages(Construct):
    def __init__(self, scope, construct_id):
        super().__init__(scope, construct_id)
        stack = Stack.of(self)
        if Token.is_unresolved(stack.stack_name):
            raise ValueError("Worker image repositories require a concrete stack name")
        self.namespace = stack.stack_name.lower()
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.namespace):
            raise ValueError("Stack name must contain letters, digits or hyphens")

        build_logs = logs.LogGroup(
            self,
            "CopyLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.project = codebuild.Project(
            self,
            "CopyProject",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.SMALL,
                privileged=True,
            ),
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "env": {"shell": "bash"},
                    "phases": {"build": {"commands": [COPY_COMMANDS]}},
                }
            ),
            timeout=Duration.minutes(30),
            queued_timeout=Duration.minutes(10),
            logging=codebuild.LoggingOptions(
                cloud_watch=codebuild.CloudWatchLoggingOptions(log_group=build_logs)
            ),
        )
        CfnOutput(stack, "ImageCopyProjectName", value=self.project.project_name)
        CfnOutput(stack, "ImageCopyLogGroup", value=build_logs.log_group_name)
        handlers = {}
        for name, handler in [("Start", "on_event"), ("Check", "is_complete")]:
            # Two fixed handlers share one provider for every image in this stack.
            handlers[name] = lambda_.Function(
                self,
                name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler=f"handler.{handler}",
                code=lambda_.Code.from_asset(
                    str(ROOT / "infra" / "image_publisher"), exclude=["__pycache__", "*.pyc"]
                ),
                environment={"PROJECT_NAME": self.project.project_name},
                timeout=Duration.seconds(30),
                log_group=logs.LogGroup(
                    self,
                    name + "Logs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
            )
        for name, action in [
            ("Start", "codebuild:StartBuild"),
            ("Check", "codebuild:BatchGetBuilds"),
        ]:
            iam.Grant.add_to_principal(
                grantee=handlers[name], actions=[action], resource_arns=[self.project.project_arn]
            )
        self.provider = resources.Provider(
            self,
            "Provider",
            on_event_handler=handlers["Start"],
            is_complete_handler=handlers["Check"],
            query_interval=Duration.seconds(20),
            total_timeout=Duration.minutes(45),
        )

    def image(self, name, dockerfile):
        # Names come from the fixed worker roles, keeping construct IDs stable across builds.
        repository = ecr.Repository(
            self,
            name + "Repository",
            repository_name=f"{self.namespace}/{name}",
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            image_scan_on_push=True,
            encryption=ecr.RepositoryEncryption.AES_256,
            removal_policy=RemovalPolicy.RETAIN,
        )
        build_ca = os.environ.get("PIPELINE_BUILD_CA")
        secrets = None
        if build_ca:
            if not Path(build_ca).is_file():
                raise ValueError("PIPELINE_BUILD_CA must point to a trusted PEM CA bundle")
            secrets = {"build_ca": assets.DockerBuildSecret.from_src(build_ca)}
        asset = assets.DockerImageAsset(
            self,
            name + "Asset",
            directory=str(ROOT),
            file=dockerfile,
            build_secrets=secrets,
            platform=assets.Platform.LINUX_AMD64,
            exclude=[".git", ".venv", "node_modules", "cdk.out", "data", ".ruff_cache"],
        )
        grants = [
            asset.repository.grant_pull(self.project),
            repository.grant_pull_push(self.project),
            repository.grant(self.project, "ecr:DescribeImages"),
        ]
        publication = CustomResource(
            self,
            name + "Publication",
            service_token=self.provider.service_token,
            resource_type="Custom::WorkerImage",
            properties={
                "SourceImage": asset.image_uri,
                "DestinationImage": repository.repository_uri_for_tag(asset.image_tag),
                "RepositoryName": repository.repository_name,
                "ImageTag": asset.image_tag,
                "Registry": Fn.select(0, Fn.split("/", repository.repository_uri)),
            },
        )
        for grant in grants:
            grant.apply_before(publication)
        # The task definition must not be registered until its image exists in ECR.
        # GetAtt also makes ECS services and scheduled task users wait through that dependency.
        image = ecs.ContainerImage.from_ecr_repository(
            repository, tag=publication.get_att_string("ImageTag")
        )
        CfnOutput(Stack.of(self), name.title() + "RepositoryUri", value=repository.repository_uri)
        return image
