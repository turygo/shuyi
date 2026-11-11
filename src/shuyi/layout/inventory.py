from __future__ import annotations

from dataclasses import asdict

from lxml import etree

from shuyi.contracts import Book, ContentBinding, Inventory, LayoutNode
from shuyi.epub.bilingual import (
    content_context,
    find_bilingual,
    is_exporter_reference,
    require_bilingual,
)
from shuyi.epub.dom import element_path, has_direct_text, is_protected, node_digest, parse_xml
from shuyi.epub.styles import style_evidence
from shuyi.layout.protocol import VERSION, canonical, fingerprint

XHTML = "http://www.w3.org/1999/xhtml"
NATIVE = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li",
        "blockquote",
        "td",
        "th",
        "dt",
        "dd",
        "figcaption",
    }
)
CONTAINERS = frozenset(
    {
        "div",
        "section",
        "article",
        "aside",
        "header",
        "footer",
        "main",
        "body",
        "figure",
        "address",
        "details",
        "summary",
    }
)


def features(node: etree._Element) -> dict[str, object]:
    attributes = {
        key: value
        for key, value in sorted(node.attrib.items())
        if key in {"class", "role", "style", "{http://www.idpf.org/2007/ops}type"}
        or key.startswith("aria-")
    }
    return {"tag": node.tag, "attributes": attributes}


def visible_text(node: etree._Element) -> str:
    return "".join(node.itertext())


def candidate(node: etree._Element) -> bool:
    if not isinstance(node.tag, str) or is_protected(node):
        return False
    name = etree.QName(node)
    if name.namespace not in {None, XHTML} or not visible_text(node).strip():
        return False
    if name.localname not in NATIVE | CONTAINERS:
        return False
    return has_direct_text(node)


def document_nodes(
    book: Book,
    resource: str,
    start: int,
    *,
    bilingual: bool = False,
    bindings: tuple[ContentBinding, ...] = (),
) -> list[LayoutNode]:
    root = parse_xml(book.entries[resource], resource)
    css = style_evidence(book, resource, root)
    roots = {binding.path for binding in bindings}
    source_scopes = tuple(binding.path for binding in bindings if binding.content == "source")
    candidates = []
    for node in root.iter():
        if candidate(node):
            candidates.append(node)
        elif bilingual and isinstance(node.tag, str) and not is_protected(node):
            path = element_path(root, node)
            if path in roots or (
                is_exporter_reference(node)
                and any(path[: len(scope)] == scope for scope in source_scopes)
            ):
                candidates.append(node)
    result = []
    for index, node in enumerate(candidates):
        path = element_path(root, node)
        ancestors = canonical([features(parent) for parent in reversed(list(node.iterancestors()))])
        source_css = css.get(path, "")
        before = visible_text(candidates[index - 1])[-500:] if index else ""
        after = visible_text(candidates[index + 1])[:500] if index + 1 < len(candidates) else ""
        identity = content_context(root, path, bindings, bilingual=bilingual)
        signature = {"node": features(node), "ancestors": ancestors, "css": source_css}
        if bilingual:
            signature["content"] = {
                key: value for key, value in identity.items() if key != "pair_id"
            }
        group = fingerprint(signature)
        result.append(
            LayoutNode(
                node_id=f"n{start + index:06d}",
                resource=resource,
                path=path,
                source_sha256=node_digest(node),
                tag=etree.QName(node).localname,
                text=visible_text(node),
                markup=etree.tostring(node, encoding="unicode", with_tail=False),
                context=before + after,
                ancestors=ancestors,
                source_css=source_css,
                group_key=group,
                **identity,
            )
        )
    return result


def build_inventory(book: Book, *, bilingual: bool = False) -> Inventory:
    bindings: tuple[ContentBinding, ...] = ()
    warnings = list(book.warnings)
    if bilingual:
        report = find_bilingual(book)
        require_bilingual(report)
        bindings = report.bindings
        warnings.extend(
            f"{issue.code}:{issue.resource}:{'.'.join(map(str, issue.path))}"
            for issue in report.issues
        )
    nodes: list[LayoutNode] = []
    for resource in book.documents:
        if resource not in book.protected:
            resource_bindings = tuple(item for item in bindings if item.resource == resource)
            nodes.extend(
                document_nodes(
                    book, resource, len(nodes) + 1, bilingual=bilingual, bindings=resource_bindings
                )
            )
    digest = fingerprint(
        {
            "version": VERSION,
            "source_sha256": book.source_sha256,
            "bilingual": bilingual,
            "content": [asdict(binding) for binding in bindings],
            "nodes": [asdict(node) for node in nodes],
        }
    )
    return Inventory(book.source_sha256, digest, tuple(nodes), tuple(warnings), bilingual, bindings)
