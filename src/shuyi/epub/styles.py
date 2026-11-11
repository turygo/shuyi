from __future__ import annotations

import json
import re

import cssselect2
import tinycss2
from lxml import etree

from shuyi.contracts import Book, ElementPath, ShuyiError, ThemePlan
from shuyi.epub.bilingual import is_exporter_reference
from shuyi.epub.css_values import SAFE_PROPERTIES, validate_css_value
from shuyi.epub.dom import XHTML, element_path, is_protected, parse_xml
from shuyi.epub.package import resolve_href
from shuyi.epub.reference_css import (
    compile_reference_selector,
    first_letter_base,
    reference_block,
    validate_first_letter,
)
from shuyi.epub.source_conditions import dynamic_property
from shuyi.epub.source_css import CSSRule, parse_source_rules, source_selectors
from shuyi.epub.source_dynamics import validate_source_dynamics

NOTE_PROPERTIES = frozenset({"display", "width", "height", "min-width"})
STATIC_PSEUDOS = frozenset(
    {
        "root",
        "first-child",
        "last-child",
        "only-child",
        "first-of-type",
        "last-of-type",
        "only-of-type",
        "nth-child",
        "nth-last-child",
        "nth-of-type",
        "nth-last-of-type",
        "not",
        "is",
        "where",
        "empty",
        "lang",
    }
)
DARK_MEDIA = frozenset({"(prefers-color-scheme: dark)", "(prefers-color-scheme: light)"})


def declarations(text: str) -> tuple[tuple[str, str, bool], ...]:
    result = []
    for item in tinycss2.parse_declaration_list(text, skip_comments=True, skip_whitespace=True):
        if item.type != "declaration":
            raise ShuyiError("css_declaration_invalid")
        result.append((item.lower_name, tinycss2.serialize(item.value).strip(), item.important))
    return tuple(result)


def compile_selector(selector: str) -> list[cssselect2.compiler.CompiledSelector]:
    if "::" in selector or any(
        name.lower() not in STATIC_PSEUDOS for name in re.findall(r":([\w-]+)", selector)
    ):
        raise ShuyiError("css_selector_unsupported")
    try:
        compiled = cssselect2.compile_selector_list(
            selector,
            namespaces={None: XHTML, "html": XHTML, "epub": "http://www.idpf.org/2007/ops"},
        )
        if any(item.pseudo_element or item.never_matches for item in compiled):
            raise ShuyiError("css_selector_unsupported")
        return compiled
    except (cssselect2.SelectorError, TypeError, ValueError) as exc:
        raise ShuyiError("css_selector_invalid") from exc


def validate_value(name: str, value: str, *, note: bool = False, reference: bool = False) -> None:
    if name == "opacity" and reference and value == "1":
        return
    if name not in SAFE_PROPERTIES and not (note and name in NOTE_PROPERTIES):
        raise ShuyiError("css_property_unsupported")

    def walk(tokens):
        for token in tokens:
            if token.type in {"error", "url", "at-keyword"}:
                raise ShuyiError("css_value_unsafe")
            if token.type == "function":
                if token.lower_name not in {
                    "rgb",
                    "rgba",
                    "hsl",
                    "hsla",
                    "calc",
                    "min",
                    "max",
                    "clamp",
                }:
                    raise ShuyiError("css_value_unsafe")
                walk(token.arguments)
            if hasattr(token, "content"):
                walk(token.content)

    walk(tinycss2.parse_component_value_list(value))
    if name in NOTE_PROPERTIES:
        if name == "display" and value != "inline-block":
            raise ShuyiError("css_note_value_unsafe")
        if name != "display" and not re.fullmatch(r"(?:0|(?:0?\.\d+|[12](?:\.\d+)?)em)", value):
            raise ShuyiError("css_note_value_unsafe")
    else:
        validate_css_value(name, value)


