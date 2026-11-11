from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol
from zipfile import ZipInfo

ElementPath = tuple[int, ...]
Progress = Callable[[str], None]
ROLES = frozenset(
    {
        "body",
        "heading",
        "chapter-number",
        "subtitle",
        "caption",
        "quote",
        "quote-text",
        "quote-attribution",
        "chapter-summary",
        "table-text",
        "list-text",
        "footnote",
        "endnote",
        "noteref",
    }
)


class ShuyiError(ValueError):
    def __init__(self, code: str, resource: str = "") -> None:
        self.code = code
        self.resource = resource
        super().__init__(f"{code}: {resource}" if resource else code)


@dataclass(frozen=True)
class Book:
    source_path: Path
    source_sha256: str
    entries: dict[str, bytes]
    infos: dict[str, ZipInfo]
    opf_path: str
    documents: tuple[str, ...]
    protected: frozenset[str]
    version: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContentBinding:
    resource: str
    path: ElementPath
    content: Literal["source", "target"]
    pair_id: str
    flow: Literal["block", "inline"]
    exporter: bool = False


@dataclass(frozen=True)
class ContentIssue:
    resource: str
    path: ElementPath
    code: str


@dataclass(frozen=True)
class BilingualReport:
    bindings: tuple[ContentBinding, ...]
    issues: tuple[ContentIssue, ...] = ()


@dataclass(frozen=True)
class LayoutNode:
    node_id: str
    resource: str
    path: ElementPath
    source_sha256: str
    tag: str
    text: str
    markup: str
    context: str
    ancestors: str
    source_css: str
    group_key: str
    content: str = "target"
    content_root: bool = False
    pair_id: str | None = None
    flow: str = "block"
    exporter: bool = False


@dataclass(frozen=True)
class Inventory:
    source_sha256: str
    digest: str
    nodes: tuple[LayoutNode, ...]
    warnings: tuple[str, ...] = ()
    bilingual: bool = False
    content: tuple[ContentBinding, ...] = ()


@dataclass(frozen=True)
class Assignment:
    node_id: str
    role: str | None
    level: int | None = None


@dataclass(frozen=True)
class ClassificationBatch:
    observations: tuple[Assignment, ...]
    usage: dict[str, int] = field(default_factory=dict)


class Classifier(Protocol):
    provenance: str

    def classify(self, samples: Sequence[LayoutNode]) -> ClassificationBatch: ...


@dataclass(frozen=True)
class LayoutProfile:
    source_sha256: str
    inventory_digest: str
    assignments: dict[str, Assignment]
    provenance: str
    usage: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0


@dataclass(frozen=True)
class AttributeEdit:
    resource: str
    path: ElementPath
    name: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class TextEdit:
    resource: str
    path: ElementPath
    field: Literal["text", "tail"]
    before: str | None
    after: str | None


@dataclass(frozen=True)
class NoteMarker:
    resource: str
    path: ElementPath
    kind: Literal["noteref", "backlink"]
    target_resource: str
    target_path: ElementPath


@dataclass(frozen=True)
class StyledNode:
    node_id: str
    resource: str
    path: ElementPath
    source_sha256: str
    role: str
    level: int | None
    content: str = "target"
    content_root: bool = False
    pair_id: str | None = None
    flow: str = "block"
    exporter: bool = False


@dataclass(frozen=True)
class ThemePlan:
    source_sha256: str
    replacements: dict[str, bytes]
    attributes: tuple[AttributeEdit, ...]
    texts: tuple[TextEdit, ...]
    bindings: tuple[StyledNode, ...]
    asset_path: str
    asset_id: str
    role_counts: dict[str, int]
    note_count: int
    unknown_count: int
    warnings: tuple[str, ...] = ()
    bilingual: bool = False
