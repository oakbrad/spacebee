"""FastAPI entrypoint: wires config, auth middleware, and the WebDAV router."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from spacebee import auth, config
from spacebee.adapters.web import make_router as make_web_router
from spacebee.adapters.webdav.passthrough import Passthrough
from spacebee.adapters.webdav.router import DAVContext, make_router
from spacebee.atproto.client import ATProtoClient


def create_app() -> FastAPI:
    cfg = config.load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("spacebee")

    client = ATProtoClient(cfg.pds, cfg.bsky_handle, cfg.bsky_app_password)
    passthrough = Passthrough(cfg.passthrough_root)
    ctx = DAVContext(client=client, passthrough=passthrough)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        log.info("spacebee starting (pds=%s handle=%s)", cfg.pds, cfg.bsky_handle)
        try:
            yield
        finally:
            await client.close()
            log.info("spacebee stopped")

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)

    def _public(request: Request) -> bool:
        """Routes that bypass basic auth.

        The read-only web dashboard (`GET /`) and cover proxy (`GET /blob/*`)
        are intentionally public — the data they expose is already public on
        bookhive.buzz. Everything else (WebDAV on any verb, including
        PROPFIND/GET/PUT on `/` and `/Books/*`) stays gated.
        """
        path = request.url.path
        if path == "/healthz":
            return True
        return request.method == "GET" and (path == "/" or path.startswith("/blob/"))

    class BasicAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if _public(request):
                return await call_next(request)
            if not auth.check(request, cfg.dav_user, cfg.dav_password):
                return auth.challenge()
            return await call_next(request)

    app.add_middleware(BasicAuthMiddleware)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"ok": True}

    # Web dashboard is registered first so `GET /` and `/blob/{cid}` win over
    # the WebDAV catch-all on `/{path:path}`.
    app.include_router(make_web_router(ctx))
    app.include_router(make_router(ctx))
    return app


app = create_app()
