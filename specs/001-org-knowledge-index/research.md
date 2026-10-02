# Research: Org knowledge index

Measured 2026-10-02 in a cloud session: seven of the eight non-fork repos cloned at their pinned or
default commits (`awesome-ocean-science` was not), `sqlite-vec` 0.1.9 from PyPI, SQLite 3.45.1. Not measured: bge-m3 itself
(huggingface.co and ollama.com were blocked from the session), and issue/PR text volume.

## R1. Vector store: `asg017/sqlite-vec`

- **Decision**: `sqlite-vec` `vec0`, brute-force KNN, cosine, float32[1024], metadata columns
  `kind`/`repo`/`lang`, next to an FTS5 table in the same file.
- **Rationale**: one portable file; MIT/Apache-2.0; `python3Packages.sqlite-vec` 0.1.9 is in the
  devkit's pinned nixpkgs; SQL joins give dedup and graph queries for free. Brute force is exact
  and fast enough: 8,000 random 1024-d vectors, filtered KNN 26 ms, hybrid RRF 36 ms, file 34 MB.
  It is also one of the three formats marola-dev/marola#398 lists for the cloud bot.
- **Alternatives**: `sqliteai/sqlite-vector`: Elastic License 2.0 outside open-source use, needs
  a commercial licence for production; no gain at this size. Lucene (MIP-0055): right for the JVM
  product, wrong for a Python dev tool. LanceDB: reads from `gs://` directly but adds a Rust/JNI
  dependency; not needed for a 35 MB file. A hosted vector DB: bills around the clock.
- **Trap found**: inside a `vec0` KNN, `kind = coalesce(:kind, kind)` returns zero rows with no
  error. Filters must be appended only when set (`contracts/queries.sql`, checked by
  `contracts/check_contracts.py`).

## R2. Deduplication across the umbrella

Measured over marola, marola-app, -site, -ml, -corpus, -oods and -devkit (`git ls-files -s`):

| Source of duplication | Size | Rule |
|---|---|---|
| Umbrella gitlinks to the 5 submodules | ~2.0 MB of text, all of it doubled | FR-002: index each repo from GitHub; skip mode `160000`; never `--recurse-submodules` |
| Devkit-synced files (issue/PR templates, `labels.yml`, `pr.yml`, `notify-umbrella.yml`, LICENSE) | 75 files, 94 KB | FR-004: one document per normalized content hash, all locations kept |
| Shared blocks (the invariants block in every AGENTS.md, template-filled PR bodies) | 90 chunks | FR-004: chunk-level hash |
| Copied fixtures (`board.schema.json`, `areas.json` in app and site) | ~30 KB | same hash rule, and fixtures are excluded by path anyway |
| Forks (`open-sustainable-technology`, `awesome-open-climate-science`) | upstream's content | FR-003: skip `isFork` |
| Generated copies (docs.marola.dev, `mkdocs/generated-docs`, Scaladoc, corpus tarball, `site-data`) | — | FR-003: never read; their sources are indexed |

Result: 643 text files → 568 unique contents; after path excludes, 591 files → 3,830 chunks →
**3,740 unique** at ≤ 1,600 chars (~1.26 M tokens). Canonical location by producer precedence
(FR-005) so a synced template points at `marola-devkit`, its source.

## R3. Embedder: bge-m3 through Ollama

- **Decision**: `ollama pull bge-m3`; `POST /api/embed {"model": "bge-m3", "input": [...]}` in
  batches of 32; vectors L2-normalized by the tool (idempotent if Ollama already did).
- **Rationale**: 1024 dimensions, 8,192-token context, 100+ languages, so "corrente de retorno"
  and "rip current" land together (MIP-0054); no query instruction prefix, unlike bge-en; MIP-0055
  names it the candidate for the product, so one golden set can compare both. `ollama` 0.34.4 is in the
  pinned nixpkgs.
- **Open**: CPU throughput is unmeasured here; SC-003's 60 minutes for ~1.3 M tokens assumes
  ~400 tokens/s. T014 measures it and the reuse cache (FR-009) makes it a one-off cost anyway.
- **Not used**: bge-m3's sparse and ColBERT outputs; Ollama returns dense only. FTS5 covers lexical.

## R4. Chunking

Markdown splits at `#`–`###` headings, then merges to ≤ 1,600 chars (≤ 512 tokens with margin);
code splits at blank lines (tree-sitter definitions are a later task); an issue or PR is its body
plus one chunk per comment or review thread. Each chunk gets a deterministic header
(`repo · path › heading path`, or `repo#N title`), embedded with the text. That is Anthropic's
contextual-retrieval idea without an LLM call per chunk.

## R5. Freshness: rebuild and reuse, not mutate

Every build writes a new file from scratch and copies a vector from the previous file when
`embed_key = sha256(model ‖ header ‖ text)` matches. Chunking the org takes seconds; only embedding
is slow, and it is cached. No partial-update code, no drift, and the fingerprint is honest.

