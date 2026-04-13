"""HTTP Basic gate on all DAV requests.

Single shared credential — waggle only serves one ATProto identity, so the
Basic auth here just lets Moon+ Reader say "I'm allowed to talk to this box."
It does not map to different atproto accounts.
"""

from __future__ import annotations

import base64
import hmac

from fastapi import Request, Response


def check(request: Request, dav_user: str, dav_password: str) -> bool:
    """Return True if the request's Basic credentials match config."""
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    except Exception:
        return False
    user, _, pw = raw.partition(":")
    return hmac.compare_digest(user, dav_user) and hmac.compare_digest(pw, dav_password)


def challenge() -> Response:
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="waggle"'})
