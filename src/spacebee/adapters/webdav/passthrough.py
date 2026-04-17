"""Local-disk WebDAV fallback for paths outside the Moon+ cache virtual tree.

Moon+ Reader syncs non-position files (settings, bookmark lists, theme data)
alongside `.po` files. spacebee stores those on disk under `PASSTHROUGH_ROOT` and
serves them back over DAV. Eventually, annotation-bearing files here may be
routed to `margin.at` records — out of scope for v1.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)


class Passthrough:
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        # Skeleton dirs Moon+ Reader expects so PROPFIND on ancestors succeeds.
        # `Cache` is a placeholder — real requests under it route to bookhive
        # (see moonreader.under_prefix in router.py); this empty dir only
        # exists so it appears when Moon+ Reader lists `/Books/.Moon+/`.
        for sub in ("Books", "Books/.Moon+", "Books/.Moon+/Settings", "Books/.Moon+/Cache"):
            (self._root / sub).mkdir(parents=True, exist_ok=True)

    def _local(self, path: str) -> Path:
        """Resolve a WebDAV path to a local Path, refusing escapes."""
        rel = path.lstrip("/")
        candidate = (self._root / rel).resolve()
        # Directory traversal guard
        if not str(candidate).startswith(str(self._root)):
            raise PermissionError(f"path escapes passthrough root: {path!r}")
        return candidate

    # -- verbs -------------------------------------------------------------

    async def propfind(
        self, path: str, depth: str
    ) -> tuple[int, bytes, dict[str, str]]:
        target = self._local(path)
        if not target.exists():
            return 404, b"", {}

        responses: list[str] = [_entry_xml(target, path, self._root)]
        if target.is_dir() and depth != "0":
            for child in sorted(target.iterdir()):
                child_href = path.rstrip("/") + "/" + child.name
                if child.is_dir():
                    child_href += "/"
                responses.append(_entry_xml(child, child_href, self._root))

        body = _multistatus(responses)
        return 207, body, {"Content-Type": "application/xml; charset=utf-8"}

    async def get(
        self, path: str, *, head: bool = False
    ) -> tuple[int, bytes, dict[str, str]]:
        target = self._local(path)
        if not target.is_file():
            return 404, b"", {}
        data = target.read_bytes()
        mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=UTC)
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
            "Last-Modified": format_datetime(mtime, usegmt=True),
        }
        return 200, (b"" if head else data), headers

    async def put(
        self, path: str, body: bytes
    ) -> tuple[int, bytes, dict[str, str]]:
        target = self._local(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        new = not target.exists()
        target.write_bytes(body)
        return (201 if new else 204), b"", {}

    async def delete(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        target = self._local(path)
        if not target.exists():
            return 404, b"", {}
        if target.is_dir():
            _rmtree(target)
        else:
            target.unlink()
        return 204, b"", {}

    async def mkcol(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        target = self._local(path)
        if target.exists():
            return 405, b"", {}
        target.mkdir(parents=True)
        return 201, b"", {}


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _entry_xml(local: Path, href: str, root: Path) -> str:
    is_dir = local.is_dir()
    resourcetype = "<D:collection/>" if is_dir else ""
    mtime = datetime.fromtimestamp(local.stat().st_mtime, tz=UTC)
    lastmod = format_datetime(mtime, usegmt=True)
    size_xml = ""
    ct_xml = ""
    if not is_dir:
        size = local.stat().st_size
        size_xml = f"<D:getcontentlength>{size}</D:getcontentlength>"
        ct_xml = "<D:getcontenttype>application/octet-stream</D:getcontenttype>"
    return (
        "<D:response>"
        f"<D:href>{quote(href)}</D:href>"
        "<D:propstat>"
        "<D:prop>"
        f"<D:resourcetype>{resourcetype}</D:resourcetype>"
        f"<D:displayname>{local.name or 'root'}</D:displayname>"
        f"<D:getlastmodified>{lastmod}</D:getlastmodified>"
        f"{size_xml}{ct_xml}"
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


def _rmtree(p: Path) -> None:
    for child in p.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink()
    p.rmdir()
