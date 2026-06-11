# Approval Gates Metadata

This is a metadata-only seed for package approval gates. It is not integrated
into Automic Vault, Nucleus, or isotope enforcement yet.

The shape follows `docs/package-approval-metadata.md` in the parent repo:

- ecosystem folders at the root
- one package per YAML file
- declarative command matchers
- consequence labels
- anxiety-level gate recommendations

## Seed Set

The initial `brew/` manifests cover the top 20 Homebrew formulae by 365-day
install-on-request analytics, fetched from:

```text
https://formulae.brew.sh/api/analytics/install-on-request/365d.json
```

Snapshot used: 2026-05-21.

| Rank | Formula | Installs on request |
| ---: | --- | ---: |
| 1 | `gh` | 2,507,412 |
| 2 | `node` | 2,495,502 |
| 3 | `awscli` | 2,284,962 |
| 4 | `git` | 1,575,630 |
| 5 | `ffmpeg` | 1,533,662 |
| 6 | `uv` | 1,446,687 |
| 7 | `cmake` | 1,223,308 |
| 8 | `go` | 1,218,093 |
| 9 | `pyenv` | 998,553 |
| 10 | `coreutils` | 935,374 |
| 11 | `pkgconf` | 878,381 |
| 12 | `python@3.13` | 828,012 |
| 13 | `openssl@3` | 825,096 |
| 14 | `imagemagick` | 801,526 |
| 15 | `xcbeautify` | 796,658 |
| 16 | `gemini-cli` | 785,354 |
| 17 | `jq` | 773,231 |
| 18 | `docker` | 756,811 |
| 19 | `mkcert` | 743,064 |
| 20 | `mise` | 710,929 |

## Validation

Current validation is intentionally light:

```sh
ruby -rdate -ryaml -e 'ARGV.each { |path| YAML.safe_load(File.read(path), permitted_classes: [Date], aliases: true); puts "#{path}: ok" }' brew/*.yaml
```

Future work should add a small schema validator before this metadata is used by
runtime tools.
