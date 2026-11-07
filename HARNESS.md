# Development and acceptance harness

## Source of truth

SPEC.md defines product scope, shared contracts, safety boundaries and acceptance. AGENTS.md assigns owners and decision boundaries. Contracts are resolved before concurrent implementation; executors escalate missing design rather than inventing shared APIs. No persistent progress ledger is required.

## Fast local loop

```sh
uv sync --group dev
uv run shuyi --help
uv run python -m unittest discover -s tests
```

## Complete offline gate

```sh
make check
uv build
```

The gate runs Ruff lint/format verification, architecture dependency/size checks and all deterministic unittest contracts. Tests inject only the Classifier external boundary; EPUB reads, theme compilation, independent verification and production CLI publication remain real. Fixtures are generated EPUB2/3 archives. The default suite sends no model requests.

## Opt-in private-book acceptance

```sh
export SHUYI_TEST_EPUB='/absolute/path/to/private.epub'
export SHUYI_TEST_OUTPUT_DIR='/absolute/path/to/acceptance-output'
export SHUYI_LIVE_ACCEPTANCE=1
# Configure ~/.config/shuyi/config.toml and its API key environment variable first.
uv run python -m unittest tests.test_live_acceptance -v
```

`SHUYI_TEST_CONFIG` optionally selects an explicit Shuyi TOML file. To reuse a locally installed AiNiee application's currently selected model for acceptance only, set `SHUYI_TEST_AINIEE_CONFIG` to its config.json path. The harness reads the selected endpoint/model and places its key only in the child process environment; it writes a secret-free model.toml beside acceptance evidence. This is a test adapter, not a production configuration fallback.

The live acceptance invokes the production CLI with the real configured model, checks output/source preservation and runs again against the accepted cache. Output archives and JSON evidence go outside the source tree by default or into ignored artifacts/. A skipped live test is not evidence of successful live acceptance. Never commit the private book, extracts, cached model evidence or credentials.


For browser-based evidence, load chapters as `application/xhtml+xml` (for example, an `.xhtml` preview copy in the extracted resource directory). Opening an EPUB XHTML member whose filename ends in `.html` as `text/html` misparses legal self-closing XML elements and is not valid EPUB rendering evidence.

## Visual inspection

Open selected original/output chapters using a real local reading surface with scripts and external resources disabled. Inspect representative body text, headings, quotes and notes when present. Report the actual inspected surface; browser rendering is not proof of compatibility with every EPUB reader. Archive/content checks remain mandatory.

## Failure diagnostics

Production errors expose stable codes and archive resource names only, never raw source or API response text. Failed analysis retains completed checkpoints; failed refresh retains the previously accepted profile. Failed verification leaves no published output. CI runs only locked offline gates, never private-book/model acceptance.
