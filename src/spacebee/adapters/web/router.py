"""HTML dashboard + cover-blob proxy. Single-user, read-only."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, PackageLoader, select_autoescape

from spacebee.adapters.webdav.router import DAVContext
from spacebee.atproto import bookhive

from . import view

log = logging.getLogger(__name__)

_env = Environment(
    loader=PackageLoader("spacebee.adapters.web", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _stars_display(stars: int | None) -> str:
    """Half-star rendering — bookhive stores 1–10, we map to 1–5 with halves."""
    if not stars:
        return ""
    out_of_five = stars / 2
    full = int(out_of_five)
    half = (out_of_five - full) >= 0.5
    return "★" * full + ("½" if half else "")


_env.filters["stars"] = _stars_display


def make_router(ctx: DAVContext) -> APIRouter:
    router = APIRouter()

    @router.get("/", include_in_schema=False, response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        records = await bookhive.list_records(ctx.client)
        books = view.build_books_view(records)
        current_year = datetime.now(UTC).year
        sections = view.partition(books, current_year)
        try:
            profile = await ctx.client.get_profile()
        except Exception as e:
            log.warning("Profile fetch failed: %s", e)
            profile = {"handle": "", "displayName": "", "avatar": ""}
        html = _env.get_template("dashboard.html").render(
            sections=sections, profile=profile, book_count=len(books),
        )
        return HTMLResponse(html)

    @router.get("/blob/{cid}", include_in_schema=False)
    async def cover(cid: str) -> Response:
        # Only serve cids that appear as covers on records we own. Prevents
        # this endpoint from being a generic open blob proxy.
        records = await bookhive.list_records(ctx.client)
        if cid not in view.cover_cids(records):
            raise HTTPException(status_code=404)
        resp = await bookhive.fetch_blob(ctx.client, cid)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="blob fetch failed")
        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "private, max-age=3600"},
        )

    return router
