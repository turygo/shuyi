from __future__ import annotations

import re

import tinycss2
from lxml import etree

from shuyi.epub.dom import XHTML, is_protected

INLINE = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
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
BLOCK = frozenset(
    {
        "p",
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
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "blockquote",
        "td",
        "th",
        "dt",
        "dd",
        "figcaption",
    }
)
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]")
LATIN = re.compile(r"[A-Za-z\u00c0-\u024f]")


def local(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def eligible(node: etree._Element) -> bool:
    return (
        isinstance(node.tag, str)
        and etree.QName(node).namespace in {None, XHTML}
        and local(node) in INLINE | BLOCK
        and not is_protected(node)
    )


def text(node: etree._Element) -> str:
    if not isinstance(node.tag, str) or is_protected(node):
        return ""
    parts = [node.text or ""]
    for child in node:
        parts.append(text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def script(value: str) -> str:
    han, latin = len(HAN.findall(value)), len(LATIN.findall(value))
    if latin > han:
        return "source"
    if han:
        return "target"
    return "neutral"


def has_han(value: str) -> bool:
    return HAN.search(value) is not None


def language(value: str | None) -> str | None:
    value = (value or "").lower().strip()
    if value == "zh" or value.startswith("zh-"):
        return "target"
    if value == "en" or value.startswith("en-"):
        return "source"
    return None


def own_language(node: etree._Element) -> tuple[str | None, bool]:
    html, xml = node.get("lang"), node.get(XML_LANG)
    first, second = language(html), language(xml)
    conflict = html is not None and xml is not None and first != second
    return first or second, conflict


def exporter_signature(node: etree._Element) -> bool:
    declarations = {}
    for item in tinycss2.parse_declaration_list(
        node.get("style", ""), skip_whitespace=True, skip_comments=True
    ):
        if item.type != "declaration":
            return False
        declarations[item.lower_name] = (
            tinycss2.serialize([token for token in item.value if token.type != "comment"])
            .strip()
            .lower()
        )
    try:
        opacity = float(declarations.get("opacity", "nan")) == 0.8
        size = declarations.get("font-size", "")
        size_ok = size.endswith("em") and float(size[:-2]) == 0.85
    except ValueError:
        return False
    if not opacity or not size_ok:
        return False
    full = (
        declarations.get("color") in {"#888", "#888888", "rgb(136, 136, 136)"}
        and declarations.get("font-style") == "italic"
        and bool(declarations.get("margin-top"))
    )
    short = (
        own_language(node) == ("source", False)
        and set(declarations) <= {"opacity", "font-size", "display"}
        and declarations.get("display", "block") == "block"
    )
    return full or short


def preceding_inline_text(node: etree._Element) -> str:
    parts = []
    previous = node.getprevious()
    while previous is not None:
        parts.append(previous.tail or "")
        if not isinstance(previous.tag, str) or local(previous) == "br":
            previous = previous.getprevious()
            continue
        if (
            local(previous) not in INLINE
            or not eligible(previous)
            or exporter_signature(previous)
            or previous.get("data-bilingual") == "source"
            or own_language(previous)[0] == "source"
        ):
            break
        parts.append(text(previous))
        previous = previous.getprevious()
    if previous is None and node.getparent() is not None:
        parts.append(node.getparent().text or "")
    return "".join(reversed(parts))


def previous_segment(node: etree._Element) -> etree._Element | str | None:
    previous = node.getprevious()
    while previous is not None:
        if (previous.tail or "").strip():
            segment = previous.tail
            if not has_han(segment):
                prefix = preceding_inline_text(node)
                if has_han(prefix):
                    return prefix
            return segment
        if not isinstance(previous.tag, str) or (
            local(previous) == "br" and etree.QName(previous).namespace in {None, XHTML}
        ):
            previous = previous.getprevious()
            continue
        if local(previous) in INLINE and (local(node) in BLOCK or not has_han(text(previous))):
            prefix = preceding_inline_text(node)
            if has_han(prefix):
                return prefix
        return previous
    parent = node.getparent()
    return parent.text if parent is not None and (parent.text or "").strip() else None


def compatible(target: etree._Element, source: etree._Element, *, explicit: bool) -> bool:
    if not eligible(target):
        return False
    first, second = local(target), local(source)
    return (
        first in INLINE
        and second in INLINE
        or first in BLOCK
        and second in BLOCK
        and (explicit or first == second)
    )
