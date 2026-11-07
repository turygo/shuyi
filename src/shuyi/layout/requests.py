from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from shuyi.contracts import LayoutNode, ShuyiError
from shuyi.layout.protocol import MAX_CHARACTERS, MAX_OBSERVATIONS, canonical, request_size


def chunks(node: LayoutNode) -> tuple[LayoutNode, ...]:
    if request_size([node]) <= MAX_CHARACTERS:
        return (node,)
    empty = replace(node, text="", markup="", node_id=node.node_id + ":c0")
    if request_size([empty]) >= MAX_CHARACTERS:
        raise ShuyiError("model_context_limit", node.resource)
    own = canonical({"text": node.text, "markup": node.markup})
    result = []
    offset = 0
    while offset < len(own):
        low, high = 1, min(8000, len(own) - offset)
        selected = 0
        while low <= high:
            middle = (low + high) // 2
            sample = replace(
                empty, node_id=f"{node.node_id}:c{len(result)}", text=own[offset : offset + middle]
            )
            own_size = len(canonical({"text": sample.text, "markup": ""}))
            if own_size <= 8000 and request_size([sample]) <= MAX_CHARACTERS:
                selected = middle
                low = middle + 1
            else:
                high = middle - 1
        if not selected:
            raise ShuyiError("model_context_limit", node.resource)
        result.append(
            replace(
                empty,
                node_id=f"{node.node_id}:c{len(result)}",
                text=own[offset : offset + selected],
            )
        )
        offset += selected
    return tuple(result)


def batches(samples: Sequence[LayoutNode]) -> list[tuple[LayoutNode, ...]]:
    result = []
    pending: list[LayoutNode] = []
    for sample in samples:
        if request_size([sample]) > MAX_CHARACTERS:
            raise ShuyiError("model_context_limit", sample.resource)
        if pending and (
            len(pending) >= MAX_OBSERVATIONS or request_size([*pending, sample]) > MAX_CHARACTERS
        ):
            result.append(tuple(pending))
            pending = []
        pending.append(sample)
    if pending:
        result.append(tuple(pending))
    return result
