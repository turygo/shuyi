from __future__ import annotations

import tinycss2
from lxml import etree

from shuyi.contracts import ShuyiError
from shuyi.epub.dom import element_path, is_protected
from shuyi.epub.source_conditions import dynamic_property
from shuyi.epub.source_css import source_declarations


def validate_source_dynamics(root: etree._Element, matches: dict) -> None:
    def has_text(node: etree._Element) -> bool:
        if is_protected(node):
            return False
        if (node.text or "").strip():
            return True
        return any(
            (isinstance(child.tag, str) and has_text(child)) or (child.tail or "").strip()
            for child in node
        )

    for node in root.iter():
        if not isinstance(node.tag, str) or is_protected(node):
            continue
        rules = matches[element_path(root, node)]
        inline = source_declarations(tinycss2.parse_component_value_list(node.get("style", "")))
        if any(
            dynamic_property(name)
            for values in [inline, *(rule.declarations for rule in rules)]
            for name, _, _ in values
        ) and has_text(node):
            raise ShuyiError("css_cascade_unsupported")
