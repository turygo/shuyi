from __future__ import annotations

import re

from lxml import etree

from shuyi.contracts import Book, ShuyiError, ThemePlan
from shuyi.epub.dom import EPUB, is_protected, node_digest, parse_xml, resolve_element
from shuyi.epub.notes import find_note_markers, label_fields, semantics
from shuyi.epub.styles import validate_inline_demotion


def validate_bindings(book: Book, plan: ThemePlan) -> dict[tuple[str, tuple[int, ...]], object]:
    bindings = {}
    node_ids = set()
    roots = {}
    from shuyi.contracts import ROLES

    for binding in plan.bindings:
        key = (binding.resource, binding.path)
        if (
            key in bindings
            or binding.node_id in node_ids
            or binding.role not in ROLES
            or binding.resource not in book.documents
            or binding.resource in book.protected
            or not re.fullmatch(r"n[0-9]{6,}", binding.node_id)
            or (
                binding.role == "heading"
                and (type(binding.level) is not int or not 1 <= binding.level <= 6)
            )
            or (binding.role != "heading" and binding.level is not None)
        ):
            raise ShuyiError("verification_binding_invalid", binding.resource)
        if binding.resource not in roots:
            roots[binding.resource] = parse_xml(book.entries[binding.resource], binding.resource)
        root = roots[binding.resource]
        node = resolve_element(root, binding.path)
        if is_protected(node) or node_digest(node) != binding.source_sha256:
            raise ShuyiError("verification_binding_invalid", binding.resource)
        bindings[key] = binding
        node_ids.add(binding.node_id)
    return bindings


def admit_attribute(
    name: str,
    before: str | None,
    after: str | None,
    binding: object | None,
    marker: object | None,
    source: etree._Element,
) -> bool:
    if name == "data-sy-node":
        return before is None and binding is not None and after == binding.node_id
    if name == "data-sy-role":
        return binding is not None and before is None and after == binding.role
    if name == "data-sy-level":
        return (
            binding is not None
            and binding.role == "heading"
            and before is None
            and after == str(binding.level)
        )
    if name == "data-sy-content":
        return binding is not None and before is None and after == "target"
    if name == "data-sy-note-kind":
        return marker is not None and before is None and after == marker.kind
    if name == "data-sy-note-id":
        return marker is not None and before is None and after is not None
    if marker is not None and name in {f"{{{EPUB}}}type", "role"}:
        expected = marker.kind if name != "role" else f"doc-{marker.kind}"
        return set((after or "").split()) == set((before or "").split()) | {
            expected
        } and not semantics(source) & (
            {"noteref", "doc-noteref"}
            if marker.kind == "backlink"
            else {"backlink", "doc-backlink", "footnote", "endnote", "doc-footnote", "doc-endnote"}
        )
    return False


def reverse_edits(
    book: Book,
    output: Book,
    plan: ThemePlan,
    properties: dict[tuple[str, tuple[int, ...]], set[str]],
) -> dict[str, etree._Element]:
    bindings = validate_bindings(book, plan)
    markers = {(marker.resource, marker.path): marker for marker in find_note_markers(book)}
    note_ids = {key: f"note{index:06d}" for index, key in enumerate(markers, 1)}
    roots = {
        path: parse_xml(output.entries[path], path)
        for path in book.documents
        if path in plan.replacements
    }
    source_roots = {path: parse_xml(book.entries[path], path) for path in roots}
    seen = set()
    for edit in plan.attributes:
        key = (edit.resource, edit.path)
        identity = (*key, edit.name)
        if identity in seen or edit.resource not in roots:
            raise ShuyiError("verification_edit_invalid", edit.resource)
        seen.add(identity)
        source = resolve_element(source_roots[edit.resource], edit.path)
        node = resolve_element(roots[edit.resource], edit.path)
        if source.get(edit.name) != edit.before or node.get(edit.name) != edit.after:
            raise ShuyiError("verification_edit_mismatch", edit.resource)
        if edit.name == "style" and edit.before is not None and edit.after is not None:
            note_owned = any(
                resource == key[0]
                and key[1][: len(path)] == path
                and (key[1] == path or etree.QName(source).localname == "sup")
                for resource, path in markers
            )
            if key not in bindings and not note_owned:
                raise ShuyiError("verification_attribute_forbidden", edit.resource)
            validate_inline_demotion(edit.before, edit.after, properties.get(key, set()))
        elif not admit_attribute(
            edit.name, edit.before, edit.after, bindings.get(key), markers.get(key), source
        ):
            raise ShuyiError("verification_attribute_forbidden", edit.resource)
        if edit.name == "data-sy-node" and key in bindings and edit.after != bindings[key].node_id:
            raise ShuyiError("verification_binding_invalid", edit.resource)
        if edit.name == "data-sy-note-id" and edit.after != note_ids.get(key):
            raise ShuyiError("verification_note_id_invalid", edit.resource)
        if edit.before is None:
            node.attrib.pop(edit.name, None)
        else:
            node.set(edit.name, edit.before)
    allowed_texts = note_text_fields(markers, source_roots)
    text_seen = set()
    for edit in plan.texts:
        if edit.resource == book.opf_path:
            continue
        identity = (edit.resource, edit.path, edit.field)
        if (
            identity in text_seen
            or identity not in allowed_texts
            or edit.resource not in roots
            or edit.after != allowed_texts[identity]
        ):
            raise ShuyiError("verification_text_forbidden", edit.resource)
        text_seen.add(identity)
        source = resolve_element(source_roots[edit.resource], edit.path)
        node = resolve_element(roots[edit.resource], edit.path)
        if getattr(source, edit.field) != edit.before or getattr(node, edit.field) != edit.after:
            raise ShuyiError("verification_edit_mismatch", edit.resource)
        setattr(node, edit.field, edit.before)
    actual_roots = {resource: parse_xml(output.entries[resource], resource) for resource in roots}
    for key, marker in markers.items():
        if key[0] not in plan.replacements:
            raise ShuyiError("verification_note_missing", key[0])
        anchor = resolve_element(actual_roots[key[0]], key[1])
        if (
            "".join(anchor.itertext()).strip() != "注"
            or anchor.get("data-sy-note-kind") != marker.kind
            or anchor.get("data-sy-note-id") != note_ids[key]
        ):
            raise ShuyiError("verification_note_label_invalid", key[0])
    for key, binding in bindings.items():
        if key[0] not in actual_roots:
            raise ShuyiError("verification_binding_invalid", key[0])
        node = resolve_element(actual_roots[key[0]], key[1])
        if (
            node.get("data-sy-node") != binding.node_id
            or node.get("data-sy-role") != binding.role
            or node.get("data-sy-content") != "target"
            or node.get("data-sy-level")
            != (str(binding.level) if binding.level is not None else None)
        ):
            raise ShuyiError("verification_binding_invalid", key[0])
    return roots


def note_text_fields(markers: dict, source_roots: dict) -> dict:
    from shuyi.epub.dom import element_path

    allowed = {}
    for key in markers:
        root = source_roots.get(key[0])
        if root is None:
            continue
        anchor = resolve_element(root, key[1])
        for index, (node, field) in enumerate(label_fields(anchor)):
            allowed[(key[0], element_path(root, node), field)] = "注" if index == 0 else ""
    return allowed
