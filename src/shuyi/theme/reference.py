from __future__ import annotations

from decimal import Decimal

from lxml import etree

from shuyi.epub.dom import has_direct_text, resolve_element

BODY_ROLES = frozenset(
    {"body", "quote", "quote-text", "chapter-summary", "caption", "subtitle", "quote-attribution"}
)
HEADING_ROLES = frozenset({"heading", "chapter-number"})
DARK = "(prefers-color-scheme: dark)"


def primary_scale(binding):
    if binding.role == "heading":
        return Decimal({1: "1.6", 2: "1.4", 3: "1.3", 4: "1.2", 5: "1.1", 6: "1"}[binding.level])
    if binding.role in {"body", "quote-text"}:
        return Decimal("1.15")
    if binding.role in {"footnote", "endnote"}:
        return Decimal("0.9")
    return Decimal(1)


def reference_size(binding, bindings, node, *, structural=False):
    ancestors = [
        item
        for item in bindings
        if item.resource == binding.resource
        and len(item.path) < len(binding.path)
        and binding.path[: len(item.path)] == item.path
    ]
    if not binding.content_root:
        return "inherit"
    if any(item.content == "source" for item in ancestors):
        return "inherit"
    if structural or binding.flow == "inline" or binding.role not in BODY_ROLES | HEADING_ROLES:
        return "0.82em"
    parent_scale = Decimal(1)
    root = node.getroottree().getroot()
    for ancestor in ancestors:
        if ancestor.role == "body" and not has_direct_text(resolve_element(root, ancestor.path)):
            continue
        parent_scale *= primary_scale(ancestor)
    ratio = Decimal("0.943") / parent_scale
    return format(ratio.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".") + "em"


def reference_rules(binding, node, bindings):
    if not binding.content_root:
        values = {"font-size": "inherit"}
        if binding.exporter:
            values["opacity"] = "1"
            if etree.QName(node).localname not in {"em", "i"}:
                values["font-style"] = "normal"
        return [("", values)]
    tag = etree.QName(node).localname
    structural = any(
        etree.QName(parent).localname in {"li", "td", "th", "dt", "dd"}
        for parent in [node, *node.iterancestors()]
        if isinstance(parent.tag, str)
    )
    values = {
        "font-family": "Arial,Helvetica,sans-serif",
        "font-size": reference_size(binding, bindings, node, structural=structural),
        "color": "#6b7280",
        "line-height": "1.5",
    }
    if tag not in {"em", "i"}:
        values["font-style"] = "normal"
    if tag not in {"b", "strong"}:
        values["font-weight"] = "normal"
    if binding.exporter:
        values["opacity"] = "1"
    dark = {"color": "#929dac"}
    if binding.flow == "block":
        values["text-indent"] = "0"
        if binding.role in BODY_ROLES and not structural:
            values.update(
                {
                    "border-left": "1px solid #d8dfe7",
                    "padding-left": "0.65em",
                    "margin": "0.25em 0 1em",
                    "text-align": "start",
                }
            )
            dark["border-left-color"] = "#48515e"
        elif binding.role in HEADING_ROLES:
            values.update({"text-align": "center", "margin": "0.25em 0 1em"})
    return [("", values), (DARK, dark)]
