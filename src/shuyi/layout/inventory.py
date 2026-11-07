from __future__ import annotations

from dataclasses import asdict

from lxml import etree

from shuyi.contracts import Book, Inventory, LayoutNode
from shuyi.epub.dom import element_path, is_protected, node_digest, parse_xml
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
INLINE = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "br",
        "cite",
        "em",
        "i",
        "mark",
        "q",
        "ruby",
        "s",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "u",
        "wbr",
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
    direct = bool((node.text or "").strip())
    for child in node:
        if not isinstance(child.tag, str):
            direct = direct or bool((child.tail or "").strip())
            continue
        local = etree.QName(child).localname
        if local in INLINE and visible_text(child).strip():
            direct = True
        direct = direct or bool((child.tail or "").strip())
    return direct


def document_nodes(book: Book, resource: str, start: int) -> list[LayoutNode]:
    root = parse_xml(book.entries[resource], resource)
    css = style_evidence(book, resource, root)
    candidates = [node for node in root.iter() if candidate(node)]
    result = []
    for index, node in enumerate(candidates):
        path = element_path(root, node)
        ancestors = canonical([features(parent) for parent in reversed(list(node.iterancestors()))])
        source_css = css.get(path, "")
        before = visible_text(candidates[index - 1])[-500:] if index else ""
        after = visible_text(candidates[index + 1])[:500] if index + 1 < len(candidates) else ""
        group = fingerprint({"node": features(node), "ancestors": ancestors, "css": source_css})
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
            )
        )
    return result


def build_inventory(book: Book) -> Inventory:
    nodes: list[LayoutNode] = []
    for resource in book.documents:
        if resource not in book.protected:
            nodes.extend(document_nodes(book, resource, len(nodes) + 1))
    digest = fingerprint(
        {
            "version": VERSION,
            "source_sha256": book.source_sha256,
            "nodes": [asdict(node) for node in nodes],
        }
    )
    return Inventory(book.source_sha256, digest, tuple(nodes), book.warnings)
