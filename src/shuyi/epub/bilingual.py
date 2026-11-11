from __future__ import annotations

from lxml import etree

from shuyi.contracts import (
    BilingualReport,
    Book,
    ContentBinding,
    ContentIssue,
    ElementPath,
    ShuyiError,
)
from shuyi.epub.bilingual_evidence import (
    INLINE,
    compatible,
    eligible,
    exporter_signature,
    has_han,
    local,
    own_language,
    previous_segment,
    script,
    text,
)
from shuyi.epub.dom import element_path, parse_xml, resolve_element


def is_exporter_reference(node: etree._Element) -> bool:
    return eligible(node) and exporter_signature(node)


class DocumentPairs:
    def __init__(self, root: etree._Element, resource: str, start: int) -> None:
        self.root = root
        self.resource = resource
        self.start = start
        self.bindings: dict[ElementPath, ContentBinding] = {}
        self.issues: list[ContentIssue] = []
        self.invalid: set[ElementPath] = set()
        self.pairs = 0

    def issue(self, node: etree._Element, code: str) -> None:
        path = element_path(self.root, node)
        value = ContentIssue(self.resource, path, code)
        if value not in self.issues:
            self.issues.append(value)
        self.invalid.add(path)

    def unresolved(self, node: etree._Element) -> None:
        code = (
            "bilingual_pair_ambiguous"
            if node.get("data-bilingual") is not None
            else "bilingual_unpaired_reference"
        )
        self.issue(node, code)

    def enclosing(self, node: etree._Element) -> ContentBinding | None:
        for parent in node.iterancestors():
            binding = self.bindings.get(element_path(self.root, parent))
            if binding is not None:
                return binding
        return None

    def validate_language(self, node: etree._Element) -> None:
        identity = node.get("data-bilingual")
        language, conflict = own_language(node)
        if conflict or identity in {"target", "source"} and language not in {None, identity}:
            self.issue(node, "bilingual_language_conflict")
        elif identity is not None and identity not in {"target", "source"}:
            self.issue(node, "bilingual_pair_ambiguous")

    def add_pair(
        self, source: etree._Element, target: etree._Element | None, exporter: bool
    ) -> None:
        source_path = element_path(self.root, source)
        target_path = element_path(self.root, target) if target is not None else None
        if source_path in self.invalid or target_path in self.invalid:
            return
        if target_path is not None and target_path in self.bindings:
            self.issue(source, "bilingual_pair_ambiguous")
            return
        self.pairs += 1
        pair = f"b{self.start + self.pairs:06d}"
        flow = "inline" if local(source) in INLINE else "block"
        if target_path is not None:
            target_flow = "inline" if local(target) in INLINE else "block"
            self.bindings[target_path] = ContentBinding(
                self.resource, target_path, "target", pair, target_flow
            )
        self.bindings[source_path] = ContentBinding(
            self.resource, source_path, "source", pair, flow, exporter
        )

    def recognize(self, source: etree._Element) -> None:
        identity = source.get("data-bilingual")
        language, _ = own_language(source)
        exporter = is_exporter_reference(source)
        if identity == "target":
            return
        if identity != "source" and language != "source" and not exporter:
            return
        if language == "target":
            self.issue(source, "bilingual_language_conflict")
            return
        marked = identity == "source" or exporter
        previous = previous_segment(source)
        source_script = script(text(source))
        if source_script == "neutral":
            return
        if source_script != "source":
            if identity == "source" or language == "source":
                self.issue(source, "bilingual_language_conflict")
            elif exporter:
                self.unresolved(source)
            return
        if isinstance(previous, str):
            if marked and has_han(previous):
                parent = source.getparent()
                target = parent if parent.get("data-bilingual") == "target" else None
                if target is not None and not self.metadata_matches(target, source):
                    return
                if target is not None and element_path(self.root, target) in self.bindings:
                    target = None
                self.add_pair(source, target, exporter)
            elif marked:
                self.unresolved(source)
            return
        if previous is None:
            if marked:
                self.unresolved(source)
            return
        target_identity = previous.get("data-bilingual")
        target_language, _ = own_language(previous)
        explicit = identity == "source" or target_identity is not None
        evidence = (
            identity == "source"
            and target_identity == "target"
            or identity is None
            and target_identity is None
            and language == "source"
            and target_language == "target"
            or identity is None
            and target_identity in {None, "target"}
            and exporter
        )
        if not evidence:
            if marked or target_identity == "target":
                self.unresolved(source)
            return
        if not compatible(previous, source, explicit=explicit) or not has_han(text(previous)):
            self.unresolved(source)
            return
        if not self.metadata_matches(previous, source):
            return
        self.add_pair(source, previous, exporter)

    def metadata_matches(self, target: etree._Element, source: etree._Element) -> bool:
        first, second = target.get("data-bilingual-pair"), source.get("data-bilingual-pair")
        if first != second and (first is not None or second is not None):
            self.issue(source, "bilingual_pair_ambiguous")
            return False
        target_language, conflict = own_language(target)
        if conflict or target_language == "source":
            self.issue(target, "bilingual_language_conflict")
            return False
        return True

    def run(self) -> None:
        nodes = [node for node in self.root.iter() if eligible(node)]
        for node in nodes:
            self.validate_language(node)
        for node in nodes:
            path = element_path(self.root, node)
            if path in self.invalid:
                continue
            enclosing = self.enclosing(node)
            identity = node.get("data-bilingual")
            language, _ = own_language(node)
            if enclosing is not None and enclosing.content == "source":
                if (identity == "target" or language == "target") and script(
                    text(node)
                ) != "neutral":
                    self.issue(node, "bilingual_nested_conflict")
                continue
            if path not in self.bindings:
                self.recognize(node)
        for node in nodes:
            path = element_path(self.root, node)
            if node.get("data-bilingual") not in {"target", "source"}:
                continue
            if path in self.bindings or path in self.invalid or script(text(node)) == "neutral":
                continue
            enclosing = self.enclosing(node)
            if enclosing is None or enclosing.content != node.get("data-bilingual"):
                self.issue(node, "bilingual_pair_ambiguous")


