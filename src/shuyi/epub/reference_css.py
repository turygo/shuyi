from __future__ import annotations

import re

import cssselect2
from lxml import etree

from shuyi.contracts import ShuyiError
from shuyi.epub.bilingual_evidence import BLOCK
from shuyi.epub.dom import XHTML, element_path
from shuyi.epub.source_css import source_selectors

FIRST_LETTER_RESET = (
    ("font-size", "1em"),
    ("line-height", "inherit"),
    ("float", "none"),
    ("margin", "0"),
    ("padding", "0"),
)
REFERENCE_FIRST_LETTER = re.compile(
    r'(\[data-sy-node="n[0-9]{6,}"\](?::not\(#shuyi-never\)){1,128})::first-letter'
)


def first_letter_base(selector: str) -> str | None:
    matched = REFERENCE_FIRST_LETTER.fullmatch(selector)
    return matched[1] if matched is not None else None


def compile_reference_selector(selector: str) -> list:
    if first_letter_base(selector) is None:
        raise ShuyiError("css_selector_unsupported")
    return cssselect2.compile_selector_list(selector, namespaces={None: XHTML})


def reference_block(node: etree._Element) -> bool:
    return isinstance(node.tag, str) and etree.QName(node).localname in BLOCK


def validate_first_letter(declarations) -> None:
    # 仅允许完整的固定首字复位，不能借此开放任意伪元素或布局声明。
    if (
        len(declarations) != len(FIRST_LETTER_RESET)
        or {name: value for name, value, _ in declarations} != dict(FIRST_LETTER_RESET)
        or not all(important for _, _, important in declarations)
    ):
        raise ShuyiError("generated_css_reference_invalid")


def first_letter_paths(root: etree._Element, rules) -> frozenset[tuple[int, ...]]:
    matcher = cssselect2.Matcher()
    for rule in rules:
        if not rule.source_selector:
            continue
        original = cssselect2.compile_selector_list(
            rule.source_selector, namespaces=dict(rule.namespaces)
        )
        subjects = source_selectors(rule.source_selector, rule.namespaces)
        for before, subject in zip(original, subjects, strict=True):
            if before.pseudo_element == "first-letter":
                matcher.add_selector(subject, True)
    return frozenset(
        element_path(root, wrapped.etree_element)
        for wrapped in cssselect2.ElementWrapper.from_xml_root(root).iter_subtree()
        if reference_block(wrapped.etree_element) and matcher.match(wrapped)
    )
