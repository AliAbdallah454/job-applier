"""EC2 servers for a self-managed Kubernetes cluster (kubeadm) running the API and web app, behind
a Network Load Balancer: one control plane and an Auto Scaling group of workers.

The stack only creates the servers, their network and permissions; the API and web images are in
the Repositories stack. Each server installs containerd, kubeadm and the ECR plugin on first boot,
from cluster/control-plane.sh or cluster/worker.sh. Creating the cluster and installing the chart
is done by hand; the workers then join by themselves, with the join command that
scripts/publish_join_command.sh puts in Parameter Store. See docs/kubernetes.md.

It references the other stacks' resources by their generated names, not cross-stack exports, so
that it never forces a deployment order on them.
"""

from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack, Tags
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from constructs import Construct

from infra.repositories_stack import find_repository

# Ubuntu 24.04 LTS, amd64, gp3, published by Canonical (owner 099720109477) on 2026-10-23.
# Pinned because a new image replaces both servers, and the cluster with them. To move to a newer
# one, rebuild the cluster: see docs/kubernetes.md.
NODE_IMAGES = {"us-east-1": "ami-0d6dd9e8265d48906"}
CONTROL_PLANE_SETUP = Path(__file__).resolve().parent / "cluster" / "control-plane.sh"
WORKER_SETUP = Path(__file__).resolve().parent / "cluster" / "worker.sh"
# Where the workers find the kubeadm join command; keep in step with cluster/worker.sh and
# scripts/publish_join_command.sh.
JOIN_PARAMETER = "/job-applier/cluster/join-command"
# The most workers the group can hold. How many run is set on the group by hand, never by a
# deploy: see docs/kubernetes.md.
MAX_WORKERS = 4
# The tag on every worker, which lets a worker turn off its own source/dest check.
WORKER_TAG = ("job-applier:node", "worker")
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

        image = ec2.MachineImage.generic_linux(NODE_IMAGES)
        disks = [
            ec2.BlockDevice(
                device_name="/dev/sda1",
                volume=ec2.BlockDeviceVolume.ebs(
                    30, volume_type=ec2.EbsDeviceVolumeType.GP3, encrypted=True
                ),
            )
        ]

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
        self.grant_api(worker_role, [find_repository(self, name) for name in ("api", "web")])
        self.grant_join(worker_role)
        self.grant_publish_join(control_role)

        control_plane = ec2.Instance(
            self,
            "ControlPlane",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            associate_public_ip_address=True,  # No NAT gateway, so this is the way out.
            instance_type=ec2.InstanceType(control_plane_type),
            machine_image=image,
            user_data=ec2.UserData.custom(CONTROL_PLANE_SETUP.read_text()),
            role=control_role,
            security_group=nodes,
            http_endpoint=True,
            http_tokens=ec2.HttpTokens.REQUIRED,  # IMDSv2 only.
            http_put_response_hop_limit=1,
            # Calico routes pod traffic between nodes without wrapping it, so packets carry
            # pod addresses, which EC2 drops unless this check is off.
            source_dest_check=False,
            block_devices=disks,
        )

        # Only the workers run pods that need AWS. A hop limit of 2 lets them reach the
        # metadata service through the node, which also means every pod on them acts as its role.
        # A launch template cannot turn the source/dest check off: worker.sh does it at boot.
        template = ec2.LaunchTemplate(
            self,
            "WorkerTemplate",
            instance_type=ec2.InstanceType(worker_type),
            machine_image=image,
            user_data=ec2.UserData.custom(WORKER_SETUP.read_text()),
            role=worker_role,
            security_group=nodes,
            associate_public_ip_address=True,
            require_imdsv2=True,
            http_put_response_hop_limit=2,
            block_devices=disks,
        )
        # No desired size: how many workers run is set on the group itself, with
        # `aws autoscaling set-desired-capacity` or the console, and a deploy never changes it
        # (a new stack starts at 0). Never stop a worker: the group sees a stopped server as
        # unhealthy and replaces it. A new launch template only reaches workers started after it.
        group = autoscaling.AutoScalingGroup(
            self,
            "Workers",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            launch_template=template,
            min_capacity=0,
            max_capacity=MAX_WORKERS,
        )
        Tags.of(group).add(*WORKER_TAG, apply_to_launched_instances=True)

        # The website's fixed address, now on the load balancer. In CloudFormation it keeps the
        # id it had when it was on the first worker: a new id would mean a new address, and so a
        # new DNS record and certificate.
        address = ec2.CfnEIP(self, "WebsiteAddress")
        address.override_logical_id("WorkerAddress")

        if website:
            self.add_balancer(vpc, balancer_group, address, group)

        for name, value in {
            "ControlPlaneInstanceId": control_plane.instance_id,
            "ControlPlanePrivateIp": control_plane.instance_private_ip,
            "WorkerGroupName": group.auto_scaling_group_name,
            "PublicIp": address.attr_public_ip,
        }.items():
            CfnOutput(self, name, value=value)

    def add_balancer(self, vpc, balancer_group, address, group):
        """The website's way in: ports 80 and 443 on the Elastic IP, to Traefik on each worker."""
        # Plain TCP: HTTPS and its certificate stay with Traefik and cert-manager. Each worker
        # is a target on Traefik's node ports, registered by the group as it starts and stops
        # them. Traefik's Service sends node port traffic only to a Traefik pod on the same node,
        # so a worker without one fails the health check and gets no traffic.
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
                targets=[group],
                health_check=elbv2.HealthCheck(
                    protocol=elbv2.Protocol.TCP,
                    interval=Duration.seconds(10),
                    healthy_threshold_count=2,
                    unhealthy_threshold_count=2,
                ),
                deregistration_delay=Duration.seconds(30),
            )

    def join_parameter_arn(self):
        """The Parameter Store entry holding the kubeadm join command."""
        return self.format_arn(
            service="ssm", resource="parameter", resource_name=JOIN_PARAMETER[1:]
        )

    def grant_publish_join(self, role):
        """The control plane may write the join command the workers read, and nothing else: it
        is where kubeadm mints join tokens (scripts/publish_join_command.sh, or a scheduled
        publish on the server itself)."""
        role.add_to_policy(
            iam.PolicyStatement(actions=["ssm:PutParameter"], resources=[self.join_parameter_arn()])
        )

    def grant_join(self, role):
        """What a worker needs at boot: the join command, and to turn off its source/dest check."""
        role.add_to_policy(
            iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[self.join_parameter_arn()])
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ec2:ModifyInstanceAttribute"],
                resources=[self.format_arn(service="ec2", resource="instance", resource_name="*")],
                conditions={"StringEquals": {f"ec2:ResourceTag/{WORKER_TAG[0]}": WORKER_TAG[1]}},
            )
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
