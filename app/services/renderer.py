"""Headless rendering of the mirrored site with Playwright.

Pages are served straight from the local mirror through request interception,
so no extra web server is needed and every request the page makes is observed.
Those observed requests become the page's runtime dependencies.
"""

import asyncio
import mimetypes
import posixpath
import sys
import threading
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.async_api import Browser, Page, Playwright, Route, async_playwright

from app.core.logging import get_logger

log = get_logger(__name__)

mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/javascript", ".js")

POPUP_TRIGGERS = (
    "[data-modal-open], [aria-haspopup='dialog'], [data-bs-toggle='modal'], [data-toggle='modal']"
)

# Walks the DOM and returns ordered text blocks, skipping scripts, styles,
# navigation chrome, form controls and anything not rendered.
EXTRACT_BLOCKS_JS = r"""
(rootSelector) => {
  const root = rootSelector ? document.querySelector(rootSelector) : document.body;
  if (!root) return [];
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "CANVAS", "IFRAME",
    "NAV", "FOOTER", "BUTTON", "FORM", "INPUT", "SELECT", "TEXTAREA", "LABEL"]);
  const SKIP_SELECTOR = "body > header, [role=banner], [role=navigation], [role=contentinfo], " +
    "[aria-hidden=true], [data-extract=ignore], [role=toolbar]";
  const DIALOG_SELECTOR = "[role=dialog], dialog, [aria-modal=true]";
  const BLOCK_TAGS = new Set(["P", "LI", "DT", "DD", "FIGCAPTION", "BLOCKQUOTE", "SUMMARY", "PRE",
    "TD", "TH", "CAPTION", "H1", "H2", "H3", "H4", "H5", "H6"]);
  const clean = (text) => (text || "").replace(/\s+/g, " ").trim();
  const blocks = [];

  const hidden = (el) => {
    if (el.hidden) return true;
    const style = getComputedStyle(el);
    return style.display === "none" || style.visibility === "hidden";
  };

  const visit = (el) => {
    if (SKIP_TAGS.has(el.tagName) || hidden(el)) return;
    if (el !== root && (el.matches(SKIP_SELECTOR) || el.matches(DIALOG_SELECTOR))) return;
    if (BLOCK_TAGS.has(el.tagName)) {
      const text = clean(el.innerText);
      if (!text) return;
      if (/^H[1-6]$/.test(el.tagName)) blocks.push({ type: "heading", level: Number(el.tagName[1]), text });
      else if (el.tagName === "LI") blocks.push({ type: "item", text });
      else if (el.tagName === "SUMMARY") blocks.push({ type: "heading", level: 3, text });
      else blocks.push({ type: "text", text });
      return;
    }
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = clean(node.textContent);
        if (text) blocks.push({ type: "text", text });
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        visit(node);
      }
    }
  };

  visit(root);
  return blocks;
}
"""

AUTO_SCROLL_JS = r"""
async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let stable = 0;
  let lastHeight = 0;
  for (let i = 0; i < 80 && stable < 3; i++) {
    window.scrollBy(0, Math.max(200, window.innerHeight * 0.8));
    await sleep(100);
    const height = document.documentElement.scrollHeight;
    const atBottom = window.scrollY + window.innerHeight >= height - 2;
    stable = atBottom && height === lastHeight ? stable + 1 : 0;
    lastHeight = height;
  }
  window.scrollTo(0, 0);
}
"""

SCROLL_CONTAINERS_JS = r"""
async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const scrollables = [...document.querySelectorAll("body *")].filter((el) => {
    const style = getComputedStyle(el);
    return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 2;
  });
  for (const el of scrollables) {
    for (let i = 0; i < 40 && el.scrollTop + el.clientHeight < el.scrollHeight - 2; i++) {
      el.scrollTop += Math.max(50, el.clientHeight * 0.8);
      await sleep(80);
    }
  }
  return scrollables.length;
}
"""

VISIBLE_DIALOGS_JS = r"""
() => [...document.querySelectorAll("[role=dialog], dialog, [aria-modal=true]")]
  .filter((el) => {
    if (el.hidden) return false;
    const style = getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden";
  })
  .map((el, index) => {
    if (!el.id) el.id = `__popup_${Date.now()}_${index}`;
    return "#" + CSS.escape(el.id);
  })
"""


