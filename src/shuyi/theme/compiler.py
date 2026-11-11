from __future__ import annotations

from importlib.resources import files

from shuyi.contracts import AttributeEdit, ShuyiError
from shuyi.epub.dom import element_path, has_direct_text, resolve_element
from shuyi.epub.reference_css import FIRST_LETTER_RESET
from shuyi.epub.styles import demote_inline, matching_rules, parse_rules
from shuyi.theme.reference import reference_rules

NOTE_CSS = (
    "display:inline-block;width:1.8em;min-width:1.8em;height:1.8em;padding:0;"
    "border-radius:50%;background-color:#946126;color:#fff8ef;font-size:0.7em;"
    "line-height:1.8em;text-align:center;text-indent:0;margin-inline:0.15em;"
    "text-decoration:none;vertical-align:0.15em"
)
SUP_CSS = "font-size:1em;line-height:inherit;vertical-align:baseline"


def theme_rules(css: bytes | None):
    try:
        text = (
            files("shuyi.theme").joinpath("reading.css").read_text("utf-8")
            if css is None
            else css.decode("utf-8-sig")
        )
    except UnicodeError as exc:
        raise ShuyiError("css_encoding_unsupported") from exc
    return parse_rules(text, custom=True)


def emit_rule(selector, values, condition=""):
    body = ";".join(f"{name}:{value} !important" for name, value in values.items())
    result = selector + "{" + body + "}\n"
    return "@media " + condition + "{" + result + "}\n" if condition else result


def compile_document(
    resource, root, bindings, rules, guard, attributes, *, builtin=True, first_letters=frozenset()
):
    matches = matching_rules(root, rules)
    output = []
    for binding in bindings:
        if binding.resource != resource:
            continue
        path = binding.path
        matched = matches[path]
        node = resolve_element(root, path)
        # 行内文本与纯作用域容器不重复应用整段字号和几何。
        if (
            builtin
            and binding.content != "source"
            and (binding.flow == "inline" or binding.role == "body" and not has_direct_text(node))
        ):
            matched = ()
        selector = f'[data-sy-node="{binding.node_id}"]' + ":not(#shuyi-never)" * guard
        unconditional = set()
        conditional = set()
        if builtin and binding.content == "source":
            from shuyi.epub.styles import validate_value

            for condition, values in reference_rules(binding, node, bindings):
                for name, value in values.items():
                    validate_value(name, value, reference=binding.content == "source")
                output.append(emit_rule(selector, values, condition))
                (conditional if condition else unconditional).update(values)
            if path in first_letters:
                output.append(emit_rule(selector + "::first-letter", dict(FIRST_LETTER_RESET)))
        else:
            for priority in (False, True):
                for rule in matched:
                    for name, value, important in rule.declarations:
                        if priority != important:
                            continue
                        output.append(emit_rule(selector, {name: value}, rule.condition))
                        (conditional if rule.condition else unconditional).add(name)
        before = node.get("style")
        if before:
            after = demote_inline(before, unconditional)
            # 条件样式不能无条件降低源内联优先级。
            if demote_inline(after, conditional) != after:
                raise ShuyiError("css_inline_conditional_override", resource)
            if before != after:
                attributes.append(AttributeEdit(resource, path, "style", before, after))
                node.set("style", after)
    return output


def compile_note(resource, root, node, guard, attributes, *, builtin):
    from shuyi.epub.styles import declarations

    selector = (
        f'[data-sy-note-id="{node.get("data-sy-note-id")}"]'
        f'[data-sy-note-kind="{node.get("data-sy-note-kind")}"]'
    )
    values = dict((name, value) for name, value, _ in declarations(NOTE_CSS)) if builtin else {}
    output = []
    if values:
        output.append(emit_rule(selector + ":not(#shuyi-never)" * guard, values))
        before = node.get("style")
        if before:
            after = demote_inline(before, set(values))
            if before != after:
                record_inline(resource, root, node, before, after, attributes)
                node.set("style", after)
    for child in node.iterdescendants():
        if not isinstance(child.tag, str) or child.tag.rsplit("}", 1)[-1] != "sup" or not builtin:
            continue
        relative = element_path(node, child)
        suffix = "".join(f" > :nth-child({index + 1})" for index in relative)
        subvalues = dict((name, value) for name, value, _ in declarations(SUP_CSS))
        output.append(emit_rule(selector + suffix + ":not(#shuyi-never)" * guard, subvalues))
        before = child.get("style")
        if before:
            after = demote_inline(before, set(subvalues))
            if before != after:
                record_inline(resource, root, child, before, after, attributes)
                child.set("style", after)
    return output


def record_inline(resource, root, node, before, after, attributes):
    path = element_path(root, node)
    for index, edit in enumerate(attributes):
        if (edit.resource, edit.path, edit.name) == (resource, path, "style"):
            attributes[index] = AttributeEdit(resource, path, "style", edit.before, after)
            return
    attributes.append(AttributeEdit(resource, path, "style", before, after))
