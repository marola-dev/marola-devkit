---
name: routing
description: Pick the cheapest source for a question about where something lives in marola, before reading any tree. Use for cross-repo questions (what produces, consumes, pins or triggers an image, release asset, pin file, dispatch or workflow; how a change in one repo reaches another or docs.marola.dev) and for "where is X" when the keyword or the repo is unfamiliar.
---

# Routing a "where is it" question

- **What produces, consumes, pins or triggers X?** Read the generated wiring block (between
  `<!-- wiring:start -->` and `<!-- wiring:end -->`) in the umbrella's
  `docs/2-Building-marola/REPOS.md`, or, from a submodule, the same page at
  <https://docs.marola.dev/2-Building-marola/REPOS/>.
- **Where is a symbol, with the repo unfamiliar?** Run `just graph query "<identifiers>"`
  (`just graph build` first if it reports no graph or a stale one), then read only the files it
  names. Ask with the code's own words, a class, file or function name ("Telegram Main",
  "Recommender"), not a description: the graph matches symbol names, not meanings.
- **A concept or behaviour ("where is the safety veto applied?"), or a known keyword.** `git grep`
  the concept word first, with `--recurse-submodules` in the umbrella; query the graph with the
  identifiers it turns up.
- Never read `GRAPH_REPORT.md` or `graph.json` whole.
