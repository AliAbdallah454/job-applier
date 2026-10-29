"""Each image's tag covers its own Dockerfile and code folders."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def tag(dockerfile):
    return subprocess.run(
        ["bash", "scripts/image_tag.sh", dockerfile],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class ImageTagTests(unittest.TestCase):
    def test_each_image_has_a_stable_distinct_tag(self):
        names = ["seed", "scrape", "classify", "documents"]
        tags = [tag(f"containers/Dockerfile.{name}") for name in names]
        self.assertEqual(len(set(tags)), 4)
        self.assertTrue(all(len(value) == 40 for value in tags))
        self.assertEqual(tags[0], tag("containers/Dockerfile.seed"))

    def test_unlisted_dockerfile_is_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError) as error:
            tag("containers/Dockerfile.unknown")
        self.assertIn("No code folders", error.exception.stderr)
