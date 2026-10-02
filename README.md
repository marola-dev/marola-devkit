# marola-devkit

The shared dev-flow harness for the marola repos (MIP-0070 §5.1): the stacked-PR, PR-body, issue
and cost tools, the git hooks, the org invariants block, the issue forms and labels, and a Claude
Code plugin with the generic skills, agents and hooks. Every marola repo consumes it at a pinned
version, and its own AGENTS.md says which pieces it opts out of.

## Use it from a repo

**Tools, dev shell and just module** — add the flake input and append its tools:

```nix
inputs.marola-devkit = {
  url = "github:marola-dev/marola-devkit/v0.2.3";
  inputs.nixpkgs.follows = "nixpkgs";
};

# in devShells.default
packages = [ /* repo tools */ ] ++ marola-devkit.lib.${system}.tools;
shellHook = marola-devkit.lib.${system}.shellHook + ''
  # the repo's own hook lines
'';
```

`lib.<system>.tools` puts `stack`, `uprd`, `uprds`, `pr-flow` (the PR workflow; not `pr`, which
is coreutils'), `issues`, `cost-split`, `cost-fill`, `agents-check` (and the rest in `flake.nix`)
on `PATH`. Docs tooling (`mkdocs`) is not in v0.2.3 either; it comes with MIP-0070 task 10. The shellHook links the pinned
tree at `.devkit` (gitignore it), so the justfile can take the shared recipes:

```just
import? '.devkit/devkit.just'
```

A repo that defines a recipe with the same name needs `set allow-duplicate-recipes`.

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

Each tool is also a flake app: `nix run github:marola-dev/marola-devkit/v0.2.3#uprd`.

**Claude Code plugin** — in the repo's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "marola-devkit": {
      "source": { "source": "github", "repo": "marola-dev/marola-devkit", "ref": "v0.2.3" }
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
default `just quality`, or `just stop-gate` when the repo's own justfile defines that recipe (any
`just`-visible spelling: `Justfile`/`.justfile`, an `@`-prefixed recipe, one pulled in via
`import`). `MAROLA_STOP_GATE` overrides both, naming the exact command(s) to nag for (it can be
more than one, e.g. `"just build && just test && just quality"`, for a repo whose `quality` does
not itself run tests). Since the hook runs inside Claude Code's own process, not a login shell,
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

**Ruleset sync** — `.github/rulesets/main-rule.json` is the one branch ruleset every marola repo
carries (the org is on GitHub Free, so an org-level ruleset isn't available). `ruleset-sync check
[owner/repo ...]` compares a repo's live ruleset against it (default: the caller's repo) and exits
non-zero on a miss or a diff; `ruleset-sync apply <owner/repo ...>` creates or updates it, `--dry-run`
previews the `gh api` call instead of making it. `--all-org ORG` runs either over every non-archived,
non-fork repo of an org (`--include-forks` adds forks). `just rulesets-check`/`just rulesets-apply` wrap both.

**The umbrella** — tools that read MIPs or `.tasks.md` look in the repo, then `../` (an umbrella
checkout), then `gh api repos/$MAROLA_UMBRELLA/contents/docs/MIPs`. `MAROLA_UMBRELLA` defaults to
`marola-dev/marola`.

**Reusable workflows** — eight `workflow_call` workflows (`scala-ci`, `python-ci`, `static-ci`,
`notify-umbrella`, `labels-sync`, `agents-check`, `pr-body`, `ci-short-circuit`); every input,
secret and example caller is in [docs/workflows.md](docs/workflows.md). One caller, needing a
second checkout of this repo for its own scripts:

```yaml
jobs:
  agents-check:
    uses: marola-dev/marola-devkit/.github/workflows/agents-check.yml@v0.2.3
    with:
      devkit-ref: v0.2.3
```

This repo runs all eight against its own pull requests (`.github/workflows/pr.yml`), so a change
to one of them is exercised by a real caller before a tag is cut.

## Work on it

See [AGENTS.md](AGENTS.md). `nix develop`, then `just quality`. Docs for the aggregated site are in
[docs/](docs/index.md). A release that changes behaviour for consumers moves three things
together: `plugins/marola-devkit/.claude-plugin/plugin.json`'s `version`, `flake.nix`'s package
`version`, and a new git tag — Claude Code uses the plugin version to decide when to refresh an
installed copy.
