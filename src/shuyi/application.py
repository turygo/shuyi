from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from shuyi.config import configured_classifier, default_cache_dir, load_config
from shuyi.contracts import Classifier, Progress, ShuyiError
from shuyi.epub.archive import load_book, write_book
from shuyi.layout.analysis import analyze
from shuyi.layout.inventory import build_inventory
from shuyi.theme.service import plan_theme
from shuyi.verify.preservation import check_book, verify_output


def inspect_book(source: Path) -> dict[str, object]:
    book = load_book(source.expanduser())
    inventory = build_inventory(book)
    return {
        "source": str(book.source_path),
        "source_sha256": book.source_sha256,
        "epub_version": book.version,
        "members": len(book.entries),
        "documents": len(book.documents),
        "protected_documents": sorted(book.protected),
        "candidates": len(inventory.nodes),
        "groups": len({node.group_key for node in inventory.nodes}),
        "warnings": list(dict.fromkeys((*book.warnings, *inventory.warnings))),
    }


def check(source: Path, *, epubcheck: bool = False) -> dict[str, object]:
    book = load_book(source.expanduser())
    report = check_book(book)
    if epubcheck:
        executable = shutil.which("epubcheck")
        if executable is None:
            raise ShuyiError("epubcheck_unavailable")
        try:
            result = subprocess.run(
                [executable, str(book.source_path)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ShuyiError("epubcheck_failed") from None
        if result.returncode:
            raise ShuyiError("epubcheck_failed")
        report["epubcheck"] = "passed"
    return report


def _destination(source: Path, output: Path | None) -> Path:
    selected = (output or source.with_name(f"{source.stem}.shuyi.epub")).expanduser().absolute()
    if selected.resolve() == source.resolve():
        raise ShuyiError("destination_is_source")
    if os.path.lexists(selected):
        raise ShuyiError("destination_exists")
    if not selected.parent.is_dir():
        raise ShuyiError("destination_directory_missing")
    return selected


def _publish(temporary: Path, output: Path) -> None:
    try:
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, output)
    except FileExistsError:
        raise ShuyiError("destination_exists") from None
    except OSError:
        raise ShuyiError("publication_failed") from None
    if os.name != "nt":
        try:
            descriptor = os.open(output.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            raise ShuyiError("publication_sync_failed") from None


def style(
    source: Path,
    output: Path | None = None,
    *,
    css_path: Path | None = None,
    config_path: Path | None = None,
    reanalyze: bool = False,
    classifier: Classifier | None = None,
    cache_dir: Path | None = None,
    on_progress: Progress | None = None,
) -> dict[str, object]:
    source = source.expanduser().absolute()
    destination = _destination(source, output)
    book = load_book(source)
    custom_css = None
    if css_path is not None:
        try:
            if css_path.stat().st_size > 256 * 1024:
                raise ShuyiError("theme_too_large")
            custom_css = css_path.read_bytes()
        except OSError:
            raise ShuyiError("theme_unreadable") from None
    inventory = build_inventory(book)
    selected = (
        classifier if classifier is not None else configured_classifier(load_config(config_path))
    )
    profile = analyze(
        inventory,
        selected,
        cache_dir or default_cache_dir(),
        reanalyze=reanalyze,
        on_progress=on_progress,
    )
    plan = plan_theme(book, inventory, profile, custom_css)
    try:
        with tempfile.TemporaryDirectory(prefix=".shuyi-", dir=destination.parent) as directory:
            temporary = Path(directory) / "output.epub"
            write_book(book, plan.replacements, temporary)
            report = verify_output(book, temporary, plan)
            _publish(temporary, destination)
    except OSError:
        raise ShuyiError("output_io_failed") from None
    return {
        **report,
        "output": str(destination),
        "source_sha256": book.source_sha256,
        "role_counts": dict(Counter(plan.role_counts)),
        "note_markers": plan.note_count,
        "unknown_nodes": plan.unknown_count,
        "protected_documents": sorted(book.protected),
        "analysis_provenance": profile.provenance,
        "analysis_usage": profile.usage,
        "cache_hits": profile.cache_hits,
        "warnings": list(dict.fromkeys((*book.warnings, *inventory.warnings, *plan.warnings))),
    }
