"""End-to-end tests for the WebDAV adapter with ATProto mocked via respx."""

from __future__ import annotations

import tempfile

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from waggle.atproto import bookhive


@pytest.fixture
def app(monkeypatch):
    # Clear module-level record cache between tests.
    bookhive.invalidate_cache()

    tmp = tempfile.mkdtemp(prefix="waggle-test-")
    monkeypatch.setenv("PDS", "pds.example")
    monkeypatch.setenv("BSKY_HANDLE", "tester.example")
    monkeypatch.setenv("BSKY_APP_PASSWORD", "app-pw")
    monkeypatch.setenv("DAV_USER", "u")
    monkeypatch.setenv("DAV_PASSWORD", "p")
    monkeypatch.setenv("PASSTHROUGH_ROOT", tmp)

    # Reload the app factory to pick up env.
    from waggle import main as main_mod

    return main_mod.create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


AUTH = ("u", "p")
DID = "did:plc:tester"


def _mock_create_session(respx_mock) -> None:
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


def _records(books: list[dict]) -> dict:
    return {"records": books}


def _book_record(
    rkey: str,
    title: str,
    authors: str,
    *,
    moon_file: str | None = None,
    position: str | None = None,
) -> dict:
    value: dict = {
        "$type": "buzz.bookhive.book",
        "title": title,
        "authors": authors,
        "status": "buzz.bookhive.defs#reading",
    }
    if moon_file and position:
        value["bookProgress"] = {
            "percent": 30,
            "currentChapter": 25,
            "updatedAt": "2026-04-13T19:00:18.000Z",
            "moonReader": {
                "position": position,
                "file": moon_file,
                "syncedAt": "2026-04-13T19:00:18.000Z",
            },
        }
    return {"uri": f"at://{DID}/buzz.bookhive.book/{rkey}", "cid": "cid", "value": value}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_healthz_requires_no_auth(client):
    assert client.get("/healthz").status_code == 200


def test_options_unauthenticated_rejected(client):
    assert client.request("OPTIONS", "/").status_code == 401


def test_options_authenticated_ok(client):
    r = client.request("OPTIONS", "/", auth=AUTH)
    assert r.status_code == 200
    assert "1" in r.headers["DAV"]
    assert "PROPFIND" in r.headers["Allow"]


# ---------------------------------------------------------------------------
# Moon+ cache PROPFIND / GET / PUT
# ---------------------------------------------------------------------------

@respx.mock
def test_propfind_cache_synthesizes_from_records(client):
    _mock_create_session(respx.mock)
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(
            200,
            json=_records([
                _book_record(
                    "rk1", "The Lesser Dead", "Christopher Buehlman",
                    moon_file="The Lesser Dead - Christopher Buehlman.epub.po",
                    position="1703297605115*24@0#0:30.0%",
                ),
                # No moonReader data — should NOT appear in listing.
                _book_record("rk2", "Other Book", "Nobody"),
            ]),
        )
    )

    r = client.request(
        "PROPFIND", "/Books/.Moon+/Cache/",
        auth=AUTH, headers={"Depth": "1"},
    )
    assert r.status_code == 207
    body = r.text
    assert "The Lesser Dead - Christopher Buehlman.epub.po" in body
    assert "Other Book" not in body


@respx.mock
def test_get_returns_stored_position_verbatim(client):
    _mock_create_session(respx.mock)
    raw = "1703297605115*24@0#0:30.0%"
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(
            200,
            json=_records([
                _book_record(
                    "rk1", "The Lesser Dead", "Christopher Buehlman",
                    moon_file="The Lesser Dead - Christopher Buehlman.epub.po",
                    position=raw,
                ),
            ]),
        )
    )

    r = client.get(
        "/Books/.Moon+/Cache/The Lesser Dead - Christopher Buehlman.epub.po",
        auth=AUTH,
    )
    assert r.status_code == 200
    assert r.text == raw
    assert r.headers["Content-Type"].startswith("text/plain")


@respx.mock
def test_put_updates_existing_record(client):
    _mock_create_session(respx.mock)
    bookhive.invalidate_cache()

    filename = "The Lesser Dead - Christopher Buehlman.epub.po"
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(
            200,
            json=_records([
                _book_record(
                    "rk1", "The Lesser Dead", "Christopher Buehlman",
                    moon_file=filename,
                    position="1703297605115*24@0#0:30.0%",
                ),
            ]),
        )
    )
    put_route = respx.post("https://pds.example/xrpc/com.atproto.repo.putRecord").mock(
        return_value=httpx.Response(200, json={"uri": "...", "cid": "..."}),
    )

    new_po = "1703297605115*30@0#0:42.5%"
    r = client.put(
        f"/Books/.Moon+/Cache/{filename}",
        auth=AUTH,
        content=new_po.encode(),
    )
    assert r.status_code == 204
    assert put_route.called
    body = put_route.calls[0].request.read()
    # The new bookProgress should have hit percent=42 + moonReader.position=new_po.
    import json
    payload = json.loads(body)
    assert payload["collection"] == "buzz.bookhive.book"
    assert payload["record"]["bookProgress"]["percent"] == 42
    assert payload["record"]["bookProgress"]["moonReader"]["position"] == new_po


@respx.mock
def test_put_is_noop_when_unchanged(client):
    _mock_create_session(respx.mock)
    bookhive.invalidate_cache()

    filename = "The Lesser Dead - Christopher Buehlman.epub.po"
    raw = "1703297605115*24@0#0:30.0%"
    respx.get("https://pds.example/xrpc/com.atproto.repo.listRecords").mock(
        return_value=httpx.Response(
            200,
            json=_records([
                _book_record(
                    "rk1", "The Lesser Dead", "Christopher Buehlman",
                    moon_file=filename, position=raw,
                ),
            ]),
        )
    )
    put_route = respx.post("https://pds.example/xrpc/com.atproto.repo.putRecord").mock(
        return_value=httpx.Response(200, json={"uri": "...", "cid": "..."}),
    )

    r = client.put(f"/Books/.Moon+/Cache/{filename}", auth=AUTH, content=raw.encode())
    assert r.status_code == 204
    # Crucial: we must NOT hit putRecord when the position hasn't moved.
    # Moon+ Reader uploads on every pause event; most are duplicates.
    assert not put_route.called


# ---------------------------------------------------------------------------
# Passthrough
# ---------------------------------------------------------------------------

def test_passthrough_put_then_get(client):
    r = client.put(
        "/Books/.Moon+/Settings/theme.json", auth=AUTH, content=b'{"dark": true}'
    )
    assert r.status_code in (201, 204)
    r = client.get("/Books/.Moon+/Settings/theme.json", auth=AUTH)
    assert r.status_code == 200
    assert r.content == b'{"dark": true}'


def test_passthrough_propfind_lists_skeleton(client):
    r = client.request(
        "PROPFIND", "/Books/.Moon+/", auth=AUTH, headers={"Depth": "1"}
    )
    assert r.status_code == 207
    # The virtual Cache/ dir should be visible alongside real Settings/.
    assert "Cache" in r.text
    assert "Settings" in r.text
