"""End-to-end: moto S3 server -> ingestion -> real headless Chromium -> SQLite -> HTTP API."""

import httpx
import pytest

from app.main import create_app
from tests.conftest import BUCKET, PREFIX, SITE_DIR

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(settings, s3):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as http:
            yield http


async def ingest(client) -> dict:
    response = await client.post("/ingestion/run")
    assert response.status_code == 200, response.text
    return response.json()


def rendered(result) -> set[str]:
    return {p["path"] for p in result["pages"] if p["status"] in ("changed", "unchanged")}


async def test_full_flow(client, s3):
    # 1. First run: every file is new, all five pages are rendered and extracted.
    first = await ingest(client)
    site_files = sum(1 for f in SITE_DIR.rglob("*") if f.is_file())
    assert first["counts"]["created"] == first["objects_listed"] == site_files
    assert rendered(first) == {"index.html", "trails.html", "guides.html", "gear.html", "info/contact.html"}
    assert first["versioning_enabled"] is True

    pages = (await client.get("/pages")).json()
    assert pages["total"] == 5
    by_path = {p["path"]: p for p in pages["items"]}

    home = (await client.get(f"/pages/{by_path['index.html']['id']}")).json()
    assert "Granite Stair Summit" in home["content"]          # injected by JavaScript from JSON
    assert "Mirror Lake boardwalk was rebuilt" in home["content"]  # lazy-loaded after scrolling
    assert "[Popup] Trail safety briefing" in home["content"]      # modal opened by the extractor
    assert "Read the trail safety briefing" not in home["content"]  # button chrome is excluded
    assert "data/season.json" in {d["asset_path"] for d in home["dependencies"]}

    trails = (await client.get(f"/pages/{by_path['trails.html']['id']}")).json()
    assert "Snowshoe Basin" in trails["content"]               # last item of the scrollable list

    gear = (await client.get(f"/pages/{by_path['gear.html']['id']}")).json()
    assert "Headlamp with spare batteries" in gear["content"]  # collapsed <details> content

    contact = (await client.get(f"/pages/{by_path['info/contact.html']['id']}")).json()
    assert "Cancel more than 72 hours" in contact["content"]
    assert "css/main.css" in {d["asset_path"] for d in contact["dependencies"]}  # ../ resolved

    # 2. Re-run with no changes: nothing recorded, nothing rendered.
    second = await ingest(client)
    assert second["counts"]["file_events_recorded"] == 0
    assert second["pages"] == []

    # 3. Page-specific stylesheet changes: only the trails page is re-rendered.
    s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}css/trails.css", Body=b".trail { color: red; }")
    third = await ingest(client)
    assert third["counts"]["updated"] == 1
    assert rendered(third) == {"trails.html"}

    # 4. Shared data file changes: only the pages that fetched it are re-rendered.
    s3.put_object(
        Bucket=BUCKET,
        Key=f"{PREFIX}data/season.json",
        Body=b'{"notes": ["The north trailhead car park is closed for resurfacing."]}',
    )
    fourth = await ingest(client)
    assert rendered(fourth) == {"index.html"}
    home_v2 = (await client.get(f"/pages/{by_path['index.html']['id']}")).json()
    assert "car park is closed" in home_v2["content"]
    assert home_v2["version"] == home["version"] + 1

    # 5. Deletion: the page is retired and the file is flagged, not removed.
    s3.delete_object(Bucket=BUCKET, Key=f"{PREFIX}gear.html")
    fifth = await ingest(client)
    assert fifth["counts"]["deleted"] == 1
    assert [p["status"] for p in fifth["pages"]] == ["retired"]
    assert (await client.get("/pages")).json()["total"] == 4
    deleted = (await client.get("/files", params={"operation": "deleted"})).json()
    assert [f["path"] for f in deleted["items"]] == ["gear.html"]
    assert deleted["items"][0]["deleted_at"] is not None

    # 6. Reprocess endpoint re-renders one page on demand; unchanged content keeps its version.
    response = await client.post(f"/pages/{by_path['trails.html']['id']}/reprocess")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content_changed"] is False
    assert body["page"]["dependencies"]

    gone = await client.post(f"/pages/{by_path['gear.html']['id']}/reprocess")
    assert gone.status_code == 410

    runs = (await client.get("/ingestion/runs")).json()
    assert len(runs) == 5 and all(run["status"] == "succeeded" for run in runs)


async def test_sample_site_is_complete():
    """Guards against assets being dropped (e.g. by .gitignore) from the shipped sample site."""
    required = {
        "index.html", "trails.html", "guides.html", "gear.html", "info/contact.html",
        "data/trails.json", "data/season.json", "css/main.css", "css/typography.css", "css/trails.css",
        "js/site.js", "js/home.js", "js/trails.js", "fonts/ridgeline-icons.ttf", "images/trail-map.png",
    }
    present = {f.relative_to(SITE_DIR).as_posix() for f in SITE_DIR.rglob("*") if f.is_file()}
    assert required <= present


async def test_unreachable_storage_fails_cleanly(settings):
    broken = settings.model_copy(update={"s3_endpoint_url": "http://127.0.0.1:9"})
    app = create_app(broken)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as http:
            response = await http.post("/ingestion/run")
            assert response.status_code == 502
            assert response.json()["detail"]["run_id"] == 1
            runs = (await http.get("/ingestion/runs")).json()
            assert runs[0]["status"] == "failed" and runs[0]["error"]
            assert (await http.get("/files")).json()["total"] == 0


async def test_unknown_page_returns_404(client):
    assert (await client.get("/pages/999")).status_code == 404
    assert (await client.post("/pages/999/reprocess")).status_code == 404


async def test_files_listing_filters(client):
    await ingest(client)
    fonts = (await client.get("/files", params={"asset_type": "font"})).json()
    assert [f["path"] for f in fonts["items"]] == ["fonts/ridgeline-icons.ttf"]
    assert fonts["items"][0]["etag"] and fonts["items"][0]["version_id"]
