---
description: "Task list for the org knowledge index"
---

# Tasks: Org knowledge index

**Input**: `specs/001-org-knowledge-index/` (spec, plan, research, data-model, contracts)

**Tests**: required (constitution V). In every story the self-test task comes first and must fail.

**Gate**: a task is implemented only through a GitHub issue carrying `agent-ready` (constitution
IV). `/speckit-taskstoissues` drafts them as `001-T0NN: …`; a person files them. Tasks marked
**[umbrella]** land in `marola-dev/marola` on the same branch name; **[human]** tasks are not for
an agent.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Add `ps.sqlite-vec` and `ps.mcp` to `python`, `pkgs.zstd` and `pkgs.ollama` to `runtimeDeps`, and `org-index = "scripts/org-index.py"` to `tools` in `flake.nix`
- [ ] T002 [P] Create `scripts/org-index.py`: argparse subcommands and exit codes from `contracts/cli.md`, all `NotImplemented`, a `--self-test` that runs and fails; add it to `py_tests` in `tests/self-tests.sh`
- [ ] T003 [P] Copy `contracts/schema.sql` to `scripts/lib/org_index/schema.sql`; the self-test runs `contracts/check_contracts.py`'s assertions against that copy

## Phase 2: Foundational (blocks every story)

- [ ] T004 Fixture builder `scripts/fixtures/org-index/make_fixtures.py`: an umbrella repo with two gitlinks, the two "submodule" repos, a devkit repo whose PR template is copied into all four, a repo marked fork, a `vendor/` file, a `pt-BR` doc, two issues sharing a `MIP-0055` ref; a `LocalSource` reading them like `sources.py` reads GitHub
- [ ] T005 [P] `scripts/lib/org_index/normalize.py`: normalize, `content_sha`, kind by extension, `lang` by stop-word ratio and `pt-BR` path; table-driven cases in the self-test first
- [ ] T006 [P] `scripts/lib/org_index/embed.py`: `Embedder` protocol; `FakeEmbedder` (sha256-seeded unit vectors, call counter); `OllamaEmbedder` (`/api/show` model check → exit 3 naming `ollama pull bge-m3`, `/api/embed` in batches, L2-normalize)
- [ ] T007 `scripts/lib/org_index/store.py`: create schema, insert, canonical rank from config (`data-model.md`), fingerprint into `meta`, write to `PATH.tmp` then rename

**Checkpoint**: fixtures, normalizer, embedders and store exist; stories can start.

## Phase 3: User Story 1 - Build one deduplicated index (P1) 🎯 MVP

**Goal**: `org-index build` writes the deduplicated, embedded file.

**Independent Test**: build the fixtures with `FakeEmbedder`; the assertions in T008 hold.

- [ ] T008 [US1] Self-test first: documents == unique contents; no umbrella path under a gitlink directory is indexed; the fork is absent; the PR template is 1 document with 4 locations, canonical in devkit; `vendor/` is skipped
- [ ] T009 [US1] `sources.py`: `gh api orgs/{org}/repos --paginate` minus forks and archived; each repo's default-branch tarball at the commit read (a tarball has no submodule content, and mode `160000` is skipped when reading a clone); excludes from `org-index.toml`
- [ ] T010 [US1] `chunk.py`: Markdown by `#`–`###`, code by blank lines, merge to ≤ 1,600 chars, hard cap 512 tokens by a conservative chars-per-token bound; headers per `research.md` R4; chunk-level hash dedup
- [ ] T011 [US1] Issues, PRs, comments and review threads through `gh api graphql` with an `updatedAt` cursor; bot authors skipped; `edge` rows for `MIP-\d{4}` and `owner/repo#N`
- [ ] T012 [US1] `--reuse PREV`: copy vectors by `embed_key`; self-test: a second build of unchanged fixtures makes 0 `FakeEmbedder` calls (acceptance 4)
- [ ] T013 [US1] `build --dry-run` and `stats` (counts per repo, kind, lang; documents and chunks folded)
- [ ] T014 [US1] **[human]** First real build on a 4-core laptop: record wall time, tokens/s, file size and counts in `research.md` R3; confirm or revise SC-003

**Checkpoint**: a real `org-index.sqlite` of the whole org exists.

## Phase 4: User Story 2 - Ask the index (P1)

**Goal**: CLI, SQL and MCP answers; a recall number.

