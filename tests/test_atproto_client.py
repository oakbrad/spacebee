"""Token-refresh behavior of ATProtoClient.request()."""

from __future__ import annotations

import httpx
import pytest
import respx

from spacebee.atproto.client import ATProtoClient


@pytest.fixture
def client():
    return ATProtoClient("pds.example", "tester.example", "app-pw")


def _session_body(jwt: str = "access.jwt") -> dict:
    return {
        "accessJwt": jwt,
        "refreshJwt": "refresh.jwt",
        "did": "did:plc:tester",
        "handle": "tester.example",
    }


@respx.mock
async def test_retries_on_400_expired_token(client):
    """The real incident: PDS returns 400 ExpiredToken, not 401."""
    respx.post("https://pds.example/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=_session_body("old.jwt"))
    )
    respx.post("https://pds.example/xrpc/com.atproto.server.refreshSession").mock(
        return_value=httpx.Response(200, json=_session_body("new.jwt"))
    )

    put = respx.post("https://pds.example/xrpc/com.atproto.repo.putRecord").mock(
        side_effect=[
            httpx.Response(400, json={"error": "ExpiredToken", "message": "expired"}),
            httpx.Response(200, json={"uri": "...", "cid": "..."}),
        ]
    )

    resp = await client.request("POST", "com.atproto.repo.putRecord", json={"x": 1})
    assert resp.status_code == 200
    assert put.call_count == 2
    # Second call carries the refreshed token.
    assert put.calls[1].request.headers["Authorization"] == "Bearer new.jwt"
    await client.close()


@respx.mock
async def test_retries_on_401(client):
    """Original contract — plain 401 still triggers refresh."""
    respx.post("https://pds.example/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=_session_body("old.jwt"))
    )
    respx.post("https://pds.example/xrpc/com.atproto.server.refreshSession").mock(
        return_value=httpx.Response(200, json=_session_body("new.jwt"))
    )
    route = respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        side_effect=[
            httpx.Response(401, json={"error": "AuthenticationRequired"}),
            httpx.Response(200, json={"records": []}),
        ]
    )
    resp = await client.request("GET", "com.atproto.repo.listRecords", params={"q": 1})
    assert resp.status_code == 200
    assert route.call_count == 2
    await client.close()


@respx.mock
async def test_does_not_retry_on_unrelated_400(client):
    """A non-token 400 (e.g. lexicon validation) must NOT trigger a refresh."""
    respx.post("https://pds.example/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=_session_body())
    )
    refresh = respx.post("https://pds.example/xrpc/com.atproto.server.refreshSession").mock(
        return_value=httpx.Response(200, json=_session_body("new.jwt"))
    )
    put = respx.post("https://pds.example/xrpc/com.atproto.repo.putRecord").mock(
        return_value=httpx.Response(400, json={"error": "InvalidRequest", "message": "bad"}),
    )
    resp = await client.request("POST", "com.atproto.repo.putRecord", json={})
    assert resp.status_code == 400
    assert put.call_count == 1
    assert not refresh.called
    await client.close()
