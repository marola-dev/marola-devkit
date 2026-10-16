# Feature Specification: Org knowledge index

**Feature Branch**: `001-org-knowledge-index` (developed on `claude/bold-noether-egai63`)

**Created**: 2026-10-02

**Status**: Draft

**Input**: User description: "Create the asg017/sqlite-vec database. Index, chunk and embed ALL
marola-dev content without duplication (due to the umbrella structure). Commands to use bge-m3. How
to host this database in GCS with Besom. How to query or test it. Consider more effective approaches
for a memory layer / Q&A RAG bot."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build one deduplicated index of the whole org, locally (Priority: P1)

A maintainer or a coding agent runs one command and gets a single SQLite file holding every text
file, issue, pull request and review comment of every marola-dev repo, chunked and embedded with
bge-m3 through a local Ollama, with each piece of content stored once however many repos carry it.

**Why this priority**: Without the file nothing else exists. It costs nothing and needs no phase gate.

**Independent Test**: Run the build against fixture repos (one umbrella with gitlinks to two
"submodules", one devkit-synced file copied into all three, one fork) with a fake embedder; check
the document count equals the unique-content count and no submodule file is indexed twice.

**Acceptance Scenarios**:

1. **Given** Ollama with `bge-m3` pulled and a `gh` login, **When** `org-index build` runs,
   **Then** it writes `org-index.sqlite` with one vector per unique chunk and prints counts per repo,
   kind and language.
2. **Given** `.github/PULL_REQUEST_TEMPLATE.md` is identical in 7 repos, **When** the index is built,
   **Then** it holds 1 document with 7 locations, and its canonical location is `marola-devkit`.
3. **Given** the umbrella's `marola-app` gitlink, **When** the umbrella is indexed, **Then** no file
   under `marola-app/` is read from the umbrella; those files come only from `marola-dev/marola-app`.
4. **Given** a previous index, **When** the build runs again with no changes, **Then** zero
   embedding calls are made.

---

### User Story 2 - Ask the index a question (Priority: P1)

A maintainer types a question in English or Portuguese, or an agent calls an MCP tool, and gets
the top passages with repo, path or issue link, and score, mixing keyword and semantic matches.

**Why this priority**: The index exists to be queried; with US1 it is the MVP.

**Independent Test**: Against a fixture index, `org-index query "corrente de retorno"` returns the
fixture chunk that only BM25 finds and the one only the vector finds; `org-index eval` on a golden
set prints recall@5 and MRR.

**Acceptance Scenarios**:

1. **Given** a built index, **When** `org-index query "where is the KnowledgeStore trait?" -k 5`,
   **Then** results print as `score repo:path › heading` with a GitHub URL, in under 1 s after the
   query embedding.
2. **Given** `--kind issue --repo marola-dev/marola`, **When** querying, **Then** only umbrella
   issues are returned.
3. **Given** the plugin's MCP server configured, **When** Claude Code calls `search_org`, **Then**
   it receives the same results as JSON.
4. **Given** `evals/golden.jsonl`, **When** `org-index eval`, **Then** it prints recall@k and MRR
   per mode (`vec`, `fts`, `hybrid`) and exits non-zero below the stored baseline.

---

### User Story 3 - Keep it fresh and shared at no cost (Priority: P2)

The umbrella rebuilds the index daily and on submodule updates, reusing every unchanged vector,
and publishes it as a GitHub release asset; `org-index fetch` downloads the latest.

**Why this priority**: Without refresh the index rots; GitHub hosting keeps it in Phase 1.

**Independent Test**: Run the reusable workflow with `act` or on a branch; the second run with no
change embeds 0 chunks and publishes a file with the same fingerprint.

**Acceptance Scenarios**:

1. **Given** one changed MIP, **When** the workflow runs, **Then** only that MIP's changed chunks
   are embedded.
2. **Given** a published index, **When** `org-index fetch`, **Then** the file lands in
   `~/.cache/marola/org-index.sqlite` and its checksum matches the release.

---

### User Story 4 - Host the index in a GCS bucket declared in Besom (Priority: P3, Phase 2)

