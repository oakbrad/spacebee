"""WebDAV method dispatch.

Paths under `/Books/.Moon+/Cache/` are virtual — served from bookhive records.
Everything else hits the local-disk passthrough. FastAPI's `api_route` with
custom `methods=[...]` lets us register verbs like PROPFIND/LOCK that aren't
in the default HTTP verb list.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response

from spacebee.atproto.client import ATProtoClient

from . import moonreader
from .passthrough import Passthrough

log = logging.getLogger(__name__)


@dataclass
class DAVContext:
    client: ATProtoClient
    passthrough: Passthrough


def make_router(ctx: DAVContext) -> APIRouter:
    router = APIRouter()

    def use_moonreader(path: str) -> bool:
        return moonreader.under_prefix(path)

    # ---- OPTIONS --------------------------------------------------------
    @router.api_route("/{path:path}", methods=["OPTIONS"], include_in_schema=False)
    async def options(path: str) -> Response:
        return Response(
            status_code=200,
            headers={
                "DAV": "1, 2",
                "Allow": "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, MKCOL, LOCK, UNLOCK",
                "MS-Author-Via": "DAV",
            },
        )

    # ---- PROPFIND -------------------------------------------------------
    @router.api_route("/{path:path}", methods=["PROPFIND"], include_in_schema=False)
    async def propfind(path: str, request: Request) -> Response:
        dav_path = "/" + path
        depth = request.headers.get("Depth", "1")
        if use_moonreader(dav_path):
            status, body, headers = await moonreader.propfind(ctx.client, dav_path, depth)
        else:
            status, body, headers = await ctx.passthrough.propfind(dav_path, depth)
        return Response(status_code=status, content=body, headers=headers)

    # ---- GET / HEAD -----------------------------------------------------
    @router.api_route("/{path:path}", methods=["GET"], include_in_schema=False)
    async def get(path: str) -> Response:
        dav_path = "/" + path
        if use_moonreader(dav_path):
            status, body, headers = await moonreader.get(ctx.client, dav_path)
        else:
            status, body, headers = await ctx.passthrough.get(dav_path)
        return Response(status_code=status, content=body, headers=headers)

    @router.api_route("/{path:path}", methods=["HEAD"], include_in_schema=False)
    async def head(path: str) -> Response:
        dav_path = "/" + path
        if use_moonreader(dav_path):
            status, body, headers = await moonreader.get(ctx.client, dav_path, head=True)
        else:
            status, body, headers = await ctx.passthrough.get(dav_path, head=True)
        return Response(status_code=status, content=body, headers=headers)

    # ---- PUT ------------------------------------------------------------
    @router.api_route("/{path:path}", methods=["PUT"], include_in_schema=False)
    async def put(path: str, request: Request) -> Response:
        dav_path = "/" + path
        body = await request.body()
        if use_moonreader(dav_path):
            status, resp_body, headers = await moonreader.put(ctx.client, dav_path, body)
        else:
            status, resp_body, headers = await ctx.passthrough.put(dav_path, body)
        return Response(status_code=status, content=resp_body, headers=headers)

    # ---- DELETE ---------------------------------------------------------
    @router.api_route("/{path:path}", methods=["DELETE"], include_in_schema=False)
    async def delete(path: str) -> Response:
        dav_path = "/" + path
        if use_moonreader(dav_path):
            status, body, headers = await moonreader.delete(ctx.client, dav_path)
        else:
            status, body, headers = await ctx.passthrough.delete(dav_path)
        return Response(status_code=status, content=body, headers=headers)

    # ---- MKCOL ----------------------------------------------------------
    @router.api_route("/{path:path}", methods=["MKCOL"], include_in_schema=False)
    async def mkcol(path: str) -> Response:
        dav_path = "/" + path
        if use_moonreader(dav_path):
            # The Moon+ cache dir is virtual; creating subdirs is meaningless.
            return Response(status_code=405)
        status, body, headers = await ctx.passthrough.mkcol(dav_path)
        return Response(status_code=status, content=body, headers=headers)

    # ---- LOCK / UNLOCK --------------------------------------------------
    # WebDAV Class 2. Some clients fail hard without LOCK support; we return
    # a synthetic lock token that's never actually enforced (no concurrent
    # writers matter for a single-user homelab sync).
    @router.api_route("/{path:path}", methods=["LOCK"], include_in_schema=False)
    async def lock(path: str) -> Response:
        token = f"opaquelocktoken:{uuid.uuid4()}"
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<D:prop xmlns:D="DAV:">'
            "<D:lockdiscovery><D:activelock>"
            "<D:locktype><D:write/></D:locktype>"
            "<D:lockscope><D:exclusive/></D:lockscope>"
            "<D:depth>infinity</D:depth>"
            "<D:timeout>Second-3600</D:timeout>"
            f"<D:locktoken><D:href>{token}</D:href></D:locktoken>"
            "</D:activelock></D:lockdiscovery>"
            "</D:prop>"
        ).encode()
        return Response(
            status_code=200,
            content=body,
            headers={
                "Lock-Token": f"<{token}>",
                "Content-Type": "application/xml; charset=utf-8",
            },
        )

    @router.api_route("/{path:path}", methods=["UNLOCK"], include_in_schema=False)
    async def unlock(path: str) -> Response:
        return Response(status_code=204)

    return router
