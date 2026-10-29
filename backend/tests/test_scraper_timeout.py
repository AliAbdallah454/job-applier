"""A stuck scraper exits without acknowledging work; successful work cancels its deadline."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.jobs.workers.scraper import process_scrape


class ScraperTimeoutTests(unittest.TestCase):
    def test_deadline_covers_fetching_and_persistence_and_does_not_acknowledge(self):
        for mode in ("fetch", "save", "success"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                acknowledgment = Path(directory) / "acknowledged"
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        """
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

from backend.utils.runtime import handle_message
from backend.jobs.workers.scraper import process_scrape

mode, acknowledgment = sys.argv[1:]
body = {'board': {'source': 'greenhouse', 'board': 'example'}}
sqs = Mock()
sqs.delete_message.side_effect = lambda **kwargs: Path(acknowledgment).write_text('done')
store = Mock()
hang = lambda *args, **kwargs: threading.Event().wait()
if mode == 'save':
    store.save_jobs.side_effect = hang

with patch('backend.jobs.workers.scraper.create_fetcher') as factory:
    fetcher = factory.return_value.__enter__.return_value
    fetcher.fetch.return_value = []
    if mode == 'fetch':
        fetcher.fetch.side_effect = hang
    handle_message(
        sqs, 'queue', {'Body': json.dumps(body), 'ReceiptHandle': 'receipt'},
        lambda body: process_scrape(body, store, timeout_seconds=0.1),
    )
time.sleep(0.2)
""",
                        mode,
                        str(acknowledgment),
                    ],
                    cwd=Path(__file__).resolve().parents[2],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if mode == "success":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(acknowledgment.exists())
                else:
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("Scrape deadline exceeded", result.stderr)
                    self.assertFalse(acknowledgment.exists())

    def test_non_positive_deadlines_are_rejected_before_fetching(self):
        for seconds in (0, -1):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                process_scrape(
                    {"board": {"source": "greenhouse", "board": "example"}},
                    Mock(),
                    timeout_seconds=seconds,
                )
