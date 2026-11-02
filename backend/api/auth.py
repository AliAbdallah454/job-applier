"""The login routes: to Auth0 and back, and out again. The session is the ID token in a cookie."""

import secrets

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from backend.api.deps import SESSION_COOKIE, AppContext

router = APIRouter(prefix="/api/auth", tags=["auth"], include_in_schema=False)

STATE_COOKIE = "login_state"
NONCE_COOKIE = "login_nonce"
LOGIN_SECONDS = 600  # Time allowed on the login page.


def auth_of(context):
    if context.settings.auth is None:
        raise HTTPException(404, "Login is not set up")
    return context.settings.auth


def set_cookie(response, auth, name, value, max_age):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,  # Out of reach of page scripts.
        samesite="lax",  # Other sites cannot send it with a write.
        secure=auth.secure_cookies,
    )


@router.get("/login")
def login(context: AppContext) -> RedirectResponse:
    auth = auth_of(context)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    response = RedirectResponse(auth.login_url(state, nonce), status_code=302)
    set_cookie(response, auth, STATE_COOKIE, state, LOGIN_SECONDS)
    set_cookie(response, auth, NONCE_COOKIE, nonce, LOGIN_SECONDS)
    return response


@router.get("/callback")
def callback(
    request: Request,
    context: AppContext,
    code: str | None = None,
    state: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    auth = auth_of(context)
    if error_description:
        raise HTTPException(400, f"Login failed: {error_description}")
    expected = request.cookies.get(STATE_COOKIE)
    # Proves that this browser started the login, so nobody can log it in to their own account.
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "This login link is no longer valid. Start again.")
    try:
        token = auth.exchange(code)
        claims = auth.verify(token)
    except httpx.HTTPError:
        raise HTTPException(503, "Auth0 could not be reached. Try again.") from None
    except jwt.PyJWTError:
        raise HTTPException(400, "Auth0's answer could not be checked. Start again.") from None
    # Ties the token to this login, so a captured one cannot be replayed through this route.
    if claims.get("nonce") != request.cookies.get(NONCE_COOKIE):
        raise HTTPException(400, "This login link is no longer valid. Start again.")
    if claims["sub"] != auth.owner_id:
        hint = "" if auth.owner_id else f" Set AUTH0_OWNER_ID={claims['sub']} to allow it."
        raise HTTPException(403, f"This account is not allowed.{hint}")
    response = RedirectResponse(auth.app_url, status_code=302)
    set_cookie(response, auth, SESSION_COOKIE, token, claims["exp"] - claims["iat"])
    response.delete_cookie(STATE_COOKIE)
    response.delete_cookie(NONCE_COOKIE)
    return response


@router.get("/logout")
def logout(context: AppContext) -> RedirectResponse:
    auth = auth_of(context)
    response = RedirectResponse(auth.logout_url(), status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
