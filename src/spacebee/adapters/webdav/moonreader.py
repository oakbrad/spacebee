"""Moon+ Reader `.po` virtual directory served from bookhive records.

The `/Books/.Moon+/Cache/` prefix is NOT backed by disk — each PROPFIND/GET/PUT
is translated to an ATProto round-trip against `buzz.bookhive.book`. See the
project plan and `atproto/bookhive.py` for the translation contract.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import quote

from spacebee.atproto import bookhive
from spacebee.atproto.client import ATProtoClient

log = logging.getLogger(__name__)

VIRTUAL_PREFIX = "/Books/.Moon+/Cache/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def under_prefix(path: str) -> bool:
    """Is this path the cache dir itself or something inside it?"""
    return path == VIRTUAL_PREFIX.rstrip("/") or path.startswith(VIRTUAL_PREFIX)


def filename_from_path(path: str) -> str | None:
    """Return the `.po` filename from a path, or None if path is the dir itself."""
    if not under_prefix(path):
        return None
    tail = path[len(VIRTUAL_PREFIX):] if path.startswith(VIRTUAL_PREFIX) else ""
    return tail or None


def _http_date(iso: str | None) -> str:
    if not iso:
        return format_datetime(datetime.now(UTC), usegmt=True)
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return format_datetime(datetime.now(UTC), usegmt=True)
    return format_datetime(dt, usegmt=True)


def _etag(content: str) -> str:
    return '"' + hashlib.sha1(content.encode("utf-8")).hexdigest()[:16] + '"'


# ---------------------------------------------------------------------------
# PROPFIND XML synthesis
# ---------------------------------------------------------------------------

def _response_xml(
    *,
    href: str,
    is_collection: bool,
    last_modified_http: str,
    content_length: int = 0,
    etag: str | None = None,
    display_name: str | None = None,
) -> str:
    resourcetype = "<D:collection/>" if is_collection else ""
    content_type = "" if is_collection else "<D:getcontenttype>text/plain</D:getcontenttype>"
    length = "" if is_collection else f"<D:getcontentlength>{content_length}</D:getcontentlength>"
    etag_xml = f"<D:getetag>{etag}</D:getetag>" if etag else ""
    dn = display_name or href.rsplit("/", 1)[-1] or href
    return (
        "<D:response>"
        f"<D:href>{quote(href)}</D:href>"
        "<D:propstat>"
        "<D:prop>"
        f"<D:resourcetype>{resourcetype}</D:resourcetype>"
        f"<D:displayname>{dn}</D:displayname>"
        f"<D:getlastmodified>{last_modified_http}</D:getlastmodified>"
        f"{length}"
        f"{content_type}"
        f"{etag_xml}"
        "</D:prop>"
        "<D:status>HTTP/1.1 200 OK</D:status>"
        "</D:propstat>"
        "</D:response>"
    )


def _multistatus(responses: list[str]) -> bytes:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:multistatus xmlns:D="DAV:">'
        + "".join(responses)
        + "</D:multistatus>"
    )
    return body.encode("utf-8")


async def propfind(
    client: ATProtoClient, path: str, depth: str
) -> tuple[int, bytes, dict[str, str]]:
    """Synthesize a WebDAV 207 Multi-Status response from bookhive records."""
    filename = filename_from_path(path)

    if filename is None:
        # PROPFIND on the cache dir itself.
        records = await bookhive.list_records(client)
        responses = [
            _response_xml(
                href=VIRTUAL_PREFIX,
                is_collection=True,
                last_modified_http=format_datetime(datetime.now(UTC), usegmt=True),
                display_name="Cache",
            )
        ]
        if depth != "0":
            for r in records:
                moon = (r["value"].get("bookProgress") or {}).get("moonReader") or {}
                fn = moon.get("file")
                if not fn:
                    continue
                pos = moon.get("position") or ""
                updated = (r["value"].get("bookProgress") or {}).get("updatedAt")
                responses.append(
                    _response_xml(
                        href=f"{VIRTUAL_PREFIX}{fn}",
                        is_collection=False,
                        last_modified_http=_http_date(updated),
                        content_length=len(pos.encode("utf-8")),
                        etag=_etag(pos),
                        display_name=fn,
                    )
                )
        return 207, _multistatus(responses), {"Content-Type": "application/xml; charset=utf-8"}

    # PROPFIND on a specific .po file.
    record = await bookhive.resolve_record(client, filename)
    if not record:
        return 404, b"", {}
    content = bookhive.serialize_po(record["value"])
    updated = (record["value"].get("bookProgress") or {}).get("updatedAt")
    response = _response_xml(
        href=f"{VIRTUAL_PREFIX}{filename}",
        is_collection=False,
        last_modified_http=_http_date(updated),
        content_length=len(content.encode("utf-8")),
        etag=_etag(content),
        display_name=filename,
    )
    return 207, _multistatus([response]), {"Content-Type": "application/xml; charset=utf-8"}


# ---------------------------------------------------------------------------
# GET / HEAD
# ---------------------------------------------------------------------------

async def get(
    client: ATProtoClient, path: str, *, head: bool = False
) -> tuple[int, bytes, dict[str, str]]:
    filename = filename_from_path(path)
    if not filename:
        return 400, b"", {}
    record = await bookhive.resolve_record(client, filename)
    if not record:
        return 404, b"", {}
    content = bookhive.serialize_po(record["value"])
    updated = (record["value"].get("bookProgress") or {}).get("updatedAt")
    body = content.encode("utf-8")
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Length": str(len(body)),
        "Last-Modified": _http_date(updated),
        "ETag": _etag(content),
    }
    return 200, (b"" if head else body), headers


# ---------------------------------------------------------------------------
# PUT / DELETE
# ---------------------------------------------------------------------------

async def put(
    client: ATProtoClient, path: str, body: bytes
) -> tuple[int, bytes, dict[str, str]]:
    filename = filename_from_path(path)
    if not filename:
        return 400, b"", {}
    status_msg = await bookhive.apply_po_put(client, filename, body)
    log.info("PUT %s -> %s", filename, status_msg)
    return 204, b"", {}


async def delete(
    client: ATProtoClient, path: str
) -> tuple[int, bytes, dict[str, str]]:
    filename = filename_from_path(path)
    if not filename:
        return 400, b"", {}
    status_msg = await bookhive.apply_po_delete(client, filename)
    log.info("DELETE %s -> %s", filename, status_msg)
    return 204, b"", {}
