from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from shuyi.contracts import Assignment, Classifier, Inventory, LayoutNode, LayoutProfile, ShuyiError
from shuyi.layout.cache import Checkpoints, atomic_json, exclusive
from shuyi.layout.protocol import VERSION, validate_batch
from shuyi.layout.requests import batches, chunks


def unanimous(assignments: Sequence[Assignment]) -> tuple[str, int | None] | None:
    if not assignments or assignments[0].role is None:
        return None
    vote = (assignments[0].role, assignments[0].level)
    return vote if all((item.role, item.level) == vote for item in assignments) else None


class Analyzer:
    def __init__(
        self,
        inventory: Inventory,
        classifier: Classifier,
        directory: Path,
        on_progress: Callable[[str], None] | None,
    ) -> None:
        self.inventory = inventory
        self.classifier = classifier
        self.directory = directory
        self.on_progress = on_progress
        self.parts = {node.node_id: chunks(node) for node in inventory.nodes}
        universe = {part.node_id: part for parts in self.parts.values() for part in parts}
        self.state = Checkpoints(inventory, universe)
        self.results: dict[str, Assignment] = {}

    def classify(self, nodes: Sequence[LayoutNode]) -> list[Assignment]:
        samples = [part for node in nodes for part in self.parts[node.node_id]]
        missing = [part for part in samples if part.node_id not in self.state.observations]
        self.state.cache_hits += len(samples) - len(missing)
        if missing and self.state.accepted is not None:
            raise ShuyiError("cache_invalid")
        for batch in batches(missing):
            if self.on_progress:
                self.on_progress(f"Classifying {len(batch)} layout observations")
            try:
                response = self.classifier.classify(batch)
            except ShuyiError:
                raise
            except Exception:
                raise ShuyiError("model_request_failed") from None
            validated = validate_batch(response, batch)
            provenance = self.classifier.provenance
            if not isinstance(provenance, str) or not provenance:
                raise ShuyiError("model_provenance_invalid")
            self.state.append(batch, validated, provenance)
            atomic_json(self.directory / "working.json", self.state.data())
        result = []
        for node in nodes:
            observations = [self.state.observations[p.node_id] for p in self.parts[node.node_id]]
            vote = unanimous(observations)
            assignment = Assignment(node.node_id, *vote) if vote else Assignment(node.node_id, None)
            self.results[node.node_id] = assignment
            result.append(assignment)
        return result

    def group(self, nodes: list[LayoutNode]) -> None:
        if len(nodes) <= 6:
            self.classify(nodes)
            return
        initial_indices = {0, len(nodes) // 2, len(nodes) - 1}
        initial = [node for i, node in enumerate(nodes) if i in initial_indices]
        observations = self.classify(initial)
        remaining = [node for i, node in enumerate(nodes) if i not in initial_indices]
        if unanimous(observations) is not None:
            held_indices = {0, len(remaining) // 2, len(remaining) - 1}
            held = [node for i, node in enumerate(remaining) if i in held_indices]
            observations.extend(self.classify(held))
            remaining = [node for i, node in enumerate(remaining) if i not in held_indices]
            vote = unanimous(observations)
            if vote is not None:
                for node in remaining:
                    self.results[node.node_id] = Assignment(node.node_id, *vote)
                return
        self.classify(remaining)

    def assignments(self) -> dict[str, Assignment]:
        groups: dict[str, list[LayoutNode]] = {}
        for node in self.inventory.nodes:
            groups.setdefault(node.group_key, []).append(node)
        for group in groups.values():
            self.group(group)
        ordered = {node.node_id: self.results[node.node_id] for node in self.inventory.nodes}
        if self.state.accepted is not None and self.state.accepted != ordered:
            raise ShuyiError("cache_invalid")
        return ordered

    def run(self, reanalyze: bool) -> LayoutProfile:
        accepted = self.directory / "accepted.json"
        working = self.directory / "working.json"
        if not reanalyze:
            if accepted.exists():
                self.state.load(accepted, accepted=True)
            elif working.exists():
                self.state.load(working, accepted=False)
        else:
            if accepted.exists():
                fresh = self.state
                self.state = Checkpoints(self.inventory, fresh.universe)
                self.state.load(accepted, accepted=True)
                self.assignments()
                self.state = fresh
                self.results = {}
            if working.exists():
                self.state.load(working, accepted=False)
            else:
                atomic_json(working, self.state.data())
        ordered = self.assignments()
        if self.state.accepted is None:
            atomic_json(accepted, self.state.data(ordered))
            working.unlink(missing_ok=True)
        provenance = "; ".join(self.state.provenances) or "no-model-observations"
        return LayoutProfile(
            self.inventory.source_sha256,
            self.inventory.digest,
            ordered,
            provenance,
            dict(self.state.usage),
            self.state.cache_hits,
        )


def analyze(
    inventory: Inventory,
    classifier: Classifier,
    cache_dir: Path,
    *,
    reanalyze: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> LayoutProfile:
    directory = cache_dir / f"{inventory.source_sha256}-{VERSION}-{inventory.digest}"
    with exclusive(directory):
        return Analyzer(inventory, classifier, directory, on_progress).run(reanalyze)
