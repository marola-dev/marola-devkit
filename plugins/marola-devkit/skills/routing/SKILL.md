---
name: routing
description: Pick the cheapest source for a question about where something lives in marola, before reading any tree. Use for cross-repo questions (what produces, consumes, pins or triggers an image, release asset, pin file, dispatch or workflow; how a change in one repo reaches another or docs.marola.dev) and for "where is X" when the keyword or the repo is unfamiliar.
---

# Routing a "where is it" question

- **What produces, consumes, pins or triggers X?** Read the generated wiring block (between
  `<!-- wiring:start -->` and `<!-- wiring:end -->`) in the umbrella's
  `docs/2-Building-marola/REPOS.md`, or, from a submodule, the same page at
  <https://docs.marola.dev/2-Building-marola/REPOS/>.
- **Where is symbol or concept X, with the keyword unknown or the repo unfamiliar?** Run
  `just graph query "<question>"` (`just graph build` first if it reports no graph or a stale one),
  then read only the files it names.
- **The keyword is known.** Use `git grep`, with `--recurse-submodules` in the umbrella.
- Never read `GRAPH_REPORT.md` or `graph.json` whole.
