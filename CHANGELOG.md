# Changelog

## [Unreleased]

### Added

- Independent `style`, `inspect`, and `check` commands for EPUB 2/3, preserving prose, resources, navigation, and original CSS.
- Source-based model role analysis with strict bounded requests, sampling/held-out checks, complete evidence chunking, atomic checkpoints, interrupted-refresh recovery, and credential-free accepted-cache reuse.
- Chinese reading typography, exact-node CSS overrides, conservative inline-important coverage, and confirmed note labels rendered as circular `注` links.
- Independent reversible change verification, bounded archive safety, non-overwriting publication, and optional EPUBCheck integration.
- Executable offline/real-book acceptance harness, architecture gates, CI, source distribution, and wheel packaging.
- Private TOML `model.api_key` credentials with redacted representations, strict single-source selection, and credential-free cached acceptance even when the initial configuration contains a literal key.

### Fixed

- Serialize removed multipart note-label text/tails as absent XML values, retaining nested markup and reciprocal links without weakening independent edit verification.
