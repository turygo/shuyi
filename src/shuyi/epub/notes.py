from __future__ import annotations

import re

from lxml import etree

from shuyi.contracts import Book, NoteMarker, ShuyiError
from shuyi.epub.dom import EPUB, XHTML, element_path, is_protected, parse_xml
from shuyi.epub.note_context import in_note_target, note_targets
from shuyi.epub.package import resolve_href

NOTE_TYPES = {"footnote", "endnote", "rearnote", "footnotes", "endnotes", "rearnotes"}
NOTE_ROLES = {"doc-footnote", "doc-endnote", "doc-endnotes"}
SHORT_LABEL = re.compile(
    r"^[\s\[\]（）()【】〔〕]*([0-9０-９]{1,4}|[①-⑳*†‡※注])[\s\[\]（）()【】〔〕]*$"
)
RETURN_LABEL = re.compile(
    r"^[\s\[\]（）()【】〔〕]*(?:[↩↵↑←⤴↰]|返回|回|back|[0-9０-９]{1,4})[\s\[\]（）()【】〔〕]*$",
    re.I,
)


def semantics(node: etree._Element) -> set[str]:
    return set(node.get(f"{{{EPUB}}}type", "").split()) | set(node.get("role", "").split())


def note_context(node: etree._Element) -> bool:
    return any(
        semantics(item) & (NOTE_TYPES | NOTE_ROLES) for item in (node, *node.iterancestors())
    )


def label_fields(anchor: etree._Element) -> tuple[tuple[etree._Element, str], ...]:
    fields = []

    def collect(node: etree._Element) -> None:
        if node.text:
            fields.append((node, "text"))
        for child in node:
            if isinstance(child.tag, str):
                collect(child)
            else:
                if (child.tail or "").strip():
                    raise ShuyiError("theme_note_label_unsupported")
                continue
            if child.tail:
                fields.append((child, "tail"))

    collect(anchor)
    return tuple(fields)


def find_note_markers(book: Book) -> tuple[NoteMarker, ...]:
    roots = {
        path: parse_xml(book.entries[path], path)
        for path in book.documents
        if path not in book.protected
    }
    targets = note_targets(book)
    ids, links = index_note_links(roots)
    confirmed: dict[tuple[str, tuple[int, ...]], NoteMarker] = {}
    for key, (anchor, target) in links.items():
        destination = ids.get(target)
        if destination is None:
            continue
        tokens = semantics(anchor)
        kind = "noteref" if tokens & {"noteref", "doc-noteref"} else None
        if tokens & {"backlink", "doc-backlink"}:
            kind = "backlink"
        if kind is None and note_context(destination) and not note_context(anchor):
            kind = "noteref"
        if kind:
            confirmed[key] = NoteMarker(
                key[0], key[1], kind, target[0], element_path(roots[target[0]], destination)
            )
    for key, (anchor, target) in links.items():
        destination = ids.get(target)
        if destination is None or key in confirmed:
            continue
        label = "".join(anchor.itertext()).strip()
        if not SHORT_LABEL.fullmatch(label):
            continue
        for back in destination.iter():
            back_key = (
                (target[0], element_path(roots[target[0]], back))
                if isinstance(back.tag, str)
                else None
            )
            if back_key not in links:
                continue
            return_target = ids.get(links[back_key][1])
            if not safe_return(anchor, return_target):
                continue
            if not RETURN_LABEL.fullmatch("".join(back.itertext()).strip()):
                continue
            numeric = bool(re.search(r"[0-9０-９①-⑳]", label))
            if numeric and not (
                note_context(destination)
                or note_context(back)
                or in_note_target(target[0], destination, targets)
                or semantics(anchor) & {"noteref", "doc-noteref"}
            ):
                continue
            siblings = [
                node
                for node in destination.iter()
                if isinstance(node.tag, str) and (node.text or "").strip()
            ]
            if siblings and siblings[0] is not back and back not in siblings[0].iterancestors():
                continue
            confirmed[key] = NoteMarker(
                key[0], key[1], "noteref", target[0], element_path(roots[target[0]], destination)
            )
            confirmed[back_key] = NoteMarker(
                back_key[0],
                back_key[1],
                "backlink",
                key[0],
                element_path(roots[key[0]], return_target),
            )
            break
    for key, marker in list(confirmed.items()):
        if marker.kind != "noteref":
            continue
        anchor, target = links[key]
        destination = ids[target]
        for back in destination.iter():
            if not isinstance(back.tag, str):
                continue
            back_key = (target[0], element_path(roots[target[0]], back))
            if back_key not in links:
                continue
            return_target = ids.get(links[back_key][1])
            if safe_return(anchor, return_target):
                if RETURN_LABEL.fullmatch("".join(back.itertext()).strip()) or semantics(back) & {
                    "backlink",
                    "doc-backlink",
                }:
                    confirmed[back_key] = NoteMarker(
                        back_key[0],
                        back_key[1],
                        "backlink",
                        key[0],
                        element_path(roots[key[0]], return_target),
                    )
    return tuple(confirmed[key] for key in sorted(confirmed))


def index_note_links(roots: dict[str, etree._Element]) -> tuple[dict, dict]:
    ids, links = {}, {}
    for resource, root in roots.items():
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            for value in (node.get("id"), node.get("name")):
                if value:
                    ids[(resource, value)] = node
            if node.tag == f"{{{XHTML}}}a" and node.get("href") and not is_protected(node):
                tokens = semantics(node)
                if tokens & {"noteref", "doc-noteref"} and tokens & {"backlink", "doc-backlink"}:
                    continue
                target = resolve_href(resource, node.get("href"))
                if target and target[1]:
                    links[(resource, element_path(root, node))] = (node, target)
    return ids, links


def safe_return(anchor: etree._Element, target: etree._Element | None) -> bool:
    if target is anchor:
        return True
    if target is None or target not in anchor.iterancestors():
        return False
    anchors = [node for node in target.iter(f"{{{XHTML}}}a") if node.get("href")]
    return len(anchors) == 1 and anchors[0] is anchor
