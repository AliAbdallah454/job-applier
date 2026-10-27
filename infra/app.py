"""CDK entry point; synthesizes CloudFormation without needing AWS credentials."""

import aws_cdk as cdk

from infra.stack import JobPipelineStack

app = cdk.App()
JobPipelineStack(app, "JobPipeline")
app.synth()
