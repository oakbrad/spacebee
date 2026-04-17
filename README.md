# spacebee

A WebDAV shim for syncing [Moon+ Reader](https://www.moondownload.com) reading position. Translates
the `.po` position files into ATProto reads/writes on your
[bookhive.buzz](https://bookhive.buzz) book records. 

```json
{
  "$type": "buzz.bookhive.book",
  "bookProgress": {
    "percent": 11,
    "updatedAt": "2026-04-17T10:01:24.000Z",
    "moonReader": {
      "file": "The Necromancers House - Buehlman Christopher.epub.po",
      "position": "1703297605115*21@0#4826:11.1%",
      "syncedAt": "2026-04-17T10:01:24.000Z"
    },
    "currentChapter": 22
  }
}
```

Also serves a static page showing your reading progress.

> 🚨 *This is vibecoded, I don't know what I'm doing but it works 👍️*

## How it works

```mermaid
graph LR
    MR[Moon+ Reader] -->|"WebDAV: PROPFIND / GET / PUT"| SB[spacebee]
    SB -->|"buzz.bookhive.book"| PDS[ATProto PDS]
```

- `PROPFIND /Books/.Moon+/Cache/` synthesizes a directory listing from your
  `buzz.bookhive.book` records that have a `bookProgress.moonReader.file`
  field.
- `GET /Books/.Moon+/Cache/{file}.po` returns the stored position string
  verbatim (preserves Moon+ Reader's internal chapter/offset encoding).
- `PUT /Books/.Moon+/Cache/{file}.po` parses the `.po` body, finds the
  matching bookhive record (or catalog-searches and creates one), and
  updates `bookProgress.{percent,currentChapter,moonReader}` on your PDS.

Non-position WebDAV paths (`Books/.Moon+/Settings/`, backups, etc.) fall through to a local-disk scratch area rooted at `$PASSTHROUGH_ROOT`.

spacebee also serves a small read-only HTML dashboard at `/` that renders
your bookhive records (currently-reading, finished, etc.). The dashboard and
cover-image blob proxy at `/blob/{cid}` are public; all WebDAV endpoints are
gated by HTTP Basic.

## Configuration

Copy `.env.example` to `.env` and fill in:

| Var | Purpose |
| --- | --- |
| `BSKY_HANDLE` | The handle spacebee writes records as |
| `BSKY_APP_PASSWORD` | An [app password] for that handle |
| `DAV_USER` / `DAV_PASSWORD` | Basic-auth credentials Moon+ Reader will send |
| `PASSTHROUGH_ROOT` | Local-disk scratch dir for non-`.po` paths |
| `PDS` | *Optional.*  If unset, resolved from the handle. |

## Running locally

```sh
cp .env.example .env
uv sync --extra dev
uv run uvicorn spacebee.main:app --reload --port 8080
```

Point a test device at `http://<your-laptop>:8080/` as the WebDAV target.

## Related

- [bookhive.buzz](https://bookhive.buzz) — the AT Protocol book tracker whose records spacebee
  reads and writes.
- [Moon+ Reader](https://www.moondownload.com/) - Android eReader app