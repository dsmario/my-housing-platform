"""Identity adapter.

Routes depend on `current_principal` and see a Principal. Which provider issued
the token is configuration (IDENTITY_PROVIDER), so the provider can change without
touching application code.

  cognito  Verifies Cognito access tokens: signature (JWKS), issuer, expiry,
           token_use=access and our app client. Needs COGNITO_ISSUER and
           COGNITO_CLIENT_ID (users pool). STAFF_COGNITO_ISSUER and
           STAFF_COGNITO_CLIENT_ID add the staff pool; without them, staff
           tokens are refused.
  stub     Offline development and tests only. Token format:
           "stub:<username>:<group,group>[:<pool>]", pool "users" or "staff".

There is deliberately no default: an unset IDENTITY_PROVIDER fails at startup
rather than silently accepting stub tokens.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Principal:
    subject: str
    username: str
    groups: tuple[str, ...]
    pool: str  # "users" (youth, supporters) or "staff"


class InvalidToken(Exception):
    pass


class CognitoVerifier:
    def __init__(self, issuer: str, client_id: str, pool: str):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.pool = pool
        self.jwks = jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json", cache_keys=True)

    def verify(self, token: str) -> Principal:
        try:
            key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "token_use", "client_id"]},
            )
        except jwt.PyJWTError as e:
            raise InvalidToken(str(e)) from e
        if claims["token_use"] != "access":
            raise InvalidToken("not an access token")
        if claims["client_id"] != self.client_id:
            raise InvalidToken("token was issued to a different app client")
        return Principal(
            subject=claims["sub"],
            username=claims.get("username", claims["sub"]),
            groups=tuple(claims.get("cognito:groups", [])),
            pool=self.pool,
        )


class PoolRouter:
    """Sends each token to the verifier for the pool that issued it."""

    def __init__(self, verifiers: list[CognitoVerifier]):
        self.by_issuer = {v.issuer: v for v in verifiers}

    def verify(self, token: str) -> Principal:
        try:
            # Unverified read, only to choose the verifier; that verifier
            # then checks the signature and the issuer.
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss", "")
        except jwt.PyJWTError as e:
            raise InvalidToken(str(e)) from e
        verifier = self.by_issuer.get(issuer.rstrip("/"))
        if verifier is None:
            raise InvalidToken("token is from an unknown issuer")
        return verifier.verify(token)


class StubVerifier:
    def verify(self, token: str) -> Principal:
        kind, _, rest = token.partition(":")
        username, _, rest = rest.partition(":")
        groups, _, pool = rest.partition(":")
        if kind != "stub" or not username or pool not in ("", "users", "staff"):
            raise InvalidToken("expected stub:<username>:<groups>[:<pool>]")
        return Principal(
            subject=f"stub-{username}",
            username=username,
            groups=tuple(g for g in groups.split(",") if g),
            pool=pool or "users",
        )


@lru_cache
def get_verifier() -> PoolRouter | StubVerifier:
    provider = os.environ.get("IDENTITY_PROVIDER")
    if provider == "cognito":
        verifiers = [
            CognitoVerifier(os.environ["COGNITO_ISSUER"], os.environ["COGNITO_CLIENT_ID"], "users")
        ]
        if os.environ.get("STAFF_COGNITO_ISSUER"):
            verifiers.append(
                CognitoVerifier(
                    os.environ["STAFF_COGNITO_ISSUER"], os.environ["STAFF_COGNITO_CLIENT_ID"], "staff"
                )
            )
        return PoolRouter(verifiers)
    if provider == "stub":
        return StubVerifier()
    raise RuntimeError(f"IDENTITY_PROVIDER must be 'cognito' or 'stub', got {provider!r}")


def current_principal(request: Request) -> Principal:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Sign-in required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return get_verifier().verify(token)
    except InvalidToken:
        raise HTTPException(
            401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}
        ) from None


def staff_admin(request: Request) -> Principal:
    """Only members of the staff pool's admin group. A users-pool token is
    refused even if it somehow carries an "admin" group."""
    principal = current_principal(request)
    if principal.pool != "staff" or "admin" not in principal.groups:
        raise HTTPException(403, "Staff admin access required")
    return principal
