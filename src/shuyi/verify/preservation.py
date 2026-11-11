from __future__ import annotations

import posixpath
import re
from pathlib import Path

import tinycss2
from lxml import etree

from shuyi.contracts import Book, ShuyiError, ThemePlan
from shuyi.epub.archive import load_book
from shuyi.epub.dom import OPF, XHTML, canonical_document, parse_xml
from shuyi.epub.package import check_references, manifest_resources, resolve_href
from shuyi.epub.reference_css import first_letter_base
from shuyi.epub.styles import validate_generated_css
from shuyi.verify.edits import reverse_edits, validate_bindings
from shuyi.verify.metadata import reverse_modified


def css_properties(output: Book, asset: str) -> dict[tuple[str, tuple[int, ...]], set[str]]:
    from shuyi.epub.dom import element_path, resolve_element

    rules, _ = tinycss2.parse_stylesheet_bytes(
        output.entries[asset], skip_comments=True, skip_whitespace=True
    )
    result: dict[tuple[str, tuple[int, ...]], set[str]] = {}
    addresses = {}
    for resource in output.documents:
        root = parse_xml(output.entries[resource], resource)
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            for kind in ("node", "note-id"):
                value = node.get(f"data-sy-{kind}")
                if value:
                    addresses[(kind, value)] = (resource, root, node)
    for rule in rules:
        if rule.type != "qualified-rule":
            continue
        selector = tinycss2.serialize(rule.prelude).strip()
        if first_letter_base(selector) is not None:
            continue
        match = re.match(r'^\[data-sy-(node|note-id)="([^"]+)"\]', selector)
        if match is None or match.groups() not in addresses:
            raise ShuyiError("verification_css_address_invalid")
        resource, root, node = addresses[match.groups()]
        relative = tuple(
            int(index) - 1 for index in re.findall(r">\s*:nth-child\(([0-9]+)\)", selector)
        )
        node = resolve_element(node, relative)
        declarations = tinycss2.parse_declaration_list(
            rule.content, skip_comments=True, skip_whitespace=True
        )
        properties = {
            item.lower_name
            for item in declarations
            if item.type == "declaration" and item.important
        }
        key = (resource, element_path(root, node))
        result.setdefault(key, set()).update(properties)
    return result


def reverse_document_assets(book: Book, plan: ThemePlan, roots: dict[str, etree._Element]) -> None:
    for resource, root in roots.items():
        heads = root.findall(f"{{{XHTML}}}head")
        if len(heads) != 1:
            raise ShuyiError("verification_head_invalid", resource)
        links = [
            node
            for node in heads[0]
            if node.tag == f"{{{XHTML}}}link"
            and resolve_href(resource, node.get("href", "")) == (plan.asset_path, "")
        ]
        if len(links) != 1:
            raise ShuyiError("verification_theme_link_invalid", resource)
        link = links[0]
        expected = {
            "rel": "stylesheet",
            "type": "text/css",
            "href": posixpath.relpath(plan.asset_path, posixpath.dirname(resource)),
        }
        if dict(link.attrib) != expected or len(link) or link.text or link.tail:
            raise ShuyiError("verification_theme_link_invalid", resource)
        heads[0].remove(link)
        if canonical_document(root) != canonical_document(
            parse_xml(book.entries[resource], resource)
        ):
            raise ShuyiError("verification_structure_changed", resource)


def verify_package(book: Book, output: Book, plan: ThemePlan) -> None:
    original = parse_xml(book.entries[book.opf_path], book.opf_path)
    changed = parse_xml(output.entries[book.opf_path], book.opf_path)
    manifest = changed.find(f"{{{OPF}}}manifest")
    if manifest is None:
        raise ShuyiError("verification_package_invalid", book.opf_path)
    items = [node for node in manifest if node.get("id") == plan.asset_id]
    expected = {"id": plan.asset_id, "href": "shuyi/reading.css", "media-type": "text/css"}
    if (
        len(items) != 1
        or dict(items[0].attrib) != expected
        or len(items[0])
        or items[0].text
        or items[0].tail
    ):
        raise ShuyiError("verification_package_invalid", book.opf_path)
    manifest.remove(items[0])
    reverse_modified(book, plan, original, changed)
    if canonical_document(original) != canonical_document(changed):
        raise ShuyiError("verification_package_changed", book.opf_path)