@dataclass
class RenderResult:
    path: str
    url: str
    title: str
    blocks: list[dict]
    popups: list[list[dict]]
    requested: set[str] = field(default_factory=set)
    missing: set[str] = field(default_factory=set)
    scroll_containers: int = 0


class _BrowserLoop:
    """A private event loop on its own thread for everything Playwright does.

    Playwright starts the browser as a subprocess, which the selector event loop used by
    `uvicorn --reload` on Windows cannot do. Running it on a dedicated loop (a Proactor
    loop on Windows) works regardless of how the server was started.
    """

    def __init__(self) -> None:
        self._loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="playwright", daemon=True)
        self._thread.start()

    async def run(self, coro: Coroutine):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)
        self._loop.close()


class SiteRenderer:
    def __init__(self, mirror_dir: Path, origin: str, timeout_ms: int = 20_000):
        self._mirror = mirror_dir.resolve()
        self._origin = origin.rstrip("/")
        self._timeout_ms = timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._worker: _BrowserLoop | None = None

    async def __aenter__(self) -> "SiteRenderer":
        self._worker = _BrowserLoop()
        try:
            await self._worker.run(self._start())
        except BaseException:
            self._worker.close()
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._worker is None:
            return
        try:
            await self._worker.run(self._stop())
        finally:
            self._worker.close()
            self._worker = None

    async def _start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(args=["--no-sandbox"])

    async def _stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    def url_for(self, path: str) -> str:
        return f"{self._origin}/{path}"

    def _site_path(self, url: str) -> str | None:
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != self._origin:
            return None
        return posixpath.normpath(unquote(parts.path).lstrip("/")) or "index.html"

    async def render(self, path: str) -> RenderResult:
        if self._worker is None:
            raise RuntimeError("renderer must be used as an async context manager")
        return await self._worker.run(self._render(path))

    async def _render(self, path: str) -> RenderResult:
        requested: set[str] = set()
        missing: set[str] = set()

        async def serve(route: Route) -> None:
            site_path = self._site_path(route.request.url)
            if site_path is None:
                await route.abort()  # never leave the sandboxed origin
                return
            requested.add(site_path)
            file = (self._mirror / site_path).resolve()
            if file.is_file() and file.is_relative_to(self._mirror):
                content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                await route.fulfill(status=200, body=file.read_bytes(), content_type=content_type)
            else:
                missing.add(site_path)
                await route.fulfill(status=404, body="not found", content_type="text/plain")

        context = await self._browser.new_context(viewport={"width": 1280, "height": 800}, service_workers="block")
        try:
            await context.route("**/*", serve)
            page = await context.new_page()
            page.set_default_timeout(self._timeout_ms)
            url = self.url_for(path)
            await page.goto(url, wait_until="networkidle")

            await page.evaluate(AUTO_SCROLL_JS)
            containers = await page.evaluate(SCROLL_CONTAINERS_JS)
            await page.evaluate("document.querySelectorAll('details').forEach((d) => { d.open = true; })")
            await page.wait_for_load_state("networkidle")

            popups = await self._capture_popups(page)
            title = (await page.title()).strip()
            blocks = await page.evaluate(EXTRACT_BLOCKS_JS, None)
        finally:
            await context.close()

        requested.discard(path)
        missing.discard(path)
        log.info("render.done", path=path, blocks=len(blocks), popups=len(popups), requests=len(requested))
        return RenderResult(path, url, title, blocks, popups, requested, missing, containers)

    async def _capture_popups(self, page: Page) -> list[list[dict]]:
        popups: list[list[dict]] = []
        seen: set[str] = set()
        triggers = page.locator(POPUP_TRIGGERS)
        for index in range(await triggers.count()):
            trigger = triggers.nth(index)
            if not await trigger.is_visible():
                continue
            try:
                await trigger.click(timeout=3_000)
                await asyncio.sleep(0.25)
                for selector in await page.evaluate(VISIBLE_DIALOGS_JS):
                    blocks = await page.evaluate(EXTRACT_BLOCKS_JS, selector)
                    signature = "|".join(block["text"] for block in blocks)
                    if blocks and signature not in seen:
                        seen.add(signature)
                        popups.append(blocks)
            except Exception as exc:  # an unclickable trigger should not fail the page
                log.warning("render.popup_failed", trigger=index, error=str(exc))
            finally:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.1)
        return popups
