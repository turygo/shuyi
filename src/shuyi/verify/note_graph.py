from __future__ import annotations

from shuyi.contracts import Book
from shuyi.epub.dom import XHTML, element_path, parse_xml
from shuyi.epub.package import resolve_href


def reciprocal_markers(book: Book) -> frozenset[tuple[str, tuple[int, ...], str]]:
    roots = {resource: parse_xml(book.entries[resource], resource) for resource in book.documents}
    ids = {}
    links = {}
    for resource, root in roots.items():
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            for value in (node.get("id"), node.get("name")):
                if value:
                    ids[(resource, value)] = node
            if node.tag == f"{{{XHTML}}}a" and node.get("href"):
                target = resolve_href(resource, node.get("href"))
                if target:
                    links[node] = (resource, target)
    result = set()
    for anchor, (resource, target) in links.items():
        if anchor.get("data-sy-note-kind") != "noteref":
            continue
        destination = ids.get(target)
        if destination is None:
            continue
        for back in destination.iter():
            if back not in links or back.get("data-sy-note-kind") != "backlink":
                continue
            back_resource, back_target = links[back]
            return_node = ids.get(back_target)
            if return_node is anchor or (
                return_node is not None and return_node in anchor.iterancestors()
            ):
                result.add((resource, element_path(roots[resource], anchor), "noteref"))
                result.add((back_resource, element_path(roots[back_resource], back), "backlink"))
    return frozenset(result)
