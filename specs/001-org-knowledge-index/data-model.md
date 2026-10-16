# Data Model: Org knowledge index

The DDL is [`contracts/schema.sql`](contracts/schema.sql); `contracts/check_contracts.py` runs it.
This file says what each table means and which invariant it carries.

```text
source 1─* location *─1 document 1─* chunk_occurrence *─1 chunk 1─1 chunk_vec
                              │                                  └─1 chunk_fts
                              └─* edge (ref → 'MIP-0055', 'marola-dev/marola#398', …)
```

| Table | One row per | Key | Invariant |
|---|---|---|---|
| `meta` | build fact | `key` | `embed_model`, `embed_dims`, `chunker_version`, `fingerprint` are present; `query` refuses on a model or dimension mismatch |
| `source` | indexed repo | `repo` | only non-fork, non-archived repos; `commit_sha` is the default branch HEAD read |
| `document` | unique normalized content | `content_sha` | the dedup key: equal content in N repos is 1 row |
| `location` | place a document lives | `(repo, path)` | ≥ 1 per document, exactly 1 with `is_canonical = 1` (partial unique index) |
| `chunk` | unique normalized passage | `content_sha` | ≤ 512 tokens; `header` from the canonical location; `embed_key` = sha256(model ‖ header ‖ text) |
| `chunk_occurrence` | chunk position in a document | `(document_id, ord)` | a chunk shared by documents has several rows, one vector |
| `chunk_vec` | vector | `chunk_id` | 1024 float32, L2-normalized; `kind`, `repo` (canonical), `lang` copied for in-scan filtering |
| `chunk_fts` | FTS5 entry | `rowid = chunk.id` | external content, rebuilt after insert; `remove_diacritics 2` |
| `edge` | outbound reference | `(document_id, ref)` | extracted by regex: `MIP-\d{4}`, `owner/repo#N`, `#N` resolved against the document's repo |

## Normalization and hashing

`normalize(text)` = CRLF → LF, strip trailing whitespace per line, strip leading and trailing blank
lines, NFC. `content_sha = sha256(normalize(text))`. Chunk hashes use `normalize` plus whitespace
runs collapsed to one space, so re-wrapped Markdown still matches.

## Canonical location

Lowest rank wins, then the shortest path, then lexical order:

| Rank | Repo | Why |
|---|---|---|
| 0 | `marola-devkit` | source of every synced file |
| 1 | `marola-corpus` | producer of ocean knowledge |
| 2 | `marola-app` | producer of the image and resources |
| 3 | `marola-ml`, `marola-oods`, `marola-site` | consumers |
| 4 | `marola` | the umbrella aggregates; it owns only what nothing else has |
| 5 | any other | |

The ranks live in `org-index.toml` (umbrella), not in code.

## Kinds and languages

`kind`: `doc` (Markdown, rst, txt), `code` (by extension), `config` (yaml, toml, json, nix,
justfile, Dockerfile), `issue`, `pr`, `comment` (an issue comment or a review thread). `lang`:
`pt` or `en` by a stop-word ratio on prose, `NULL` for code and config. The `.pt-BR.md` suffix and
the `pt-BR/` directory force `pt`.

## Fingerprint

`sha256(schema_version ‖ embed_model ‖ embed_digest ‖ chunker_version ‖ sorted(repo@commit) ‖
issues_through)`, first 12 hex chars in file names. Equal fingerprint ⇒ identical index.
