# Quickstart: Org knowledge index

What runs **today** is marked *now*; the rest is the target interface once tasks.md lands.

## 1. bge-m3 through Ollama (*now*)

```bash
nix develop                                  # devkit shell: ollama 0.34.4 on PATH
ollama serve &                               # or the system service
ollama pull bge-m3                           # ~1.2 GB, once
curl -s localhost:11434/api/embed \
  -d '{"model":"bge-m3","input":["corrente de retorno","rip current"]}' \
  | jq '.embeddings | length, (.[0] | length)'          # 2, then 1024
```

## 2. The contracts against real sqlite-vec (*now*)

```bash
python3 -m pip install sqlite-vec==0.1.9     # or: nix shell --impure --expr '(import <nixpkgs> {}).python3.withPackages (p: [p.sqlite-vec])'
python3 specs/001-org-knowledge-index/contracts/check_contracts.py
# contracts ok (sqlite 3.x, sqlite-vec v0.1.9)
```

## 3. Build the index

```bash
org-index build --dry-run                    # free: files, unique docs, chunks, tokens; no embedding
org-index build                              # ~1.3 M tokens through bge-m3, first time only
org-index build --reuse ~/.cache/marola/org-index.sqlite   # later: only changed chunks are embedded
org-index stats
```

`stats` shows the dedup at work (the counts are the 2026-10-02 measurement over 7 repos; the
fingerprint is illustrative):

```text
fingerprint 3f9a1c0b7e21  bge-m3 (1024)  chunker v1  2026-10-02T21:00Z
repos 7   files 591 → documents 527 (64 duplicates folded)   chunks 3,830 → 3,740 unique
```

Or don't build: `org-index fetch` downloads the umbrella's latest release asset.

## 4. Query it

```bash
org-index query "how does the safety footer decide when to show?"
org-index query "corrente de retorno Joaquina" --lang pt -k 5
org-index query "KnowledgeStore" --mode fts            # no Ollama needed
org-index query "phase 2 gate" --kind issue --repo marola-dev/marola --json | jq '.[0].url'
org-index show marola-dev/marola#398                   # whole document, locations, edges
```

Raw SQL, with any SQLite that can load the extension (statements in `contracts/queries.sql`):

```bash
sqlite3 ~/.cache/marola/org-index.sqlite \
  -cmd ".load $(python3 -c 'import sqlite_vec; print(sqlite_vec.loadable_path())')" \
  "SELECT l.repo, l.path, count(*) OVER (PARTITION BY d.id) AS copies
     FROM document d JOIN location l ON l.document_id = d.id
    WHERE d.id IN (SELECT document_id FROM location GROUP BY document_id HAVING count(*) > 1)
    ORDER BY copies DESC LIMIT 10;"
```

From Claude Code: the plugin's MCP server exposes `search_org` and `get_document`; ask "search the
org index for …".

## 5. Test it

| Layer | Command | Needs |
|---|---|---|
| Unit + fixtures | `python3 scripts/org-index.py --self-test` (part of `just quality`) | nothing: fixture repos with a gitlink, a synced file and a fork; a hash-based fake embedder |
| Contracts | `python3 specs/001-…/contracts/check_contracts.py` | `sqlite-vec` |
| Retrieval quality | `org-index eval evals/org-index-golden.jsonl -k 5 --baseline evals/org-index-baseline.json` | a built index, Ollama |
| Live smoke | `just org-index-smoke`: build `--repo marola-dev/marola-corpus`, query, expect a `knowledge/` hit | Ollama, `gh` |

## 6. Host it

Phase 0/1: the umbrella's workflow publishes the rolling `org-index` release asset; nothing to set up.
Phase 2: [`contracts/gcs-hosting.md`](contracts/gcs-hosting.md), behind marola-dev/marola#447 and
a human `pulumi up`.
