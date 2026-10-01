# AGENTS.md

Instructions for any AI coding agent working in **marola-devkit**. This is the repo layer
(MIP-0070 §5.1): the workspace rules live in the umbrella's
[AGENTS.md](https://github.com/marola-dev/marola/blob/main/AGENTS.md); this file says what this
repo is and where it differs.

<!-- invariants:start -->
## Org invariants

Non-negotiable in every marola repo; a repo may make these stricter, never looser (MIP-0070 §5.1).

- **Cost and deployment safety**: never provision or deploy a paid cloud resource without explicit human confirmation first ([AGENTS.md](AGENTS.md#cost--deployment-safety-hard-rule)).
- **No secrets in code**: never hardcode a key/connection string/secret; `.env.example` holds placeholders only ([AGENTS.md](AGENTS.md#cost--deployment-safety-hard-rule)).
- **The agent-ready gate**: an agent may only begin implementation on an issue carrying `agent-ready` ([AGENTS.md](AGENTS.md#issue-tracking-hard-rule)).
- **The three commit trailers**: commits carry three trailers and nothing else — `Tested:`, `Cost:`, and `Co-Authored-By: Claude <noreply@anthropic.com>` ([AGENTS.md](AGENTS.md#attribution-and-cost-accounting-hard-rule)).
- **Phase discipline**: work one phase at a time; never start a later phase before the current one is done ([AGENTS.md](AGENTS.md#phase-discipline-hard-rule)).
<!-- invariants:end -->

## What this repo is

The shared dev-flow harness every marola repo consumes at a pinned version:

- `scripts/`: the tools the flake puts on `PATH` (`stack`, `uprd`, `uprds`, `pr-flow`, `issues`,
  `cost-split`, `cost-fill`, `agents-check`, and the rest listed in `flake.nix`). Each
  finds its own `lib/` and `fixtures/` next to itself, never through the caller's cwd.
- `plugins/marola-devkit/`: the Claude Code plugin (generic skills, the MIP reviewer agents, the
  format/stop/session hooks), listed by `.claude-plugin/marketplace.json`.
- `agents/invariants.md`: the org invariants block; a consuming repo's `agents-check` compares its
  AGENTS.md against this file in its pinned devkit.
- `.github/`: `labels.yml`, `ISSUE_TEMPLATE/` and the PR template, synced into every repo.
- `devkit.just`: the just module consumers import; `flake.nix`: packages, apps, the base dev shell
  and `lib.<system>` for consumers.

It holds no product code, no MIPs and no phase list: those are the umbrella's.

## Commands

```bash
nix develop          # the devkit's own shell: every tool on PATH, plus the lint toolchain
just quality         # ruff, shellcheck, actionlint, every --self-test, agents-check, plugin validate
bash tests/self-tests.sh   # just the self-tests
claude --plugin-dir plugins/marola-devkit   # try the plugin from this checkout
```

A change to a script lands with its `--self-test` extended first (red, then green). A change that
alters behaviour for consumers is a version bump: `plugin.json`'s `version`, the flake package
version and a new tag move together.

## Cost & deployment safety (hard rule)

As in the umbrella. This repo deploys nothing; `setup-runners`/`gha-runner` touch a self-hosted
runner only when a human runs them.

## Issue tracking (hard rule)

An agent starts work only on an issue carrying `agent-ready`, in this repo (MIP-0070 §5.7).

## Attribution and cost accounting (hard rule)

Commits carry `Tested:`, `Cost:` and `Co-Authored-By: Claude <noreply@anthropic.com>`, nothing
else. `.claude/settings.json` sets the attribution; `pr-flow` (`just pr`) fills missing trailers.

## Phase discipline (hard rule)

The phase list is the umbrella's `docs/PHASES.md`. Devkit work serves the current phase.

## Code style

Shell: `set -euo pipefail`, `shellcheck`-clean (`.shellcheckrc`). Python: `ruff` (`ruff.toml`).
Comments only for why, a trap, or a pointer, as the umbrella's AGENTS.md spells out.
