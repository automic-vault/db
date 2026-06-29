# Goal: Port Automic Vault Package Data Generation

Build this repository into the central package database used by Automic Vault,
fed by deterministic source imports and later enriched by agent/skill runs.

## Current Goal: Package History and Usage Enrichment

Supplement every package with source-backed, Wikipedia-style project history
focused on package nerds: how the tool began, how it was adopted, how people
use it, and why it matters in CLI/package-manager culture.

- Store the data as nullable agent enrichment under `history`.
- Use `null` for niche packages where reliable source-backed history is thin.
- Allow more paragraphs for significant packages.
- Keep sources with the published history block so package pages can show their
  receipts.
- Render the data in `../av.www` package pages and markdown alternates.

## Scope

- Start with Homebrew formulae only.
- Keep the data model open for npm, PyPI, casks, and other ecosystems, but do
  not generate those package records yet.
- Port the install-command logic for other package managers from
  `~/src/automic-vault` so Homebrew entries can still expose source-backed
  install lines such as apt, dnf, pacman, nix, winget, scoop, and friends.

## Repository Shape

- Put executable pipeline steps in `scripts/`, named with numeric prefixes in
  run order, for example `scripts/01-brew-fetch.py`.
- Put reusable Python code in importable modules under `scripts/lib/` or another
  small package directory, so numbered scripts stay thin and composable.
- Store downloaded and intermediate artifacts in `cache/`.
- Treat `projects/` as the published database output. Never update files there
  until the pipeline has produced and validated a complete staged result.
- Preserve the current YAML-per-project shape under `projects/<provider>/`.
  Expand the example schema only as needed for deterministic Homebrew data,
  install lines, provenance, and later agent curation.

## Pipeline

- Use Python for data generation.
- Use a real build runner with dependency tracking, content fingerprints, and
  incremental rebuilds. `make` is acceptable but not preferred; choose something
  less annoying if it keeps file dependencies explicit and cache-aware.
- Design the pipeline as ordered steps:
  - fetch source indexes into `cache/`
  - normalize/cache parsed source facts
  - derive install-line indexes
  - render candidate project YAML into a staged output directory
  - validate staged output
  - atomically replace `projects/` files only after validation passes
- Support a fast debug path that reuses cache by default.
- Support an explicit refresh mode for daily GitHub updates.

## Clean Diff Requirements

- Sort projects, YAML keys, arrays, and install-line maps deterministically.
- Preserve stable wrapping and formatting so unchanged upstream data does not
  churn files.
- Do not write timestamps into `projects/` output unless they are meaningful
  source facts. Put volatile metadata in `cache/` instead.
- Use content comparisons before replacing files, so file mtimes and git diffs
  stay quiet when data is unchanged.
- Validate that every generated record represents a CLI, not a transitive or
  library-only package.

## Homebrew Inputs To Port

- Homebrew formula API fetching and caching from `scripts/build-db.py` and
  `scripts/generate-pkg-page-enrichment.py`.
- Formula metadata normalization: name, display name, homepage, repository,
  docs, description, license, version, package manager URL, dependencies,
  executables, bottle/platform facts, and source provenance.
- Existing rules that identify CLI packages and executable names.
- Homebrew native install lines:
  - `brew: brew install <formula>`
  - `av: sudo av install brew:<formula>`
- Cross-manager install-line generation from `generate-pkg-manager-indexes.py`
  and `generate-pkg-cross-ecosystem.py`, restricted initially to Homebrew
  packages as targets.

## Acceptance Criteria

- Running the pipeline from a clean checkout creates `cache/` artifacts and
  validated Homebrew YAML records under `projects/brew/`.
- Running it a second time without refresh produces no git diff.
- Cached runs are fast enough for iterative debugging.
- Refresh runs are suitable for a daily scheduled GitHub workflow.
- The generated `projects/brew/awscli.yml` remains cleanly formatted and gains
  the complete set of deterministic keys required by the new schema.
