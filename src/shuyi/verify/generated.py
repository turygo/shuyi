from __future__ import annotations

import re

from shuyi.contracts import ROLES, Book, ShuyiError
from shuyi.epub.dom import OPF, XHTML, is_protected, parse_xml
from shuyi.epub.notes import find_note_markers
from shuyi.epub.package import resolve_href
from shuyi.verify.note_graph import reciprocal_markers

RESERVED = {
    "data-sy-node",
    "data-sy-role",
    "data-sy-level",
    "data-sy-content",
    "data-sy-note-kind",
    "data-sy-note-id",
}


def check_generated_theme(book: Book, asset: str, markers: list, resources: dict[str, str]) -> None:
    if asset not in book.entries or resources.get(asset) != "text/css":
        raise ShuyiError("generated_asset_missing")
    package = parse_xml(book.entries[book.opf_path], book.opf_path)
    entries = package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item")
    admitted = [node for node in entries if node.get("id") == "shuyi-reading"]
    if len(admitted) != 1 or resolve_href(book.opf_path, admitted[0].get("href", "")) != (
        asset,
        "",
    ):
        raise ShuyiError("generated_manifest_invalid")
    node_ids = set()
    note_ids = set()
    confirmed = {(marker.resource, marker.path): marker for marker in find_note_markers(book)}
    reciprocal = reciprocal_markers(book)
    for resource, node in markers:
        if (
            resource in book.protected
            or is_protected(node)
            or any(name.startswith("data-sy-") and name not in RESERVED for name in node.attrib)
        ):
            raise ShuyiError("generated_marker_invalid", resource)
        role = node.get("data-sy-role")
        node_id = node.get("data-sy-node")
        level = node.get("data-sy-level")
        if role is not None:
            if role not in ROLES or not node_id or node_id in node_ids:
                raise ShuyiError("generated_binding_invalid", resource)
            if (role == "heading" and level not in {"1", "2", "3", "4", "5", "6"}) or (
                role != "heading" and level is not None
            ):
                raise ShuyiError("generated_binding_invalid", resource)
            node_ids.add(node_id)
            if node.get("data-sy-content") != "target":
                raise ShuyiError("generated_binding_invalid", resource)
        elif node_id is not None or level is not None or node.get("data-sy-content") is not None:
            raise ShuyiError("generated_binding_invalid", resource)
        kind = node.get("data-sy-note-kind")
        if kind is not None:
            from shuyi.epub.dom import element_path

            key = (resource, element_path(node.getroottree().getroot(), node))
            recognized = key in confirmed and confirmed[key].kind == kind
            if (
                kind not in {"noteref", "backlink"}
                or node.tag != f"{{{XHTML}}}a"
                or "".join(node.itertext()).strip() != "注"
                or not (recognized or (*key, kind) in reciprocal)
            ):
                raise ShuyiError("generated_note_invalid", resource)
            note_id = node.get("data-sy-note-id", "")
            if not re.fullmatch(r"note[0-9]{6,}", note_id) or note_id in note_ids:
                raise ShuyiError("generated_note_id_invalid", resource)
            note_ids.add(note_id)
        elif node.get("data-sy-note-id") is not None:
            raise ShuyiError("generated_note_id_invalid", resource)
        if role is None and kind is None:
            raise ShuyiError("generated_marker_invalid", resource)
    for resource in book.documents:
        root = parse_xml(book.entries[resource], resource)
        links = [
            node
            for node in root.iter(f"{{{XHTML}}}link")
            if resolve_href(resource, node.get("href", "")) == (asset, "")
        ]
        styled = any(path == resource for path, _ in markers)
        if len(links) != (1 if styled else 0):
            raise ShuyiError("generated_link_invalid", resource)
        if links and (links[0].get("rel") != "stylesheet" or links[0].get("type") != "text/css"):
            raise ShuyiError("generated_link_invalid", resource)
    from shuyi.epub.styles import validate_standalone_css

    validate_standalone_css(book, asset)
