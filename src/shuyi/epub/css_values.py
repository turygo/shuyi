from __future__ import annotations

import re

import tinycss2
from tinycss2.color3 import parse_color

from shuyi.contracts import ShuyiError

SAFE_PROPERTIES = frozenset(
    (
        "font-family font-size font-weight font-style font-variant font-stretch line-height "
        "color background-color text-align text-indent text-decoration text-decoration-color "
        "text-decoration-line text-decoration-style text-transform letter-spacing word-spacing "
        "white-space vertical-align margin margin-top margin-right margin-bottom margin-left "
        "margin-inline margin-inline-start margin-inline-end margin-block margin-block-start "
        "margin-block-end padding padding-top padding-right padding-bottom padding-left "
        "padding-inline padding-inline-start padding-inline-end padding-block padding-block-start "
        "padding-block-end border border-width border-style border-color border-top border-right "
        "border-bottom border-left border-top-width border-right-width border-bottom-width "
        "border-left-width border-top-style border-right-style border-bottom-style "
        "border-left-style "
        "border-top-color border-right-color border-bottom-color border-left-color border-radius "
        "break-before break-after break-inside page-break-before page-break-after "
        "page-break-inside "
        "orphans widows hyphens overflow-wrap word-break"
    ).split()
)
GLOBAL = {"inherit", "initial", "unset", "revert"}
BORDER_STYLES = {
    "none",
    "hidden",
    "dotted",
    "dashed",
    "solid",
    "double",
    "groove",
    "ridge",
    "inset",
    "outset",
}
ENUMS = {
    "font-style": "normal italic oblique",
    "font-variant": "normal none small-caps",
    "font-stretch": (
        "normal ultra-condensed extra-condensed condensed semi-condensed "
        "semi-expanded expanded extra-expanded ultra-expanded"
    ),
    "text-align": "start end left right center justify match-parent",
    "text-decoration-style": "solid double dotted dashed wavy",
    "text-transform": "none capitalize uppercase lowercase full-width full-size-kana",
    "white-space": "normal pre nowrap pre-wrap pre-line break-spaces",
    "hyphens": "none manual auto",
    "overflow-wrap": "normal break-word anywhere",
    "word-break": "normal break-all keep-all break-word",
    "break-before": (
        "auto avoid always all avoid-page page left right recto verso avoid-column column"
    ),
    "break-after": (
        "auto avoid always all avoid-page page left right recto verso avoid-column column"
    ),
    "break-inside": "auto avoid avoid-page avoid-column",
    "page-break-before": "auto always avoid left right",
    "page-break-after": "auto always avoid left right",
    "page-break-inside": "auto avoid",
}
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
LENGTH = re.compile(rf"({NUMBER})(px|em|rem|ex|ch|pt|pc|in|cm|mm|q|lh|rlh|vw|vh|vmin|vmax|%)?")


def length(value: str, *, negative: bool = True, percent: bool = True) -> bool:
    match = LENGTH.fullmatch(value.lower())
    if match is None:
        return False
    number, unit = float(match[1]), match[2]
    return (
        (negative or number >= 0) and (percent or unit != "%") and (unit is not None or number == 0)
    )


def components(value: str) -> list[str]:
    return [
        tinycss2.serialize([token]).strip()
        for token in tinycss2.parse_component_value_list(value)
        if token.type not in {"whitespace", "comment"}
    ]


def family(value: str) -> bool:
    groups = [[]]
    for token in tinycss2.parse_component_value_list(value):
        if token.type in {"whitespace", "comment"}:
            continue
        if token.type == "literal" and token.value == ",":
            groups.append([])
        elif token.type in {"ident", "string"}:
            groups[-1].append(token)
        else:
            return False
    return all(
        group
        and (len(group) == 1 or all(t.type == "ident" for t in group))
        and not any(t.type == "ident" and t.value.lower() in GLOBAL for t in group)
        for group in groups
    )


