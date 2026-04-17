# spacebee

WebDAV shim that impersonates Moon+ Reader's sync backend and translates
requests into ATProto reads/writes on `buzz.bookhive.book` records. Protocol
adapter, not a poller — every inbound DAV request is a round-trip to the PDS.

## Shape

```
MoonReader ─[WebDAV: PROPFIND/GET/PUT/HEAD/OPTIONS]──→ spacebee ──→ ATProto PDS
Passthrough (Settings/, etc.) ─────────────────→ local disk at $PASSTHROUGH_ROOT
```

- `/Books/.Moon+/Cache/**` is **virtual** — served from bookhive records. No
  disk backing. Synthesizing the listing comes from `listRecords` on
  `buzz.bookhive.book`; each record with a `bookProgress.moonReader.file`
  appears as a `.po`.
- Every other path hits `Passthrough` over a local filesystem. Annotation
  routing lives outside v1.

## Design constraints (don't re-derive)

- **Namespace**: all our extensions live under `bookProgress.moonReader.*` —
  never flatten into top-level lexicon fields. Same pattern leaves room to
  add other reader namespaces (`bookProgress.koReader.*`, etc.) without
  collision.
- **`moonReader.position` is stored verbatim**. On GET we return it unchanged
  so Moon+ Reader's internal `timestamp_ms` (the ebook's import mtime) stays
  stable. Re-synthesizing would trip Moon+ into thinking the position
  changed.
- **PUT is idempotent**: if the incoming position + filename match what's on
  the record, skip the write. Moon+ Reader PUTs on every pause event and
  most are no-ops.
- **Finished books don't flip back to reading**: if `status` is
  `buzz.bookhive.defs#finished`, a PUT updates the bookProgress but keeps
  the status.
- **Filename = book identity for Moon+**. `moonReader.file` is the key we
  look up by. If `resolve_record()` misses, fall back to fuzzy
  title/author match, then catalog-search-and-create.

## Single-user

One atproto identity in env. `DAV_USER` / `DAV_PASSWORD` are a single shared
credential — just a gate on the service, not a multi-tenant mapping.

## Running

```sh
cp .env.example .env   # fill in creds
uv sync --extra dev
uv run uvicorn spacebee.main:app --reload --port 8080
uv run pytest -q
uv run ruff check src tests
```

## Deploy

Single Docker container. The published `docker-compose.yml` takes the image
tag from the `IMAGE` env var (defaults to `spacebee:latest`). Mount
`./data:/data` for the passthrough scratch area. Put behind HTTPS reverse
proxy — Moon+ Reader requires TLS for WebDAV.

CI: `.forgejo/workflows/ci.yml` runs tests on every push/PR and builds +
pushes an image on `main` **only if** the `REGISTRY` repo var is set. Configure
`REGISTRY` + `IMAGE_NAME` as repo/org vars and `REGISTRY_USER` +
`REGISTRY_TOKEN` as secrets to enable image publishing; otherwise the job
skips cleanly.

## Related

- `bookhive.buzz` — the AT Protocol book tracker that renders the records
  spacebee writes.
