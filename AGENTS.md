# Shuyi development contract

Read SPEC.md before architecture or behavior changes. Its shared APIs and preservation invariants are authoritative. Read HARNESS.md for verification commands. Keep the project independent of sibling repositories.

The main agent owns architecture, contracts.py, application.py, cli.py, config.py and integration acceptance. Executors own only assigned files; escalate shared-interface conflicts before changing them. Read ~/.agents/Agents.md and its Executor section, plus ~/.agents/workflows/delegation.md, before delegated editing.

Production dependencies: epub -> contracts; layout -> epub/contracts; theme -> epub/contracts; verify -> epub/contracts (not theme writer); application -> these capabilities; cli -> application/config. No cycles, sibling imports, or cross-module private imports. Shared low-level DOM/CSS/notes primitives belong to epub, not the application.

Keep Python files <=500 physical lines, functions <=100 lines, classes <=300 lines. Comments/docstrings use Simplified Chinese; documentation, identifiers and user-facing messages use English. Code must never log book text or credentials. New behavior needs deterministic offline consumer-visible regression coverage. Private copyrighted books and model credentials must never enter the repository.

Concurrent executors skip builds, lint, formatters and tests until the main agent integrates the batch. Never weaken preservation checks, invent role fallbacks, or mark skipped live acceptance as successful. Update CHANGELOG.md for permanent behavior changes.
