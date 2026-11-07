from __future__ import annotations

import posixpath
from collections import Counter
from datetime import UTC, datetime

from lxml import etree

from shuyi.contracts import (
    ROLES,
    AttributeEdit,
    Book,
    Inventory,
    LayoutProfile,
    ShuyiError,
    StyledNode,
    TextEdit,
    ThemePlan,
)
from shuyi.epub.dom import (
    OPF,
    XHTML,
    element_path,
    is_protected,
    node_digest,
    parse_xml,
    resolve_element,
    serialize_xml,
)
from shuyi.epub.notes import find_note_markers, label_fields
from shuyi.epub.styles import specificity_guard
from shuyi.theme.compiler import compile_document, compile_note, theme_rules

RESERVED = frozenset(
    {
        "data-sy-node",
        "data-sy-role",
        "data-sy-level",
        "data-sy-content",
        "data-sy-note-kind",
        "data-sy-note-id",
    }
)


def set_attribute(resource, root, node, name, value, edits):
    before = node.get(name)
    if before == value:
        return
    path = element_path(root, node)
    # 同一属性只记录一次源值与最终值，便于独立逆向验证。
    for index, edit in enumerate(edits):
        if (edit.resource, edit.path, edit.name) == (resource, path, name):
            edits[index] = AttributeEdit(resource, path, name, edit.before, value)
            break
    else:
        edits.append(AttributeEdit(resource, path, name, before, value))
    node.set(name, value)


def check_collisions(book, roots, opf, asset):
    if asset in book.entries:
        raise ShuyiError("theme_already_present", asset)
    from shuyi.epub.package import resolve_href

    extra = []
    for item in opf.iter(f"{{{OPF}}}item"):
        media = item.get("media-type", "")
        if media.endswith("+xml") or media in {"application/xml", "text/xml"}:
            target = resolve_href(book.opf_path, item.get("href", ""))
            if target and target[0] not in roots and target[0] in book.entries:
                extra.append(parse_xml(book.entries[target[0]], target[0]))
    for root in [opf, *roots.values(), *extra]:
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            if node.get("id") in {"shuyi-never", "shuyi-reading"} or RESERVED.intersection(
                node.attrib
            ):
                raise ShuyiError("theme_marker_collision")
            if any(name.startswith("data-sy-") for name in node.attrib):
                raise ShuyiError("theme_marker_collision")


def admitted_bindings(book, inventory, profile, roots):
    if (
        inventory.source_sha256 != book.source_sha256
        or profile.source_sha256 != book.source_sha256
        or profile.inventory_digest != inventory.digest
    ):
        raise ShuyiError("theme_profile_mismatch")
    ids = {node.node_id for node in inventory.nodes}
    if len(ids) != len(inventory.nodes) or set(profile.assignments) != ids:
        raise ShuyiError("theme_profile_mismatch")
    result = []
    locations = set()
    for candidate in inventory.nodes:
        assignment = profile.assignments[candidate.node_id]
        if assignment.node_id != candidate.node_id or assignment.role not in ROLES | {None}:
            raise ShuyiError("theme_assignment_invalid")
        if assignment.role == "heading":
            if type(assignment.level) is not int or not 1 <= assignment.level <= 6:
                raise ShuyiError("theme_assignment_invalid")
        elif assignment.level is not None:
            raise ShuyiError("theme_assignment_invalid")
        if candidate.resource not in roots:
            raise ShuyiError("theme_node_invalid")
        node = resolve_element(roots[candidate.resource], candidate.path)
        if node_digest(node) != candidate.source_sha256:
            raise ShuyiError("theme_node_digest_mismatch", candidate.resource)
        location = (candidate.resource, candidate.path)
        if location in locations:
            raise ShuyiError("theme_node_duplicate")
        locations.add(location)
        if assignment.role is None:
            continue
        if candidate.resource in book.protected or is_protected(node):
            raise ShuyiError("theme_node_protected", candidate.resource)
        if (
            not candidate.node_id.isascii()
            or not candidate.node_id.startswith("n")
            or not candidate.node_id[1:].isdigit()
        ):
            raise ShuyiError("theme_node_id_invalid")
        result.append(
            StyledNode(
                candidate.node_id,
                candidate.resource,
                candidate.path,
                candidate.source_sha256,
                assignment.role,
                assignment.level,
            )
        )
    return tuple(result)


