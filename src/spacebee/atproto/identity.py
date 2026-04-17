"""Resolve an atproto handle to its PDS hostname.

Two hops, both plain HTTPS (no DNS-TXT lookup, so no extra deps):

    handle ──(public bsky appview)──▶ DID ──(PLC directory / did:web doc)──▶ PDS

Called lazily from `ATProtoClient._ensure_session()` when `PDS` env is unset,
so users only need to configure their handle + app password.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

# Public, unauthenticated endpoints. Same appview we already hit for profiles.
_RESOLVE_HANDLE_URL = "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle"
_PLC_DIRECTORY_URL = "https://plc.directory"


async def resolve_pds(http: httpx.AsyncClient, handle: str) -> str:
    """Return the PDS hostname (no scheme, no trailing slash) for the handle."""
    did = await _resolve_handle(http, handle)
    doc = await _fetch_did_doc(http, did)
    endpoint = _pds_from_did_doc(doc, did)
    host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
    log.info("Resolved %s → DID %s → PDS %s", handle, did, host)
    return host


async def _resolve_handle(http: httpx.AsyncClient, handle: str) -> str:
    resp = await http.get(_RESOLVE_HANDLE_URL, params={"handle": handle})
    resp.raise_for_status()
    return resp.json()["did"]


async def _fetch_did_doc(http: httpx.AsyncClient, did: str) -> dict:
    if did.startswith("did:plc:"):
        url = f"{_PLC_DIRECTORY_URL}/{did}"
    elif did.startswith("did:web:"):
        # did:web:example.com          → https://example.com/.well-known/did.json
        # did:web:example.com:u:alice  → https://example.com/u/alice/did.json
        parts = did.removeprefix("did:web:").split(":")
        url = (
            f"https://{parts[0]}/.well-known/did.json"
            if len(parts) == 1
            else f"https://{'/'.join(parts)}/did.json"
        )
    else:
        raise RuntimeError(f"Unsupported DID method for PDS resolution: {did}")

    resp = await http.get(url)
    resp.raise_for_status()
    return resp.json()


def _pds_from_did_doc(doc: dict, did: str) -> str:
    for svc in doc.get("service") or []:
        if svc.get("type") == "AtprotoPersonalDataServer" and svc.get("serviceEndpoint"):
            return svc["serviceEndpoint"]
    raise RuntimeError(f"No AtprotoPersonalDataServer service entry in DID doc for {did}")