## R6. Publishing and hosting

| Target | Phase | Cost | Query in place? |
|---|---|---|---|
| GitHub release asset on the umbrella (rolling tag `org-index`) | 0/1 | $0 | no: download, then query locally |
| GCS bucket `org-index/<fingerprint>/` + `latest.json` | 2 | ~35 MB × $0.02/GB-month ≈ $0.001/month; inside the 5 GB US-regional free tier | no: SQLite needs a local file; Cloud Run downloads at cold start (marola-dev/marola#398) |

A SQLite file cannot be queried over GCS: range-request VFS layers exist for plain SQLite, not with
a loadable `vec0`. Every reader downloads; at 35 MB (zstd ~25 MB) that is seconds.

## R7. Infrastructure as code: Besom

- **Decision**: a standalone scala-cli project (`infra/org-index/`, not an sbt module) with
  `besom-core` 0.5.2 and `besom-gcp` 9.0.0-core.0.5, the versions the umbrella's
  `docs/3-Working-on-the-repo/GEMINI-CODE-ASSIST.md` §4 already verified on Maven Central. State in
  a GCS bucket (`pulumi login gs://…`), no Pulumi Cloud account. CI authenticates with Workload
  Identity Federation scoped to `marola-dev/marola`. `pulumi` 3.263.0 is in nixpkgs; the Scala
  language plugin is not (`pulumi plugin install language scala 0.5.2 --server github://api.github.com/VirtusLab/besom`).
- **Where**: with #398's `infra/`, one Pulumi project and one state bucket for all GCP. Until #398's
  MIP decides that home, the program sketch lives in `contracts/gcs-hosting.md`, uncompiled.
- **Gate**: Phase 2, blocked by marola-dev/marola#447; `pulumi up` by a human only.

## R8. More effective approaches for a memory layer / Q&A bot

In the order to try them, each judged by the golden set (FR-013), not by reading answers:

1. **Measure first.** A golden set of ~50 questions with the expected document (shared with
   MIP-0055's) turns every idea below into a recall@k delta. Without it, none can be judged.
2. **Hybrid lexical + dense with RRF** (in v1). Questions name `KnowledgeStore`, `MIP-0055`,
   `#398`: exact tokens that dense vectors blur and BM25 nails.
3. **Deterministic context headers** (in v1, R4). An LLM-written context line per chunk is the
   upgrade, with a local model for $0, only if the eval shows the deterministic header leaves a gap.
4. **Route before retrieving.** Many questions are lookups: "status of MIP-0055" is a table read,
   "who closed #398" is a GitHub call, "where is `--ask` defined" is a grep. A regex router (in
   the spirit of MIP-0045's NLP-over-LLM) sends those to SQL, `gh` or ripgrep, and only open
   questions to retrieval. Cheapest large win for a maintainer bot.
5. **Rerank the top 50** with a multilingual cross-encoder (`bge-reranker-v2-m3`). Usually the
   largest precision gain after hybrid. Ollama has no rerank endpoint; llama.cpp's `--reranking`
   server or sentence-transformers would host it. Unverified here, so measure before adopting.
6. **Graph expansion over references** (`edge` table, in v1's schema). MIP ↔ issue ↔ PR links are
   regex-extractable and exact; pulling one hop around a hit answers "what came of this MIP?".
   Full GraphRAG (LLM-extracted entities, community summaries) is not worth it at ~600 documents.
7. **Agentic retrieval for code.** A coding agent's grep-and-read loop beats embedding retrieval on
   exact code questions. Expose the index as one MCP tool next to grep, not instead of it, and let
   a bot run a bounded loop (`search_org` → `get_document` → follow an edge, at most 3 steps).
8. **Long context instead of retrieval**, for the docs subset only: measure it as one more arm of
   MIP-0032's matrix with prompt caching, not as a default; for a public bot every question would
   pay for the whole context.
9. **Memory is three stores, not one index**:
   - *Knowledge*: this index. Immutable, rebuilt, shared, public content only.
   - *Conversation memory* for a bot: per-user rolling summary plus last turns, in the bot's own
     store with a TTL and delete-on-request (LGPD). Never written into the org index.
   - *Decisions and facts*: already have a home, MIPs and AGENTS.md. An agent "remembers" by
     opening a PR a human reviews, not by inserting a vector.
   Memory services (mem0, Letta, Zep) add a running service for a problem that is a table at this
   size; not evaluated.
10. **bge-m3 sparse + multi-vector** through FlagEmbedding: one model for both halves of hybrid.
    Needs torch outside Ollama; only if FTS5 hybrid underperforms on the golden set.
11. **Quantization / ANN**: unnecessary below ~100 k chunks; `vec0` supports int8 and bit vectors
    when the org grows.
