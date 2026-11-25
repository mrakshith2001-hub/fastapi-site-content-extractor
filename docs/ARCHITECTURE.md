# Architecture

```
S3-compatible bucket (prefix site/)
      | list_objects_v2 (+ list_object_versions when versioning is on)
      v
Change detection  -- compares listing with s3_objects inventory --> created / updated / deleted
      |
      v
Ingestion & metadata -- upsert s3_objects, append file_events (ON CONFLICT DO NOTHING)
      |
      v
Mirror sync -- download created/updated, remove deleted, backfill missing files
      |
      v
Impact planning -- changes x page_dependencies graph --> pages to render / retire
      |
      v
Headless rendering (Playwright Chromium, served from the mirror by request interception)
      |   scroll page, scroll containers, open <details>, click popup triggers
      v
Content extraction -- ordered text blocks + popup sections, SHA-256 hash, version bump if changed
      |
      v
Dependency tracking -- static HTML/CSS parse  UNION  requests observed in the browser
      |
      v
SQL (SQLite or PostgreSQL, async SQLAlchemy) --> FastAPI endpoints
```

## Data model

| Table | Purpose |
|---|---|
| `s3_objects` | Application-side inventory: one row per key with ETag, version id, size, last-modified, `last_operation`, `is_deleted`, `deleted_at` |
| `file_events` | Append-only change log. Unique on `(s3_object_id, operation, fingerprint)`, where fingerprint = `etag \| version_id \| last_modified` |
| `ingestion_runs` | One row per run with status, window and a JSON summary |
| `pages` | Extracted page: path, URL, title, content, `content_hash`, `version`, word count, status (`active` / `deleted` / `failed`) |
| `page_dependencies` | Page-to-asset edges with source `static`, `runtime` or `both` |

## S3 ingestion and change detection

1. The bucket is listed with the `list_objects_v2` paginator, so prefixes of any size work.
2. If bucket versioning is enabled, `list_object_versions` adds the current version id of every key
   and the timestamp of any delete marker.
3. `detect_changes()` is a pure function that compares the listing with the persisted inventory:
   * key unknown, or previously deleted: **created**;
   * version id differs (when both sides have one), otherwise ETag differs: **updated**;
   * key in the inventory but missing from the listing: **deleted**;
   * anything else is unchanged and skipped.
4. The 24-hour window (`LOOKBACK_HOURS`) is recorded on every run. By default the inventory is
   authoritative, so a change that happened 30 hours ago and was missed because a scheduled run did
   not happen is still picked up. Set `STRICT_WINDOW=true` to ignore created/updated objects whose
   `LastModified` falls before the window.

### Idempotency

* Unchanged objects produce no change, no event and no rendering.
* `file_events` has a unique constraint. Writes use `INSERT ... ON CONFLICT DO NOTHING`, so a
  crashed or concurrent re-run cannot record the same change twice.
* Page content is only rewritten, and `version` only incremented, when the content hash changes.
  Re-rendering an unchanged page is harmless.
* An in-process lock stops two ingestion runs, or a run and a reprocess, from overlapping. The API
  returns `409` instead.

## Deletion detection: strategy and trade-offs

The primary strategy is a **persisted application-side inventory**. The inventory is enriched with
**S3 versioning** when the bucket supports it.

| Approach | Pros | Cons |
|---|---|---|
| Persisted inventory diff (used) | Works on every S3-compatible service; needs no bucket configuration; catches deletions however old | Needs a full listing per run (O(objects)); the deletion time is only "detected at" unless versioning is on |
| S3 versioning delete markers (used when available) | Exact deletion timestamp; lets a deletion be checked against the 24 h window | Must be enabled on the bucket; old versions add storage cost; still needs a listing |
| S3 event notifications (SQS/SNS/EventBridge) | Near real time, no listing cost | Extra infrastructure; events can be delayed, duplicated or dropped, so a reconciliation sweep is still needed; not available everywhere |
| S3 Inventory reports | Cheap for millions of objects | Daily or weekly delivery only, so it cannot meet a 24 h freshness need reliably; AWS-only |

