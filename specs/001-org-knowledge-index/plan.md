# Implementation Plan: Org knowledge index

**Branch**: `001-org-knowledge-index` (on `claude/bold-noether-egai63`; run spec-kit commands with
`SPECIFY_FEATURE_DIRECTORY=specs/001-org-knowledge-index`) | **Date**: 2026-10-02 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-org-knowledge-index/spec.md`

## Summary

A devkit tool, `org-index`, reads every non-fork marola-dev repo from GitHub at its default branch
plus its issues and PRs. It deduplicates by normalized content hash at document and chunk level,
embeds each unique chunk once with bge-m3 through Ollama, and writes one SQLite file with a
sqlite-vec `vec0` table and an FTS5 table. It answers hybrid queries from a CLI and an MCP server,
scores itself on a golden set, and is rebuilt by the umbrella's workflow, reusing every unchanged
vector. Hosting is a GitHub release asset now and a Besom-declared GCS bucket in Phase 2.

## Technical Context

**Language/Version**: Python 3.14 (the pinned nixpkgs `python3`); Scala 3 + Besom 0.5.2 for the
Phase 2 infra program only

**Primary Dependencies**: `sqlite-vec` 0.1.9, `mcp` 1.29.0 (both `python3Packages` in the pinned
nixpkgs), stdlib `sqlite3`/`urllib`/`tomllib`; `gh`, `zstd` and `ollama` 0.34.4 on `PATH`

**Storage**: one SQLite file (`contracts/schema.sql`), ~35 MB, zstd ~25 MB

**Testing**: `--self-test` (fixture git repos built in a temp dir, a deterministic fake embedder),
`contracts/check_contracts.py`, `org-index eval` on a golden set

**Target Platform**: Linux and macOS laptops in `nix develop`; GitHub-hosted `ubuntu-latest` for CI

**Project Type**: CLI tool + MCP server inside the devkit; a reusable workflow; an IaC program

**Performance Goals**: query < 100 ms after embedding (measured 36 ms); unchanged rebuild < 2 min
with 0 embedding calls; first full build < 60 min on 4 CPU cores (to measure, T014)

**Constraints**: $0 in Phase 0/1; self-test offline; no read of one repo through another; bge-m3
chunk ≤ 512 tokens

**Scale/Scope**: 8 repos, ~600 files, ~670 issues/PRs, ~3.7 k file chunks (measured) + an estimated
2–3 k issue/PR chunks; brute-force KNN holds to ~100 k

## Constitution Check

*Gate: passed before research; re-checked after design.*

| Principle | Status |
|---|---|
| I. Local and free first | pass: Ollama + a file; GCS is US4, opt-in by `ORG_INDEX_BUCKET` |
| II. Cost and deployment gate | pass: Phase 1 is $0; `pulumi preview` only in CI; `up` is a human step; OIDC, no keys |
| III. Phase discipline | pass: US4 is split out, labelled Phase 2, blocked by marola-dev/marola#447 |
| IV. Agent-ready gate | pass: tasks.md is a plan; each task is implemented only through an `agent-ready` issue |
| V. Test first, offline | pass: each task lists its failing self-test first; no network in `--self-test` |
| VI. No repo reads another's tree | pass: repos are read from GitHub at their own HEAD; the umbrella workflow only runs the tool |
| VII. Few words | pass |

## Project Structure

### Documentation (this feature)

```text
specs/001-org-knowledge-index/
├── spec.md  plan.md  research.md  data-model.md  quickstart.md  tasks.md
├── checklists/requirements.md
└── contracts/
    ├── schema.sql  queries.sql  check_contracts.py   # executable contracts
    ├── cli.md  mcp.md
    └── gcs-hosting.md                                 # Besom program sketch + cost
```

### Source Code

```text
marola-devkit/
├── scripts/org-index.py                 # entry: subcommands, --self-test
├── scripts/lib/org_index/
│   ├── sources.py                       # gh api: repos (no forks), tarball at HEAD, issues/PRs (GraphQL)
│   ├── normalize.py                     # normalize, hash, kind, lang
│   ├── chunk.py                         # markdown/code/issue chunkers + headers
│   ├── embed.py                         # OllamaEmbedder, FakeEmbedder
│   ├── store.py                         # schema, write, canonical, reuse by embed_key, fingerprint
│   ├── search.py                        # vec / fts / hybrid, filters appended only when set
│   ├── evaluate.py                      # recall@k, MRR, baseline gate
│   ├── mcp_server.py                    # search_org, get_document
│   └── schema.sql                       # = contracts/schema.sql
├── scripts/fixtures/org-index/          # fixture repo builder, golden mini-set
├── plugins/marola-devkit/.mcp.json      # registers `org-index mcp`
├── plugins/marola-devkit/skills/org-search/SKILL.md
├── .github/workflows/org-index.yml      # reusable: build --reuse, eval, publish
├── devkit.just                          # org-index, org-index-smoke
├── flake.nix                            # tools.org-index; python += sqlite-vec, mcp; zstd
└── tests/self-tests.sh                  # + scripts/org-index.py

marola (umbrella, its own PR, same branch name)
├── .github/workflows/org-index.yml      # caller: daily, on submodule-updated, manual
├── org-index.toml                       # repo ranks, excludes, bot authors
└── evals/org-index-golden.jsonl         # ~50 questions, en + pt

infra/org-index/                         # Phase 2, home decided with marola-dev/marola#398's MIP
├── Pulumi.yaml
└── project.scala
```

**Structure Decision**: the tool is generic (any org, config from a file), so it lives in the
devkit beside `cost-split`. The org-specific config, golden set and schedule live in the umbrella,
the one repo whose job is to span the others. Nothing goes into marola-app: MIP-0055's Lucene store
is the product's retrieval, and this is the team's.

## Workflow (umbrella caller → devkit reusable)

```text
schedule 05:17 UTC | repository_dispatch submodule-updated | workflow_dispatch
  → nix develop (devkit flake) → ollama serve; restore ~/.ollama/models from actions/cache keyed on the bge-m3 digest
  → gh release download org-index → prev.sqlite (absent on first run)
  → org-index build --reuse prev.sqlite --config org-index.toml
  → org-index eval evals/org-index-golden.jsonl --baseline … (fails the run below baseline)
  → org-index publish --to github  (and --to gcs when vars.ORG_INDEX_BUCKET is set, Phase 2)
```

If a first full build exceeds a hosted runner's 6-hour limit (T014 tells), a maintainer seeds it once
from a laptop with `org-index publish`; CI then only embeds deltas.

## Complexity Tracking

| Choice | Why needed | Simpler alternative rejected because |
|---|---|---|
| Two dedup levels (document and chunk) | 64 duplicate files and 90 duplicate chunks measured, the chunk ones inside otherwise unique files (the invariants block, PR templates) | file-level only leaves the shared blocks as near-identical top hits |
| FTS5 beside `vec0` | identifiers (`KnowledgeStore`, `MIP-0055`, `#398`) are exact tokens | vector-only misses them; a second store would mean two files |
| `edge` table in v1 | costs one regex pass; enables graph expansion later without a rebuild of the schema | adding it later bumps `schema_version`, which every reader checks |
