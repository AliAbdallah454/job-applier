"""Auth0 login: build its links, swap the code it returns for an ID token, and check that token."""

from urllib.parse import urlencode

import httpx
import jwt


class Auth0:
    def __init__(
        self,
        domain,
        client_id,
        client_secret,
        owner_id=None,
        *,
        app_url="http://localhost:5173",
        callback_url="http://localhost:8000/api/auth/callback",
    ):
        self.domain = domain
        self.client_id = client_id
        self.client_secret = client_secret
        self.owner_id = owner_id  # The account that holds the existing data and the mailbox.
        self.app_url = app_url
        self.callback_url = callback_url
        self.keys = jwt.PyJWKClient(f"https://{domain}/.well-known/jwks.json")

    @classmethod
    def from_values(cls, values):
        """Auth0 from these settings, or None when none are set; part of them is a mistake."""
        required = ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET")
        found = [values.get(name) for name in required]
        if not any(found):
            return None
        if not all(found):
            raise ValueError(f"Set all of {', '.join(required)}, or none of them")
        urls = {
            "app_url": values.get("AUTH0_APP_URL"),
            "callback_url": values.get("AUTH0_CALLBACK_URL"),
        }
        return cls(
            *found,
            values.get("AUTH0_OWNER_ID"),
            **{name: url for name, url in urls.items() if url},
        )

    @property
    def secure_cookies(self):
        return self.app_url.startswith("https://")

    def login_url(self, state, nonce):
        query = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
            "scope": "openid",
            "state": state,
            "nonce": nonce,
        }
        return f"https://{self.domain}/authorize?{urlencode(query)}"

    def logout_url(self):
        query = {"client_id": self.client_id, "returnTo": self.app_url}
        return f"https://{self.domain}/v2/logout?{urlencode(query)}"

    def exchange(self, code):
        """The ID token for a code from the login page."""
        response = httpx.post(
            f"https://{self.domain}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.callback_url,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["id_token"]

    def signing_key(self, token):
        return self.keys.get_signing_key_from_jwt(token).key

    def verify(self, token):
        """The token's claims; raises jwt.PyJWTError unless Auth0 made it for this app."""
        return jwt.decode(
            token,
            self.signing_key(token),
            algorithms=["RS256"],  # Fixed here, so a token cannot choose a weaker one.
            audience=self.client_id,
            issuer=f"https://{self.domain}/",
            options={"require": ["exp", "iat", "sub"]},
        )
