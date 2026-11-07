from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "contracts": set(),
    "epub": {"contracts"},
    "layout": {"contracts", "epub"},
    "theme": {"contracts", "epub"},
    "verify": {"contracts", "epub"},
    "config": {"contracts", "layout"},
    "application": {"contracts", "config", "epub", "layout", "theme", "verify"},
    "cli": {"application", "contracts"},
    "__main__": {"cli"},
    "__init__": set(),
}


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT / "src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports(tree: ast.AST) -> list[tuple[int, str, str | None]]:
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name, None) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                result.append((node.lineno, "<relative>", None))
            elif node.module:
                result.extend((node.lineno, node.module, alias.name) for alias in node.names)
    return result


def check_sizes(path: Path, source: str, tree: ast.AST) -> list[str]:
    relative = path.relative_to(ROOT)
    failures = []
    if len(source.splitlines()) > 500:
        failures.append(f"file_size {relative}: current={len(source.splitlines())} limit=500")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            count = node.end_lineno - start + 1
            limit = 300 if isinstance(node, ast.ClassDef) else 100
            if count > limit:
                failures.append(
                    f"symbol_size {relative}:{node.name}: current={count} limit={limit}"
                )
    return failures


def check_imports(path: Path, tree: ast.AST, modules: set[str]) -> tuple[list[str], set[str]]:
    name = module_name(path)
    capability = name.split(".")[1] if "." in name else "__init__"
    failures = []
    edges = set()
    for line, target, member in imports(tree):
        location = f"{path.relative_to(ROOT)}:{line}"
        if target == "<relative>":
            failures.append(f"absolute_import {location}: use an absolute public import")
        if target.startswith("trans_novel"):
            failures.append(f"sibling_dependency {location}: {target}")
        if not target.startswith("shuyi"):
            continue
        resolved = f"{target}.{member}" if member and f"{target}.{member}" in modules else target
        if resolved in modules and resolved != name:
            edges.add(resolved)
        if member and member.startswith("_") and target != name:
            failures.append(f"private_import {location}: {target}.{member}")
        target_capability = resolved.split(".")[1] if "." in resolved else "__init__"
        if target_capability not in {capability, "__init__"}:
            if target_capability not in ALLOWED.get(capability, set()):
                failures.append(
                    f"dependency_direction {location}: {capability} -> {target_capability}"
                )
    return failures, edges


def cycles(graph: dict[str, set[str]]) -> list[str]:
    visited: set[str] = set()
    active: list[str] = []
    failures = []

    def visit(name: str) -> None:
        if name in active:
            cycle = " -> ".join([*active[active.index(name) :], name])
            failures.append(f"import_cycle {cycle}")
            return
        if name in visited:
            return
        active.append(name)
        for target in sorted(graph.get(name, ())):
            visit(target)
        active.pop()
        visited.add(name)

    for name in sorted(graph):
        visit(name)
    return failures


def main() -> int:
    files = sorted(
        path
        for directory in ("src", "tests", "scripts")
        for path in (ROOT / directory).rglob("*.py")
    )
    modules = {module_name(path) for path in files if path.is_relative_to(ROOT / "src")}
    failures = []
    graph: dict[str, set[str]] = {}
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            failures.append(f"parse_error {path.relative_to(ROOT)}")
            continue
        failures.extend(check_sizes(path, source, tree))
        if path.is_relative_to(ROOT / "src"):
            problems, edges = check_imports(path, tree, modules)
            failures.extend(problems)
            graph[module_name(path)] = edges
    failures.extend(cycles(graph))
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Architecture passed: {len(files)} Python files; no boundary or size violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
