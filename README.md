# waggle

A WebDAV shim that impersonates a cloud sync backend for [Moon+ Reader] and
translates its `.po` position files into ATProto reads/writes on your
[bookhive.buzz] book records.

Point Moon+ Reader at `https://waggle.yourdomain/` (Basic auth). Open a book,
read for a while, pause the app — your `bookProgress` on your PDS updates. Flip
to `bookhive.buzz` and the progress is already there. Flip between devices and
waggle hands each one the current state.

## How it works

```
MoonReader ─[WebDAV: PROPFIND / GET / PUT]──→ waggle ──→ ATProto PDS
                                                         (buzz.bookhive.book)
```

- `PROPFIND /Books/.Moon+/Cache/` synthesizes a directory listing from your
  `buzz.bookhive.book` records that have a `bookProgress.moonReader.file` field.
- `GET /Books/.Moon+/Cache/{file}.po` returns the stored position string
  verbatim (preserves Moon+ Reader's internal chapter/offset encoding).
- `PUT /Books/.Moon+/Cache/{file}.po` parses the `.po` body, finds the matching
  bookhive record (or catalog-searches and creates one), and updates
  `bookProgress.{percent,currentChapter,moonReader}` on your PDS.

Non-position WebDAV paths (`Books/.Moon+/Settings/`, etc.) fall through to a
local-disk scratch area rooted at `$PASSTHROUGH_ROOT`. Those files are not
synced anywhere — they just keep Moon+ Reader happy.

## Running locally

```sh
cp .env.example .env    # fill in PDS, handle, app password, DAV creds
uv run uvicorn waggle.main:app --reload --port 8080
```

Point a test device at `http://<your-laptop>:8080/` as the WebDAV target.

## Deploying

Single Docker container. `docker-compose.yml` is CasaOS-shaped; put waggle
behind a reverse proxy with HTTPS (Moon+ Reader requires TLS for WebDAV).

## Related

- [`../moon2hive`](../moon2hive) — the previous one-way cron script this
  replaces.
- [`bookhive.buzz`](https://bookhive.buzz) — the AT Protocol book tracker
  whose records waggle reads and writes.

[Moon+ Reader]: https://www.moondownload.com/
[bookhive.buzz]: https://bookhive.buzz/
