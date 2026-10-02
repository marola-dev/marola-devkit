# Contract: `org-index` CLI

On `PATH` through `flake.nix`'s `tools` map (`org-index = "scripts/org-index.py"`), and as
`just org-index *args` from `devkit.just`. Default database:
`${ORG_INDEX_DB:-${XDG_CACHE_HOME:-~/.cache}/marola/org-index.sqlite}`.

```text
org-index build   [--org marola-dev] [--out PATH] [--reuse PREV.sqlite] [--config org-index.toml]
                  [--repo OWNER/NAME ...] [--no-issues] [--include-forks] [--dry-run]
                  [--embed-model bge-m3] [--ollama-host URL] [--batch 32]
org-index query   TEXT [-k 8] [--mode hybrid|vec|fts] [--kind K] [--repo R] [--lang en|pt] [--json]
org-index show    CHUNK_ID | URL                # the document, every location, its edges
org-index stats                                 # fingerprint, counts per repo/kind/lang, dedup saved
org-index eval    GOLDEN.jsonl [-k 5] [--baseline FILE] [--update-baseline]
org-index mcp                                   # stdio MCP server, see mcp.md
org-index fetch   [--from github|gcs] [--to PATH]
org-index publish [--to github|gcs] PATH
org-index --self-test
```

| Exit | Meaning |
|---|---|
| 0 | ok |
| 1 | runtime error (network, GitHub, SQLite) |
| 2 | usage |
| 3 | embedder problem: Ollama unreachable, model not pulled, or model/dims differ from `meta` |
| 4 | `eval` below baseline |

## Behaviour

- `build` checks Ollama and the model before reading any repo (exit 3 names
  `ollama pull bge-m3`). `--dry-run` reads and chunks but embeds nothing: it prints files, unique
  documents, chunks, unique chunks, tokens and how many vectors `--reuse` would cover. It is the
  free way to see what a build would cost.
- `build` reads a repo with `gh api repos/{r}/tarball/{sha}` (or a `--depth 1` clone), never a
  local checkout, so a gitlink is just an absent directory.
- `build` writes to `PATH.tmp` and renames on success; a failed build leaves the old file.
- `query --mode vec|hybrid` needs Ollama for the query vector; `--mode fts` does not.
- `query` output, one line per hit, then the text indented:

  ```text
  0.0315  marola-dev/marola:docs/MIPs/MIP-0055-vector-store-and-ocean-knowledge-chunking.md › 5.4
          https://github.com/marola-dev/marola/blob/<sha>/docs/MIPs/MIP-0055-….md#54-…
  ```

- `--json` emits `[{chunk_id, score, header, text, url, kind, lang, locations: [{repo, path, url,
  canonical}]}]`.
- `eval` reads JSONL lines `{"q": "...", "expect": ["marola-dev/marola:docs/MIPs/MIP-0055-….md",
  "marola-dev/marola#398"], "lang": "en"}`; a hit is any returned location matching an `expect`
  entry. Prints recall@k and MRR per mode.
- `publish --to github` replaces the asset on the umbrella's rolling `org-index` release;
  `--to gcs` uploads to `gs://$ORG_INDEX_BUCKET/org-index.sqlite.zst` with `fingerprint` and
  `sha256` as object metadata (contracts/gcs-hosting.md).

## Environment

| Variable | Default | Use |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | embedder |
| `ORG_INDEX_DB` | see above | every subcommand |
| `ORG_INDEX_BUCKET` | — | `fetch/publish --to gcs`; unset means GCS is off |
| `GH_TOKEN` | `gh auth token` | GitHub reads; no extra scope for public repos |
