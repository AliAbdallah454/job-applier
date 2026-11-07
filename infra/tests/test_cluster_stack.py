"""The Kubernetes servers: what is open to the internet and what the API's role may do."""

import json
import unittest

from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infra.cluster_stack import ClusterStack


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
            self.assertEqual(
                sorted(target["Id"]["Ref"][: len("Worker2")] for target in properties["Targets"]),
                ["Worker1", "Worker2"],
            )

    def test_servers_require_imdsv2_and_only_the_workers_let_pods_reach_it(self):
        servers = self.template.find_resources("AWS::EC2::Instance")
        options = {name: item["Properties"]["MetadataOptions"] for name, item in servers.items()}
        self.assertEqual(len(options), 3)
        for name, metadata in options.items():
            self.assertEqual(metadata["HttpTokens"], "required")
            self.assertEqual(
                metadata["HttpPutResponseHopLimit"], 2 if name.startswith("Worker") else 1
            )

    def test_servers_have_encrypted_disks_and_the_setup_script(self):
        self.template.resource_count_is("AWS::EC2::Instance", 3)
        self.template.all_resources_properties(
            "AWS::EC2::Instance",
            {
                "SourceDestCheck": False,
                "BlockDeviceMappings": [
                    Match.object_like(
                        {"Ebs": Match.object_like({"Encrypted": True, "VolumeSize": 30})}
                    )
                ],
                "UserData": Match.any_value(),
            },
        )
        self.template.resource_count_is("AWS::EC2::EIP", 1)
        self.template.resource_count_is("AWS::EC2::NatGateway", 0)

    def test_the_control_plane_gets_no_app_permissions(self):
        actions = json.dumps(statements(self.template, "ControlPlaneRole"))
        self.assertNotIn("dynamodb", actions)
        self.assertNotIn("s3:", actions)

    def test_both_workers_use_the_worker_role(self):
        profiles = self.template.find_resources("AWS::IAM::InstanceProfile")
        roles = {
            name: profile["Properties"]["Roles"][0]["Ref"] for name, profile in profiles.items()
        }
        worker_profiles = [name for name, role in roles.items() if role.startswith("WorkerRole")]
        self.assertEqual(len(worker_profiles), 2)

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
        template.resource_count_is("AWS::EC2::Instance", 3)