The same file is uploaded to a private, versioned GCS bucket by CI through OIDC, with every
resource declared in Besom, so the future Cloud Run bot (marola-dev/marola#398) loads it at cold
start.

**Why this priority**: GCP is Phase 2 (`docs/PHASES.md`); blocked by the phase gate
(marola-dev/marola#447) and by a human `pulumi up`.

**Independent Test**: `just infra-preview` shows the bucket, IAM bindings and the Workload Identity
provider with no console step, and no resource is created.

**Acceptance Scenarios**:

1. **Given** Pulumi state and a GCP project, **When** `pulumi preview`, **Then** the plan lists the
   resources in `contracts/gcs-hosting.md` and their estimated monthly cost is cents.
2. **Given** a human ran `pulumi up` once, **When** the workflow publishes, **Then**
   `gs://<bucket>/org-index/<fingerprint>/org-index.sqlite.zst` and `latest.json` exist and the
   bucket refuses public access.

---

### Edge Cases

- A file exists in two repos with whitespace-only differences → normalized hash makes them one
  document.
- A file moves between repos (MIP-0074 moved docs app → umbrella) → only default-branch HEAD is
  indexed, so it exists once; history is out of scope.
- Fork repos (`open-sustainable-technology`, `awesome-open-climate-science`) → skipped by default;
  their content is upstream's.
- Generated trees (`mkdocs/generated-docs`, Scaladoc, `site-data` branch, release tarballs) →
  never read; they are copies of indexed sources.
- Vendored or fixture files (`site/static/vendor/`, `src/test/resources/`, `fixtures/`) → skipped
  by the default exclude list; overridable.
- Bot-authored issues, PRs and comments (`chore/pointer-sync`, dependabot) → skipped by default.
- Ollama absent or `bge-m3` not pulled → the build fails before reading anything, naming the
  command to run.
- The index was built with another model or dimension → `query` refuses with both names.
- Content over the embedder's context → the chunker never emits a chunk over 512 tokens.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The tool MUST store the index as one SQLite file using `asg017/sqlite-vec` `vec0`
  for vectors and FTS5 for keywords, readable by any SQLite with the extension loaded.
- **FR-002**: The tool MUST enumerate repos from the GitHub org API and read each at its default
  branch HEAD; it MUST NOT recurse into submodules or read one repo through another's checkout.
- **FR-003**: The tool MUST skip gitlinks (mode `160000`), forks, archived repos, binaries,
  lockfiles, vendored and fixture paths, and generated trees, each list overridable by config.
- **FR-004**: The tool MUST deduplicate at two levels: a document per unique normalized content
  hash with every location kept, and a chunk per unique normalized chunk hash.
- **FR-005**: The tool MUST pick one canonical location per document by a fixed repo precedence
  (producer before consumer: devkit, corpus, app, ml, oods, site, umbrella, others).
- **FR-006**: The tool MUST index issues, pull requests and their comments and reviews from every
  non-fork repo, skipping bot authors by default.
- **FR-007**: The tool MUST chunk by structure: Markdown by heading, code by blank-line blocks
  (definitions later), issues by body and by comment; at most 512 tokens per chunk, and each chunk
  carries a header of repo, path or issue title, and heading path.
- **FR-008**: The tool MUST embed with `bge-m3` through Ollama's `/api/embed` by default (1024
  dimensions), with the model, host and batch size configurable.
- **FR-009**: The tool MUST reuse a vector from a previous index when model, header and text are
  unchanged, so an unchanged rebuild makes zero embedding calls.
- **FR-010**: The tool MUST record a fingerprint (schema version, embed model and digest, chunker
  version, each repo's commit, the issues high-water mark) and refuse to query with a mismatched
  embedder.
- **FR-011**: The tool MUST answer queries in `vec`, `fts` and `hybrid` (reciprocal rank fusion)
  modes with `kind`, `repo` and `lang` filters, as text or JSON.
- **FR-012**: The tool MUST expose `search_org` and `get_document` as an MCP stdio server, and the
  plugin MUST register it.
- **FR-013**: The tool MUST evaluate retrieval on a golden set (recall@k, MRR) and gate on a
  stored baseline.
- **FR-014**: The tool MUST have a `--self-test` that needs no network, model or credentials.
- **FR-015**: Publishing MUST default to a GitHub release asset on the umbrella; GCS is a second
  target behind config, never the default.
- **FR-016**: The GCS infrastructure MUST be declared in Besom, run `preview` only in CI, and keep
  `up`/`destroy` to a human on go-ahead.

### Key Entities

- **Source**: one repo snapshot: name, default branch, commit, issues high-water mark.
- **Document**: one unique piece of content (file, issue, PR, comment thread), keyed by its
  normalized hash; kind, language, title.
- **Location**: where a document lives: repo, path or `issues/N`, URL. Many per document.
- **Chunk**: one unique retrievable passage with its header; occurs in one or more documents.
- **Vector**: one bge-m3 embedding per chunk, with `kind` and canonical `repo` as filters.
- **Edge**: a reference from a document to a MIP, issue or PR (`MIP-0055`,
  `marola-dev/marola#398`), for graph expansion later.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Indexed documents equal unique normalized contents: on 2026-10-02's trees, 568
  unique of 643 files before the path excludes.
- **SC-002**: No path inside a submodule is ever read from the umbrella (self-test asserts it).
- **SC-003**: A full first build of the org finishes in under 60 minutes on a 4-core laptop CPU
  (target, to measure in T014); a rebuild with nothing changed finishes in under 2 minutes with 0
  embedding calls.
- **SC-004**: A hybrid query returns in under 100 ms after the query embedding at the org's size
  (measured: 36 ms over 8,000 vectors with sqlite-vec 0.1.9).
- **SC-005**: Hybrid recall@5 on the golden set is at least the better of `vec` and `fts` alone.
- **SC-006**: Total spend for Phase 1 is $0; Phase 2 storage stays under $0.10/month.

## Assumptions

- The index serves maintainers and agents. A public bot (marola-dev/marola#398) builds its own
  allow-listed index with the same tool and config; it never serves this one.
- Every marola-dev repo is public, so the index holds nothing private and a public release asset is
  acceptable. A private repo added later needs a private publish target first.
- Default branches only; git history and closed-unmerged branches are out of scope for v1.
- Ollama is the embedding runtime; bge-m3's sparse and multi-vector outputs are not exposed by
  Ollama and are not used (FTS5 covers the lexical side).
- This is a devkit tool: it lives here, runs from the umbrella's workflow, and is not part of the
  product. MIP-0055's in-app Lucene store is a separate concern.
