"""CDK entry point; synthesizes CloudFormation without needing AWS credentials."""

import aws_cdk as cdk

from infra.application_stack import ApplicationStack
from infra.cluster_stack import ClusterStack
from infra.stack import JobPipelineStack

app = cdk.App()
JobPipelineStack(app, "JobPipeline")
ApplicationStack(app, "Application")
ClusterStack(app, "Cluster")  # Deployed by ci-cd.yml after the other two: see docs/kubernetes.md.
app.synth()
