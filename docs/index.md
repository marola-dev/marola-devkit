# marola-devkit

The shared harness behind every marola repo's dev flow: what each tool does, and how a repo takes
it in. The repo's [README](https://github.com/marola-dev/marola-devkit/blob/main/README.md) has the
consumption snippets; this page is what the umbrella's docs site mounts.

## Tools on PATH

| Tool | What it does |
|---|---|
| `stack` | Stacked-PR mechanics for `mip-NNNN/k-slug` branches: `start`, `pr`, `restack`, `status`, `link` |
| `uprd` / `uprds` | Write a PR body from the branch's commits; the same for a whole MIP stack |
| `pr-flow` | The whole PR workflow: fill trailers, push, write the body |
| `cost-split` / `cost-fill` | Attribute Claude Code usage to commits; add a missing `Cost:`/`Tested:` trailer |
| `issues` | Labels sync, Definition of Ready, the `agent-ready` queue, claims, `tasks-to-issues`, the board |
| `agents-check` | Compare AGENTS.md's invariants block with the pinned devkit's `agents/invariants.md` |
| `ruleset-sync` | Check or apply `.github/rulesets/main-rule.json`'s branch ruleset to a repo, or every repo of an org |
| `api-docs-push` | Force-push a directory as one orphan commit to a branch; the `api-docs` reusable workflow's own push step |
| `mip-resolve` | Find a MIP or `.tasks.md` in the repo, the umbrella checkout, or via `gh api` |
| `mip-stack`, `docs-mip-stack`, `deps-stack`, `deps-merge`, `branches`, `pr-label` | Stack and merge helpers |
| `gha-runner`, `setup-runners`, `runner-preflight`, `temps` | The self-hosted Actions runner |

Every tool has a `--self-test` that runs offline.

## Claude Code plugin

`marola-devkit@marola-devkit`: the `mip`, `mip-tasks`, `mip-solve-perpetual`, `triage`,
`humanizer`, `ponytail*`, `sharingan`, `skill-copy`, `obsidian-vault`, `voice-note-ingest` and
`voice-to-feature` skills; the `mip-reviewer` and `mip-claims-auditor` agents; and three hooks
(format on edit, a once-per-session nudge to run `just quality`, a session-start summary).

## Issue forms and labels

`.github/labels.yml`, `.github/ISSUE_TEMPLATE/` and the PR template are the same in every repo, so
`agent-ready` means the same thing everywhere (MIP-0070 §5.7).
