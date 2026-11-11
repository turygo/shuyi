from __future__ import annotations

import re
from dataclasses import dataclass

import cssselect2
import tinycss2

from shuyi.contracts import ShuyiError
from shuyi.epub.source_conditions import validate_keyframes, validate_supports


@dataclass(frozen=True)
class CSSRule:
    selector: str
    declarations: tuple[tuple[str, str, bool], ...]
    condition: str = ""
    source_selector: str = ""
    namespaces: tuple[tuple[str | None, str], ...] = ()


STATES = {"link", "visited", "hover", "active", "focus"}
PSEUDO_ELEMENTS = {"first-letter", "first-line", "before", "after"}
STATIC = {
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
    "empty",
    "lang",
    "any-link",
}


def selector_subject(tokens, *, negated=False) -> str:
    result = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type != "literal" or token.value != ":":
            result.append(tinycss2.serialize([token]))
            index += 1
            continue
        index += 1
        double = (
            index < len(tokens) and tokens[index].type == "literal" and tokens[index].value == ":"
        )
        if double:
            index += 1
        if index >= len(tokens):
            raise ShuyiError("css_selector_invalid")
        pseudo = tokens[index]
        name = getattr(pseudo, "lower_name", getattr(pseudo, "lower_value", ""))
        if pseudo.type == "ident" and name in PSEUDO_ELEMENTS:
            if negated or any(t.type not in {"whitespace", "comment"} for t in tokens[index + 1 :]):
                raise ShuyiError("css_selector_unsupported")
            result.append(":where(*|*)")
        elif double:
            raise ShuyiError("css_selector_unsupported")
        elif pseudo.type == "ident" and name in STATES:
            if negated:
                raise ShuyiError("css_selector_unsupported")
            result.append(":any-link" if name in {"link", "visited"} else ":where(*|*)")
        elif pseudo.type == "function" and name in {"is", "where", "not"}:
            inner = selector_subject(pseudo.arguments, negated=negated or name == "not")
            result.append(f":{name}({inner})")
        elif name in STATIC and pseudo.type in {"ident", "function"}:
            if pseudo.type == "function" and any(
                t.type == "literal" and t.value == ":" for t in pseudo.arguments
            ):
                raise ShuyiError("css_selector_unsupported")
            result.append(":" + tinycss2.serialize([pseudo]))
        else:
            raise ShuyiError("css_selector_unsupported")
        index += 1
    return "".join(result)


def source_selectors(selector: str, namespaces: tuple) -> list:
    mapping = dict(namespaces)
    try:
        original = cssselect2.compile_selector_list(selector, namespaces=mapping)
        # 分开处理列表，伪元素只能出现在每个选择器的末尾。
        groups = split_commas(tinycss2.parse_component_value_list(selector))
        subject = ",".join(selector_subject(group) for group in groups)
        compiled = cssselect2.compile_selector_list(subject, namespaces=mapping)
        for item, before in zip(compiled, original, strict=True):
            item.specificity = before.specificity
        return compiled
    except (cssselect2.SelectorError, TypeError, ValueError, NotImplementedError) as exc:
        raise ShuyiError("css_selector_invalid") from exc


def split_commas(tokens) -> list[list]:
    groups = [[]]
    for token in tokens:
        if token.type == "literal" and token.value == ",":
            groups.append([])
        elif token.type not in {"comment", "whitespace"} or groups[-1]:
            groups[-1].append(token)
    return groups


