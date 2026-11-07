"""Three EC2 servers (a control plane and two workers) for a self-managed Kubernetes cluster
(kubeadm) running the API and web app.

The stack only creates the servers, their network and permissions, and the image repositories.
Each server installs containerd, kubeadm and the ECR plugin from cluster/node-setup.sh on first
boot; creating the cluster and installing the chart is done by hand, see docs/kubernetes.md.

It references the other stacks' resources by their generated names, not cross-stack exports, so
that it never forces a deployment order on them.
"""

from pathlib import Path

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct

# Ubuntu 24.04 LTS, amd64, gp3, published by Canonical (owner 099720109477) on 2026-10-23.
# Pinned because a new image replaces both servers, and the cluster with them. To move to a newer
# one, rebuild the cluster: see docs/kubernetes.md.
NODE_IMAGES = {"us-east-1": "ami-0d6dd9e8265d48906"}
NODE_SETUP = Path(__file__).resolve().parent / "cluster" / "node-setup.sh"
# The model the API calls for mail classification; keep in step with backend/mail/classifier.py.
MAIL_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class ClusterStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        control_plane_type = self.node.try_get_context("controlPlaneType") or "t3.small"
        worker_type = self.node.try_get_context("workerType") or "t3.medium"

        vpc = ec2.Vpc(
            self,
            "ClusterVpc",
            ip_addresses=ec2.IpAddresses.cidr("10.20.0.0/16"),
            max_azs=1,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
        )

        # No port 22 and no port 6443 from the internet: shells and kubectl go through Systems
        # Manager, so only the website is open.
        nodes = ec2.SecurityGroup(
            self, "Nodes", vpc=vpc, description="Kubernetes nodes", allow_all_outbound=True
        )
        nodes.add_ingress_rule(nodes, ec2.Port.all_traffic(), "Traffic between the nodes")
        for port in (80, 443):
            nodes.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(port), "Website")

        repositories = {}
        for name in ("api", "web"):
            repository = ecr.Repository(
                self,
                name.title() + "Repository",
                repository_name=f"{self.stack_name.lower()}/{name}",
                image_tag_mutability=ecr.TagMutability.IMMUTABLE,
                image_scan_on_push=True,
                removal_policy=RemovalPolicy.RETAIN,
                lifecycle_rules=[ecr.LifecycleRule(max_image_count=10)],
            )
            repositories[name] = repository
            CfnOutput(self, name.title() + "RepositoryUri", value=repository.repository_uri)

        image = ec2.MachineImage.generic_linux(NODE_IMAGES)
        setup = ec2.UserData.custom(NODE_SETUP.read_text())

        def server(name, instance_type, role, hop_limit):
            return ec2.Instance(
                self,
                name,
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                associate_public_ip_address=True,  # No NAT gateway, so this is the way out.
                instance_type=ec2.InstanceType(instance_type),
                machine_image=image,
                user_data=setup,
                role=role,
                security_group=nodes,
                http_endpoint=True,
                http_tokens=ec2.HttpTokens.REQUIRED,  # IMDSv2 only.
                http_put_response_hop_limit=hop_limit,
                # Calico routes pod traffic between nodes without wrapping it, so packets carry
                # pod addresses, which EC2 drops unless this check is off.
                source_dest_check=False,
                block_devices=[
                    ec2.BlockDevice(
                        device_name="/dev/sda1",
                        volume=ec2.BlockDeviceVolume.ebs(
                            30, volume_type=ec2.EbsDeviceVolumeType.GP3, encrypted=True
                        ),
                    )
                ],
            )

        managed = iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
        control_role = iam.Role(
            self,
            "ControlPlaneRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[managed],
        )
        worker_role = iam.Role(
            self,
            "WorkerRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[managed],
        )
        self.grant_api(worker_role, repositories.values())

        # Only the workers run pods that need AWS. A hop limit of 2 lets them reach the
        # metadata service through the node, which also means every pod on them acts as its role.
        control_plane = server("ControlPlane", control_plane_type, control_role, 1)
        worker = server("Worker", worker_type, worker_role, 2)
        second_worker = server("Worker2", worker_type, worker_role, 2)

        # A fixed address for DNS; a server's own public IP changes when it stops. Only the first
        # worker has one, so Traefik must run there: see docs/kubernetes.md.
        address = ec2.CfnEIP(self, "WorkerAddress", instance_id=worker.instance_id)

        for name, value in {
            "ControlPlaneInstanceId": control_plane.instance_id,
            "ControlPlanePrivateIp": control_plane.instance_private_ip,
            "WorkerInstanceId": worker.instance_id,
            "WorkerPublicIp": address.attr_public_ip,
            "Worker2InstanceId": second_worker.instance_id,
        }.items():
            CfnOutput(self, name, value=value)

    def grant_api(self, role, repositories):
        """What the API does in AWS, and nothing more, as every pod on the workers gets it."""

        def arn(service, resource, account=None, region=None):
            return self.format_arn(
                service=service, resource=resource, account=account, region=region
            )

        for repository in repositories:
            repository.grant_pull(role)
        role.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        statements = [
            # Table names and stack outputs, read at start-up.
            (
                ["cloudformation:DescribeStacks"],
                [
                    arn("cloudformation", f"stack/{stack}/*")
                    for stack in ("Application", "JobPipeline")
                ],
            ),
            # The job catalogue is read only.
            (
                ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan"],
                [arn("dynamodb", "table/JobPipeline-*")],
            ),
            # Profiles, generations and mail.
            (
                [
                    "dynamodb:GetItem",
                    "dynamodb:BatchGetItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                ],
                [arn("dynamodb", "table/Application-*")],
            ),
            (["s3:ListBucket"], ["arn:aws:s3:::application-documents*"]),
            (
                [
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:PutObject",
                    "s3:DeleteObject",
                ],
                ["arn:aws:s3:::application-documents*/users/*"],
            ),
            (["sqs:SendMessage"], [arn("sqs", "Application-DocumentGenerationQueue*")]),
        ]
        for actions, resources in statements:
            role.add_to_policy(iam.PolicyStatement(actions=actions, resources=resources))

        # Mail classification: the one model the API calls itself, through its US profile.
        profile = arn("bedrock", f"inference-profile/{MAIL_MODEL}")
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:GetInferenceProfile"],
                resources=[profile],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    arn(
                        "bedrock",
                        f"foundation-model/{MAIL_MODEL.split('.', 1)[1]}",
                        account="",
                        region="*",
                    )
                ],
                conditions={"StringEquals": {"bedrock:InferenceProfileArn": profile}},
            )
        )
