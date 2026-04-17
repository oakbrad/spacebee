"""Handle → DID → PDS resolution."""

from __future__ import annotations

import httpx
import pytest
import respx

from spacebee.atproto import identity


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@respx.mock
async def test_resolve_plc_handle(http):
    respx.get(
        "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": "alice.bsky.social"},
    ).mock(return_value=httpx.Response(200, json={"did": "did:plc:abc123"}))
    respx.get("https://plc.directory/did:plc:abc123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "did:plc:abc123",
                "service": [
                    {
                        "id": "#atproto_pds",
                        "type": "AtprotoPersonalDataServer",
                        "serviceEndpoint": "https://morel.us-east.host.bsky.network",
                    }
                ],
            },
        )
    )

    host = await identity.resolve_pds(http, "alice.bsky.social")
    assert host == "morel.us-east.host.bsky.network"


@respx.mock
async def test_resolve_did_web_root(http):
    respx.get(
        "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": "alice.example.com"},
    ).mock(return_value=httpx.Response(200, json={"did": "did:web:example.com"}))
    respx.get("https://example.com/.well-known/did.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "service": [
                    {
                        "id": "#atproto_pds",
                        "type": "AtprotoPersonalDataServer",
                        "serviceEndpoint": "https://pds.example.com/",
                    }
                ]
            },
        )
    )

    host = await identity.resolve_pds(http, "alice.example.com")
    assert host == "pds.example.com"


@respx.mock
async def test_missing_pds_service_raises(http):
    respx.get(
        "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": "alice.bsky.social"},
    ).mock(return_value=httpx.Response(200, json={"did": "did:plc:abc"}))
    respx.get("https://plc.directory/did:plc:abc").mock(
        return_value=httpx.Response(200, json={"service": []})
    )

    with pytest.raises(RuntimeError, match="No AtprotoPersonalDataServer"):
        await identity.resolve_pds(http, "alice.bsky.social")
