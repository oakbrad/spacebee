"""End-to-end tests for the dashboard + cover proxy routes."""

from __future__ import annotations

import tempfile

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spacebee.atproto import bookhive


@pytest.fixture
def app(monkeypatch):
    bookhive.invalidate_cache()
    tmp = tempfile.mkdtemp(prefix="spacebee-web-test-")
    monkeypatch.setenv("PDS", "pds.example")
    monkeypatch.setenv("BSKY_HANDLE", "tester.example")
    monkeypatch.setenv("BSKY_APP_PASSWORD", "app-pw")
    monkeypatch.setenv("DAV_USER", "u")
    monkeypatch.setenv("DAV_PASSWORD", "p")
    monkeypatch.setenv("PASSTHROUGH_ROOT", tmp)
    from spacebee import main as main_mod
    return main_mod.create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


AUTH = ("u", "p")
DID = "did:plc:tester"


def _mock_session(respx_mock) -> None:
    respx_mock.post("https://pds.example/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(
            200,
            json={
                "accessJwt": "access.jwt",
                "refreshJwt": "refresh.jwt",
                "did": DID,
                "handle": "tester.example",
            },
        )
    )


def _mock_profile(respx_mock) -> None:
    respx_mock.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile").mock(
        return_value=httpx.Response(200, json={
            "did": DID,
            "handle": "tester.example",
            "displayName": "Test User",
            "avatar": "https://cdn.bsky.app/avatar.jpg",
        })
    )


def _records(books: list[dict]) -> dict:
    return {"records": books}


def _book(rkey: str, title: str, *, percent: int | None = None, cover_cid: str | None = None,
          status: str = "buzz.bookhive.defs#reading", updated_at: str | None = None) -> dict:
    value: dict = {
        "$type": "buzz.bookhive.book",
        "title": title,
        "authors": "A. Author",
        "status": status,
    }
    if percent is not None:
        value["bookProgress"] = {
            "percent": percent,
            "updatedAt": updated_at or "2026-04-13T19:00:00.000Z",
        }
    if cover_cid:
        value["cover"] = {
            "$type": "blob",
            "ref": {"$link": cover_cid},
            "mimeType": "image/jpeg",
            "size": 1000,
        }
    return {"uri": f"at://{DID}/buzz.bookhive.book/{rkey}", "cid": "cid", "value": value}


@respx.mock
def test_dashboard_is_public(client):
    """Web dashboard is intentionally unauthenticated — data is already public on bookhive.buzz."""
    _mock_session(respx.mock)
    _mock_profile(respx.mock)
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(200, json=_records([_book("rk1", "Book")])),
    )
    assert client.get("/").status_code == 200


def test_webdav_on_root_still_requires_auth(client):
    """Bypassing auth on GET / must not leak PROPFIND / etc. to the world."""
    assert client.request("PROPFIND", "/").status_code == 401
    assert client.request("OPTIONS", "/").status_code == 401


@respx.mock
def test_dashboard_renders_books(client):
    _mock_session(respx.mock)
    _mock_profile(respx.mock)
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(200, json=_records([
            _book("rk1", "The Lesser Dead", percent=42),
            _book("rk2", "Another Book"),
        ])),
    )

    r = client.get("/", auth=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "The Lesser Dead" in r.text
    assert "Another Book" in r.text
    assert "Test User" in r.text
    assert "tester.example" in r.text
    assert "42%" in r.text


@respx.mock
def test_dashboard_profile_failure_still_renders(client):
    _mock_session(respx.mock)
    respx.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile").mock(
        return_value=httpx.Response(500, text="boom"),
    )
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(200, json=_records([_book("rk1", "Solo Book")])),
    )

    r = client.get("/", auth=AUTH)
    assert r.status_code == 200
    assert "Solo Book" in r.text


@respx.mock
def test_cover_proxy_serves_known_cid(client):
    _mock_session(respx.mock)
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(200, json=_records([
            _book("rk1", "With Cover", cover_cid="bafycover"),
        ])),
    )
    respx.get("https://pds.example/xrpc/com.atproto.sync.getBlob").mock(
        return_value=httpx.Response(
            200, content=b"\xff\xd8\xff fake-jpeg",
            headers={"content-type": "image/jpeg"},
        ),
    )

    # No AUTH — cover proxy is public, same as the dashboard.
    r = client.get("/blob/bafycover")
    assert r.status_code == 200
    assert r.content.startswith(b"\xff\xd8\xff")
    assert r.headers["content-type"] == "image/jpeg"


@respx.mock
def test_cover_proxy_rejects_unknown_cid(client):
    _mock_session(respx.mock)
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(200, json=_records([
            _book("rk1", "No Cover"),
        ])),
    )

    r = client.get("/blob/bafysneaky", auth=AUTH)
    assert r.status_code == 404


def test_webdav_propfind_still_routes_on_root(client):
    # Sanity: mounting the web router must not swallow DAV methods on /.
    r = client.request("OPTIONS", "/", auth=AUTH)
    assert r.status_code == 200
    assert "PROPFIND" in r.headers["Allow"]
