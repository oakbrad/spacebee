"""FastAPI entrypoint: wires config, auth middleware, and the WebDAV router."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from waggle import auth, config
from waggle.adapters.webdav.passthrough import Passthrough
from waggle.adapters.webdav.router import DAVContext, make_router
from waggle.atproto.client import ATProtoClient


def create_app() -> FastAPI:
    cfg = config.load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("waggle")

    client = ATProtoClient(cfg.pds, cfg.bsky_handle, cfg.bsky_app_password)
    passthrough = Passthrough(cfg.passthrough_root)
    ctx = DAVContext(client=client, passthrough=passthrough)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        log.info("waggle starting (pds=%s handle=%s)", cfg.pds, cfg.bsky_handle)
        try:
            yield
        finally:
            await client.close()
            log.info("waggle stopped")

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)

    class BasicAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path == "/healthz":
                return await call_next(request)
            if not auth.check(request, cfg.dav_user, cfg.dav_password):
                return auth.challenge()
            return await call_next(request)

    app.add_middleware(BasicAuthMiddleware)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"ok": True}

    app.include_router(make_router(ctx))
    return app


app = create_app()
