from __future__ import annotations

import re

import tinycss2

from shuyi.contracts import ShuyiError


def significant(tokens) -> list:
    return [t for t in tokens if t.type not in {"whitespace", "comment"}]


def validate_supports(tokens) -> None:
    parts = significant(tokens)
    if not parts:
        raise ShuyiError("css_condition_unsupported")
    if parts[0].type == "ident" and parts[0].lower_value == "not":
        if len(parts) != 2:
            raise ShuyiError("css_condition_unsupported")
        validate_supports_atom(parts[1])
        return
    operator = None
    for index, token in enumerate(parts):
        if index % 2 == 0:
            validate_supports_atom(token)
        else:
            name = getattr(token, "lower_value", "")
            if name not in {"and", "or"} or (operator and name != operator):
                raise ShuyiError("css_condition_unsupported")
            operator = name
    if len(parts) % 2 == 0:
        raise ShuyiError("css_condition_unsupported")


def validate_supports_atom(token) -> None:
    if token.type != "() block":
        raise ShuyiError("css_condition_unsupported")
    parts = significant(token.content)
    if (
        len(parts) >= 3
        and parts[0].type == "ident"
        and parts[1].type == "literal"
        and parts[1].value == ":"
    ):
        if parts[0].value.startswith("--"):
            raise ShuyiError("css_condition_unsupported")
        validate_simple_tokens(parts[2:])
    else:
        validate_supports(parts)


def validate_simple_tokens(tokens) -> None:
    for token in tokens:
        if token.type in {
            "ident",
            "number",
            "dimension",
            "percentage",
            "hash",
            "string",
            "whitespace",
            "comment",
        }:
            continue
        if token.type == "literal" and token.value in {"/", ","}:
            continue
        raise ShuyiError("css_condition_unsupported")


def validate_keyframes(item) -> None:
    prelude = significant(item.prelude)
    if item.content is None or len(prelude) != 1 or prelude[0].type not in {"ident", "string"}:
        raise ShuyiError("css_cascade_unsupported")
    frames = tinycss2.parse_rule_list(item.content, skip_comments=True, skip_whitespace=True)
    for frame in frames:
        if frame.type != "qualified-rule":
            raise ShuyiError("css_cascade_unsupported")
        label = tinycss2.serialize(frame.prelude).strip().lower()
        if not re.fullmatch(
            r"(?:from|to|(?:\d+(?:\.\d*)?|\.\d+)%)\s*(?:,\s*(?:from|to|(?:\d+(?:\.\d*)?|\.\d+)%)\s*)*",
            label,
        ):
            raise ShuyiError("css_cascade_unsupported")
        for token in frame.prelude:
            if token.type == "percentage" and not 0 <= token.value <= 100:
                raise ShuyiError("css_cascade_unsupported")
        values = tinycss2.parse_declaration_list(
            frame.content, skip_comments=True, skip_whitespace=True
        )
        for declaration in values:
            if declaration.type != "declaration" or declaration.important:
                raise ShuyiError("css_cascade_unsupported")
            validate_simple_tokens(declaration.value)


def dynamic_property(name: str) -> bool:
    return name in {
        "animation",
        "transition",
        "-webkit-animation",
        "-webkit-transition",
    } or name.startswith(("animation-", "transition-", "-webkit-animation-", "-webkit-transition-"))
