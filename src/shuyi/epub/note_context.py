from __future__ import annotations

from shuyi.contracts import Book
from shuyi.epub.dom import EPUB, OPF, XHTML, parse_xml
from shuyi.epub.package import manifest_resources, resolve_href

CONTEXT_TYPES = {"notes", "footnotes", "endnotes", "rearnotes", "footnote", "endnote", "rearnote"}


def note_targets(book: Book) -> frozenset[tuple[str, str]]:
    targets = set()
    package = parse_xml(book.entries[book.opf_path], book.opf_path)
    for node in package.findall(f"{{{OPF}}}guide/{{{OPF}}}reference"):
        if node.get("type") in CONTEXT_TYPES:
            target = resolve_href(book.opf_path, node.get("href", ""))
            if target:
                targets.add(target)
    resources = manifest_resources(book)
    manifest = package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item")
    nav_paths = set()
    for item in manifest:
        if "nav" in item.get("properties", "").split():
            target = resolve_href(book.opf_path, item.get("href", ""))
            if target:
                nav_paths.add(target[0])
    for resource in nav_paths:
        if resources.get(resource) != "application/xhtml+xml":
            continue
        root = parse_xml(book.entries[resource], resource)
        for anchor in root.iter(f"{{{XHTML}}}a"):
            if any(
                set(node.get(f"{{{EPUB}}}type", "").split()) & CONTEXT_TYPES
                for node in (anchor, *anchor.iterancestors())
            ):
                target = resolve_href(resource, anchor.get("href", ""))
                if target:
                    targets.add(target)
    return frozenset(targets)


def in_note_target(resource: str, node, targets: frozenset[tuple[str, str]]) -> bool:
    return (resource, "") in targets or any(
        (resource, item.get("id")) in targets or (resource, item.get("name")) in targets
        for item in (node, *node.iterancestors())
    )
