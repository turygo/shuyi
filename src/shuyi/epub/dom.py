from __future__ import annotations

import hashlib

from lxml import etree

from shuyi.contracts import ElementPath, ShuyiError

XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
OPF = "http://www.idpf.org/2007/opf"
SVG = "http://www.w3.org/2000/svg"
MATHML = "http://www.w3.org/1998/Math/MathML"
PROTECTED_TAGS = frozenset(
    {"nav", "pre", "code", "svg", "math", "script", "style", "form", "rt", "rp", "rtc"}
)


def parse_xml(data: bytes, resource: str) -> etree._Element:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        remove_blank_text=False,
        strip_cdata=False,
    )
    try:
        root = etree.fromstring(data, parser)
        if any(isinstance(node, etree._Entity) for node in root.iter()):
            raise ShuyiError("xml_entity_unsupported", resource)
        return root
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ShuyiError("xml_invalid", resource) from exc


def serialize_xml(root: etree._Element) -> bytes:
    return etree.tostring(root.getroottree(), encoding="UTF-8", xml_declaration=True)


def element_path(root: etree._Element, node: etree._Element) -> ElementPath:
    indices = []
    while node is not root:
        parent = node.getparent()
        if parent is None:
            raise ShuyiError("element_path_invalid")
        children = [child for child in parent if isinstance(child.tag, str)]
        indices.append(children.index(node))
        node = parent
    return tuple(reversed(indices))


def resolve_element(root: etree._Element, path: ElementPath) -> etree._Element:
    node = root
    try:
        for index in path:
            if type(index) is not int or index < 0:
                raise IndexError
            node = [child for child in node if isinstance(child.tag, str)][index]
        return node
    except (IndexError, TypeError) as exc:
        raise ShuyiError("element_path_invalid") from exc


def node_digest(node: etree._Element) -> str:
    return hashlib.sha256(
        etree.tostring(node, method="c14n", with_comments=True, with_tail=False)
    ).hexdigest()


def is_protected(node: etree._Element) -> bool:
    def protected(element: etree._Element) -> bool:
        if not isinstance(element.tag, str):
            return False
        name = etree.QName(element)
        return (
            name.localname in PROTECTED_TAGS
            or name.namespace in {SVG, MATHML}
            or element.get("contenteditable") is not None
        )

    return any(protected(item) for item in node.iterancestors()) or any(
        protected(item) for item in node.iter()
    )


def canonical_document(root: etree._Element) -> bytes:
    tree = root.getroottree()
    return document_doctype(root) + b"\n" + etree.tostring(tree, method="c14n", with_comments=True)


def document_doctype(root: etree._Element) -> bytes:
    data = etree.tostring(root.getroottree(), encoding="UTF-8")
    offset = 0
    while True:
        start = data.find(b"<", offset)
        if start < 0:
            return b""
        if data.startswith(b"<!--", start):
            offset = data.index(b"-->", start) + 3
        elif data.startswith(b"<?", start):
            offset = data.index(b"?>", start) + 2
        elif data.startswith(b"<!DOCTYPE", start):
            break
        else:
            return b""
    quote = 0
    depth = 0
    index = start + 9
    while index < len(data):
        byte = data[index]
        if quote:
            if byte == quote:
                quote = 0
        elif data.startswith(b"<!--", index):
            index = data.index(b"-->", index) + 3
            continue
        elif data.startswith(b"<?", index):
            index = data.index(b"?>", index) + 2
            continue
        elif byte in (34, 39):
            quote = byte
        elif byte == 91:
            depth += 1
        elif byte == 93:
            depth -= 1
        elif byte == 62 and depth == 0:
            return data[start : index + 1]
        index += 1
    raise ShuyiError("xml_doctype_invalid")
