# spacebee

A WebDAV shim that impersonates a cloud sync backend for [Moon+ Reader] and
translates its `.po` position files into ATProto reads/writes on your
[bookhive.buzz] book records.

Point Moon+ Reader at `https://spacebee.yourdomain/` (Basic auth). Open a
book, read for a while, pause the app — your `bookProgress` on your PDS
updates. Flip to `bookhive.buzz` and the progress is already there. Flip
between devices and spacebee hands each one the current state.

## How it works

```
MoonReader ─[WebDAV: PROPFIND / GET / PUT]──→ spacebee ──→ ATProto PDS
                                                           (buzz.bookhive.book)
```

- `PROPFIND /Books/.Moon+/Cache/` synthesizes a directory listing from your
  `buzz.bookhive.book` records that have a `bookProgress.moonReader.file`
  field.
- `GET /Books/.Moon+/Cache/{file}.po` returns the stored position string
  verbatim (preserves Moon+ Reader's internal chapter/offset encoding).
- `PUT /Books/.Moon+/Cache/{file}.po` parses the `.po` body, finds the
  matching bookhive record (or catalog-searches and creates one), and
  updates `bookProgress.{percent,currentChapter,moonReader}` on your PDS.

Non-position WebDAV paths (`Books/.Moon+/Settings/`, etc.) fall through to a
local-disk scratch area rooted at `$PASSTHROUGH_ROOT`. Those files are not
synced anywhere — they just keep Moon+ Reader happy.

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
| `PDS` | *Optional.* PDS host (e.g. `bsky.social`). If unset, resolved from the handle at first use via the public bsky appview + PLC directory. |
| `DAV_USER` / `DAV_PASSWORD` | Basic-auth credentials Moon+ Reader will send |
| `PASSTHROUGH_ROOT` | Local-disk scratch dir for non-`.po` paths |

## Running locally

```sh
cp .env.example .env
uv sync --extra dev
uv run uvicorn spacebee.main:app --reload --port 8080
```

Point a test device at `http://<your-laptop>:8080/` as the WebDAV target.

## Deploying

Single Docker container. Build locally or pull an image you've pushed to a
registry, then:

```sh
IMAGE=ghcr.io/you/spacebee:latest docker compose up -d
```

The provided `docker-compose.yml` takes the image tag from the `IMAGE`
environment variable and defaults to `spacebee:latest` (expects a local
build). Put behind a reverse proxy with HTTPS — Moon+ Reader requires TLS
for WebDAV.

## CI

`.forgejo/workflows/ci.yml` runs lint + tests on every push and PR. On
pushes to `main` it will also build and push a Docker image, **but only if
`REGISTRY` is set as a repo/org variable**. To enable image publishing,
configure:

- Repo/org vars: `REGISTRY` (e.g. `ghcr.io`), `IMAGE_NAME` (e.g. `you/spacebee`)
- Repo/org secrets: `REGISTRY_USER`, `REGISTRY_TOKEN`

If `REGISTRY` is unset, the build job is skipped and the workflow is
test-only — safe to fork without needing any registry credentials.

## Related

- [`bookhive.buzz`] — the AT Protocol book tracker whose records spacebee
  reads and writes.

[Moon+ Reader]: https://www.moondownload.com/
[bookhive.buzz]: https://bookhive.buzz/
[app password]: https://bsky.app/settings/app-passwords