def border_value(name: str, parts: list[str]) -> bool:
    if name == "border-radius":
        sets = [[]]
        for part in parts:
            if part == "/" and len(sets) == 1:
                sets.append([])
            else:
                sets[-1].append(part)
        return all(
            1 <= len(values) <= 4 and all(length(v, negative=False) for v in values)
            for values in sets
        )
    limit = 4 if name in {"border-width", "border-style", "border-color"} else 1
    if name.endswith("-color"):
        return 1 <= len(parts) <= limit and all(parse_color(p) is not None for p in parts)
    if name.endswith("-width"):
        return 1 <= len(parts) <= limit and all(
            p in {"thin", "medium", "thick"} or length(p, negative=False, percent=False)
            for p in parts
        )
    if name.endswith("-style"):
        return 1 <= len(parts) <= limit and all(p in BORDER_STYLES for p in parts)
    groups = []
    for part in parts:
        if part in BORDER_STYLES:
            groups.append("style")
        elif part in {"thin", "medium", "thick"} or length(part, negative=False, percent=False):
            groups.append("width")
        elif parse_color(part) is not None:
            groups.append("color")
        else:
            return False
    return 1 <= len(groups) <= 3 and len(set(groups)) == len(groups)


def decoration_value(parts: list[str], *, line_only: bool) -> bool:
    if parts == ["none"]:
        return True
    lines = {"underline", "overline", "line-through"}
    styles = {"solid", "double", "dotted", "dashed", "wavy"}
    if not parts or len(set(parts)) != len(parts):
        return False
    if line_only:
        return set(parts) <= lines
    style_count = color_count = 0
    for part in parts:
        if part in styles:
            style_count += 1
        elif part in lines:
            continue
        elif parse_color(part) is not None:
            color_count += 1
        else:
            return False
    return style_count <= 1 and color_count <= 1


def valid_value(name: str, value: str) -> bool:
    lower = value.lower().strip()
    if lower in GLOBAL:
        return True
    if name in {"color", "background-color", "text-decoration-color"}:
        return parse_color(lower) is not None
    if name == "font-family":
        return family(value)
    if name == "font-size":
        return lower in {
            "xx-small",
            "x-small",
            "small",
            "medium",
            "large",
            "x-large",
            "xx-large",
            "xxx-large",
            "smaller",
            "larger",
        } or length(lower, negative=False)
    if name == "font-weight":
        return lower in {"normal", "bold", "bolder", "lighter"} or bool(
            re.fullmatch(r"\d{1,4}", lower) and 1 <= int(lower) <= 1000
        )
    if name == "line-height":
        return (
            lower == "normal"
            or bool(re.fullmatch(NUMBER, lower) and float(lower) >= 0)
            or length(lower, negative=False)
        )
    if name == "font-stretch" and lower.endswith("%"):
        return length(lower, negative=False)
    if name in ENUMS:
        return lower in ENUMS[name].split()
    if name in {"orphans", "widows"}:
        return bool(re.fullmatch(r"[1-9]\d*", lower))
    if name in {"letter-spacing", "word-spacing"}:
        return lower == "normal" or length(lower, percent=False)
    if name == "vertical-align":
        return lower in {
            "baseline",
            "sub",
            "super",
            "text-top",
            "text-bottom",
            "middle",
            "top",
            "bottom",
        } or length(lower)
    parts = components(lower)
    if name == "text-indent":
        return len(parts) == 1 and length(lower)
    if name.startswith(("margin", "padding")):
        limit = (
            4
            if name in {"margin", "padding"}
            else 2
            if name in {"margin-inline", "margin-block", "padding-inline", "padding-block"}
            else 1
        )
        return 1 <= len(parts) <= limit and all(
            (name.startswith("margin") and part == "auto")
            or length(part, negative=name.startswith("margin"))
            for part in parts
        )
    if name.startswith("border"):
        return border_value(name, parts)
    if name in {"text-decoration", "text-decoration-line"}:
        return decoration_value(parts, line_only=name == "text-decoration-line")
    return False


def validate_css_value(name: str, value: str) -> None:
    try:
        valid = bool(value.strip()) and valid_value(name, value)
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise ShuyiError("css_value_unsupported")