**Independent Test**: on the fixture index, T015's assertions hold and `eval` prints recall@5.

- [ ] T015 [US2] Self-test first: hybrid returns both the FTS-only and the vector-only fixture chunk; `kind`/`repo` filters hold, including a regression test for the `coalesce` trap; a model mismatch exits 3
- [ ] T016 [US2] `search.py`, `query` and `show` per `contracts/cli.md` (filters appended only when set)
- [ ] T017 [US2] `mcp_server.py` with `search_org` and `get_document` per `contracts/mcp.md`, `fts` fallback with `degraded`; `plugins/marola-devkit/.mcp.json`
- [ ] T018 [US2] `evaluate.py` and `eval`: recall@k and MRR per mode, baseline file, exit 4 below it; a 5-question fixture golden set
- [ ] T019 [P] [US2] `plugins/marola-devkit/skills/org-search/SKILL.md`: use `search_org` for cross-repo, history and Portuguese questions; grep for exact code
- [ ] T020 [US2] **[umbrella]** **[human]** `evals/org-index-golden.jsonl`: ~50 questions, en and pt, each with expected locations; reuse MIP-0055's questions where they overlap

**Checkpoint**: MVP complete (US1 + US2).

## Phase 5: User Story 3 - Fresh and shared at no cost (P2)

**Goal**: daily rebuild, deltas only, published as a release asset.

**Independent Test**: two workflow runs on a branch; the second embeds 0 chunks and publishes the
same fingerprint.

- [ ] T021 [US3] Self-test first, then `publish --to github` (rolling `org-index` release on the umbrella, sha256 in the asset label) and `fetch` (checksum verified)
- [ ] T022 [US3] `.github/workflows/org-index.yml` (`workflow_call`: config, golden and baseline inputs; Ollama model dir cached on the bge-m3 digest; `contents: write`); actionlint-clean
- [ ] T023 [P] [US3] `devkit.just`: `org-index *args`, `org-index-smoke` (build `--repo marola-dev/marola-corpus`, expect a `knowledge/` hit)
- [ ] T024 [US3] Version bump: `plugin.json`, the flake package version, a tag
- [ ] T025 [US3] **[umbrella]** Caller workflow (daily, `submodule-updated`, manual) pinned to T024's tag, and `org-index.toml`

## Phase 6: User Story 4 - GCS via Besom (P3, Phase 2, blocked by marola-dev/marola#447)

**Goal**: the same file in a private, versioned bucket, every resource in Besom.

**Independent Test**: `pulumi preview` lists `contracts/gcs-hosting.md`'s resources and creates nothing.

- [ ] T026 [US4] Decide the infra home with marola-dev/marola#398's MIP; write `Pulumi.yaml` and `project.scala` from `contracts/gcs-hosting.md`; `scala-cli compile` passes
- [ ] T027 [US4] CI job running `pulumi preview` only; a Claude Code hook refusing `pulumi up|destroy|import` and `gcloud … create` without a human
- [ ] T028 [US4] **[human]** On an explicit go-ahead: create the state bucket, `pulumi up`, set `GCP_WIF_PROVIDER`, `GCP_ORG_INDEX_SA` and `ORG_INDEX_BUCKET` as repository variables
- [ ] T029 [US4] Self-test first (fake `gcloud`), then `publish --to gcs` / `fetch --from gcs` with object metadata; the workflow's WIF step behind `vars.ORG_INDEX_BUCKET`

## Phase 7: Polish

- [ ] T030 [P] `docs/index.md` and `README.md`: `org-index` in the tool list
- [ ] T031 Run `quickstart.md` end to end; replace every illustrative number with a measured one
- [ ] T032 [P] One spec per eval-gated experiment from `research.md` R8 (router, reranker, LLM context headers), each adopted only if it raises golden-set recall

## Dependencies & Execution Order

- Setup → Foundational → US1 → US2 (needs an index) → US3 (needs build + eval) → US4 (needs
  publish, plus the Phase 2 gate).
- T014, T020, T028 are human tasks; T020 blocks T018's real baseline but not its fixture test.
- [P] tasks touch different files: T002/T003, T005/T006, T019, T023, T030, T032.

## Implementation Strategy

MVP is US1 + US2: a local, free, deduplicated, queryable index of the org. Stop there and measure
recall before building refresh. US3 makes it shared. US4 waits for Phase 2.