For a 5-page site, or a site with tens of thousands of files, a full listing per run is cheap and
the inventory gives correct results on MinIO, LocalStack and AWS alike. At very large scale the
natural next step is event notifications for low latency, with this inventory diff kept as the
periodic reconciliation job.

Deleted files are never removed from the database. `s3_objects.is_deleted` and `deleted_at` are
set, a `deleted` event is appended, and a deleted HTML page is marked `status = deleted`, which keeps
its last content for auditing. The file is also removed from the local mirror.

## Website reconstruction and rendering

* Changed objects are downloaded into a local mirror (`MIRROR_DIR`) using atomic `.part` and
  rename writes. Missing files are back-filled, so a fresh container rebuilds the site
  automatically.
* Playwright loads `http://site.local/<page>`. A `context.route("**/*")` handler serves every
  request straight from the mirror. This gives three things:
  * relative URLs (`../css/main.css`) resolve exactly as they would on a real web server;
  * external requests are aborted, so rendering is sandboxed and deterministic;
  * every URL the page requests is observed, including `fetch()` calls, CSS-loaded fonts and
    background images, and files that returned 404.
* Interaction sequence per page:
  1. Wait for `networkidle`.
  2. Auto-scroll to the bottom until the page height is stable, which triggers lazy loaders.
  3. Scroll every overflow container to its end.
  4. Open all `<details>` elements.
  5. Click each popup trigger (`[data-modal-open]`, `[aria-haspopup=dialog]`, Bootstrap toggles),
     capture each visible dialog, then press Escape.
* Pages render concurrently (`RENDER_CONCURRENCY`), each in its own browser context. The browser
  is only started when at least one page needs rendering.

## Content extraction

A DOM walker running inside the page produces ordered blocks (headings, paragraphs, list items).
It skips:

* script, style, noscript, template, svg, canvas and iframe elements;
* navigation, banner and footer regions, buttons, forms and toolbars;
* `aria-hidden` and `data-extract="ignore"` elements;
* anything with `display: none` or `visibility: hidden`.

Dialogs are excluded from the main body and appended as `## [Popup] <title>` sections. The result
is stored as lightly formatted text: `#` marks headings and `-` marks list items. The SHA-256 of the
title plus the content is the change hash.

## Dependency tracking and selective reprocessing

Each page's dependency set is the union of two sources:

* **static**: `<link>`, `<script>`, `<img>`/`srcset`, `<source>`, media, inline styles and `<style>`
  blocks, plus transitive CSS `@import` and `url()` references;
* **runtime**: every request observed while rendering, including JSON fetched by JavaScript and
  lazily loaded content that static parsing cannot see.

`plan_reprocessing()` maps each change to the pages it affects:

* created or updated HTML: that page only;
* deleted HTML: the page is retired, and nothing is rendered;
* any other file: only the pages whose dependency set contains it. Because 404 requests are
  recorded too, creating a file that a page was already asking for re-renders that page.
* An HTML page that has never been extracted, or whose last extraction failed, is always scheduled.

With the sample site:

| Change | Pages rendered |
|---|---|
| `css/trails.css` | `trails.html` only |
| `data/season.json` | `index.html` only |
| `css/main.css` | all 5 pages |
| an unreferenced image | none |

## Configuration

All settings are environment variables (see `.env.example`), loaded with `pydantic-settings`.

## Logging

structlog renders JSON (or console output with `LOG_JSON=false`). Every request gets a
`request_id`, which is echoed in the `x-request-id` header, and every log line carries the
contextual fields: `ingestion.change`, `render.done`, `page.stored`, `ingestion.finished` and so on.

## Known limitations

* The run lock is per process. Running several API replicas would need a database advisory lock
  or a job queue.
* Rendering runs inside the API request. For a large site, `POST /ingestion/run` should enqueue a
  background job and return a run id.
* Popup discovery relies on common trigger conventions. Custom widgets can opt in with
  `data-modal-open`.
