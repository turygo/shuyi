from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from shuyi.contracts import Assignment, ClassificationBatch, Inventory, LayoutNode, ShuyiError
from shuyi.layout.protocol import (
    MAX_CHARACTERS,
    MAX_OBSERVATIONS,
    VERSION,
    canonical,
    fingerprint,
    request_size,
    strict_json,
    validate_observations,
    validate_usage,
)


@contextmanager
def exclusive(directory: Path) -> Iterator[None]:
    try:
        import fcntl
    except ImportError:
        raise ShuyiError("cache_lock_unsupported") from None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "analysis.lock").open("a+b") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ShuyiError("cache_busy") from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        raise ShuyiError("cache_io_failed") from None


def atomic_json(path: Path, value: object) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise ShuyiError("cache_io_failed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def binding(samples: Sequence[LayoutNode]) -> str:
    from dataclasses import asdict

    return fingerprint([asdict(node) for node in samples])


class Checkpoints:
    def __init__(self, inventory: Inventory, universe: dict[str, LayoutNode]) -> None:
        self.inventory = inventory
        self.universe = universe
        self.records: list[dict[str, object]] = []
        self.observations: dict[str, Assignment] = {}
        self.usage: dict[str, int] = {}
        self.provenances: list[str] = []
        self.accepted: dict[str, Assignment] | None = None
        self.cache_hits = 0

    def data(self, assignments: dict[str, Assignment] | None = None) -> dict[str, object]:
        return {
            "version": VERSION,
            "source_sha256": self.inventory.source_sha256,
            "inventory_digest": self.inventory.digest,
            "nodes": {node.node_id: node.source_sha256 for node in self.inventory.nodes},
            "records": self.records,
            "assignments": None
            if assignments is None
            else [
                {"id": a.node_id, "role": a.role, "level": a.level} for a in assignments.values()
            ],
        }

    def load(self, path: Path, *, accepted: bool) -> None:
        try:
            value = strict_json(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or set(value) != set(self.data()):
                raise ValueError
            identity = self.data()
            for key in ("version", "source_sha256", "inventory_digest", "nodes"):
                if value[key] != identity[key]:
                    raise ValueError
            if not isinstance(value["records"], list):
                raise ValueError
            for record in value["records"]:
                self.load_record(record)
            raw = value["assignments"]
            if accepted:
                assignments = validate_observations(raw, [n.node_id for n in self.inventory.nodes])
                self.accepted = {a.node_id: a for a in assignments}
            elif raw is not None:
                raise ValueError
        except (ShuyiError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ShuyiError("cache_invalid") from None
        except OSError:
            raise ShuyiError("cache_io_failed") from None

    def load_record(self, record: object) -> None:
        if not isinstance(record, dict) or set(record) != {
            "ids",
            "binding",
            "observations",
            "usage",
            "provenance",
        }:
            raise ValueError
        ids = record["ids"]
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
            raise ValueError
        if len(set(ids)) != len(ids) or any(
            i not in self.universe or i in self.observations for i in ids
        ):
            raise ValueError
        samples = [self.universe[i] for i in ids]
        if len(samples) > MAX_OBSERVATIONS or request_size(samples) > MAX_CHARACTERS:
            raise ValueError
        if record["binding"] != binding(samples):
            raise ValueError
        observations = validate_observations(record["observations"], ids)
        usage = validate_usage(record["usage"])
        provenance = record["provenance"]
        if not isinstance(provenance, str) or not provenance:
            raise ValueError
        self.records.append(record)
        self.observations.update({a.node_id: a for a in observations})
        for key, count in usage.items():
            self.usage[key] = self.usage.get(key, 0) + count
        if provenance not in self.provenances:
            self.provenances.append(provenance)

    def append(
        self, samples: Sequence[LayoutNode], batch: ClassificationBatch, provenance: str
    ) -> None:
        self.load_record(
            {
                "ids": [n.node_id for n in samples],
                "binding": binding(samples),
                "observations": [
                    {"id": a.node_id, "role": a.role, "level": a.level} for a in batch.observations
                ],
                "usage": batch.usage,
                "provenance": provenance,
            }
        )