def apply_notes(book, roots, guards, attributes, texts, builtin):
    output = []
    markers = find_note_markers(book)
    for index, marker in enumerate(markers, 1):
        root = roots[marker.resource]
        node = resolve_element(root, marker.path)
        if marker.resource in book.protected or is_protected(node):
            raise ShuyiError("theme_note_protected", marker.resource)
        if any(
            not isinstance(child.tag, str) and (child.tail or "").strip()
            for child in node.iterdescendants()
        ):
            raise ShuyiError("theme_note_label_unsupported", marker.resource)
        set_attribute(marker.resource, root, node, "data-sy-note-kind", marker.kind, attributes)
        set_attribute(
            marker.resource, root, node, "data-sy-note-id", f"note{index:06d}", attributes
        )
        explicit = set(node.get("{http://www.idpf.org/2007/ops}type", "").split())
        if node.get("role") is None and (not explicit or marker.kind in explicit):
            set_attribute(marker.resource, root, node, "role", f"doc-{marker.kind}", attributes)
        fields = label_fields(node)
        if not fields:
            raise ShuyiError("theme_note_label_invalid", marker.resource)
        for position, (owner, field) in enumerate(fields):
            before = getattr(owner, field)
            after = "注" if position == 0 else ""
            if before != after:
                texts.append(
                    TextEdit(marker.resource, element_path(root, owner), field, before, after)
                )
                setattr(owner, field, after)
        output.extend(
            compile_note(
                marker.resource, root, node, guards[marker.resource], attributes, builtin=builtin
            )
        )
    return output, len(markers)


def add_assets(book, roots, opf, asset, changed, texts):
    for resource in changed:
        root = roots[resource]
        head = root.find(f"{{{XHTML}}}head")
        if head is None:
            raise ShuyiError("theme_head_missing", resource)
        etree.SubElement(
            head,
            f"{{{XHTML}}}link",
            rel="stylesheet",
            type="text/css",
            href=posixpath.relpath(asset, posixpath.dirname(resource)),
        )
    manifest = opf.find(f"{{{OPF}}}manifest")
    if manifest is None:
        raise ShuyiError("package_manifest_missing", book.opf_path)
    etree.SubElement(
        manifest,
        f"{{{OPF}}}item",
        id="shuyi-reading",
        href="shuyi/reading.css",
        **{"media-type": "text/css"},
    )
    if book.version.startswith("3"):
        metadata = opf.find(f"{{{OPF}}}metadata")
        if metadata is None:
            raise ShuyiError("package_metadata_missing", book.opf_path)
        existing = metadata.xpath(
            './opf:meta[@property="dcterms:modified"]', namespaces={"opf": OPF}
        )
        if len(existing) > 1:
            raise ShuyiError("package_modified_ambiguous", book.opf_path)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if existing:
            meta = existing[0]
            if meta.text != now:
                texts.append(
                    TextEdit(book.opf_path, element_path(opf, meta), "text", meta.text, now)
                )
                meta.text = now
        else:
            meta = etree.SubElement(metadata, f"{{{OPF}}}meta", property="dcterms:modified")
            meta.text = now


def plan_theme(
    book: Book, inventory: Inventory, profile: LayoutProfile, css: bytes | None = None
) -> ThemePlan:
    roots = {resource: parse_xml(book.entries[resource], resource) for resource in book.documents}
    opf = parse_xml(book.entries[book.opf_path], book.opf_path)
    asset = posixpath.join(posixpath.dirname(book.opf_path), "shuyi/reading.css")
    check_collisions(book, roots, opf, asset)
    bindings = admitted_bindings(book, inventory, profile, roots)
    affected = {binding.resource for binding in bindings} | {
        marker.resource for marker in find_note_markers(book)
    }
    guards = {resource: specificity_guard(book, resource, roots[resource]) for resource in affected}
    rules = theme_rules(css)
    attributes, texts, generated = [], [], []
    for binding in bindings:
        root = roots[binding.resource]
        node = resolve_element(root, binding.path)
        for name, value in [
            ("data-sy-node", binding.node_id),
            ("data-sy-role", binding.role),
            ("data-sy-content", "target"),
        ]:
            set_attribute(binding.resource, root, node, name, value, attributes)
        if binding.level is not None:
            set_attribute(
                binding.resource, root, node, "data-sy-level", str(binding.level), attributes
            )
    for resource in book.documents:
        if resource in affected:
            generated.extend(
                compile_document(
                    resource, roots[resource], bindings, rules, guards[resource], attributes
                )
            )
    note_css, note_count = apply_notes(book, roots, guards, attributes, texts, css is None)
    generated.extend(note_css)
    changed = {edit.resource for edit in [*attributes, *texts]}
    add_assets(book, roots, opf, asset, changed, texts)
    replacements = {resource: serialize_xml(roots[resource]) for resource in changed}
    replacements[book.opf_path] = serialize_xml(opf)
    replacements[asset] = "".join(generated).encode("utf-8")
    warnings = book.warnings if bindings else (*book.warnings, "zero_role_coverage")
    return ThemePlan(
        book.source_sha256,
        replacements,
        tuple(attributes),
        tuple(texts),
        bindings,
        asset,
        "shuyi-reading",
        dict(Counter(binding.role for binding in bindings)),
        note_count,
        sum(item.role is None for item in profile.assignments.values()),
        warnings,
    )
