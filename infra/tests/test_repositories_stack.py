"""Every image's repository, in the stack CI deploys before pushing."""

import unittest

from aws_cdk import App
from aws_cdk.assertions import Template

from infra.repositories_stack import RepositoriesStack


class RepositoriesStackTests(unittest.TestCase):
    def test_one_kept_immutable_repository_per_image(self):
        template = Template.from_stack(RepositoriesStack(App(), "TestRepositories"))
        repositories = template.find_resources("AWS::ECR::Repository")
        self.assertEqual(
            sorted(item["Properties"]["RepositoryName"] for item in repositories.values()),
            [
                f"job-applier/{name}"
                for name in ("api", "classifier", "documents", "scraper", "seed", "web")
            ],
        )
        for item in repositories.values():
            self.assertEqual(item["DeletionPolicy"], "Retain")
            self.assertEqual(item["Properties"]["ImageTagMutability"], "IMMUTABLE")
            self.assertIn("LifecyclePolicy", item["Properties"])
