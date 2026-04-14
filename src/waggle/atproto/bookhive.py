"""Translation layer between Moon+ Reader `.po` files and buzz.bookhive.book records.

The parsing / matching helpers are lifted (with light edits) from
`../moon2hive/moon2hive.py`. The ATProto calls are rewritten to go through
`ATProtoClient` instead of the old sync `requests` path.

Namespace contract:

    bookProgress.moonReader.{position, file, syncedAt}

`moonReader.position` is the raw `.po` content byte-for-byte. We preserve it on
write so GETs (which return this verbatim) continue to produce a file Moon+
Reader recognizes — including its internal `timestamp_ms` which is the ebook
file's import mtime, not a real timestamp. See the moon2hive CLAUDE.md for the
full story.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .client import ATProtoClient

log = logging.getLogger(__name__)

BOOKHIVE_COLLECTION = "buzz.bookhive.book"
BOOKHIVE_CATALOG_URL = "https://bookhive.buzz/xrpc/buzz.bookhive.searchBooks"

# Records cache: single-user waggle, so a module-level dict suffices.
_RECORDS_CACHE: dict[str, tuple[float, list[dict]]] = {}
RECORDS_TTL = 30.0


# ---------------------------------------------------------------------------
# `.po` parse / serialize
# ---------------------------------------------------------------------------

PO_PATTERN = re.compile(r"^(\d+)\*(\d+)@(\d+)#(\d+):([\d.]+)%$")


@dataclass
class ReadingProgress:
    timestamp_ms: int
    chapter: int        # 0-indexed (Moon+ convention)
    volume: int
    char_offset: int
    percentage: float
    raw: str


def parse_po(content: str) -> ReadingProgress | None:
    m = PO_PATTERN.match(content.strip())
    if not m:
        log.warning("Could not parse .po content: %r", content)
        return None
    return ReadingProgress(
        timestamp_ms=int(m.group(1)),
        chapter=int(m.group(2)),
        volume=int(m.group(3)),
        char_offset=int(m.group(4)),
        percentage=float(m.group(5)),
        raw=content.strip(),
    )


def serialize_po(record_value: dict) -> str:
    """Produce the .po bytes for a GET response.

    Prefers the stored raw `moonReader.position` (so the internal timestamp and
    char offset Moon+ Reader wrote last time round-trip perfectly). Falls back
    to a minimal synthesis for records that were never touched by Moon+ Reader
    but have bookhive progress (e.g. edited in the bookhive UI).
    """
    bp = record_value.get("bookProgress") or {}
    moon = bp.get("moonReader") or {}
    raw = moon.get("position")
    if raw:
        return raw

    # Synthesize. Moon+ Reader tolerates timestamp_ms=0 and char_offset=0.
    percent = float(bp.get("percent", 0))
    chapter_1idx = int(bp.get("currentChapter", 1))
    chapter = max(0, chapter_1idx - 1)
    return f"0*{chapter}@0#0:{percent:.1f}%"


# ---------------------------------------------------------------------------
# Filename parsing and fuzzy matching
# ---------------------------------------------------------------------------

FORMAT_EXTS = {
    ".epub", ".mobi", ".azw3", ".azw", ".pdf",
    ".fb2", ".djvu", ".cbz", ".cbr", ".txt",
}


def parse_filename(filename: str) -> tuple[str, str]:
    """Extract (part_a, part_b) — usually (title, author) — from a Moon+ filename.

    Ordering isn't guaranteed (both `Title - Author.epub.po` and
    `Author - Title.epub.po` appear in the wild), so callers should try both.
    """
    name = filename
    if name.endswith(".po"):
        name = name[:-3]
    for ext in FORMAT_EXTS:
        if name.endswith(ext):
            name = name[: -len(ext)]
            break

    # Strip series prefix like "(Dungeon Crawler Carl 1) "
    name = re.sub(r"^\([^)]+\)\s*", "", name)

    parts = name.split(" - ")
    if len(parts) >= 2:
        part_b = parts[-1].strip()
        part_a = " - ".join(parts[:-1]).strip()
    else:
        part_a = name.strip()
        part_b = ""

    # Moon+ Reader replaces ":" with "_" in filenames
    part_a = part_a.replace("_", ": ").replace(":  ", ": ")
    part_b = part_b.replace("_", ": ").replace(":  ", ": ")

    # "Last, First" → "First Last"
    if "," in part_b and part_b.count(",") == 1:
        last, first = part_b.split(",", 1)
        part_b = f"{first.strip()} {last.strip()}"

    # "Thing, The" → "The Thing"
    if part_a.endswith(", The"):
        part_a = "The " + part_a[:-5]
    elif part_a.endswith(", A"):
        part_a = "A " + part_a[:-3]

    # Strip trailing digits that are truncation artifacts ("Esthe13")
    part_a = re.sub(r"\d+$", "", part_a).strip()
    part_b = re.sub(r"\d+$", "", part_b).strip()

    return part_a, part_b


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article):]
            break
    return text


def _word_set(text: str) -> set[str]:
    return {w for w in _normalize(text).split() if len(w) > 2}


def _title_similarity(a: str, b: str) -> float:
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _author_matches(a: str, b: str) -> bool:
    if not a or not b:
        return False
    na, nb = _normalize(a), _normalize(b)
    sa = na.split()[-1] if na.split() else ""
    sb = nb.split()[-1] if nb.split() else ""
    return bool(sa) and len(sa) > 2 and (sa in nb or sb in na)


def match_record(
    part_a: str, part_b: str, records: list[dict]
) -> dict | None:
    """Best-effort fuzzy match. Tries both (title,author) orderings."""
    best: dict | None = None
    best_score = 0.0
    for record in records:
        v = record["value"]
        rec_title = v.get("title", "")
        rec_authors = v.get("authors", "")
        candidates = [
            (_title_similarity(part_a, rec_title), _author_matches(part_b, rec_authors)),
            (_title_similarity(part_b, rec_title), _author_matches(part_a, rec_authors)),
        ]
        for score, auth_ok in candidates:
            if score > best_score and ((auth_ok and score > 0.6) or score > 0.85):
                best_score = score
                best = record
    if best:
        log.info(
            "Fuzzy-matched %r → %r (score=%.2f)",
            f"{part_a} / {part_b}", best["value"].get("title"), best_score,
        )
    return best


# ---------------------------------------------------------------------------
# ATProto record operations
# ---------------------------------------------------------------------------

async def list_records(client: ATProtoClient, *, use_cache: bool = True) -> list[dict]:
    """Fetch all buzz.bookhive.book records for the authed user. 30s cached."""
    did = await client.did()
    now = time.time()
    if use_cache:
        cached = _RECORDS_CACHE.get(did)
        if cached and now - cached[0] < RECORDS_TTL:
            return cached[1]

    records: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict = {
            "repo": did,
            "collection": BOOKHIVE_COLLECTION,
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        resp = await client.request("GET", "com.atproto.repo.listRecords", params=params)
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        cursor = data.get("cursor")
        if not cursor:
            break

    _RECORDS_CACHE[did] = (now, records)
    log.debug("Cached %d bookhive records for %s", len(records), did)
    return records


def invalidate_cache(did: str | None = None) -> None:
    if did is None:
        _RECORDS_CACHE.clear()
    else:
        _RECORDS_CACHE.pop(did, None)


def find_by_moon_filename(records: list[dict], filename: str) -> dict | None:
    """Direct lookup: stored moonReader.file == filename."""
    for r in records:
        moon = (r["value"].get("bookProgress") or {}).get("moonReader") or {}
        if moon.get("file") == filename:
            return r
    return None


async def resolve_record(
    client: ATProtoClient, filename: str
) -> dict | None:
    """Find the bookhive record that corresponds to a Moon+ filename.

    First: exact match on stored `moonReader.file` (fast path, survives renames
    of the bookhive record's title). Fallback: fuzzy match on title/author
    parsed from filename.
    """
    records = await list_records(client)
    direct = find_by_moon_filename(records, filename)
    if direct:
        return direct
    part_a, part_b = parse_filename(filename)
    return match_record(part_a, part_b, records)


async def search_catalog(client: ATProtoClient, query: str) -> dict | None:
    """Search bookhive.buzz's catalog for a book by title. Returns top hit."""
    resp = await client.http.get(BOOKHIVE_CATALOG_URL, params={"q": query, "limit": 5})
    resp.raise_for_status()
    books = resp.json().get("books", [])
    return books[0] if books else None


async def upload_cover(client: ATProtoClient, cover_url: str) -> dict | None:
    """Download a cover image URL and upload as a PDS blob. Returns blob ref."""
    try:
        img = await client.http.get(cover_url)
        img.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("Failed to download cover %s: %s", cover_url, e)
        return None
    content_type = img.headers.get("Content-Type", "image/jpeg")
    if "image" not in content_type:
        content_type = "image/jpeg"
    resp = await client.request(
        "POST", "com.atproto.repo.uploadBlob",
        content=img.content, headers={"Content-Type": content_type},
    )
    if resp.status_code >= 400:
        log.warning("Failed to upload cover blob: %s", resp.text)
        return None
    return resp.json().get("blob")


# ---------------------------------------------------------------------------
# Write path (PUT /...po → updated record on PDS)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _build_moon_reader(progress: ReadingProgress, filename: str, now: str) -> dict:
    return {"position": progress.raw, "file": filename, "syncedAt": now}


def _merge_progress(
    existing_value: dict, progress: ReadingProgress, filename: str
) -> tuple[dict, bool]:
    """Return (new_value, changed). Skips the write if nothing moved."""
    value = dict(existing_value)
    now = _now_iso()

    existing_bp = value.get("bookProgress") or {}
    existing_moon = existing_bp.get("moonReader") or {}

    unchanged = (
        existing_moon.get("position") == progress.raw
        and existing_moon.get("file") == filename
        and existing_bp.get("percent") == int(progress.percentage)
    )
    if unchanged and value.get("status") == "buzz.bookhive.defs#reading":
        return value, False

    # Respect "finished" — don't flip completed books back to reading.
    if value.get("status") != "buzz.bookhive.defs#finished":
        value["status"] = "buzz.bookhive.defs#reading"
        if not value.get("startedAt"):
            value["startedAt"] = now

    value["bookProgress"] = {
        "percent": int(progress.percentage),
        "currentChapter": progress.chapter + 1,
        "updatedAt": now,
        "moonReader": _build_moon_reader(progress, filename, now),
    }
    return value, True


def _raise_with_body(resp: httpx.Response, value: dict, op: str) -> None:
    """raise_for_status, but log the PDS error body + payload first.

    Why: the PDS returns 400 with a JSON body that names which lexicon field
    failed validation; httpx's default exception swallows it.
    """
    if resp.status_code >= 400:
        log.error(
            "PDS %s rejected (%d): %s\nrecord we sent: %s",
            op, resp.status_code, resp.text, value,
        )
    resp.raise_for_status()


async def put_record(client: ATProtoClient, rkey: str, value: dict) -> None:
    did = await client.did()
    resp = await client.request(
        "POST", "com.atproto.repo.putRecord",
        json={
            "repo": did,
            "collection": BOOKHIVE_COLLECTION,
            "rkey": rkey,
            "record": value,
        },
    )
    _raise_with_body(resp, value, f"putRecord rkey={rkey}")
    invalidate_cache(did)


async def create_record(client: ATProtoClient, value: dict) -> str:
    did = await client.did()
    resp = await client.request(
        "POST", "com.atproto.repo.createRecord",
        json={"repo": did, "collection": BOOKHIVE_COLLECTION, "record": value},
    )
    _raise_with_body(resp, value, "createRecord")
    invalidate_cache(did)
    uri = resp.json().get("uri", "")
    return uri.rsplit("/", 1)[-1]


async def apply_po_put(
    client: ATProtoClient, filename: str, body: bytes
) -> str:
    """Full inbound PUT flow: parse → resolve/create record → update bookProgress.

    Returns a short human status string for logging.
    """
    content = body.decode("utf-8", errors="replace").strip()
    progress = parse_po(content)
    if not progress:
        return f"ignored unparsable .po ({len(body)} bytes)"

    record = await resolve_record(client, filename)
    if record:
        rkey = record["uri"].rsplit("/", 1)[-1]
        new_value, changed = _merge_progress(record["value"], progress, filename)
        if not changed:
            return f"no-op (already at {int(progress.percentage)}%)"
        await put_record(client, rkey, new_value)
        return f"updated {record['value'].get('title')!r} → {int(progress.percentage)}%"

    # Unknown book — try the bookhive catalog.
    part_a, part_b = parse_filename(filename)
    catalog = await search_catalog(client, part_a)
    if not catalog and part_b:
        catalog = await search_catalog(client, part_b)
    if not catalog:
        log.warning("No bookhive catalog match for %r; dropping PUT", filename)
        return f"dropped (no catalog match for {part_a!r})"

    now = _now_iso()
    hive_id = catalog.get("id", "")
    value: dict = {
        "$type": BOOKHIVE_COLLECTION,
        "title": catalog.get("title", part_a or "Unknown"),
        "authors": catalog.get("authors", part_b or "Unknown"),
        "hiveId": hive_id,
        "createdAt": now,
        "startedAt": now,
        "status": "buzz.bookhive.defs#reading",
        "owned": True,
        "bookProgress": {
            "percent": int(progress.percentage),
            "currentChapter": progress.chapter + 1,
            "updatedAt": now,
            "moonReader": _build_moon_reader(progress, filename, now),
        },
    }
    if catalog.get("identifiers"):
        value["identifiers"] = catalog["identifiers"]
    if hive_id:
        value["hiveBookUri"] = (
            f"at://did:plc:enu2j5xjlqsjaylv3du4myh4/buzz.bookhive.catalogBook/{hive_id}"
        )
    cover_url = catalog.get("cover")
    if cover_url:
        blob = await upload_cover(client, cover_url)
        if blob:
            value["cover"] = blob

    rkey = await create_record(client, value)
    return f"created {value['title']!r} at {int(progress.percentage)}% (rkey={rkey})"


async def apply_po_delete(client: ATProtoClient, filename: str) -> str:
    """DELETE /...po — clear only the moonReader sub-object; leave record intact."""
    record = await resolve_record(client, filename)
    if not record:
        return "no-op (no matching record)"
    value = dict(record["value"])
    bp = dict(value.get("bookProgress") or {})
    if "moonReader" not in bp:
        return "no-op (no moonReader data)"
    bp.pop("moonReader", None)
    if bp:
        value["bookProgress"] = bp
    else:
        value.pop("bookProgress", None)
    rkey = record["uri"].rsplit("/", 1)[-1]
    await put_record(client, rkey, value)
    return f"cleared moonReader on {value.get('title')!r}"
