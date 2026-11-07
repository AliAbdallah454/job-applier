"""Three EC2 servers (a control plane and two workers) for a self-managed Kubernetes cluster
(kubeadm) running the API and web app, behind a Network Load Balancer.

The stack only creates the servers, their network and permissions, and the image repositories.
Each server installs containerd, kubeadm and the ECR plugin from cluster/node-setup.sh on first
boot; creating the cluster and installing the chart is done by hand, see docs/kubernetes.md.

It references the other stacks' resources by their generated names, not cross-stack exports, so
that it never forces a deployment order on them.
"""

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_elasticloadbalancingv2_targets as targets
from aws_cdk import aws_iam as iam
from constructs import Construct

# Ubuntu 24.04 LTS, amd64, gp3, published by Canonical (owner 099720109477) on 2026-10-23.
# Pinned because a new image replaces both servers, and the cluster with them. To move to a newer
# one, rebuild the cluster: see docs/kubernetes.md.
NODE_IMAGES = {"us-east-1": "ami-0d6dd9e8265d48906"}
NODE_SETUP = Path(__file__).resolve().parent / "cluster" / "node-setup.sh"
# The model the API calls for mail classification; keep in step with backend/mail/classifier.py.
MAIL_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# The load balancer's ports and the node ports of Traefik's Service they lead to; keep in step
# with deploy/cluster/traefik-values.yaml.
TRAEFIK_PORTS = {80: 30080, 443: 30443}


class ClusterStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        # Not t3.small: with 2 GB, the API server answered too slowly under load, and the
        # scheduler and controller-manager kept losing their leases and restarting, so pods on a
        # stopped worker were never moved. Changing the type stops and starts the server; it keeps
        # its disk, address and cluster.
        control_plane_type = self.node.try_get_context("controlPlaneType") or "t3.medium"
        worker_type = self.node.try_get_context("workerType") or "t3.medium"
        # "off" deletes the load balancer, which costs money even while the servers are
        # stopped, and keeps the Elastic IP, so the address is the same when it comes back.
        website = (self.node.try_get_context("website") or "on") != "off"

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

        # Only the load balancer is open to the internet, on 80 and 443. The nodes take the
        # website's traffic from it alone; shells and kubectl go through Systems Manager, so
        # there is no port 22 or 6443 either.
        balancer_group = ec2.SecurityGroup(
            self, "Balancer", vpc=vpc, description="Website load balancer", allow_all_outbound=True
        )
        nodes = ec2.SecurityGroup(
            self, "Nodes", vpc=vpc, description="Kubernetes nodes", allow_all_outbound=True
        )
        nodes.add_ingress_rule(nodes, ec2.Port.all_traffic(), "Traffic between the nodes")
        for port, node_port in TRAEFIK_PORTS.items():
            balancer_group.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(port), "Website")
            nodes.add_ingress_rule(balancer_group, ec2.Port.tcp(node_port), "From the balancer")

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

        workers = [worker, second_worker]

        # The website's fixed address, now on the load balancer. It keeps the id it had when it
        # was on the first worker, as a new id would mean a new address and a new DNS record.
        address = ec2.CfnEIP(self, "WorkerAddress")

        if website:
            self.add_balancer(vpc, balancer_group, address, workers)

        for name, value in {
            "ControlPlaneInstanceId": control_plane.instance_id,
            "ControlPlanePrivateIp": control_plane.instance_private_ip,
            "WorkerInstanceId": worker.instance_id,
            "Worker2InstanceId": second_worker.instance_id,
            "PublicIp": address.attr_public_ip,
        }.items():
            CfnOutput(self, name, value=value)

    def add_balancer(self, vpc, balancer_group, address, workers):
        """The website's way in: ports 80 and 443 on the Elastic IP, to Traefik on each worker."""
        # Plain TCP: HTTPS and its certificate stay with Traefik and cert-manager. Each worker
        # is a target on Traefik's node ports. Traefik's Service sends node port traffic only to
        # a Traefik pod on the same node, so a worker without one fails the health check and
        # gets no traffic.
        balancer = elbv2.NetworkLoadBalancer(
            self,
            "Website",
            vpc=vpc,
            internet_facing=True,
            security_groups=[balancer_group],
        )
        # The construct cannot attach an Elastic IP, so name the subnet and address directly.
        resource = balancer.node.default_child
        resource.add_property_deletion_override("Subnets")
        resource.add_property_override(
            "SubnetMappings",
            [
                {
                    "SubnetId": vpc.public_subnets[0].subnet_id,
                    "AllocationId": address.attr_allocation_id,
                }
            ],
        )
        for port, node_port in TRAEFIK_PORTS.items():
            balancer.add_listener(f"Port{port}", port=port).add_targets(
                f"Traefik{port}",
                port=node_port,
                targets=[targets.InstanceTarget(w, node_port) for w in workers],
                health_check=elbv2.HealthCheck(
                    protocol=elbv2.Protocol.TCP,
                    interval=Duration.seconds(10),
                    healthy_threshold_count=2,
                    unhealthy_threshold_count=2,
                ),
                deregistration_delay=Duration.seconds(30),
            )

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
