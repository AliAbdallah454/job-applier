"""CDK entry point; synthesizes CloudFormation without needing AWS credentials."""

import aws_cdk as cdk

from infra.application_stack import ApplicationStack
from infra.cluster_stack import ClusterStack
from infra.repositories_stack import RepositoriesStack
from infra.stack import JobPipelineStack

app = cdk.App()

RepositoriesStack(app, "Repositories")
JobPipelineStack(app, "JobPipeline")
ApplicationStack(app, "Application")
ClusterStack(app, "Cluster")

app.synth()