def parse_rules(
    text: str, *, custom: bool = False, condition: str = "", generated: bool = False
) -> tuple[CSSRule, ...]:
    if not custom and not generated:
        return parse_source_rules(text, condition=condition)
    result = []
    for item in tinycss2.parse_stylesheet(text, skip_comments=True, skip_whitespace=True):
        if item.type == "qualified-rule":
            selector = tinycss2.serialize(item.prelude).strip()
            if generated and not custom and first_letter_base(selector) is not None:
                compile_reference_selector(selector)
            else:
                compile_selector(selector)
            values = declarations(tinycss2.serialize(item.content))
            if custom:
                for name, value, _ in values:
                    validate_value(name, value)
            if any(name == "all" or dynamic_property(name) for name, _, _ in values):
                raise ShuyiError("css_cascade_unsupported")
            result.append(CSSRule(selector, values, condition))
        elif item.type == "at-rule":
            name = item.lower_at_keyword
            prelude = tinycss2.serialize(item.prelude).strip()
            if name == "namespace":
                raise ShuyiError("css_namespace_unsupported")
            if name != "media" or item.content is None or prelude not in DARK_MEDIA:
                raise ShuyiError("css_at_rule_unsupported")
            if condition:
                raise ShuyiError("css_condition_unsupported")
            result.extend(
                parse_rules(
                    tinycss2.serialize(item.content),
                    custom=custom,
                    condition=prelude,
                    generated=generated,
                )
            )
        else:
            raise ShuyiError("css_invalid")
    return tuple(result)


def source_rules(book: Book, resource: str, root: etree._Element) -> tuple[CSSRule, ...]:
    rules = []
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        local = etree.QName(node).localname
        if local == "style":
            text = "".join(node.itertext())
        elif local == "link" and "stylesheet" in node.get("rel", "").split():
            target = resolve_href(resource, node.get("href", ""))
            if target is None or target[0] not in book.entries:
                raise ShuyiError("css_reference_unsupported", resource)
            try:
                text = book.entries[target[0]].decode("utf-8-sig")
            except UnicodeError as exc:
                raise ShuyiError("css_encoding_unsupported", resource) from exc
        else:
            continue
        media = node.get("media", "").strip()
        rules.extend(parse_rules(text, condition=media))
    return tuple(rules)


def matching_rules(
    root: etree._Element, rules: tuple[CSSRule, ...]
) -> dict[ElementPath, tuple[CSSRule, ...]]:
    matcher = cssselect2.Matcher()
    for index, rule in enumerate(rules):
        selectors = (
            source_selectors(rule.source_selector, rule.namespaces)
            if rule.source_selector
            else compile_reference_selector(rule.selector)
            if first_letter_base(rule.selector) is not None
            else compile_selector(rule.selector)
        )
        for selector in selectors:
            matcher.add_selector(selector, (index, rule))
    result = {}
    for wrapper in cssselect2.ElementWrapper.from_xml_root(root).iter_subtree():
        result[element_path(root, wrapper.etree_element)] = tuple(
            payload[1] for _, _, pseudo, payload in matcher.match(wrapper) if pseudo is None
        )
    return result


def style_evidence(book: Book, resource: str, root: etree._Element) -> dict[ElementPath, str]:
    matches = matching_rules(root, source_rules(book, resource, root))
    validate_source_dynamics(root, matches)
    result = {}
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        evidence = []
        for ancestor in [*reversed(list(node.iterancestors())), node]:
            path = element_path(root, ancestor)
            records = [
                (
                    rule.source_selector or rule.selector,
                    rule.condition,
                    rule.declarations,
                    rule.namespaces,
                )
                for rule in matches[path]
            ]
            inline = ancestor.get("style", "")
            if inline:
                declarations(inline)
            evidence.append({"tag": ancestor.tag, "css": records, "inline": inline})
        result[element_path(root, node)] = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return result


def specificity_guard(book: Book, resource: str, root: etree._Element) -> int:
    maximum = 0
    for rule in source_rules(book, resource, root):
        for selector in source_selectors(rule.source_selector or rule.selector, rule.namespaces):
            maximum = max(maximum, selector.specificity[0])
    if maximum >= 128:
        raise ShuyiError("css_specificity_limit", resource)
    return maximum + 1