def find_bilingual(book: Book) -> BilingualReport:
    bindings: list[ContentBinding] = []
    issues: list[ContentIssue] = []
    pair_count = 0
    for resource in book.documents:
        if resource in book.protected:
            continue
        detector = DocumentPairs(parse_xml(book.entries[resource], resource), resource, pair_count)
        detector.run()
        bindings.extend(sorted(detector.bindings.values(), key=lambda item: item.path))
        issues.extend(sorted(detector.issues, key=lambda item: item.path))
        pair_count += detector.pairs
    return BilingualReport(tuple(bindings), tuple(issues))


def require_bilingual(report: BilingualReport) -> None:
    failures = [issue for issue in report.issues if issue.code != "bilingual_unpaired_reference"]
    if failures:
        issue = next(
            (item for item in failures if item.code == "bilingual_language_conflict"),
            failures[0],
        )
        raise ShuyiError(issue.code, issue.resource)
    if not report.bindings:
        raise ShuyiError("bilingual_structure_missing")


def content_context(
    root: etree._Element,
    path: ElementPath,
    bindings: tuple[ContentBinding, ...],
    *,
    bilingual: bool,
) -> dict[str, object]:
    node = resolve_element(root, path)
    result: dict[str, object] = {
        "content": "unpaired" if bilingual else "target",
        "content_root": False,
        "pair_id": None,
        "flow": "inline" if bilingual and local(node) in INLINE else "block",
        "exporter": False,
    }
    if not bilingual:
        return result
    ancestors = [item for item in bindings if path[: len(item.path)] == item.path]
    ancestors.sort(key=lambda item: len(item.path))
    source_seen = False
    for item in ancestors:
        if source_seen and item.content == "target":
            raise ShuyiError("bilingual_nested_conflict", item.resource)
        source_seen = source_seen or item.content == "source"
    nearest = ancestors[-1] if ancestors else None
    if nearest is not None:
        result.update(
            content=nearest.content,
            content_root=nearest.path == path,
            pair_id=nearest.pair_id,
            exporter=nearest.content == "source" and is_exporter_reference(node),
        )
    kinds = {item.content for item in ancestors[-1:]}
    descendants = [item for item in bindings if item.path[: len(path)] == path]
    kinds.update(item.content for item in descendants)
    paired_targets = {item.pair_id for item in bindings if item.content == "target"}
    for item in descendants:
        if item.content == "source" and item.pair_id not in paired_targets:
            if len(item.path) > len(path):
                kinds.add("target")
    if kinds == {"source", "target"}:
        result["content"] = "mixed"
    return result
