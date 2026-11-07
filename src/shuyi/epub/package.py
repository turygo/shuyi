from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote, urlsplit

from lxml import etree

from shuyi.contracts import Book, ShuyiError
from shuyi.epub.dom import EPUB, OPF, XHTML, parse_xml

CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
XML_TYPES = {"application/xhtml+xml", "image/svg+xml", "application/x-dtbncx+xml"}


def resolve_href(resource: str, href: str) -> tuple[str, str] | None:
    try:
        uri = urlsplit(href)
    except ValueError as exc:
        raise ShuyiError("reference_invalid", resource) from exc
    if uri.scheme or uri.netloc:
        return None
    if re.search(r"%(?![0-9a-fA-F]{2})", uri.path + uri.fragment):
        raise ShuyiError("reference_invalid", resource)
    try:
        path = unquote(uri.path, errors="strict")
        fragment = unquote(uri.fragment, errors="strict")
    except UnicodeError as exc:
        raise ShuyiError("reference_invalid", resource) from exc
    if "\\" in path or "\x00" in path + fragment or path.startswith("/"):
        raise ShuyiError("reference_unsafe", resource)
    target = (
        posixpath.normpath(posixpath.join(posixpath.dirname(resource), path)) if path else resource
    )
    if target in {".", ".."} or target.startswith("../"):
        raise ShuyiError("reference_unsafe", resource)
    return target, fragment


def package_inventory(
    entries: dict[str, bytes],
) -> tuple[str, tuple[str, ...], frozenset[str], str]:
    try:
        container = parse_xml(entries["META-INF/container.xml"], "META-INF/container.xml")
    except KeyError as exc:
        raise ShuyiError("container_missing") from exc
    if container.tag != f"{{{CONTAINER}}}container":
        raise ShuyiError("container_invalid")
    roots = container.findall(f"{{{CONTAINER}}}rootfiles/{{{CONTAINER}}}rootfile")
    matches = [node for node in roots if node.get("media-type") == "application/oebps-package+xml"]
    if len(matches) != 1:
        raise ShuyiError("container_ambiguous")
    resolved = resolve_href("container.xml", matches[0].get("full-path", ""))
    if resolved is None or resolved[1] or resolved[0] not in entries:
        raise ShuyiError("package_missing")
    opf_path = resolved[0]
    root = parse_xml(entries[opf_path], opf_path)
    version = root.get("version", "")
    if root.tag != f"{{{OPF}}}package" or version not in {"2.0", "3.0"}:
        raise ShuyiError("package_invalid", opf_path)
    manifest = root.find(f"{{{OPF}}}manifest")
    spine = root.find(f"{{{OPF}}}spine")
    metadata = root.find(f"{{{OPF}}}metadata")
    if manifest is None or spine is None or metadata is None:
        raise ShuyiError("package_invalid", opf_path)
    identifier = root.get("unique-identifier")
    if not identifier or not metadata.xpath(".//*[@id=$value]", value=identifier):
        raise ShuyiError("package_identifier_invalid", opf_path)
    items: dict[str, tuple[str, str, set[str]]] = {}
    paths: set[str] = set()
    protected: set[str] = set()
    for item in manifest:
        if item.tag != f"{{{OPF}}}item":
            raise ShuyiError("manifest_invalid", opf_path)
        item_id, media = item.get("id"), item.get("media-type")
        target = resolve_href(opf_path, item.get("href", ""))
        if (
            not item_id
            or item_id in items
            or not media
            or target is None
            or target[1]
            or target[0] not in entries
            or target[0] in paths
        ):
            raise ShuyiError("manifest_invalid", opf_path)
        properties = set(item.get("properties", "").split())
        items[item_id] = (target[0], media, properties)
        paths.add(target[0])
        if "nav" in properties or media == "application/x-dtbncx+xml":
            protected.add(target[0])
        if media in XML_TYPES:
            doc = parse_xml(entries[target[0]], target[0])
            expected = {
                "application/xhtml+xml": f"{{{XHTML}}}html",
                "image/svg+xml": "{http://www.w3.org/2000/svg}svg",
                "application/x-dtbncx+xml": f"{{{NCX}}}ncx",
            }[media]
            if doc.tag != expected:
                raise ShuyiError("resource_namespace_invalid", target[0])
    for item in manifest:
        if any(
            item.get(name) and item.get(name) not in items for name in ("fallback", "media-overlay")
        ):
            raise ShuyiError("manifest_reference_invalid", opf_path)
    if not len(spine):
        raise ShuyiError("spine_invalid", opf_path)
    ordered: list[str] = []
    fixed = bool(
        metadata.xpath(
            ".//*[local-name()='meta' and @property='rendition:layout' and text()='pre-paginated']"
        )
    )
    toc = spine.get("toc")
    if toc and (toc not in items or items[toc][1] != "application/x-dtbncx+xml"):
        raise ShuyiError("spine_invalid", opf_path)
    for ref in spine:
        item_id = ref.get("idref", "")
        if ref.tag != f"{{{OPF}}}itemref" or item_id not in items:
            raise ShuyiError("spine_invalid", opf_path)
        path, media, properties = items[item_id]
        if media not in {"application/xhtml+xml", "image/svg+xml"}:
            raise ShuyiError("spine_invalid", opf_path)
        if path not in ordered:
            ordered.append(path)
        flags = properties | set(ref.get("properties", "").split())
        if fixed or flags & {"rendition:layout-pre-paginated", "cover", "cover-image"}:
            protected.add(path)
    for path, media, _properties in items.values():
        if media == "application/xhtml+xml" and path not in ordered:
            ordered.append(path)
    protect_frontmatter(root, opf_path, items, protected)
    for path, media, properties in items.values():
        if media == "image/svg+xml":
            protected.add(path)
        if "nav" in properties:
            protected.update(navigation_protections(entries[path], path))
    return opf_path, tuple(ordered), frozenset(protected), version


