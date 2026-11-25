"""Static dependency discovery for HTML pages (and the CSS files they pull in).

Runtime dependencies (fetch/XHR, lazily loaded images, fonts) are captured
separately by the renderer; the two sets are merged before being stored.
"""

import posixpath
import re
from collections.abc import Callable
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r"""@import\s+(?:url\()?\s*['"]([^'"]+)['"]""", re.IGNORECASE)

TAG_ATTRIBUTES = {
    "link": ("href",),
    "script": ("src",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "iframe": ("src",),
    "embed": ("src",),
    "object": ("data",),
}

# <link> types that are fetched as part of rendering the page.
LINK_RELS = {"stylesheet", "preload", "icon", "shortcut", "modulepreload", "manifest"}


def resolve(base_path: str, reference: str) -> str | None:
    """Resolve a reference relative to a site path; None for external or inline URLs."""
    reference = reference.strip()
    if not reference or reference.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
        return None
    parts = urlsplit(reference)
    if parts.scheme or parts.netloc:
        return None
    target = unquote(parts.path)
    if not target:
        return None
    if target.startswith("/"):
        joined = target.lstrip("/")
    else:
        joined = posixpath.join(posixpath.dirname(base_path), target)
    normalized = posixpath.normpath(joined)
    if normalized.startswith(".."):
        return None
    return normalized


def _srcset_urls(value: str) -> list[str]:
    return [candidate.strip().split(" ")[0] for candidate in value.split(",") if candidate.strip()]


def css_dependencies(css_path: str, css_text: str) -> set[str]:
    refs = set(CSS_URL_RE.findall(css_text)) | set(CSS_IMPORT_RE.findall(css_text))
    return {path for ref in refs if (path := resolve(css_path, ref))}


def html_dependencies(page_path: str, html: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: set[str] = set()

    for tag_name, attributes in TAG_ATTRIBUTES.items():
        for tag in soup.find_all(tag_name):
            if tag_name == "link":
                rels = {rel.lower() for rel in tag.get("rel", [])}
                if not rels & LINK_RELS:
                    continue
            for attribute in attributes:
                value = tag.get(attribute)
                if not value:
                    continue
                refs = _srcset_urls(value) if attribute == "srcset" else [value]
                found.update(path for ref in refs if (path := resolve(page_path, ref)))

    for style in soup.find_all("style"):
        found |= css_dependencies(page_path, style.get_text())
    for tag in soup.find_all(style=True):
        found |= css_dependencies(page_path, tag["style"])

    return found


def static_dependencies(page_path: str, html: str, read_text: Callable[[str], str | None]) -> set[str]:
    """HTML references plus everything reachable through CSS @import / url()."""
    dependencies = html_dependencies(page_path, html)
    pending = [path for path in dependencies if path.endswith(".css")]
    visited: set[str] = set()
    while pending:
        css_path = pending.pop()
        if css_path in visited:
            continue
        visited.add(css_path)
        text = read_text(css_path)
        if text is None:
            continue
        for path in css_dependencies(css_path, text):
            if path not in dependencies:
                dependencies.add(path)
                if path.endswith(".css"):
                    pending.append(path)
    dependencies.discard(page_path)
    return dependencies
