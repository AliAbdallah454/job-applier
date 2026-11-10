"""The Kubernetes servers: what is open to the internet and what the API's role may do."""

import json
import unittest

from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infra.cluster_stack import CONTROL_PLANE_SETUP, WORKER_SETUP, ClusterStack


def statements(template, role):
    policies = template.find_resources("AWS::IAM::Policy")
    return [
        statement
        for policy in policies.values()
        if any(ref.get("Ref", "").startswith(role) for ref in policy["Properties"]["Roles"])
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
    ]


class ClusterStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = Template.from_stack(ClusterStack(App(), "TestCluster"))

    def test_only_the_load_balancer_is_open_to_the_internet(self):
        groups = self.template.find_resources("AWS::EC2::SecurityGroup")
        open_ports = {
            group["Properties"]["GroupDescription"]: sorted(
                rule["FromPort"]
                for rule in group["Properties"].get("SecurityGroupIngress", [])
                if rule.get("CidrIp") == "0.0.0.0/0"
            )
            for group in groups.values()
        }
        self.assertEqual(open_ports, {"Website load balancer": [80, 443], "Kubernetes nodes": []})
        self.assertNotIn("22", json.dumps(groups))

    def test_the_nodes_take_only_traefiks_node_ports_from_the_load_balancer(self):
        rules = self.template.find_resources("AWS::EC2::SecurityGroupIngress")
        from_balancer = sorted(
            rule["Properties"]["FromPort"]
            for rule in rules.values()
            if rule["Properties"]["SourceSecurityGroupId"]["Fn::GetAtt"][0].startswith("Balancer")
        )
        self.assertEqual(from_balancer, [30080, 30443])

    def test_the_load_balancer_has_the_fixed_address_and_both_workers(self):
        (address,) = self.template.find_resources("AWS::EC2::EIP").values()
        self.assertNotIn("InstanceId", address.get("Properties", {}))
        self.template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            {
                "Type": "network",
                "Scheme": "internet-facing",
                "SubnetMappings": [
                    Match.object_like(
                        {"AllocationId": {"Fn::GetAtt": ["WorkerAddress", "AllocationId"]}}
                    )
                ],
            },
        )
        listeners = self.template.find_resources("AWS::ElasticLoadBalancingV2::Listener")
        self.assertEqual(
            sorted(
                (item["Properties"]["Port"], item["Properties"]["Protocol"])
                for item in listeners.values()
            ),
            [(80, "TCP"), (443, "TCP")],
        )
        groups = self.template.find_resources("AWS::ElasticLoadBalancingV2::TargetGroup")
        for group in groups.values():
            properties = group["Properties"]
            self.assertIn(properties["Port"], (30080, 30443))
            self.assertEqual(properties["HealthCheckProtocol"], "TCP")
            self.assertNotIn("Targets", properties)
        # The worker group registers its servers in both target groups itself.
        (workers,) = self.template.find_resources("AWS::AutoScaling::AutoScalingGroup").values()
        self.assertEqual(
            sorted(ref["Ref"] for ref in workers["Properties"]["TargetGroupARNs"]), sorted(groups)
        )

    def worker_template(self):
        (template,) = self.template.find_resources("AWS::EC2::LaunchTemplate").values()
        return template["Properties"]["LaunchTemplateData"]

    def test_servers_require_imdsv2_and_only_the_workers_let_pods_reach_it(self):
        (control_plane,) = self.template.find_resources("AWS::EC2::Instance").values()
        for metadata, hop_limit in (
            (control_plane["Properties"]["MetadataOptions"], 1),
            (self.worker_template()["MetadataOptions"], 2),
        ):
            self.assertEqual(metadata["HttpTokens"], "required")
            self.assertEqual(metadata["HttpPutResponseHopLimit"], hop_limit)

    def test_servers_have_encrypted_disks_and_their_setup_scripts(self):
        disk = [
            Match.object_like({"Ebs": Match.object_like({"Encrypted": True, "VolumeSize": 30})})
        ]
        self.template.resource_count_is("AWS::EC2::Instance", 1)
        self.template.has_resource_properties(
            "AWS::EC2::Instance",
            {"SourceDestCheck": False, "BlockDeviceMappings": disk, "UserData": Match.any_value()},
        )
        worker = self.worker_template()
        (worker_disk,) = worker["BlockDeviceMappings"]
        self.assertEqual(
            worker_disk["Ebs"], {"Encrypted": True, "VolumeSize": 30, "VolumeType": "gp3"}
        )
        self.assertIn("kubeadm join", json.dumps(worker["UserData"]))
        self.template.resource_count_is("AWS::EC2::EIP", 1)
        self.template.resource_count_is("AWS::EC2::NatGateway", 0)

    def test_no_deploy_sets_how_many_workers_run(self):
        self.template.has_resource_properties(
            "AWS::AutoScaling::AutoScalingGroup",
            {"MinSize": "0", "MaxSize": "4", "DesiredCapacity": Match.absent()},
        )

    def test_a_worker_reads_only_the_join_command_and_changes_only_workers(self):
        found = statements(self.template, "WorkerRole")
        (read,) = [s for s in found if "ssm:" in json.dumps(s["Action"])]
        self.assertEqual(read["Action"], "ssm:GetParameter")
        self.assertIn(":parameter/job-applier/cluster/join-command", json.dumps(read["Resource"]))
        (modify,) = [s for s in found if s["Action"] == "ec2:ModifyInstanceAttribute"]
        self.assertEqual(
            modify["Condition"], {"StringEquals": {"ec2:ResourceTag/job-applier:node": "worker"}}
        )
        (workers,) = self.template.find_resources("AWS::AutoScaling::AutoScalingGroup").values()
        self.assertIn(
            {"Key": "job-applier:node", "Value": "worker", "PropagateAtLaunch": True},
            workers["Properties"]["Tags"],
        )

    def test_the_control_plane_gets_no_app_permissions(self):
        actions = json.dumps(statements(self.template, "ControlPlaneRole"))
        self.assertNotIn("dynamodb", actions)
        self.assertNotIn("s3:", actions)

    def test_the_workers_use_the_worker_role(self):
        profiles = self.template.find_resources("AWS::IAM::InstanceProfile")
        profile = self.worker_template()["IamInstanceProfile"]["Arn"]["Fn::GetAtt"][0]
        self.assertTrue(profiles[profile]["Properties"]["Roles"][0]["Ref"].startswith("WorkerRole"))

    def test_the_stack_has_no_repositories_and_the_workers_pull_from_the_shared_ones(self):
        self.template.resource_count_is("AWS::ECR::Repository", 0)
        found = statements(self.template, "WorkerRole")
        pulls = json.dumps([s for s in found if "ecr:BatchGetImage" in s["Action"]])
        for name in ("api", "web"):
            self.assertIn(f"repository/job-applier/{name}", pulls)

    def test_the_worker_can_do_what_the_api_does_and_no_more(self):
        found = statements(self.template, "WorkerRole")
        text = json.dumps(found)
        for action in (
            "dynamodb:PutItem",
            "s3:PutObject",
            "sqs:SendMessage",
            "bedrock:InvokeModel",
        ):
            self.assertIn(action, text)
        self.assertNotIn('"*"', json.dumps([s["Action"] for s in found]))
        writes = [s for s in found if "dynamodb:PutItem" in s["Action"]]
        self.assertTrue(all("JobPipeline" not in json.dumps(s["Resource"]) for s in writes))
        self.assertNotIn("sqs:ReceiveMessage", text)


class WebsiteOffTests(unittest.TestCase):
    def test_off_removes_the_load_balancer_but_keeps_the_address(self):
        app = App(context={"website": "off"})
        template = Template.from_stack(ClusterStack(app, "TestCluster"))
        for kind in ("LoadBalancer", "Listener", "TargetGroup"):
            template.resource_count_is(f"AWS::ElasticLoadBalancingV2::{kind}", 0)
        template.resource_count_is("AWS::EC2::EIP", 1)
        template.resource_count_is("AWS::EC2::Instance", 1)
        template.resource_count_is("AWS::AutoScaling::AutoScalingGroup", 1)


class WorkersTests(unittest.TestCase):
    def test_the_worker_installs_what_the_control_plane_does(self):
        def setup_steps(path):
            text = path.read_text()
            return text[text.index("KUBERNETES_MINOR=") : text.index("systemctl enable kubelet")]

        self.assertEqual(setup_steps(WORKER_SETUP), setup_steps(CONTROL_PLANE_SETUP))
