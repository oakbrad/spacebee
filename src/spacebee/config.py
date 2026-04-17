"""Env-only config. Loaded once at startup; immutable thereafter."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    # ATProto identity spacebee writes to.
    # `pds` is optional — when unset (or empty), ATProtoClient resolves it
    # from `bsky_handle` at first use via the public bsky appview + PLC.
    pds: str | None
    bsky_handle: str
    bsky_app_password: str

    # Shared DAV credential — Moon+ Reader sends this in Basic auth
    dav_user: str
    dav_password: str

    # Local-disk fallback for non-.po DAV paths
    passthrough_root: str

    log_level: str


def load() -> Config:
    load_dotenv()
    required = [
        "BSKY_HANDLE",
        "BSKY_APP_PASSWORD",
        "DAV_USER",
        "DAV_PASSWORD",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    return Config(
        pds=os.environ.get("PDS") or None,
        bsky_handle=os.environ["BSKY_HANDLE"],
        bsky_app_password=os.environ["BSKY_APP_PASSWORD"],
        dav_user=os.environ["DAV_USER"],
        dav_password=os.environ["DAV_PASSWORD"],
        passthrough_root=os.environ.get("PASSTHROUGH_ROOT", "/data/passthrough"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
