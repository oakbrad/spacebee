"""ATProto session + XRPC client.

One lazy, module-scoped session. Refresh on 401; createSession if refresh also
fails. No persistence — a restart re-auths on first request.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from . import identity

log = logging.getLogger(__name__)

TIMEOUT = 15.0


@dataclass
class Session:
    access_jwt: str
    refresh_jwt: str
    did: str


class ATProtoClient:
    def __init__(self, pds: str | None, handle: str, app_password: str) -> None:
        # `pds` may be None — in that case it's resolved from the handle the
        # first time we authenticate. Lazy so config loading stays synchronous
        # and offline-friendly.
        self._pds: str | None = pds.rstrip("/") if pds else None
        self._handle = handle
        self._app_password = app_password
        self._session: Session | None = None
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=TIMEOUT)

    @property
    def pds_url(self) -> str:
        if self._pds is None:
            raise RuntimeError("PDS not resolved yet — call an authenticated method first")
        return f"https://{self._pds}"

    @property
    def http(self) -> httpx.AsyncClient:
        """Shared unauthenticated HTTP client (e.g. for bookhive catalog, blob downloads)."""
        return self._http

    async def close(self) -> None:
        await self._http.aclose()

    async def did(self) -> str:
        sess = await self._ensure_session()
        return sess.did

    async def get_profile(self) -> dict:
        """Fetch the authed user's bsky profile (handle, displayName, avatar).

        Hits the public bsky appview, not the user's PDS — PDSes don't serve
        `app.bsky.*` endpoints directly. Avatar is a CDN URL safe to render.
        """
        did = await self.did()
        resp = await self._http.get(
            "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
            params={"actor": did},
        )
        resp.raise_for_status()
        return resp.json()

    async def _ensure_session(self) -> Session:
        if self._session is not None:
            return self._session
        async with self._lock:
            if self._pds is None:
                self._pds = await identity.resolve_pds(self._http, self._handle)
            if self._session is None:
                self._session = await self._create_session()
        return self._session

    async def _create_session(self) -> Session:
        log.info("Authenticating to %s as %s", self._pds, self._handle)
        resp = await self._http.post(
            f"{self.pds_url}/xrpc/com.atproto.server.createSession",
            json={"identifier": self._handle, "password": self._app_password},
        )
        resp.raise_for_status()
        data = resp.json()
        return Session(
            access_jwt=data["accessJwt"],
            refresh_jwt=data["refreshJwt"],
            did=data["did"],
        )

    async def _refresh_session(self) -> None:
        assert self._session is not None
        log.debug("Refreshing ATProto session")
        resp = await self._http.post(
            f"{self.pds_url}/xrpc/com.atproto.server.refreshSession",
            headers={"Authorization": f"Bearer {self._session.refresh_jwt}"},
        )
        if resp.status_code >= 400:
            log.info("refreshSession failed (%s); re-creating", resp.status_code)
            self._session = await self._create_session()
            return
        data = resp.json()
        self._session = Session(
            access_jwt=data["accessJwt"],
            refresh_jwt=data["refreshJwt"],
            did=data["did"],
        )

    @staticmethod
    def _is_expired_token(resp: httpx.Response) -> bool:
        """True if the PDS is telling us to refresh.

        atproto's convention: a fully-unauthenticated call returns 401, but a
        call with a previously-valid token that has since expired returns 400
        with `{"error": "ExpiredToken"}`. Both should trigger a refresh.
        """
        if resp.status_code == 401:
            return True
        if resp.status_code == 400:
            try:
                return resp.json().get("error") in ("ExpiredToken", "InvalidToken")
            except ValueError:
                return False
        return False

    async def request(
        self,
        method: str,
        nsid: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        content: bytes | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        """XRPC call with auto-refresh on ExpiredToken / 401."""
        sess = await self._ensure_session()
        url = f"{self.pds_url}/xrpc/{nsid}"
        hdrs = {"Authorization": f"Bearer {sess.access_jwt}"}
        if headers:
            hdrs.update(headers)

        resp = await self._http.request(
            method, url, params=params, json=json, content=content, headers=hdrs
        )
        if self._is_expired_token(resp):
            log.info("Access token expired; refreshing and retrying %s", nsid)
            async with self._lock:
                await self._refresh_session()
            assert self._session is not None
            hdrs["Authorization"] = f"Bearer {self._session.access_jwt}"
            resp = await self._http.request(
                method, url, params=params, json=json, content=content, headers=hdrs
            )
        return resp
