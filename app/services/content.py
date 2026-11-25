"""Turn rendered text blocks into the stored, hashable page content."""

import hashlib


def _format_block(block: dict) -> str:
    if block["type"] == "heading":
        return f"{'#' * block.get('level', 2)} {block['text']}"
    if block["type"] == "item":
        return f"- {block['text']}"
    return block["text"]


def blocks_to_text(blocks: list[dict]) -> str:
    lines: list[str] = []
    for block in blocks:
        line = _format_block(block)
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines)


def build_content(blocks: list[dict], popups: list[list[dict]]) -> str:
    sections = [blocks_to_text(blocks)]
    for popup in popups:
        heading = next((b["text"] for b in popup if b["type"] == "heading"), "Popup")
        body = [b for b in popup if not (b["type"] == "heading" and b["text"] == heading)]
        sections.append(f"## [Popup] {heading}\n{blocks_to_text(body)}".rstrip())
    return "\n\n".join(section for section in sections if section).strip()


def content_hash(title: str | None, content: str) -> str:
    return hashlib.sha256(f"{title or ''}\n{content}".encode("utf-8")).hexdigest()


def word_count(content: str) -> int:
    return len(content.split())
