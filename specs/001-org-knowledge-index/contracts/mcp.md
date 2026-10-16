# Contract: `org-index mcp`

A stdio MCP server on the `mcp` Python SDK (1.29.0 in the pinned nixpkgs). Read-only: it opens the
database with `mode=ro`.

The plugin registers it in `plugins/marola-devkit/.mcp.json`:

```json
{ "mcpServers": { "org-index": { "command": "org-index", "args": ["mcp"] } } }
```

## Tools

### `search_org`

| Param | Type | Default |
|---|---|---|
| `query` | string | required |
| `k` | int 1–20 | 8 |
| `mode` | `hybrid` \| `vec` \| `fts` | `hybrid` |
| `kind` | `doc` \| `code` \| `config` \| `issue` \| `pr` \| `comment` | any |
| `repo` | `owner/name` | any |
| `lang` | `en` \| `pt` | any |

Returns the `query --json` shape plus `{"fingerprint": "…", "degraded": null}`. If Ollama is
unreachable the server answers in `fts` mode and sets `degraded` to `"fts-only: ollama
unreachable"`. An agent gets an answer and knows its quality, instead of an error.

### `get_document`

`{ "ref": "<chunk_id | GitHub URL | owner/repo:path | owner/repo#N>" }` → the whole document text,
every location, and its `edge` refs, so an agent can follow MIP ↔ issue ↔ PR links one hop at a time.

## Errors

No database: a tool error naming `org-index fetch` or `org-index build`. Model mismatch: a tool
error naming both models (CLI exit 3).
