"""Login through a fake Auth0: tokens are real and signed here, and nothing leaves the machine."""

import io
import os
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import boto3
import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from moto import mock_aws

from backend.api.app import create_app
from backend.api.auth0 import Auth0
from backend.api.demo import create_tables
from backend.api.deps import Context, Settings

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OWNER = "auth0|owner"
CV = (Path(__file__).resolve().parents[2] / "data/cv-examples/jake_cv.tex").read_bytes()


class FakeAuth0(Auth0):
    """The real checks, with this file's key in place of the one Auth0 publishes."""

    answer = None

    def signing_key(self, token):
        return KEY.public_key()

    def exchange(self, code):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def token(key=KEY, **changes):
    now = int(time.time())
    claims = {
        "iss": "https://example.auth0.com/",
        "aud": "client",
        "sub": OWNER,
        "iat": now,
        "exp": now + 3600,
        **changes,
    }
    return jwt.encode(claims, key, algorithm="RS256")


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = FakeAuth0("example.auth0.com", "client", "secret", OWNER)
        self.app = create_app(Context(self.settings(self.auth), region="us-east-1"))
        self.client = TestClient(self.app, base_url="http://localhost", follow_redirects=False)

    def settings(self, auth):
        return Settings(
            user_id="ali",
            jobs_table="jobs",
            classifications_table="classifications",
            profiles_table="profiles",
            generations_table="generations",
            documents_bucket="documents",
            document_queue_url="queue",
            auth=auth,
        )

    def options(self, session=None):
        return TestClient(
            self.app,
            base_url="http://localhost",
            cookies={"session": session} if session else None,
        ).get("/api/options")

    def start_login(self):
        response = self.client.get("/api/auth/login")
        query = parse_qs(urlsplit(response.headers["location"]).query)
        return response, query["state"][0], query["nonce"][0]

    def test_every_route_needs_a_session_except_the_login_ones(self):
        paths = self.app.openapi()["paths"]
        self.assertGreater(len(paths), 20)
        self.assertFalse([path for path in paths if path.startswith("/api/auth")])
        for path, operations in paths.items():
            for method in operations:
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path.format_map(Blank()))
                    self.assertEqual(response.status_code, 401)

    def test_a_good_session_is_the_configured_user(self):
        response = self.options(token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], "ali")
        self.assertTrue(response.json()["login_enabled"])

    def test_bad_sessions_are_refused(self):
        forged = jwt.encode({"sub": OWNER, "exp": 9999999999}, "x" * 32, algorithm="HS256")
        for name, session in {
            "expired": token(exp=int(time.time()) - 60),
            "signed by another key": token(OTHER_KEY),
            "for another app": token(aud="another-app"),
            "from another issuer": token(iss="https://evil.example/"),
            "symmetric algorithm": forged,
            "no expiry": jwt.encode({"sub": OWNER}, KEY, algorithm="RS256"),
            "not a token": "nonsense",
        }.items():
            with self.subTest(name):
                self.assertEqual(self.options(session).status_code, 401)

    def test_a_created_account_is_its_own_user(self):
        response = self.options(token(sub="auth0|friend"))
        self.assertEqual(response.json()["user_id"], "auth0|friend")

    def test_accounts_nobody_created_are_refused(self):
        for account in ("google-oauth2|123", "github|123", "auth0", "ali"):
            with self.subTest(account):
                self.assertEqual(self.options(token(sub=account)).status_code, 403)

    def test_without_an_owner_nobody_gets_in(self):
        self.auth.owner_id = None
        self.assertEqual(self.options(token()).status_code, 403)
        self.assertEqual(self.options(token(sub="auth0|friend")).status_code, 403)

    def test_login_goes_to_auth0_and_remembers_its_state(self):
        response, state, nonce = self.start_login()
        url = urlsplit(response.headers["location"])
        query = parse_qs(url.query)
        self.assertEqual(url.netloc, "example.auth0.com")
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["client"])
        self.assertEqual(query["scope"], ["openid"])
        self.assertEqual(query["redirect_uri"], ["http://localhost:8000/api/auth/callback"])
        self.assertNotIn("secret", response.headers["location"])
        self.assertEqual(self.client.cookies["login_state"], state)
        self.assertEqual(self.client.cookies["login_nonce"], nonce)
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    def test_callback_starts_a_session_and_returns_to_the_app(self):
        _, state, nonce = self.start_login()
        self.auth.answer = token(nonce=nonce)
        response = self.client.get("/api/auth/callback", params={"code": "c", "state": state})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "http://localhost:5173")
        cookie = next(
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith("session=")
        )
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertEqual(self.client.get("/api/options").status_code, 200)
        self.assertNotIn("login_state", self.client.cookies)

    def test_cookies_are_secure_when_the_app_uses_https(self):
        self.assertTrue(
            Auth0("d", "c", "s", app_url="https://app.example", callback_url="x").secure_cookies
        )

    def test_a_callback_this_browser_did_not_start_is_refused(self):
        self.start_login()
        self.auth.answer = token()
        for params in ({"code": "c", "state": "wrong"}, {"code": "c"}, {"state": "x"}, {}):
            with self.subTest(params):
                response = self.client.get("/api/auth/callback", params=params)
                self.assertEqual(response.status_code, 400)
        fresh = TestClient(self.app, base_url="http://localhost", follow_redirects=False)
        response = fresh.get("/api/auth/callback", params={"code": "c", "state": "any"})
        self.assertEqual(response.status_code, 400)

    def test_a_token_from_another_login_is_refused(self):
        _, state, _ = self.start_login()
        self.auth.answer = token(nonce="from-another-login")
        response = self.client.get("/api/auth/callback", params={"code": "c", "state": state})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("session", self.client.cookies)

    def test_a_google_login_gets_no_session(self):
        _, state, nonce = self.start_login()
        self.auth.answer = token(nonce=nonce, sub="google-oauth2|123")
        response = self.client.get("/api/auth/callback", params={"code": "c", "state": state})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("session", self.client.cookies)
        self.assertNotIn("google-oauth2|123", response.text)

    def test_a_created_account_gets_a_session(self):
        _, state, nonce = self.start_login()
        self.auth.answer = token(nonce=nonce, sub="auth0|friend")
        response = self.client.get("/api/auth/callback", params={"code": "c", "state": state})
        self.assertEqual(response.status_code, 302)
        self.assertIn("session", self.client.cookies)

    def test_the_first_login_says_which_id_to_set_as_owner(self):
        self.auth.owner_id = None
        _, state, nonce = self.start_login()
        self.auth.answer = token(nonce=nonce)
        response = self.client.get("/api/auth/callback", params={"code": "c", "state": state})
        self.assertEqual(response.status_code, 403)
        self.assertIn(f"AUTH0_OWNER_ID={OWNER}", response.json()["detail"])

    def test_auth0_errors_are_explained_and_start_no_session(self):
        _, state, _ = self.start_login()
        self.auth.answer = httpx.ConnectError("down")
        response = self.client.get("/api/auth/callback", params={"code": "c", "state": state})
        self.assertEqual(response.status_code, 503)
        response = self.client.get(
            "/api/auth/callback", params={"error": "x", "error_description": "user is blocked"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("user is blocked", response.json()["detail"])

    def test_logout_ends_the_session_at_both_ends(self):
        self.client.cookies.set("session", token())
        response = self.client.get("/api/auth/logout")
        url = urlsplit(response.headers["location"])
        self.assertEqual(url.path, "/v2/logout")
        self.assertEqual(parse_qs(url.query)["returnTo"], ["http://localhost:5173"])
        self.assertIn('session=""', response.headers["set-cookie"])

    def test_without_auth0_there_is_no_login(self):
        client = TestClient(
            create_app(Context(self.settings(None), region="us-east-1")),
            base_url="http://localhost",
        )
        self.assertEqual(client.get("/api/auth/login").status_code, 404)
        self.assertEqual(client.get("/api/auth/logout").status_code, 404)
        self.assertFalse(client.get("/api/options").json()["login_enabled"])

    def test_settings_are_all_or_nothing(self):
        self.assertIsNone(Auth0.from_values({"MAIL_ADDRESS": "a"}))
        with self.assertRaises(ValueError):
            Auth0.from_values({"AUTH0_DOMAIN": "d", "AUTH0_CLIENT_ID": "c"})
        auth = Auth0.from_values(
            {
                "AUTH0_DOMAIN": "d",
                "AUTH0_CLIENT_ID": "c",
                "AUTH0_CLIENT_SECRET": "s",
                "AUTH0_OWNER_ID": "o",
                "AUTH0_APP_URL": "https://app.example",
            }
        )
        self.assertEqual((auth.owner_id, auth.app_url), ("o", "https://app.example"))


@mock_aws
class TwoUsersTests(unittest.TestCase):
    """The owner and a created account, through the real routes on Moto."""

    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        session = boto3.Session(region_name="us-east-1")
        self.db = session.resource("dynamodb")
        create_tables(self.db)
        self.s3 = session.client("s3")
        self.s3.create_bucket(Bucket="documents")
        queue = session.client("sqs").create_queue(QueueName="documents")["QueueUrl"]
        self.open_mailbox = Mock()
        self.settings = Settings(
            user_id="ali",
            jobs_table="jobs",
            classifications_table="classifications",
            profiles_table="profiles",
            generations_table="generations",
            documents_bucket="documents",
            document_queue_url=queue,
            mail_table="mail",
            open_mailbox=self.open_mailbox,
            compile_document=Mock(),
            auth=FakeAuth0("example.auth0.com", "client", "secret", OWNER),
        )
        self.context = Context(self.settings, region="us-east-1")
        self.app = create_app(self.context)
        self.db.Table("jobs").put_item(
            Item={"job_id": "job-1", "title": "Engineer", "description": "Build", "company": "Co"}
        )
        self.owner = self.client_of(OWNER)
        self.friend = self.client_of("auth0|friend")

    def client_of(self, account):
        return TestClient(
            self.app, base_url="http://localhost", cookies={"session": token(sub=account)}
        )

    def tailor(self, client, user_id):
        client.put("/api/profile", json={"user_id": user_id, "full_name": "Someone"})
        files = {"file": ("cv.tex", io.BytesIO(CV))}
        cv_id = client.post("/api/cvs", files=files).json()["cv_id"]
        body = {"job_id": "job-1", "cv_id": cv_id}
        return cv_id, client.post("/api/generations", json=body).json()["generation_id"]

    def test_a_new_account_can_use_the_app_with_its_own_data(self):
        self.assertEqual(self.friend.get("/api/profile").status_code, 404)
        cv_id, generation_id = self.tailor(self.friend, "auth0|friend")
        self.assertEqual(self.friend.get(f"/api/generations/{generation_id}").status_code, 200)
        self.assertEqual([cv["cv_id"] for cv in self.friend.get("/api/cvs").json()], [cv_id])
        keys = self.s3.list_objects_v2(Bucket="documents")["Contents"]
        self.assertTrue(all(item["Key"].startswith("users/auth0%7Cfriend/") for item in keys))

    def test_the_owner_keeps_the_data_stored_as_the_configured_user(self):
        self.tailor(self.owner, "ali")
        self.assertEqual(self.owner.get("/api/profile").json()["user_id"], "ali")
        keys = self.s3.list_objects_v2(Bucket="documents")["Contents"]
        self.assertTrue(all(item["Key"].startswith("users/ali/") for item in keys))

    def test_one_user_cannot_reach_anothers_data(self):
        cv_id, generation_id = self.tailor(self.owner, "ali")
        prompt = {"name": "Mine", "text": "Private"}
        prompt_id = self.owner.post("/api/system-prompts", json=prompt).json()["prompt_id"]
        generation = f"/api/generations/{generation_id}"
        self.friend.put("/api/profile", json={"user_id": "auth0|friend", "full_name": "F"})

        self.assertEqual(self.friend.get("/api/cvs").json(), [])
        self.assertEqual(self.friend.get("/api/generations").json(), [])
        self.assertEqual(self.friend.get("/api/system-prompts").json(), [])
        self.assertEqual(self.friend.get("/api/profile").json()["full_name"], "F")
        for method, path in (
            ("get", generation),
            ("get", f"{generation}/files/cv_tex"),
            ("get", f"{generation}/versions/cv/1/files/cv_tex"),
            ("get", f"{generation}/source-line?page=1&x=1&y=1"),
            ("post", f"{generation}/retry"),
            ("post", f"{generation}/regenerate"),
            ("delete", f"/api/cvs/{cv_id}"),
            ("delete", f"/api/system-prompts/{prompt_id}"),
        ):
            with self.subTest(path):
                self.assertEqual(getattr(self.friend, method)(path).status_code, 404)
        edit = {"document": "cv", "latex": "x", "revision": 0}
        self.assertEqual(self.friend.post(f"{generation}/edit", json=edit).status_code, 404)
        body = {"job_id": "job-1", "cv_id": cv_id}
        self.assertEqual(self.friend.post("/api/generations", json=body).status_code, 404)
        body = {"job_id": "job-1", "cv_id": cv_id, "system_prompt_id": prompt_id}
        self.assertEqual(self.friend.post("/api/generations", json=body).status_code, 404)
        stolen = {"user_id": "ali", "full_name": "Taken"}
        self.assertEqual(self.friend.put("/api/profile", json=stolen).status_code, 403)

        self.assertEqual(len(self.owner.get("/api/cvs").json()), 1)
        self.assertEqual(len(self.owner.get("/api/system-prompts").json()), 1)
        self.assertEqual(self.owner.get("/api/profile").json()["full_name"], "Someone")

    def test_only_the_owner_can_reach_the_mailbox(self):
        with patch("backend.api.mail.run_sync") as run_sync:
            self.assertEqual(self.friend.post("/api/mail/sync").status_code, 403)
            run_sync.assert_not_called()
            self.assertEqual(self.owner.post("/api/mail/sync").status_code, 202)
        self.assertEqual(self.friend.get("/api/mail/status").json()["problem"], "owner_only")
        self.assertIsNone(self.owner.get("/api/mail/status").json()["problem"])

    def test_one_user_cannot_see_anothers_applications(self):
        store = self.context.mail
        application = store.new_application("ali", "Secret Co", "Engineer")
        store.save_interest("ali", {"job_id": "job-1", "title": "Engineer"}, "2026-01-01T00:00:00Z")
        self.assertEqual(self.friend.get("/api/applications").json(), [])
        self.assertEqual(self.friend.get("/api/applications/interests").json(), [])
        self.assertEqual(self.friend.get("/api/mail/alerts").json(), [])
        url = f"/api/applications/{application['application_id']}"
        self.assertEqual(self.friend.patch(url, json={"status": "offer"}).status_code, 404)


class Blank(dict):
    """Fills any path parameter, as the session is checked before the parameters."""

    def __missing__(self, key):
        return "x"