def property_footprint(name: str) -> frozenset[str]:
    if name.startswith("page-break-"):
        return frozenset({name.removeprefix("page-")})
    if name == "font-variant":
        return frozenset(
            f"font-variant-{part}"
            for part in (
                "ligatures",
                "caps",
                "numeric",
                "east-asian",
                "alternates",
                "position",
                "emoji",
            )
        )
    if name == "border-radius":
        return frozenset(
            f"border-{corner}-radius"
            for corner in ("top-left", "top-right", "bottom-right", "bottom-left")
        )
    if name in {"margin", "padding", "border-color", "border-width", "border-style"}:
        prefix, _, suffix = name.partition("-")
        return frozenset(
            f"{prefix}-{side}" + (f"-{suffix}" if suffix else "")
            for side in ("top", "right", "bottom", "left")
        )
    if name in {"margin-inline", "padding-inline", "margin-block", "padding-block"}:
        return frozenset({name + "-start", name + "-end"})
    if name == "border":
        return frozenset(
            f"border-{side}-{part}"
            for side in ("top", "right", "bottom", "left")
            for part in ("color", "width", "style")
        )
    if name in {"border-top", "border-right", "border-bottom", "border-left"}:
        return frozenset(name + "-" + part for part in ("color", "width", "style"))
    if name == "font":
        return property_footprint("font-variant") | frozenset(
            {
                "font-family",
                "font-size",
                "font-style",
                "font-weight",
                "font-stretch",
                "line-height",
                "font-size-adjust",
                "font-kerning",
                "font-feature-settings",
                "font-language-override",
                "font-optical-sizing",
                "font-variation-settings",
            }
        )
    if name == "text-decoration":
        return frozenset(
            {
                "text-decoration-line",
                "text-decoration-color",
                "text-decoration-style",
                "text-decoration-thickness",
            }
        )
    if name in {"all", "background", "animation", "transition"} or name.startswith("--"):
        raise ShuyiError("css_inline_unsupported")
    return frozenset({name})


def demote_inline(before: str, properties: set[str]) -> str:
    covered = (
        set().union(*(property_footprint(name) for name in properties)) if properties else set()
    )
    values = []
    changed = False
    for name, value, important in declarations(before):
        footprint = property_footprint(name)
        overlap = footprint & covered
        # 逻辑与物理属性的映射依赖书写方向，无法完整覆盖时拒绝。
        if important and any(p.startswith(("margin-", "padding-", "border-")) for p in footprint):
            family = name.split("-")[0]
            related = any(p.startswith(family + "-") for p in covered)
            if related and not overlap:
                raise ShuyiError("css_inline_overlap_unsupported")
        if important and overlap:
            if not footprint <= covered:
                raise ShuyiError("css_inline_partial_override")
            important = False
            changed = True
        values.append(f"{name}:{value}" + (" !important" if important else "") + ";")
    return "".join(values) if changed else before


def validate_inline_demotion(before: str, after: str, properties: set[str]) -> None:
    if demote_inline(before, properties) != after:
        raise ShuyiError("css_inline_demotion_invalid")


def validate_generated_css(book: Book, plan: ThemePlan, output_book: Book) -> None:
    from shuyi.epub.dom import resolve_element
    from shuyi.epub.notes import find_note_markers

    try:
        text = output_book.entries[plan.asset_path].decode("utf-8")
    except (KeyError, UnicodeError) as exc:
        raise ShuyiError("generated_css_invalid") from exc
    rules = parse_rules(text, generated=True)
    roots = {resource: parse_xml(book.entries[resource], resource) for resource in book.documents}
    notes = {
        (item.resource, item.path): (index, item)
        for index, item in enumerate(find_note_markers(book), 1)
    }
    allowed = {}
    for resource, root in roots.items():
        if not any(binding.resource == resource for binding in plan.bindings) and not any(
            location == resource for location, _ in notes
        ):
            continue
        guard = specificity_guard(book, resource, root)
        for binding in plan.bindings:
            if binding.resource != resource:
                continue
            node = resolve_element(root, binding.path)
            if resource in book.protected or is_protected(node):
                raise ShuyiError("generated_css_protected", resource)
            selector = f'[data-sy-node="{binding.node_id}"]' + ":not(#shuyi-never)" * guard
            allowed[selector] = (False, binding.content == "source" and binding.exporter)
            if binding.content == "source" and reference_block(node):
                allowed[selector + "::first-letter"] = (False, False)
        for (location, path), (index, marker) in notes.items():
            if location == resource:
                base = f'[data-sy-note-id="note{index:06d}"][data-sy-note-kind="{marker.kind}"]'
                selector = base + ":not(#shuyi-never)" * guard
                allowed[selector] = (True, False)
                node = resolve_element(root, path)
                for child in node.iterdescendants():
                    if isinstance(child.tag, str) and etree.QName(child).localname == "sup":
                        suffix = "".join(
                            f" > :nth-child({i + 1})" for i in element_path(node, child)
                        )
                        allowed[base + suffix + ":not(#shuyi-never)" * guard] = (True, False)
    for rule in rules:
        if rule.selector not in allowed:
            raise ShuyiError("generated_css_selector_invalid")
        if rule.condition and rule.condition not in DARK_MEDIA:
            raise ShuyiError("generated_css_condition_invalid")
        if first_letter_base(rule.selector) is not None:
            if rule.condition:
                raise ShuyiError("generated_css_reference_invalid")
            validate_first_letter(rule.declarations)
            continue
        for name, value, important in rule.declarations:
            if not important:
                raise ShuyiError("generated_css_priority_invalid")
            note, reference = allowed[rule.selector]
            validate_value(name, value, note=note, reference=reference)


