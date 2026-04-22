# gdrive-rag

A Go service that indexes a whitelist of Google Drive folders into an embedded
vector store (chromem-go) and exposes a search surface over HTTP + MCP. It
runs as a standalone container on rpi5 alongside the other iot-fetcher
services, and is reachable from Claude Code over the Model Context Protocol.

## How it works

```
Drive (changes.list)
    │
    ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  sync loop   │──▶│  extractor   │──▶│   chunker    │──▶│   embedder   │
│ (tick/back-  │   │ (PDF parse / │   │ (~800 tokens │   │ (Gemini      │
│  fill +      │   │  Gemini Flash│   │  + overlap)  │   │  embeddings) │
│  queue)      │   │  OCR fallback│   │              │   │              │
└──────────────┘   └──────────────┘   └──────────────┘   └──────┬───────┘
                                                                ▼
                                                        ┌──────────────┐
                                                        │   chromem-go │
                                                        │  (on /data)  │
                                                        └──────┬───────┘
                                                                ▼
                                              HTTP  /query    MCP  search_drive
                                              HTTP  /status   MCP  status
                                              HTTP  /reindex  MCP  reindex
```

- The **sync loop** periodically calls Drive `changes.list` (bootstrapping a
  page token and doing an initial backfill on first run), enqueueing any file
  under a whitelisted folder for indexing.
- The **extractor** turns a Drive file into UTF-8 text: text/* is passed
  through, PDFs with a text layer are parsed pure-Go, scanned PDFs and images
  fall back to Gemini Flash-Lite OCR.
- The **chunker** splits text into overlapping ~800-token windows.
- The **embedder** batches chunks into Gemini embedding requests under a
  token-per-minute budget.
- The **store** (chromem-go) persists chunks + embeddings under `/data/chromem`.
- The **sync state**, **ingest queue**, and per-day budget counters are
  persisted in `/data/state.json` and `/data/ingest_queue.json`.

Daily caps on Flash requests are tracked against Pacific midnight (Gemini's
rollover); on `ErrDailyBudgetExhausted` the loop sleeps until 00:05 PT and
resumes.

## Setup

### 1. Google service account

Create a service account with **Drive read-only** scope:

1. In Google Cloud Console → IAM & Admin → Service Accounts → create one.
2. Grant it no project roles; this service only needs Drive API access.
3. Create a JSON key and copy the contents — that's `GOOGLE_SERVICE_ACCOUNT`.
4. Share every root folder you want indexed with the service account's email
   (Viewer permission is enough).

### 2. Gemini API key

Create an API key at <https://aistudio.google.com/apikey>. It's used for both
embeddings (`gemini-embedding-001`) and Flash OCR
(`gemini-2.5-flash-lite`). The free tier is sufficient for typical iot-fetcher
use, provided the default budget caps are respected.

### 3. Folder whitelist

Collect the Drive folder IDs you want indexed (the 33-char blob in
`https://drive.google.com/drive/folders/<ID>`). Comma-separate them into
`RAG_ROOT_FOLDER_IDS`. Only files under those folders (at any depth) are
indexed.

## Configuration

All configuration is via environment variables; see
[`.env.template`](./.env.template) for the canonical list and defaults.

**Required**:

| Variable | Description |
|----------|-------------|
| `GOOGLE_SERVICE_ACCOUNT` | Full service-account JSON (single-line or quoted) |
| `GEMINI_API_KEY` | Gemini API key |
| `RAG_ROOT_FOLDER_IDS` | Comma-separated Drive folder IDs |

**Optional** (defaults shown):

| Variable | Default | Purpose |
|----------|---------|---------|
| `RAG_EMBED_MODEL` | `gemini-embedding-001` | Embedding model |
| `RAG_EXTRACT_MODEL` | `gemini-2.5-flash-lite` | Flash OCR / extraction model |
| `RAG_SYNC_INTERVAL` | `10m` | How often to run `changes.list` |
| `RAG_CHUNK_TOKENS` | `800` | Target chunk size |
| `RAG_CHUNK_OVERLAP` | `100` | Tokens of overlap between chunks |
| `RAG_EMBED_BATCH_SIZE` | `25` | Chunks per embedding request |
| `RAG_LISTEN_ADDR` | `:8090` | HTTP listen address |
| `RAG_DATA_DIR` | `/data` | Where state, queue, and chromem live |
| `RAG_LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |
| `RAG_EMBED_TPM_CAP` | `200000` | Embedding tokens-per-minute cap |
| `RAG_FLASH_RPM_CAP` | `10` | Flash requests per minute |
| `RAG_FLASH_DAILY_REQUEST_CAP` | `800` | Flash requests per Pacific day |
| `RAG_MAX_FILE_SIZE_MB` | `50` | Skip files larger than this |
| `RAG_MAX_PDF_PAGES` | `500` | Hard ceiling on PDF length |
| `RAG_PDF_MAX_PAGES_PER_CALL` | `20` | Pages per Flash OCR request |
| `RAG_PDF_TEXT_LAYER_MIN_CHARS_PER_PAGE` | `100` | Below this → scanned PDF |
| `RAG_SKIP_IMAGE_EXTRACTION` | `false` | Set `true` to disable image OCR |

Budget defaults are sized below Gemini April-2026 free-tier limits. Raise
them only after observing real consumption via `/status`.

## Running locally

Copy `.env.template` to `gdrive-rag/.env` and fill in the required values,
then bring it up with the local compose overlay (exposes port 8090 to the
host):

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d gdrive-rag
docker logs -f gdrive-rag
```

Smoke test:

```bash
curl -s http://localhost:8090/healthz
# ok

curl -s http://localhost:8090/status | jq
# {
#   "last_sync": "...",
#   "document_count": 0,
#   ...
# }

curl -s -X POST http://localhost:8090/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"heat pump maintenance","top_k":5}' | jq

# Kick off a manual crawl (optional; fire-and-forget, returns 202):
curl -s -X POST http://localhost:8090/reindex \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## Connecting from Claude Code

Once the container is up on your workstation or rpi5, register the MCP server
with Claude Code:

```bash
# Local dev (Mac running the service):
claude mcp add --transport http gdrive-rag http://localhost:8090/mcp

# rpi5 deployment:
claude mcp add --transport http gdrive-rag http://rpi5.local:8090/mcp
```

The server exposes three tools:

- `search_drive(query, top_k?, folder?, mime?)` — semantic search; returns
  top-k chunks with Drive links.
- `status()` — current sync/index status + budget usage.
- `reindex(folder?)` — manually enqueue every file under a folder (or every
  whitelisted folder); fire-and-forget.

## Deployment to rpi5

```bash
# From the repo root:
make -C gdrive-rag push          # build + push the image to Artifact Registry
ssh rpi5 'sudo docker compose -f docker-compose.yml -f docker-compose.local.yml pull gdrive-rag'
ssh rpi5 'sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d gdrive-rag'
```

The service is reachable at `http://rpi5.local:8090` (local only; not
proxied through Caddy). State and the chromem DB live in the
`gdrive-rag-data` named volume, so upgrading the image preserves the index.