def verify_output(book: Book, output: Path, plan: ThemePlan) -> dict[str, object]:
    if plan.source_sha256 != book.source_sha256:
        raise ShuyiError("verification_source_mismatch")
    expected_asset = posixpath.join(posixpath.dirname(book.opf_path), "shuyi/reading.css")
    if (
        plan.asset_path != expected_asset
        or plan.asset_id != "shuyi-reading"
        or plan.asset_path in book.entries
        or plan.asset_path not in plan.replacements
    ):
        raise ShuyiError("verification_asset_invalid")
    result = load_book(output)
    if set(result.entries) != set(book.entries) | {plan.asset_path}:
        raise ShuyiError("verification_members_changed")
    permitted = set(book.documents) - set(book.protected)
    if set(plan.replacements) - permitted - {book.opf_path, plan.asset_path}:
        raise ShuyiError("verification_resource_forbidden")
    for resource, data in result.entries.items():
        if data != plan.replacements.get(resource, book.entries.get(resource)):
            raise ShuyiError("verification_bytes_changed", resource)
    validate_plan_counts(book, plan)
    validate_bindings(book, plan)
    validate_generated_css(book, plan, result)
    roots = reverse_edits(book, result, plan, css_properties(result, plan.asset_path))
    reverse_document_assets(book, plan, roots)
    verify_package(book, result, plan)
    check_book(result)
    if (
        result.version != book.version
        or result.documents != book.documents
        or result.protected != book.protected
    ):
        raise ShuyiError("verification_inventory_changed")
    return {
        "source_sha256": book.source_sha256,
        "output_sha256": result.source_sha256,
        "styled_nodes": len(plan.bindings),
        "role_counts": plan.role_counts,
        "note_markers": plan.note_count,
        "unknown_count": plan.unknown_count,
        "protected_resources": sorted(book.protected),
        "warnings": list(plan.warnings),
    }


def validate_plan_counts(book: Book, plan: ThemePlan) -> None:
    from collections import Counter

    from shuyi.epub.notes import find_note_markers
    from shuyi.epub.package import XML_TYPES

    counts = dict(Counter(binding.role for binding in plan.bindings))
    if plan.role_counts != counts or plan.note_count != len(find_note_markers(book)):
        raise ShuyiError("verification_counts_invalid")
    resources = manifest_resources(book)
    for resource in {book.opf_path} | {
        path for path, media in resources.items() if media in XML_TYPES
    }:
        root = parse_xml(book.entries[resource], resource)
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            if node.get("id") in {"shuyi-never", "shuyi-reading"} or any(
                name.startswith("data-sy-") for name in node.attrib
            ):
                raise ShuyiError("verification_source_collision", resource)


def check_book(book: Book) -> dict[str, object]:
    check_references(book)
    resources = manifest_resources(book)
    asset = posixpath.join(posixpath.dirname(book.opf_path), "shuyi/reading.css")
    markers = []
    from shuyi.epub.package import XML_TYPES

    for resource, media in resources.items():
        if media not in XML_TYPES:
            continue
        root = parse_xml(book.entries[resource], resource)
        markers.extend(
            (resource, node)
            for node in root.iter()
            if isinstance(node.tag, str)
            and any(name.startswith("data-sy-") for name in node.attrib)
        )
    package = parse_xml(book.entries[book.opf_path], book.opf_path)
    generated_item = package.find(f"{{{OPF}}}manifest/{{{OPF}}}item[@id='shuyi-reading']")
    if asset in book.entries or markers or generated_item is not None:
        from shuyi.verify.generated import check_generated_theme

        check_generated_theme(book, asset, markers, resources)
    return {
        "source_sha256": book.source_sha256,
        "version": book.version,
        "documents": len(book.documents),
        "protected_resources": sorted(book.protected),
        "generated_theme": asset in book.entries,
        "warnings": list(book.warnings),
    }
