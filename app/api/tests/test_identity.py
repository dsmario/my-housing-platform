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