def protect_frontmatter(
    root: etree._Element, opf_path: str, items: dict, protected: set[str]
) -> None:
    for reference in root.findall(f"{{{OPF}}}guide/{{{OPF}}}reference"):
        if reference.get("type") in {"cover", "title-page", "toc"}:
            target = resolve_href(opf_path, reference.get("href", ""))
            if target:
                protected.add(target[0])
    for meta in root.findall(f"{{{OPF}}}metadata/{{{OPF}}}meta"):
        if meta.get("name") == "cover" and meta.get("content") in items:
            protected.add(items[meta.get("content")][0])


def navigation_protections(data: bytes, resource: str) -> set[str]:
    root = parse_xml(data, resource)
    protected = set()
    for anchor in root.iter(f"{{{XHTML}}}a"):
        tokens = set(anchor.get(f"{{{EPUB}}}type", "").split())
        if tokens & {"cover", "titlepage", "title-page"}:
            target = resolve_href(resource, anchor.get("href", ""))
            if target:
                protected.add(target[0])
    return protected


def manifest_resources(book: Book) -> dict[str, str]:
    root = parse_xml(book.entries[book.opf_path], book.opf_path)
    result = {}
    for item in root.findall(f"{{{OPF}}}manifest/{{{OPF}}}item"):
        target = resolve_href(book.opf_path, item.get("href", ""))
        if target:
            result[target[0]] = item.get("media-type", "")
    return result


def check_references(book: Book) -> list[str]:
    resources = manifest_resources(book)
    roots = {
        path: parse_xml(book.entries[path], path)
        for path, media in resources.items()
        if media in XML_TYPES
    }
    roots[book.opf_path] = parse_xml(book.entries[book.opf_path], book.opf_path)
    anchors = {path: anchor_ids(root, path) for path, root in roots.items()}
    for path, root in roots.items():
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            if (
                node.get("{http://www.w3.org/XML/1998/namespace}base") is not None
                or etree.QName(node).localname == "base"
            ):
                raise ShuyiError("reference_base_unsupported", path)
            for name, value in node.attrib.items():
                if etree.QName(name).localname not in {"href", "src", "poster", "data"}:
                    continue
                target = resolve_href(path, value)
                if target is None:
                    continue
                destination, fragment = target
                if destination not in book.entries:
                    raise ShuyiError("reference_missing", path)
                if fragment and (
                    destination not in anchors or fragment not in anchors[destination]
                ):
                    raise ShuyiError("reference_fragment_missing", path)
    return []


def anchor_ids(root: etree._Element, resource: str) -> set[str]:
    anchors = {}
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        values = [node.get("id"), node.get("{http://www.w3.org/XML/1998/namespace}id")]
        if etree.QName(node).localname == "a":
            values.append(node.get("name"))
        for value in values:
            if value and value in anchors and anchors[value] is not node:
                raise ShuyiError("duplicate_id", resource)
            if value:
                anchors[value] = node
    return set(anchors)
