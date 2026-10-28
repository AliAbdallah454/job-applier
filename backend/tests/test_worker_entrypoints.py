"""Worker entrypoints start independently without importing the other workload."""

import subprocess
import sys
import unittest
from pathlib import Path


class WorkerEntrypointTests(unittest.TestCase):
    def test_workers_have_independent_imports_and_help(self):
        for module, blocked in [
            (
                "backend.jobs.workers.scraper",
                "backend.jobs.classification,backend.jobs.storage.classifications,typesafe_sdk",
            ),
            (
                "backend.jobs.workers.classifier",
                "backend.jobs.collector,backend.jobs.job_fetchers,backend.jobs.workers.scraper",
            ),
            (
                "backend.jobs.workers.seed",
                "backend.jobs.classification,backend.jobs.storage,typesafe_sdk",
            ),
        ]:
            with self.subTest(module=module):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        """
import importlib.abc
import runpy
import sys

module, blocked = sys.argv[1], sys.argv[2].split(',')

class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise ImportError('Unexpected workload dependency: ' + fullname)

sys.meta_path.insert(0, BlockImports())
sys.argv = [module, '--help']
runpy.run_module(module, run_name='__main__')
""",
                        module,
                        blocked,
                    ],
                    cwd=Path(__file__).resolve().parents[2],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--help", result.stdout)
