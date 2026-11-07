# Shuyi — 书衣

Give an existing EPUB a Chinese reading theme without translating or rewriting its prose.

Shuyi reads EPUB 2/3 archives, asks an OpenAI-compatible model to identify semantic roles, applies bounded CSS overrides, and independently verifies preservation before publishing a new file. It does not rebuild the book through an EPUB generator.

## Install

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required for source development.

```sh
uv sync --locked --group dev
uv run shuyi --help
```

For an installed command from this checkout:

```sh
uv tool install .
shuyi --version
```

## Model configuration

Create `~/.config/shuyi/config.toml` (or `$XDG_CONFIG_HOME/shuyi/config.toml`):

```toml
[model]
base_url = "https://api.openai.com/v1"
name = "your-model"
api_key_env = "OPENAI_API_KEY"
```

Set the named API-key environment variable locally. Alternatively, replace `api_key_env` with `api_key = "your-key"` in a private local TOML file and restrict its permissions to `0600`; this stores the key in plaintext, so do not commit or share the file. Configuring both key sources is rejected. Literal keys are redacted in configuration representations and JSON serialization. `OPENAI_MODEL` and `OPENAI_BASE_URL` supply defaults; explicit TOML values take precedence. `--config PATH` selects an explicit file. Unknown keys and invalid values fail rather than being ignored.

The configured endpoint receives original text fragments, source markup and relevant CSS evidence. This has privacy and model-usage costs. No complete EPUB archive is uploaded. Keys and book excerpts are excluded from CLI errors. There is no provider fallback or rule-only mode.

## Use

```sh
# Default output: book.shuyi.epub beside the original
uv run shuyi style book.epub

# Explicit destination and configuration
uv run shuyi style book.epub -o reading.epub --config config.toml

# Replace the built-in appearance while reusing semantic analysis
uv run shuyi style book.epub --css reading.css -o custom.epub

# Refresh semantic analysis explicitly
uv run shuyi style book.epub --reanalyze -o refreshed.epub

# Local, credential-free structural inventory
uv run shuyi inspect book.epub

# Local output integrity; standalone checking cannot prove source equality
uv run shuyi check reading.epub

# Optional official EPUB conformance check (requires installed EPUBCheck/Java)
uv run shuyi check reading.epub --epubcheck
```

Commands print a JSON result to stdout; analysis progress and sanitized errors go to stderr. Existing output files, symlinks and input aliases are never overwritten. A racing destination is protected by no-clobber publication. Output parent directories must already exist.

## Theme and preservation

The built-in reading theme uses local Song/serif font fallbacks, two-character body indentation, red heading levels, Kai-style quotations, and brown circular note markers. No fonts or executable theme runtime are bundled. The CSS derives from the sibling wenyi project's Chinese reading theme, but Shuyi has no runtime dependency on wenyi.

Confirmed note references and backlinks use real `注` link text. This is the only intentional visible text replacement. Note prose, anchor structure, IDs and links remain intact. Ambiguous numeric links are not guessed to be notes. Custom CSS replaces general appearance but retains this confirmed-label policy.

The source CSS is retained. Exact-address overrides apply only to accepted non-null roles; even a custom `p` or `*` selector cannot directly style unknown nodes. Normal inheritance from styled ancestors can still affect them. Navigation, cover/title pages, code, SVG, MathML and other protected scopes are not directly themed; fixed-layout pages are retained and reported. Inline-important shorthand conflicts that cannot be safely overridden fail explicitly.

Untouched archive members retain identical decompressed bytes, including images, fonts and original CSS. Prose, navigation, link graph and book identity are checked against the source. XML formatting and namespace prefix spelling may change; whole-ZIP byte identity is not promised. EPUB3 modification timestamps are updated, not book titles or identifiers.

Custom CSS is a restricted reading-style subset: static selectors, typography, spacing, color, borders, pagination and light/dark media conditions. Imports, remote resources, bundled font loading, generated content, variables, animations, dynamic pseudo-classes and destructive layout rules are rejected. Unsupported source cascade structures fail safely; this is not a general browser CSS engine or malformed-book repair tool.

Theme values use a finite validated subset of keywords, CSS3 colors, font families and simple dimensions. CSS arithmetic functions and source `@namespace` declarations are currently rejected explicitly rather than being misinterpreted as safe override coverage or source evidence.


## Analysis and cache

Role analysis groups source elements by structure and applicable CSS, samples first/middle/last representatives, then checks held-out examples. Conflicts or unknowns trigger individual analysis of remaining nodes. Sampling may miss rare exceptions and conflict expansion can approach full-book analysis cost. Unknown never means body.

Checkpoints live under `$XDG_CACHE_HOME/shuyi` or `~/.cache/shuyi`. Complete batches survive interruption, including an interrupted refresh resumed with `--reanalyze`. Cache identity includes the original EPUB and inventory/protocol versions, not selected output CSS or model name. Switching CSS does not make new model calls; switching model settings requires `--reanalyze` to replace accepted analysis. A failed refresh retains the previously accepted profile. Valid cached analysis works without API credentials. Corrupt caches fail explicitly instead of silently triggering paid work. Only one analysis per cache identity may run at a time.

## Development

[SPEC.md](SPEC.md) is the implementation contract. [HARNESS.md](HARNESS.md) describes ownership, offline fixtures, CI gates, private-book live acceptance and visual verification.

```sh
make check
uv build
```

Default tests are offline. Live acceptance is opt-in and uses private files outside version control. EPUB standards compliance and archive preservation do not guarantee identical rendering or popup notes across readers.