def validate_media(text: str) -> None:
    groups = split_commas(tinycss2.parse_component_value_list(text))
    for group in groups:
        tokens = [t for t in group if t.type not in {"whitespace", "comment"}]
        if not tokens:
            raise ShuyiError("css_condition_unsupported")
        index = 0
        if tokens[0].type == "ident" and tokens[0].lower_value in {"not", "only"}:
            index += 1
        if index == len(tokens):
            raise ShuyiError("css_condition_unsupported")
        if index < len(tokens) and tokens[index].type == "ident":
            if tokens[index].lower_value not in {
                "all",
                "screen",
                "print",
                "amzn-kf8",
                "amzn-mobi",
                "amzn-et",
            }:
                raise ShuyiError("css_condition_unsupported")
            index += 1
            if index == len(tokens):
                continue
            if tokens[index].type != "ident" or tokens[index].lower_value != "and":
                raise ShuyiError("css_condition_unsupported")
            index += 1
        elif index and tokens[0].lower_value == "only":
            raise ShuyiError("css_condition_unsupported")
        if index == len(tokens):
            raise ShuyiError("css_condition_unsupported")
        while index < len(tokens):
            token = tokens[index]
            if token.type != "() block":
                raise ShuyiError("css_condition_unsupported")
            feature = tinycss2.serialize(token.content).strip().lower()
            dimension = r"(?:0|(?:\d+(?:\.\d*)?|\.\d+)(?:px|em|rem|ex|ch|pt|pc|in|cm|mm|q))"
            if not re.fullmatch(
                rf"(?:(?:min-|max-)?(?:device-)?(?:width|height)\s*:\s*{dimension}"
                r"|orientation\s*:\s*(?:portrait|landscape)"
                r"|prefers-color-scheme\s*:\s*(?:dark|light)"
                r"|hover\s*:\s*(?:hover|none)"
                r"|pointer\s*:\s*(?:none|coarse|fine))",
                feature,
            ):
                raise ShuyiError("css_condition_unsupported")
            index += 1
            if index == len(tokens):
                break
            if tokens[index].type != "ident" or tokens[index].lower_value != "and":
                raise ShuyiError("css_condition_unsupported")
            index += 1
            if index == len(tokens):
                raise ShuyiError("css_condition_unsupported")
        if not index or index > len(tokens):
            raise ShuyiError("css_condition_unsupported")


def namespace_rule(tokens) -> tuple[str | None, str]:
    parts = [t for t in tokens if t.type not in {"whitespace", "comment"}]
    prefix = None
    if len(parts) == 2 and parts[0].type == "ident":
        prefix = parts.pop(0).value
    if len(parts) != 1:
        raise ShuyiError("css_namespace_unsupported")
    value = parts[0]
    if value.type in {"string", "url"}:
        return prefix, value.value
    if value.type == "function" and value.lower_name == "url":
        args = [t for t in value.arguments if t.type not in {"whitespace", "comment"}]
        if len(args) == 1 and args[0].type == "string":
            return prefix, args[0].value
    raise ShuyiError("css_namespace_unsupported")


def parse_source_rules(
    text: str, *, condition: str = "", namespaces: tuple = (), nested=False
) -> tuple[CSSRule, ...]:
    if condition and not nested:
        validate_media(condition)
    mapping = dict(namespaces)
    result = []
    started = False
    for item in tinycss2.parse_stylesheet(text, skip_comments=True, skip_whitespace=True):
        if item.type == "qualified-rule":
            started = True
            selector = tinycss2.serialize(item.prelude).strip()
            scope = tuple(mapping.items())
            source_selectors(selector, scope)
            values = source_declarations(item.content)
            subject = ",".join(
                selector_subject(group)
                for group in split_commas(tinycss2.parse_component_value_list(selector))
            )
            result.append(CSSRule(subject, values, condition, selector, scope))
        elif item.type == "at-rule":
            name = item.lower_at_keyword
            prelude = tinycss2.serialize(item.prelude).strip()
            if name == "namespace":
                if started or nested or item.content is not None:
                    raise ShuyiError("css_namespace_unsupported")
                prefix, uri = namespace_rule(item.prelude)
                mapping[prefix] = uri
            elif name == "charset" and not started and not nested and item.content is None:
                continue
            elif name in {"page", "font-face"} and item.content is not None:
                started = True
            elif name in {"keyframes", "-webkit-keyframes"}:
                started = True
                validate_keyframes(item)
            elif name in {"media", "supports"} and item.content is not None:
                started = True
                if name == "media":
                    validate_media(prelude)
                    label = prelude
                else:
                    validate_supports(item.prelude)
                    label = f"@supports {prelude}"
                combined = f"({condition}) and ({label})" if condition else label
                result.extend(
                    parse_source_rules(
                        tinycss2.serialize(item.content),
                        condition=combined,
                        namespaces=tuple(mapping.items()),
                        nested=True,
                    )
                )
            else:
                raise ShuyiError("css_at_rule_unsupported")
        else:
            raise ShuyiError("css_invalid")
    return tuple(result)


def source_declarations(tokens) -> tuple[tuple[str, str, bool], ...]:
    result = []
    for item in tinycss2.parse_declaration_list(tokens, skip_comments=True, skip_whitespace=True):
        if item.type != "declaration":
            raise ShuyiError("css_declaration_invalid")
        name = item.lower_name
        if name == "all":
            raise ShuyiError("css_cascade_unsupported")
        result.append((name, tinycss2.serialize(item.value).strip(), item.important))
    return tuple(result)
