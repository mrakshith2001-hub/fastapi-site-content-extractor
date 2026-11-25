# Site Content Extractor

A FastAPI service that ingests a static website from S3-compatible storage, works out which files
were created, updated or deleted, renders the affected pages in headless Chromium, and stores the
meaningful rendered text in SQL. Only pages that actually depend on a changed file are re-rendered.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design, the change and deletion detection
strategy, and the trade-offs.

## Features

| Requirement | Where |
|---|---|
| Async S3 ingestion (aioboto3, paginated) | `app/services/storage.py` |
| Created / updated / deleted detection, 24 h window | `app/services/change_detection.py` |
| Idempotent re-runs (inventory + unique change log) | `app/services/ingestion.py`, `file_events` table |
| Deletion detection (inventory diff + S3 delete markers) | `detect_changes`, `S3Storage.latest_versions` |
| Website reconstruction (local mirror) | `app/services/mirror.py` |
| Headless rendering, JS, lazy content, scroll containers, popups | `app/services/renderer.py` |
| Clean text extraction (no scripts, styles, nav, buttons, forms) | `EXTRACT_BLOCKS_JS` in `renderer.py` |
| Dependency graph (static parse + observed browser requests) | `dependencies.py`, `page_dependencies` table |
| Selective reprocessing | `app/services/impact.py` |
| Structured JSON logging with request ids | `app/core/logging.py`, `app/main.py` |
| Alembic migrations | `migrations/` |
| Unit and integration tests (moto S3 + real Chromium) | `tests/` |

## API

| Method | Path | Description |
|---|---|---|
| POST | `/ingestion/run` | Trigger S3 ingestion. Optional body: `{"lookback_hours": 24}` |
| GET | `/ingestion/runs` | Recent runs with their summaries |
| GET | `/files` | Ingested files. Filters: `operation`, `asset_type`, `include_deleted`, `limit`, `offset` |
| GET | `/pages` | Extracted pages. `include_deleted=true` also shows retired pages |
| GET | `/pages/{page_id}` | Extracted content, hash, version and dependency list |
| POST | `/pages/{page_id}/reprocess` | Re-render a single page on demand |
| GET | `/health` | Liveness check |

Swagger UI is at **http://localhost:8000/docs**.

## Run with Docker Compose

```bash
docker compose up -d --build              # MinIO + PostgreSQL + API (migrations run on start)
docker compose --profile seed run --rm seed   # create the versioned bucket and upload sample_site/
curl -X POST http://localhost:8000/ingestion/run
curl http://localhost:8000/pages
```

The MinIO console is at http://localhost:9001 (user and password are both `minioadmin`).

## Run locally (without Docker for the API)

Requires Python 3.11+ and any S3-compatible endpoint (MinIO, LocalStack or AWS S3).

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
playwright install chromium
cp .env.example .env                                   # point S3_* at your storage

docker compose up -d minio                             # or use your own S3 endpoint
python -m scripts.seed_s3                              # upload the sample site
alembic upgrade head                                   # optional: AUTO_CREATE_SCHEMA=true also works
uvicorn app.main:app --reload
```

### Try the incremental behaviour

```bash
python -m scripts.seed_s3 --touch css/trails.css   # then POST /ingestion/run -> only trails.html re-rendered
python -m scripts.seed_s3 --touch data/season.json # -> only index.html (the only page that fetches it)
python -m scripts.seed_s3 --delete gear.html       # -> gear.html retired, file marked deleted
curl -X POST http://localhost:8000/ingestion/run   # run twice: the second run records nothing
```

## Tests

```bash
pytest            # 21 tests: unit tests + an end-to-end run against a moto S3 server and real Chromium
```

The integration test seeds a versioned bucket, then checks the first full extraction, a no-op
re-run, selective re-rendering after a CSS change and after a JSON data change, deletion handling,
the reprocess endpoint, and the 404 and 410 responses.

## Sample website

`sample_site/` holds five pages for a fictional hiking company. The pages exercise every rendering
requirement:

| Page | What it exercises |
|---|---|
| `index.html` | JS-rendered cards from `data/trails.json`, a lazy section loaded on scroll from `data/season.json`, a safety-briefing modal, images |
| `trails.html` | A fixed-height scrollable list filled by JS, a page-specific `css/trails.css`, a PNG map |
| `guides.html` | Image cards and a training modal |
| `gear.html` | Collapsed `<details>` sections, opened by the extractor |
| `info/contact.html` | A page in a subfolder using `../` asset paths, a cancellation-policy modal, a form (excluded) |

Shared assets: `css/main.css` imports `css/typography.css`, which loads the `fonts/ridgeline-icons.ttf`
icon font. There are also SVG images and `js/site.js`. `scripts/build_sample_assets.py` regenerates
the binary assets.

## Project layout

```
app/
  api/routes/        ingestion, files, pages endpoints
  core/              settings (pydantic-settings), structured logging
  db/                SQLAlchemy 2.0 async models and engine/session helpers
  schemas/           Pydantic response and request models
  services/          storage, change detection, mirror, dependencies, impact, renderer, content, pages, ingestion
migrations/          Alembic environment and initial schema
sample_site/         5-page sample website
scripts/             seed_s3.py, build_sample_assets.py
tests/unit, tests/integration
```
