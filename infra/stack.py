"""Two-table job collection/classification pipeline on managed Fargate workers."""

import os
from pathlib import Path

from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_applicationautoscaling as autoscaling
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from backend.jobs.collection_config import (
    collection_environment,
    load_collection_config,
    s3_location,
)

ROOT = Path(__file__).resolve().parents[1]


def positive_context(scope, name, default):
    raw = scope.node.try_get_context(name)
    value = int(default if raw is None else raw)
    if value < 1:
        raise ValueError(f"{name} must be positive")

    return value


class JobPipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        max_scrapers = positive_context(self, "maxScrapers", 4)

        max_classifiers = positive_context(self, "maxClassifiers", 2)
        config_path = Path(
            self.node.try_get_context("collectionConfig") or "config/collections/default.yaml"
        )
        collection = load_collection_config(str(ROOT / config_path))
        model = self.node.try_get_context("jevModel") or "jev-latest"
        schedule = str(self.node.try_get_context("schedule") or "false").lower()

        if schedule not in {"true", "false"}:
            raise ValueError("schedule must be true or false")

        self.job_source_bucket = s3.Bucket(
            self,
            "JobSourceBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.jobs = dynamodb.Table(
            self,
            "Jobs",
            partition_key=dynamodb.Attribute(name="job_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.results = dynamodb.Table(
            self,
            "Classifications",
            partition_key=dynamodb.Attribute(name="job_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        for name, key in [("ByCategorySeniority", "category"), ("ByTechSeniority", "tech_status")]:
            self.results.add_global_secondary_index(
                index_name=name,
                partition_key=dynamodb.Attribute(name=key, type=dynamodb.AttributeType.STRING),
                sort_key=dynamodb.Attribute(
                    name="seniority_job", type=dynamodb.AttributeType.STRING
                ),
                projection_type=dynamodb.ProjectionType.ALL,
            )

        def work_queue(name):
            dlq = sqs.Queue(
                self,
                name + "DLQ",
                retention_period=Duration.days(14),
                encryption=sqs.QueueEncryption.SQS_MANAGED,
                enforce_ssl=True,
            )
            queue = sqs.Queue(
                self,
                name,
                visibility_timeout=Duration.minutes(10),
                retention_period=Duration.days(4),
                receive_message_wait_time=Duration.seconds(20),
                encryption=sqs.QueueEncryption.SQS_MANAGED,
                enforce_ssl=True,
                dead_letter_queue=sqs.DeadLetterQueue(queue=dlq, max_receive_count=5),
            )
            dlq.metric_approximate_number_of_messages_visible().create_alarm(
                self,
                name + "DeadLetters",
                threshold=1,
                evaluation_periods=1,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            return queue, dlq

        scrape_queue, scrape_dlq = work_queue("ScrapeQueue")
        classify_queue, classify_dlq = work_queue("ClassifyQueue")
        routing_dlq = sqs.Queue(
            self,
            "RoutingDLQ",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )
        router = lambda_.Function(
            self,
            "StreamRouter",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(ROOT / "infra/stream_router")),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={"QUEUE_URL": classify_queue.queue_url},
            log_group=logs.LogGroup(
                self,
                "RouterLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        classify_queue.grant_send_messages(router)
        router.add_event_source(
            sources.DynamoEventSource(
                self.jobs,
                starting_position=lambda_.StartingPosition.TRIM_HORIZON,
                batch_size=100,
                bisect_batch_on_error=True,
                report_batch_item_failures=True,
                retry_attempts=3,
                max_record_age=Duration.hours(23),
                on_failure=sources.SqsDlq(routing_dlq),
            )
        )
        router.metric_errors().create_alarm(
            self,
            "RouterErrors",
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        routing_dlq.metric_approximate_number_of_messages_visible().create_alarm(
            self,
            "RoutingDeadLetters",
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        raw_key = secretsmanager.CfnSecret(
            self,
            "TypeSafeKey",
            description="TypeSafe API key as plain string; populate with infra.manage set-key",
        )
        raw_key.apply_removal_policy(RemovalPolicy.RETAIN)
        key = secretsmanager.Secret.from_secret_complete_arn(
            self, "TypeSafeKeyReference", raw_key.ref
        )

        parameters = {}
        for name, value in {
            "JevModel": model,
        }.items():
            parameters[name] = ssm.StringParameter(self, name, string_value=value)

        # Outbound-only public tasks avoid a NAT gateway's standing cost.
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Workers", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
        )
        security_group = ec2.SecurityGroup(
            self,
            "WorkersSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
            description="No inbound access; workers initiate outbound HTTPS requests",
        )
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)
        log_group = logs.LogGroup(
            self,
            "WorkerLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        build_ca = os.environ.get("PIPELINE_BUILD_CA")
        build_secrets = None
        if build_ca:
            if not Path(build_ca).is_file():
                raise ValueError("PIPELINE_BUILD_CA must point to a trusted PEM CA bundle")
            build_secrets = {"build_ca": assets.DockerBuildSecret.from_src(build_ca)}

        def task_image(role):
            return ecs.ContainerImage.from_asset(
                str(ROOT),
                file=f"containers/Dockerfile.{role}",
                build_secrets=build_secrets,
                platform=assets.Platform.LINUX_AMD64,
                exclude=[".git", ".venv", "node_modules", "cdk.out", "data", ".ruff_cache"],
            )

        common_environment = {
            "JOBS_TABLE": self.jobs.table_name,
            "CLASSIFICATIONS_TABLE": self.results.table_name,
            "AWS_DEFAULT_REGION": self.region,
        }

        def task(name, image, command, environment, secret_environment=None):
            definition = ecs.FargateTaskDefinition(
                self,
                name,
                cpu=256,
                memory_limit_mib=512,
                runtime_platform=ecs.RuntimePlatform(
                    cpu_architecture=ecs.CpuArchitecture.X86_64,
                    operating_system_family=ecs.OperatingSystemFamily.LINUX,
                ),
            )
            definition.add_container(
                "Worker",
                container_name="worker",
                image=image,
                command=command,
                environment={**common_environment, **environment},
                secrets=secret_environment,
                logging=ecs.LogDrivers.aws_logs(log_group=log_group, stream_prefix=name),
                readonly_root_filesystem=True,
                stop_timeout=Duration.seconds(120),
                linux_parameters=ecs.LinuxParameters(
                    self, name + "Linux", init_process_enabled=True
                ),
            )
            return definition

        scrape_task = task(
            "ScraperTask",
            task_image("scrape"),
            ["-m", "backend.jobs.workers.scraper"],
            {"QUEUE_URL": scrape_queue.queue_url},
        )
        self.jobs.grant(scrape_task.task_role, "dynamodb:DescribeTable", "dynamodb:UpdateItem")
        scrape_queue.grant_consume_messages(scrape_task.task_role)
        classify_task = task(
            "ClassifierTask",
            task_image("classify"),
            ["-m", "backend.jobs.workers.classifier"],
            {"QUEUE_URL": classify_queue.queue_url},
            {
                "TYPESAFE_API_KEY": ecs.Secret.from_secrets_manager(key),
                "JEV_MODEL": ecs.Secret.from_ssm_parameter(parameters["JevModel"]),
            },
        )
        self.jobs.grant(
            classify_task.task_role,
            "dynamodb:GetItem",
            "dynamodb:DescribeTable",
            "dynamodb:ConditionCheckItem",
        )
        self.results.grant(
            classify_task.task_role,
            "dynamodb:GetItem",
            "dynamodb:DescribeTable",
            "dynamodb:UpdateItem",
            "dynamodb:PutItem",
        )
        classify_queue.grant_consume_messages(classify_task.task_role)

        for name, definition, queue, maximum in [
            ("Scrapers", scrape_task, scrape_queue, max_scrapers),
            ("Classifiers", classify_task, classify_queue, max_classifiers),
        ]:
            service = ecs.FargateService(
                self,
                name,
                cluster=cluster,
                task_definition=definition,
                desired_count=0,
                assign_public_ip=True,
                security_groups=[security_group],
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                min_healthy_percent=0,
                max_healthy_percent=100,
                circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
                platform_version=ecs.FargatePlatformVersion.VERSION1_4,
            )
            capacity = service.auto_scale_task_count(min_capacity=0, max_capacity=maximum)
            # Include in-flight messages so idle workers do not scale in during active work.
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
                "QueueDepth",
                metric=backlog,
                scaling_steps=[
                    autoscaling.ScalingInterval(upper=0, change=-1),
                    autoscaling.ScalingInterval(lower=1, change=1),
                ],
                adjustment_type=autoscaling.AdjustmentType.CHANGE_IN_CAPACITY,
                cooldown=Duration.minutes(2),
            )
            CfnOutput(self, name + "Service", value=service.service_name)

        seed_task = task(
            "SeedTask",
            task_image("seed"),
            ["-m", "backend.jobs.workers.seed"],
            {
                "QUEUE_URL": scrape_queue.queue_url,
                "CLASSIFY_QUEUE_URL": classify_queue.queue_url,
                **collection_environment(collection, self.job_source_bucket.bucket_name),
            },
        )
        source_bucket, source_key = s3_location(collection.boards_s3_uri)
        if source_bucket == "YOUR_JOB_SOURCE_BUCKET":
            self.job_source_bucket.grant_read(seed_task.task_role, source_key)
        else:
            s3.Bucket.from_bucket_name(self, "BoardSourceBucket", source_bucket).grant_read(
                seed_task.task_role, source_key
            )
        scrape_queue.grant_send_messages(seed_task.task_role)
        classify_queue.grant_send_messages(seed_task.task_role)
        self.jobs.grant(seed_task.task_role, "dynamodb:Scan")
        schedule_dlq = sqs.Queue(
            self,
            "ScheduleDLQ",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )
        schedule_dlq.metric_approximate_number_of_messages_visible().create_alarm(
            self,
            "ScheduleDeadLetters",
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        rule = events.Rule(
            self,
            "DailyCollection",
            schedule=events.Schedule.rate(Duration.days(1)),
            enabled=schedule == "true",
        )
        rule.add_target(
            targets.EcsTask(
                cluster=cluster,
                task_definition=seed_task,
                task_count=1,
                launch_type=ecs.LaunchType.FARGATE,
                assign_public_ip=True,
                subnet_selection=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                security_groups=[security_group],
                platform_version=ecs.FargatePlatformVersion.VERSION1_4,
                dead_letter_queue=schedule_dlq,
                retry_attempts=2,
            )
        )
        outputs = {
            "JobSourceBucketName": self.job_source_bucket.bucket_name,
            "BoardSourceUri": collection_environment(
                collection, self.job_source_bucket.bucket_name
            )["BOARDS_S3_URI"],
            "JobsTable": self.jobs.table_name,
            "ClassificationsTable": self.results.table_name,
            "TypesafeSecretArn": key.secret_arn,
            "ClusterName": cluster.cluster_name,
            "SeedTaskDefinition": seed_task.task_definition_arn,
            "SeedExecutionRoleArn": seed_task.execution_role.role_arn,
            "SeedTaskRoleArn": seed_task.task_role.role_arn,
            "PublicSubnets": Fn.join(",", [subnet.subnet_id for subnet in vpc.public_subnets]),
            "SecurityGroupId": security_group.security_group_id,
            "ScrapeQueueUrl": scrape_queue.queue_url,
            "ClassifyQueueUrl": classify_queue.queue_url,
            "ScrapeDLQUrl": scrape_dlq.queue_url,
            "ClassifyDLQUrl": classify_dlq.queue_url,
            "RoutingDLQUrl": routing_dlq.queue_url,
            "ScheduleDLQUrl": schedule_dlq.queue_url,
            "WorkerLogGroup": log_group.log_group_name,
            "JevModelParameter": parameters["JevModel"].parameter_name,
            "ScheduleRule": rule.rule_name,
        }
        for name, value in outputs.items():
            CfnOutput(self, name, value=value)
