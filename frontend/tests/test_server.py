"""Test the HTTP interface in memory without opening a listening socket."""

import io
import json
import unittest
from unittest.mock import Mock, patch

from frontend.server import demo_page, handler_class


class Socket:
    def __init__(self, path):
        self.request = io.BytesIO(f"GET {path} HTTP/1.0\r\n\r\n".encode())
        self.response = bytearray()

    def makefile(self, *args, **kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


class ServerTests(unittest.TestCase):
    def request(self, path, reader=demo_page):
        connection = Socket(path)
        handler = handler_class(reader, demo=True)
        with patch.object(handler, "log_message"):
            handler(connection, ("127.0.0.1", 0), Mock())
        headers, body = bytes(connection.response).split(b"\r\n\r\n", 1)
        return headers.decode(), body

    def test_options_and_default_demo_filters(self):
        headers, body = self.request("/api/options")
        self.assertIn("200 OK", headers)
        self.assertTrue(json.loads(body)["demo"])
        self.assertEqual(json.loads(body)["seniorities"], ["entry", "junior", "mid", "senior"])
        _, body = self.request("/api/jobs")
        jobs = json.loads(body)["jobs"]
        self.assertEqual(len(jobs), 3)
        self.assertTrue(all(job["seniority"] in {"entry", "junior"} for job in jobs))

    def test_filters_reach_reader_and_invalid_values_return_400(self):
        _, body = self.request("/api/jobs?tech=tech&seniority=entry&contract=internship")
        self.assertEqual(len(json.loads(body)["jobs"]), 1)
        headers, _ = self.request("/api/jobs?seniority=lead")
        self.assertIn("400 Bad Request", headers)

    def test_static_assets_are_served_without_directory_traversal(self):
        for path in ("/", "/app.js", "/styles.css"):
            headers, body = self.request(path)
            self.assertIn("200 OK", headers)
            self.assertTrue(body)
            self.assertIn("Content-Security-Policy", headers)
        headers, _ = self.request("/../.env")
        self.assertIn("404 Not Found", headers)

    def test_aws_failures_have_a_useful_error_without_internal_details(self):
        reader = Mock(side_effect=RuntimeError("private internal details"))
        with patch("frontend.server.LOG.exception"):
            headers, body = self.request("/api/jobs", reader)
        self.assertIn("503 Service Unavailable", headers)
        self.assertIn("AWS login", json.loads(body)["error"])
        self.assertNotIn("private", body.decode())
