"""CDK entry point; synthesizes CloudFormation without needing AWS credentials."""

import aws_cdk as cdk

from infra.application_stack import ApplicationStack
from infra.stack import JobPipelineStack

app = cdk.App()
JobPipelineStack(app, "JobPipeline")
ApplicationStack(app, "Application")
app.synth()
