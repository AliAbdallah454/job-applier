"""The ECR repositories of every image, in a stack of their own.

CI deploys this stack first and pushes the images before deploying the others, so a task
definition or a pod never points at a tag that is not in ECR yet. The other stacks find a
repository by its name (`repository_name`), not through a cross-stack export, so that they never
force a deployment order on this one.
"""

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from constructs import Construct

# One repository per image; keep in step with the push-images matrix in ci-cd.yml.
IMAGES = ("seed", "scraper", "classifier", "documents", "api", "web")


def repository_name(image):
    return f"job-applier/{image}"


def find_repository(scope, image):
    """A repository of this stack, for another stack to pull from."""
    return ecr.Repository.from_repository_name(
        scope, image.title() + "Repository", repository_name(image)
    )


class RepositoriesStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        for image in IMAGES:
            repository = ecr.Repository(
                self,
                image.title(),
                repository_name=repository_name(image),
                # Tags are content hashes, so a tag never changes what it points at.
                image_tag_mutability=ecr.TagMutability.IMMUTABLE,
                image_scan_on_push=True,
                # Kept when the stack goes, with the images in it.
                removal_policy=RemovalPolicy.RETAIN,
                lifecycle_rules=[ecr.LifecycleRule(max_image_count=10)],
            )
            CfnOutput(self, image.title() + "RepositoryUri", value=repository.repository_uri)
