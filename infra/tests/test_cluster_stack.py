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

    def test_only_the_website_is_open_to_the_internet(self):
        groups = self.template.find_resources("AWS::EC2::SecurityGroup")
        (group,) = groups.values()
        open_ports = sorted(
            rule["FromPort"]
            for rule in group["Properties"].get("SecurityGroupIngress", [])
            if rule.get("CidrIp") == "0.0.0.0/0"
        )
        self.assertEqual(open_ports, [80, 443])
        self.assertNotIn("22", json.dumps(group))

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
