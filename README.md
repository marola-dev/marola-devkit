# marola-devkit

The shared dev-flow harness for the marola repos (MIP-0070 §5.1): the stacked-PR, PR-body, issue
and cost tools, the git hooks, the org invariants block, the issue forms and labels, the reusable
workflows, and a Claude Code plugin with the generic skills, agents and hooks. Every marola repo
consumes it at a pinned version, and its own AGENTS.md says which pieces it opts out of.

**Status:** this is v0.7.0; each repo pins its own version. Changes per release are in
[CHANGELOG.md](https://github.com/marola-dev/marola-devkit/blob/main/CHANGELOG.md).

## Try it

```bash
nix run github:marola-dev/marola-devkit/v0.7.0#docs-lint -- --self-test   # any tool, no checkout
nix develop          # in a checkout: every tool on PATH, plus the lint toolchain
just quality         # this repo's gates
claude --plugin-dir plugins/marola-devkit   # the plugin, from this checkout
```

## What's in it

| Path | Holds |
|---|---|
| `scripts/` | The tools the flake puts on `PATH`, each with its `lib/` and `fixtures/` beside it |
| `plugins/marola-devkit/` | The Claude Code plugin: skills, agents, hooks; listed by `.claude-plugin/marketplace.json` |
| `agents/invariants.md` | The org invariants block every repo's AGENTS.md carries |
| `.github/` | `labels.yml`, `ISSUE_TEMPLATE/`, the PR template, `rulesets/main-rule.json`, and the reusable workflows |
| `.githooks/` | The opt-in pre-commit and pre-push hooks |
| `devkit.just` | The just module consumers import |
| `flake.nix` | Packages, apps, the base dev shell, and `lib.<system>` for consumers |
| `tests/self-tests.sh` | Every tool's `--self-test` in one run |
| `docs/` | This repo's design, development and reference pages |

### Tools on PATH

| Tool | What it does |
|---|---|
| `stack` | Stacked-PR mechanics for `mip-NNNN/k-slug` branches: `start`, `pr`, `restack`, `status`, `link` |
| `uprd` / `uprds` | Write a PR body from the branch's commits; the same for a whole MIP stack |
| `pr-flow` | The whole PR workflow: fill trailers, push, write the body (not `pr`, which is coreutils') |
| `cost-split` / `cost-fill` | Attribute Claude Code usage to commits; add a missing `Cost:`/`Tested:` trailer |
| `issues` | Labels sync, Definition of Ready, the `agent-ready` queue, claims, `tasks-to-issues`, the board |
| `agents-check` | Compare AGENTS.md's invariants block with the pinned devkit's `agents/invariants.md` |
| `docs-lint` | MIP-0074's stale-content check over `README.md` and `docs/**/*.md` |
| `skills-vendor` | Pin vendored skills to an upstream commit in `skills.lock` (`check`, `outdated`, `update`, `init`) |
| `ruleset-sync` | Check or apply `.github/rulesets/main-rule.json`'s branch ruleset to a repo, or every repo of an org |
| `api-docs-push` | Force-push a directory as one orphan commit to a branch; the `api-docs` workflow's push step |
| `mip-resolve` | Find a MIP or `.tasks.md` in the repo, the umbrella checkout, or via `gh api` |
| `mip-stack`, `docs-mip-stack`, `deps-stack`, `deps-merge`, `branches` | Stack and merge helpers |
| `pr-label`, `backfill-pr-labels` | Apply the deterministic `area/*` label taxonomy to one PR; to every finalized unlabelled PR |
| `pr-label-nlp` | A local TF-IDF classifier for the same labels, to compare against the deterministic one |
| `workflow-runners` | Fail when a workflow other than the GPU publish one can reach the self-hosted runner |
| `gha-runner`, `setup-runners`, `runner-preflight`, `temps` | The self-hosted Actions runner |

Every tool has a `--self-test` that runs offline. Usage, flags and recipes:
[docs/4-reference_tools.md](docs/4-reference_tools.md).

### Claude Code plugin

`marola-devkit@marola-devkit`: the `mip`, `mip-tasks`, `mip-solve-perpetual`, `triage`,
`humanizer`, `ponytail*`, `sharingan`, `skill-copy`, `routing`, `obsidian-vault`,
`voice-note-ingest` and `voice-to-feature` skills; the `mip-reviewer` and `mip-claims-auditor`
agents; and three hooks (format on edit, a once-per-session nudge to run the repo's gate, a
session-start summary).
The vendored skills are pinned in `plugins/marola-devkit/skills/skills.lock`
([skills-vendor](docs/4-reference_tools.md#skills-vendor)).

## Contracts

- **Consumes:** nixpkgs and h0ffmann/nix-config's `lint` and `agentic` labs (flake inputs). Tools
  that read MIPs fall back to the umbrella's `docs/MIPs` through `gh api`; nothing reads another
  repo's tree.
- **Publishes**, at each `v*` tag: the flake (packages, apps, `lib.<system>`), the plugin
  marketplace, the reusable workflows, the invariants block, the labels, issue forms and ruleset.
- **Pinned by:** every marola repo, in three places bumped together: the flake input, every
  workflow `@v…` and `devkit-ref`, and the marketplace `ref` in `.claude/settings.json`.

## Use it from a repo

**Tools, dev shell and just module** — add the flake input and append its tools:

```nix
inputs.marola-devkit = {
  url = "github:marola-dev/marola-devkit/v0.7.0";
  inputs.nixpkgs.follows = "nixpkgs";
};

# in devShells.default
packages = [ /* repo tools */ ] ++ marola-devkit.lib.${system}.tools;
shellHook = marola-devkit.lib.${system}.shellHook + ''
  # the repo's own hook lines
'';
```

`lib.<system>.tools` puts every tool above on `PATH`. The shellHook links the pinned tree at
`.devkit` (gitignore it), so the justfile can take the shared recipes:

```just
import? '.devkit/devkit.just'
```

A repo that defines a recipe with the same name needs `set allow-duplicate-recipes`.

Each tool is also a flake app: `nix run github:marola-dev/marola-devkit/v0.7.0#uprd`.

**Git hooks** are opt-in: `git config core.hooksPath .devkit/.githooks`. The contract a consuming
repo implements is two recipes in its own justfile:

| Recipe | Run by | Should be |
|---|---|---|
| `precommit` | `.githooks/pre-commit` | fast checks on what is staged (seconds) |
| `prepush` | `.githooks/pre-push` | the gates CI would fail the push on |

A hook whose recipe the repo doesn't define prints one line and lets the commit or push through.
With no `just` to run it (outside `nix develop`, no nix fallback) the hook fails; `--no-verify`
bypasses.

`prepush` takes no positional arguments — a parameterless recipe (today's norm) must keep working,
and `just prepush <remote>` would otherwise try to run a recipe named `<remote>`. git's own pre-push
data reaches the recipe through three environment variables instead: `MAROLA_PUSH_REMOTE` and
`MAROLA_PUSH_URL` (the remote's name and URL), and `MAROLA_PUSH_REFS_FILE`, the path to a file of
git's `<local ref> <local sha1> <remote ref> <remote sha1>` lines, one per pushed ref, each one a
deletion annotated `(delete)` — so a recipe that wants the real push range instead of assuming
HEAD's, or wants to skip deletions, can read it. A recipe that reads none of the three keeps
working exactly as before.

**Claude Code plugin** — in the repo's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "marola-devkit": {
      "source": { "source": "github", "repo": "marola-dev/marola-devkit", "ref": "v0.7.0" }
    }
  },
  "enabledPlugins": { "marola-devkit@marola-devkit": true }
}
```

A `github`-sourced `extraKnownMarketplaces` entry does take a `ref` (Claude Code's plugin
marketplace reference: branch or tag, same as a plugin source's own `ref`) — the `ref` above pins
the plugin to this tag the same way the flake input is pinned; a repo adopts a new release by
bumping both together. Omitting `ref` tracks this repo's default branch instead. Background
auto-update is off by default for a third-party marketplace like this one, so bumping the pinned
`ref` — not waiting on a background refresh — is how a consumer picks up a release.

Skills then load as `/marola-devkit:mip`, `/marola-devkit:mip-tasks`, …, and the agents as
`marola-devkit:mip-reviewer`. The plugin's hooks replace the repo's own `.claude/hooks/` entries.

The Stop hook nags once per session, after an uncommitted change, to run the repo's own gate — by
default `just quality`, or the repo's `stop-gate` recipe when its own justfile defines one (any
`just`-visible spelling: `Justfile`/`.justfile`, an `@`-prefixed recipe, one pulled in via
`import`). `MAROLA_STOP_GATE` overrides both, naming the exact command(s) to nag for (it can be
more than one, e.g. `"sbt test && just quality"`, for a repo whose `quality` does not itself run
tests). Since the hook runs inside Claude Code's own process, not a login shell,
`MAROLA_STOP_GATE` must reach it explicitly — a plain repo-level export is not enough; set it in
`.claude/settings.json`'s `env` (or `.claude/settings.local.json`'s, to keep it out of git).

**Status line** — the pinned tree carries one at `.devkit/.claude/statusline.sh`
(model/effort, dir, git branch and ahead/behind, context-window bar, cost, cache hit ratio, rate
limits — nothing marola-specific). A repo wires it in its own `.claude/settings.json`:

```json
{
  "statusLine": { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.devkit/.claude/statusline.sh\"" }
}
```

A repo that wants its own instead keeps using its own `.claude/statusline.sh` path.

**Invariants** — keep the block from `agents/invariants.md` between `<!-- invariants:start -->` and
`<!-- invariants:end -->` in the repo's AGENTS.md; `agents-check` compares it against the pinned
devkit's copy (`MAROLA_INVARIANTS_BLOCK` is set by the wrapper).

**Issue forms and labels** — `.github/labels.yml`, `.github/ISSUE_TEMPLATE/` and the PR template
are the same in every repo, so `agent-ready` means the same thing everywhere (MIP-0070 §5.7).

**Ruleset sync** — `.github/rulesets/main-rule.json` is the one branch ruleset every marola repo
carries (the org is on GitHub Free, so an org-level ruleset isn't available). `ruleset-sync check
[owner/repo ...]` compares a repo's live ruleset against it (default: the caller's repo) and exits
non-zero on a miss or a diff; `ruleset-sync apply <owner/repo ...>` creates or updates it, `--dry-run`
previews the `gh api` call instead of making it. `--all-org ORG` runs either over every non-archived,
non-fork repo of an org (`--include-forks` adds forks). `just rulesets-check`/`just rulesets-apply` wrap both.

**Docs lint** — `docs-lint [repo-root]` runs MIP-0074 §7's rules over `README.md` and
`docs/**/*.md`, skipping dated records (`docs/MIPs/**`, `docs/benchmarks/**`, `SPLIT.md`); a repo
adds it to its quality recipe once its README is the landing.

**The umbrella** — tools that read MIPs or `.tasks.md` look in the repo, then `../` (an umbrella
checkout), then `gh api repos/$MAROLA_UMBRELLA/contents/docs/MIPs`. `MAROLA_UMBRELLA` defaults to
`marola-dev/marola`.

**Reusable workflows** — ten `workflow_call` workflows (`scala-ci`, `python-ci`, `static-ci`,
`notify-umbrella`, `labels-sync`, `agents-check`, `pr-body`, `ci-short-circuit`, `api-docs`,
`gemini-review`);
every input, secret and example caller is in [docs/4-reference_workflows.md](docs/4-reference_workflows.md).
One caller, needing a second checkout of this repo for its own scripts:

```yaml
jobs:
  agents-check:
    uses: marola-dev/marola-devkit/.github/workflows/agents-check.yml@v0.7.0
    with:
      devkit-ref: v0.7.0
```

This repo calls five of them on its own pull requests (`.github/workflows/pr.yml`: `python-ci`,
`static-ci`, `agents-check`, `pr-body`, `ci-short-circuit`) and `labels-sync` by hand
(`labels.yml`), so a change to those is exercised by a real caller before a tag is cut.
`scala-ci`, `notify-umbrella` and `api-docs` have no caller here: no Scala, no API docs
generator, and nothing to announce, since the umbrella's docs take the devkit at its pinned
version, not its `main` (MIP-0074 §5.3). Their first real run is in a consuming repo.

**Gemini review on request** — on a PR, request the reviewer `marola-dev/gemini` (sidebar →
Reviewers, or `gh pr edit <N> --add-reviewer marola-dev/gemini`). A few minutes later
`marola-gemini-bot` posts one review: a short summary and at most 10 inline comments tagged
`[high]`/`[medium]`/`[low]`, with a suggestion block where the fix is exact. It then pushes one
`fix: apply Gemini review findings` commit with the fixes whose original text still matches,
after the repo's `check-command` passes, and comments what it fixed and what it left. Request the
team again after new commits for a fresh review. Nothing runs unless someone asks. A fork PR
gets the review but no fix commit, and nothing from the fork is run. The bot never edits
`.github/`. A repo opts in with one file:

```yaml
# .github/workflows/gemini.yml
name: gemini
on:
  pull_request_target:   # secrets for fork PRs too; only a maintainer can request the team
    types: [review_requested]
jobs:
  gemini:
    if: github.event.requested_team.slug == 'gemini'
    uses: marola-dev/marola-devkit/.github/workflows/gemini-review.yml@v0.7.0
    secrets: inherit
    with:
      devkit-ref: v0.7.0
      check-command: ""   # a fast gate that needs no Nix, e.g. node scripts/site_check.js
```

It needs the org's `marola-gemini-bot` App installed on the repo, the `gemini` team with Read on
it, and the org's `GEMINI_API_KEY`, `GEMINI_APP_PRIVATE_KEY` and `GEMINI_APP_ID`. One Gemini API
call per review keeps it on the free tier, about 20 reviews a day per model; `vars.GEMINI_MODEL`
picks another model. This repo calls it from `gemini.yml`, with `bash tests/self-tests.sh` as
the check. Details: [docs/4-reference_workflows.md](docs/4-reference_workflows.md#gemini-review).

## Docs and rules

- [docs/1-design.md](docs/1-design.md): the layout, how a tool runs, how tools find the umbrella.
- [docs/3-development.md](docs/3-development.md): gates, self-tests, CI and the release rule.
- [docs/4-reference_tools.md](docs/4-reference_tools.md): every tool on `PATH` and its recipe.
- [docs/4-reference_config.md](docs/4-reference_config.md): every `MAROLA_*` variable.
- [docs/4-reference_hooks.md](docs/4-reference_hooks.md): the git hooks and the plugin's hooks.
- [docs/4-reference_plugin.md](docs/4-reference_plugin.md): the plugin's skills and agents.
- [docs/4-reference_runners.md](docs/4-reference_runners.md): the self-hosted runner and unattended MIP runs.
- [docs/4-reference_workflows.md](docs/4-reference_workflows.md): the reusable workflows.
- [AGENTS.md](https://github.com/marola-dev/marola-devkit/blob/main/AGENTS.md): how to work on
  this repo, and the release rule.
- [CHANGELOG.md](https://github.com/marola-dev/marola-devkit/blob/main/CHANGELOG.md): what each
  release changed.
- The org's ways of working, the MIPs and the other repos: <https://docs.marola.dev/>.

## Work on it

`nix develop`, then `just quality`. A change to a script lands with its `--self-test` extended
first. A release that changes behaviour for consumers moves `plugin.json`'s `version`, the flake
package version, every documented pin in `README.md` and `docs/` and a `CHANGELOG.md` entry
together, then a human tags it: [docs/3-development.md](docs/3-development.md#releases).
