# marola-devkit Constitution

The org invariants in [`agents/invariants.md`](../../agents/invariants.md) and the umbrella's
[AGENTS.md](https://github.com/marola-dev/marola/blob/main/AGENTS.md) come first; this file restates
them as the gates `/speckit-plan` checks, plus what is specific to this repo. Where they disagree,
AGENTS.md wins and this file is fixed in the same change.

## Core Principles

### I. Local and free first (NON-NEGOTIABLE)

Every feature runs end to end on a laptop with no paid account: Ollama for models, files for
storage. A cloud path is an opt-in second backend, never the default and never required by a test.

### II. Cost and deployment gate (NON-NEGOTIABLE)

Nothing provisions or deploys a paid resource without explicit human confirmation. A plan states
the expected cost; `pulumi preview` may run anywhere, `pulumi up|destroy|import` only by a human on
go-ahead. No secret in code; `.env.example` holds placeholders. CI authenticates to clouds by OIDC,
never by a JSON key.

### III. Phase discipline

Work serves the current phase of the umbrella's `docs/PHASES.md`. A spec that needs a later phase
(GCP is Phase 2) splits that part out, marks it blocked by the phase gate, and ships the rest.

### IV. The agent-ready gate

`/speckit-implement` runs only on a task whose GitHub issue carries `agent-ready`. Filing an issue
is a human's act (MIP-0063 §5.6); `/speckit-taskstoissues` drafts, a person files.

### V. Test first, offline

A script lands with its `--self-test` extended first, red then green, and the self-test needs no
network, no model and no credentials (fixtures and fakes). Live checks are separate recipes.

### VI. No repo reads another's tree

Consumers pin producers' releases (MIP-0070 §5.4). A tool that spans the org reads each repo from
GitHub at its default branch, never through the umbrella's submodule checkout.

### VII. Few words

Comments say why, a trap, or a pointer; nothing else. The same holds for spec prose.

## Repo constraints

- Tools are `scripts/*` on `PATH` through `flake.nix`'s `tools` map, each finding its `lib/` next to
  itself. Python is ruff-clean; shell is `set -euo pipefail` and shellcheck-clean.
- A change that alters behaviour for consumers is a version bump: `plugin.json`, the flake package
  version and a tag move together.
- Gates: `just quality` (ruff, shellcheck, actionlint, every `--self-test`, agents-check, plugin
  validate).

## Workflow

`/speckit-specify` → `/speckit-clarify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-analyze` →
issues filed by a human → `/speckit-implement` per `agent-ready` issue, one PR each. Commits carry
`Tested:`, `Cost:` and `Co-Authored-By: Claude <noreply@anthropic.com>` and nothing else. A feature
that changes a cross-repo contract also needs a MIP in the umbrella.

## Governance

This constitution amends only with the umbrella's AGENTS.md or `agents/invariants.md`; a spec
cannot loosen it. Every plan's Constitution Check lists each principle as pass, fail-with-reason, or
n/a.

**Version**: 1.0.0 | **Ratified**: 2026-10-02 | **Last Amended**: 2026-10-02