def validate_standalone_css(book: Book, asset_path: str) -> None:
    try:
        rules = parse_rules(book.entries[asset_path].decode("utf-8"), generated=True)
    except (KeyError, UnicodeError) as exc:
        raise ShuyiError("generated_css_invalid") from exc
    allowed = {}
    for resource in book.documents:
        root = parse_xml(book.entries[resource], resource)
        if not any(
            isinstance(node.tag, str)
            and (node.get("data-sy-node") or node.get("data-sy-note-kind"))
            for node in root.iter()
        ):
            continue
        for link in list(root.iter(f"{{{XHTML}}}link")):
            target = resolve_href(resource, link.get("href", ""))
            if target and target[0] == asset_path:
                link.getparent().remove(link)
        guard = specificity_guard(book, resource, root)
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            if node.get("data-sy-node"):
                if resource in book.protected or is_protected(node):
                    raise ShuyiError("generated_css_protected", resource)
                selector = (
                    f'[data-sy-node="{node.get("data-sy-node")}"]' + ":not(#shuyi-never)" * guard
                )
                content_kind = node.get("data-sy-content")
                if content_kind not in {"target", "source", "mixed"}:
                    raise ShuyiError("generated_binding_invalid", resource)
                allowed[selector] = (
                    False,
                    content_kind == "source" and is_exporter_reference(node),
                )
                if content_kind == "source" and reference_block(node):
                    allowed[selector + "::first-letter"] = (False, False)
            kind = node.get("data-sy-note-kind")
            if kind:
                if kind not in {"noteref", "backlink"} or is_protected(node):
                    raise ShuyiError("generated_css_note_invalid", resource)
                content = node.get("data-sy-note-id", "")
                if not re.fullmatch(r"note[0-9]{6,}", content):
                    raise ShuyiError("generated_css_note_invalid", resource)
                base = f'[data-sy-note-id="{content}"][data-sy-note-kind="{kind}"]'
                allowed[base + ":not(#shuyi-never)" * guard] = (True, False)
                for child in node.iterdescendants():
                    if isinstance(child.tag, str) and etree.QName(child).localname == "sup":
                        suffix = "".join(
                            f" > :nth-child({i + 1})" for i in element_path(node, child)
                        )
                        allowed[base + suffix + ":not(#shuyi-never)" * guard] = (True, False)
    for rule in rules:
        if rule.selector not in allowed or (rule.condition and rule.condition not in DARK_MEDIA):
            raise ShuyiError("generated_css_selector_invalid")
        if first_letter_base(rule.selector) is not None:
            if rule.condition:
                raise ShuyiError("generated_css_reference_invalid")
            validate_first_letter(rule.declarations)
            continue
        for name, value, important in rule.declarations:
            if not important:
                raise ShuyiError("generated_css_priority_invalid")
            note, reference = allowed[rule.selector]
            validate_value(name, value, note=note, reference=reference)
