# marola-devkit

The shared dev-flow harness for the marola repos (MIP-0070 §5.1): the stacked-PR, PR-body, issue
and cost tools, the git hooks, the org invariants block, the issue forms and labels, and a Claude
Code plugin with the generic skills, agents and hooks. Every marola repo consumes it at a pinned
version, and its own AGENTS.md says which pieces it opts out of.

## Use it from a repo

**Tools, dev shell and just module** — add the flake input and append its tools:

```nix
inputs.marola-devkit = {
  url = "github:marola-dev/marola-devkit/v0.2.0";
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
on `PATH`. Docs tooling (`mkdocs`) is not in v0.2.0 either; it comes with MIP-0070 task 10. The shellHook links the pinned
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
| `prepush *args` | `.githooks/pre-push` | the gates CI would fail the push on |

A hook whose recipe the repo doesn't define prints one line and lets the commit or push through.
With no `just` to run it (outside `nix develop`, no nix fallback) the hook fails; `--no-verify`
bypasses.

`prepush` receives git's own pre-push arguments as positional args (`<remote name> <remote URL>`)
and, as `MAROLA_PUSH_REFS_FILE`, the path to a file of git's `<local ref> <local sha1> <remote ref>
<remote sha1>` lines — one per pushed ref — so a recipe that wants the real push range instead of
assuming HEAD's can read it. Declare the recipe `prepush *args:` even when it ignores them, so
`just` doesn't refuse the extra arguments.
Each tool is also a flake app: `nix run github:marola-dev/marola-devkit/v0.2.0#uprd`.

**Claude Code plugin** — in the repo's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "marola-devkit": {
      "source": { "source": "github", "repo": "marola-dev/marola-devkit", "ref": "v0.2.0" }
    }
  },
  "enabledPlugins": { "marola-devkit@marola-devkit": true }
}
```

Skills then load as `/marola-devkit:mip`, `/marola-devkit:mip-tasks`, …, and the agents as
`marola-devkit:mip-reviewer`. The plugin's hooks replace the repo's own `.claude/hooks/` entries.

**Invariants** — keep the block from `agents/invariants.md` between `<!-- invariants:start -->` and
`<!-- invariants:end -->` in the repo's AGENTS.md; `agents-check` compares it against the pinned
devkit's copy (`MAROLA_INVARIANTS_BLOCK` is set by the wrapper).

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
    uses: marola-dev/marola-devkit/.github/workflows/agents-check.yml@v0.2.0
    with:
      devkit-ref: v0.2.0
```

This repo runs all eight against its own pull requests (`.github/workflows/pr.yml`), so a change
to one of them is exercised by a real caller before a tag is cut.

## Work on it

See [AGENTS.md](AGENTS.md). `nix develop`, then `just quality`. Docs for the aggregated site are in
[docs/](docs/index.md).
