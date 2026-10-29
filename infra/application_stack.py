"""User profile and document storage, deployed independently of job collection."""

import re

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_applicationautoscaling as autoscaling
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from infra.worker_images import WorkerImages


class ApplicationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.profiles = dynamodb.Table(
            self,
            "Profiles",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.documents = s3.Bucket(
            self,
            "Documents",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        CfnOutput(self, "ProfilesTable", value=self.profiles.table_name)
        CfnOutput(self, "DocumentsBucketName", value=self.documents.bucket_name)

        self.generations = dynamodb.Table(
            self,
            "DocumentGenerations",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="generation_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.generations.add_global_secondary_index(
            index_name="ByUserJob",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="job_created", type=dynamodb.AttributeType.STRING),
        )

        raw_maximum = self.node.try_get_context("maxDocumentWorkers")
        raw_timeout = self.node.try_get_context("documentTimeoutSeconds")
        maximum = int(2 if raw_maximum is None else raw_maximum)
        timeout = int(600 if raw_timeout is None else raw_timeout)
        if maximum < 1 or not 60 <= timeout <= 3600:
            raise ValueError("maxDocumentWorkers must be positive; documentTimeoutSeconds: 60–3600")
        model = self.node.try_get_context("documentModelId") or "us.amazon.nova-pro-v1:0"
        if not re.fullmatch(
            r"(?:(?:us|eu|apac)\.)?(?:anthropic\.[a-z0-9.:-]+|amazon\.nova-pro-v1:0)", model
        ):
            raise ValueError(
                "documentModelId must be a Claude or Nova Pro model ID or us/eu/apac profile ID"
            )

        dead_letters = sqs.Queue(
            self,
            "DocumentGenerationDLQ",
            enforce_ssl=True,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(14),
        )
        queue = sqs.Queue(
            self,
            "DocumentGenerationQueue",
            enforce_ssl=True,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            visibility_timeout=Duration.seconds(600),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(queue=dead_letters, max_receive_count=5),
        )
        dead_letters.metric_approximate_number_of_messages_visible().create_alarm(
            self,
            "DocumentFailures",
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        vpc = ec2.Vpc(
            self,
            "DocumentVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC)
            ],
        )
        cluster = ecs.Cluster(self, "DocumentCluster", vpc=vpc)
        security_group = ec2.SecurityGroup(self, "DocumentWorkers", vpc=vpc)
        task = ecs.FargateTaskDefinition(self, "DocumentTask", cpu=512, memory_limit_mib=1024)
        log_group = logs.LogGroup(
            self,
            "DocumentWorkerLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        task.add_container(
            "Documents",
            image=WorkerImages(self, "WorkerImages").image(
                "documents", "containers/Dockerfile.documents"
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="documents", log_group=log_group),
            environment={
                "GENERATIONS_TABLE": self.generations.table_name,
                "DOCUMENTS_BUCKET": self.documents.bucket_name,
                "QUEUE_URL": queue.queue_url,
                "DOCUMENT_MODEL_ID": model,
                "DOCUMENT_TIMEOUT_SECONDS": str(timeout),
                "AWS_DEFAULT_REGION": self.region,
            },
            # XeLaTeX needs writable ephemeral /tmp; the container runs as UID 10001.
            user="10001:10001",
        )
        self.generations.grant(task.task_role, "dynamodb:GetItem", "dynamodb:UpdateItem")
        self.documents.grant_read_write(task.task_role, "users/*")
        queue.grant_consume_messages(task.task_role)

        if model.startswith(("us.", "eu.", "apac.")):
            profile_arn = self.format_arn(
                service="bedrock", resource="inference-profile", resource_name=model
            )
            task.add_to_task_role_policy(
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel", "bedrock:GetInferenceProfile"],
                    resources=[profile_arn],
                )
            )
            foundation = model.split(".", 1)[1]
            task.add_to_task_role_policy(
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[
                        self.format_arn(
                            service="bedrock",
                            region="*",
                            account="",
                            resource="foundation-model",
                            resource_name=foundation,
                        )
                    ],
                    conditions={"StringEquals": {"bedrock:InferenceProfileArn": profile_arn}},
                )
            )
        else:
            task.add_to_task_role_policy(
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[
                        self.format_arn(
                            service="bedrock",
                            account="",
                            resource="foundation-model",
                            resource_name=model,
                        )
                    ],
                )
            )

        service = ecs.FargateService(
            self,
            "DocumentService",
            cluster=cluster,
            task_definition=task,
            desired_count=0,
            assign_public_ip=True,
            security_groups=[security_group],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            min_healthy_percent=0,
            max_healthy_percent=100,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )
        capacity = service.auto_scale_task_count(min_capacity=0, max_capacity=maximum)
        backlog = cloudwatch.MathExpression(
            expression="visible + inflight",
            period=Duration.minutes(1),
            using_metrics={
                "visible": queue.metric_approximate_number_of_messages_visible(
                    statistic="Maximum", period=Duration.minutes(1)
                ),
                "inflight": queue.metric_approximate_number_of_messages_not_visible(
                    statistic="Maximum", period=Duration.minutes(1)
                ),
            },
        )
        capacity.scale_on_metric(
            "DocumentBacklog",
            metric=backlog,
            scaling_steps=[
                autoscaling.ScalingInterval(upper=0, change=-1),
                autoscaling.ScalingInterval(lower=1, change=1),
            ],
            adjustment_type=autoscaling.AdjustmentType.CHANGE_IN_CAPACITY,
            cooldown=Duration.minutes(2),
        )
        for name, value in {
            "DocumentGenerationsTable": self.generations.table_name,
            "DocumentQueueUrl": queue.queue_url,
            "DocumentDLQUrl": dead_letters.queue_url,
            "DocumentModelId": model,
            "DocumentClusterName": cluster.cluster_name,
            "DocumentWorkerService": service.service_name,
            "DocumentWorkerLogGroup": log_group.log_group_name,
        }.items():
            CfnOutput(self, name, value=value)
