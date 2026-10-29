"""Identity adapter: stub tokens and choosing a verifier by issuer."""

import jwt
import pytest

from identity import CognitoVerifier, InvalidToken, PoolRouter, StubVerifier

# Only used to build test tokens; nothing checks their signature here.
KEY = "test-signing-key-that-is-long-enough-for-hs256"


@pytest.mark.parametrize(
    "token, username, groups, pool",
    [
        ("stub:yo:youth", "yo", ("youth",), "users"),
        ("stub:sam:", "sam", (), "users"),
        ("stub:alex:admin,support:staff", "alex", ("admin", "support"), "staff"),
    ],
)
def test_stub_tokens(token, username, groups, pool):
    p = StubVerifier().verify(token)
    assert (p.username, p.groups, p.pool) == (username, groups, pool)


@pytest.mark.parametrize("token", ["", "stub:", "nope:yo:youth", "stub:yo:youth:root"])
def test_bad_stub_tokens(token):
    with pytest.raises(InvalidToken):
        StubVerifier().verify(token)


def router() -> PoolRouter:
    # Building a verifier doesn't contact Cognito; keys are fetched on first use.
    return PoolRouter(
        [
            CognitoVerifier("https://issuer.example/users/", "users-client", "users"),
            CognitoVerifier("https://issuer.example/staff", "staff-client", "staff"),
        ]
    )


def test_unknown_issuer_is_refused():
    token = jwt.encode({"iss": "https://evil.example/staff"}, KEY, algorithm="HS256")
    with pytest.raises(InvalidToken, match="unknown issuer"):
        router().verify(token)


def test_garbage_token_is_refused():
    with pytest.raises(InvalidToken):
        router().verify("not.a.jwt")


def test_issuer_match_ignores_trailing_slash(monkeypatch):
    r = router()
    seen = []
    for v in r.by_issuer.values():
        monkeypatch.setattr(v, "verify", lambda token, v=v: seen.append(v.pool))
    r.verify(jwt.encode({"iss": "https://issuer.example/users"}, KEY, algorithm="HS256"))
    r.verify(jwt.encode({"iss": "https://issuer.example/staff/"}, KEY, algorithm="HS256"))
    assert seen == ["users", "staff"]


# Clock drift ------------------------------------------------------------------


def signed_token(**times) -> tuple[str, object]:
    """An RS256 access token as Cognito would issue it, and its public key."""
    import time

    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {
        "iss": "https://issuer.example/users",
        "sub": "abc",
        "token_use": "access",
        "client_id": "users-client",
        "iat": now + times.get("iat", 0),
        "exp": now + times.get("exp", 3600),
    }
    return jwt.encode(claims, private, algorithm="RS256"), private.public_key()


def verify(token, public_key, monkeypatch):
    v = CognitoVerifier("https://issuer.example/users", "users-client", "users")
    monkeypatch.setattr(v.jwks, "get_signing_key_from_jwt", lambda t: type("K", (), {"key": public_key}))
    return v.verify(token)


@pytest.mark.parametrize("iat", [0, 3, 25])
def test_token_from_a_clock_slightly_ahead_is_accepted(monkeypatch, iat):
    token, key = signed_token(iat=iat)  # Cognito's clock is ahead of ours by iat seconds
    assert verify(token, key, monkeypatch).subject == "abc"


def test_token_issued_well_in_the_future_is_refused(monkeypatch):
    token, key = signed_token(iat=120)
    with pytest.raises(InvalidToken):
        verify(token, key, monkeypatch)


def test_expired_token_is_still_refused(monkeypatch):
    token, key = signed_token(iat=-3700, exp=-60)
    with pytest.raises(InvalidToken, match="expired"):
        verify(token, key, monkeypatch)
