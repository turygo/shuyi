from __future__ import annotations

import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import quoteattr

from shuyi.contracts import Assignment, ClassificationBatch, LayoutNode

XHTML = "http://www.w3.org/1999/xhtml"
DEFAULT_DOCUMENT = (
    f'<html xmlns="{XHTML}"><head><title>Test</title>'
    '<link rel="stylesheet" href="styles.css"/></head>'
    "<body><h1>Test chapter</h1><p>正文测试内容。</p></body></html>"
)


def document(body: str, *, css_href: str = "styles.css") -> str:
    return (
        f'<html xmlns="{XHTML}" xmlns:epub="http://www.idpf.org/2007/ops">'
        f'<head><title>Test</title><link rel="stylesheet" href={quoteattr(css_href)}/></head>'
        f"<body>{body}</body></html>"
    )


def build_epub(
    path: Path,
    *,
    version: str = "3.0",
    documents: dict[str, str | bytes] | None = None,
    stylesheet: str | bytes | None = None,
    extra_entries: dict[str, str | bytes] | None = None,
    protected: tuple[str, ...] = (),
) -> Path:
    documents = {"book.xhtml": DEFAULT_DOCUMENT} if documents is None else documents
    entries: dict[str, str | bytes] = dict(documents)
    entries["styles.css"] = "p { color: black; }" if stylesheet is None else stylesheet
    entries.update(extra_entries or {})
    items = []
    spine = []
    for index, name in enumerate(entries):
        media = (
            "application/xhtml+xml"
            if name in documents
            else "text/css"
            if name.endswith(".css")
            else "image/png"
            if name.endswith(".png")
            else "image/jpeg"
            if name.endswith((".jpg", ".jpeg"))
            else "font/ttf"
            if name.endswith(".ttf")
            else "application/octet-stream"
        )
        items.append(f'<item id="i{index}" href={quoteattr(name)} media-type={quoteattr(media)}/>')
        if name in documents:
            spine.append(f'<itemref idref="i{index}"/>')
    guide = "".join(
        f'<reference type="title-page" title="Protected" href={quoteattr(name)}/>'
        for name in protected
    )
    metadata = (
        '<dc:identifier id="uid">urn:shuyi:test</dc:identifier>'
        "<dc:title>Test book</dc:title><dc:language>zh</dc:language>"
    )
    if version.startswith("3"):
        metadata += '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
    opf = (
        f'<package xmlns="http://www.idpf.org/2007/opf" version={quoteattr(version)} '
        'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"{metadata}</metadata><manifest>{''.join(items)}</manifest>"
        f"<spine>{''.join(spine)}</spine><guide>{guide}</guide></package>"
    )
    container = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "x") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", opf)
        for name, data in entries.items():
            archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return path


class FakeClassifier:
    provenance = "offline-fixture"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def classify(self, samples: Sequence[LayoutNode]) -> ClassificationBatch:
        self.calls.append(tuple(node.node_id for node in samples))
        observations = tuple(
            Assignment(node.node_id, "heading", int(node.tag[1]))
            if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
            else Assignment(node.node_id, "body")
            for node in samples
        )
        return ClassificationBatch(observations, {"prompt_tokens": 10, "completion_tokens": 5})
