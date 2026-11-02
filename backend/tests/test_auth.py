"""Login through a fake Auth0: tokens are real and signed here, and nothing leaves the machine."""

import time
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.auth0 import Auth0
from backend.api.deps import Context, Settings

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OWNER = "auth0|owner"


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

    def test_another_account_is_refused(self):
        self.assertEqual(self.options(token(sub="auth0|someone-else")).status_code, 403)

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

    def test_another_account_gets_no_session(self):
        _, state, nonce = self.start_login()
        self.auth.answer = token(nonce=nonce, sub="auth0|someone-else")
        response = self.client.get("/api/auth/callback", params={"code": "c", "state": state})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("session", self.client.cookies)
        self.assertNotIn("auth0|someone-else", response.text)

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


class Blank(dict):
    """Fills any path parameter, as the session is checked before the parameters."""

    def __missing__(self, key):
        return "x"
