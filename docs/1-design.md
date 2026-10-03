# Design

The devkit is one tree that every marola repo takes at a pinned tag: tools, a just module, git
hooks, a Claude Code plugin, reusable workflows and the files synced into each repo. It holds no
product code and reads no other repo's tree.

## Layout

| Path | What it is | How a consumer gets it |
|---|---|---|
| `scripts/*.sh`, `scripts/*.py` | One file per tool; `scripts/lib/` holds what two tools share (`mip_ref.sh`, `pr_labels.sh`, `tasks_issues.py`, …), `scripts/fixtures/` the JSON the offline self-tests read | On `PATH`, through the flake |
| `devkit.just` | One recipe per everyday command, each calling a tool by its `PATH` name | `import? '.devkit/devkit.just'` |
| `.githooks/` | `pre-commit` and `pre-push`, which run the consuming repo's own recipes | `core.hooksPath`, opt-in ([hooks](4-reference_hooks.md)) |
| `plugins/marola-devkit/` | The Claude Code plugin: skills, agents, hooks ([plugin](4-reference_plugin.md)) | The marketplace in `.claude-plugin/marketplace.json`, pinned by `ref` |
| `agents/invariants.md` | The org invariants block | `agents-check` compares a repo's AGENTS.md with it |
| `.github/` | `labels.yml`, `ISSUE_TEMPLATE/`, the PR template, `rulesets/main-rule.json`, the reusable workflows ([workflows](4-reference_workflows.md)) | Labels by `issues labels sync`, the ruleset by `ruleset-sync`, the forms and template copied as they are; workflows by `uses:` |
| `.claude/statusline.sh` | A generic status line | `.devkit/.claude/statusline.sh` |
| `flake.nix` | The package, one app per tool, `lib.<system>`, this repo's dev shell and checks | A flake input |
| `tests/self-tests.sh` | Every `--self-test` in one run | This repo only |

## How a tool runs

The flake installs the whole tree, layout intact, under `share/marola-devkit` and wraps each
script into `bin/<name>` with `makeWrapper`. The wrapper prefixes `PATH` with the other tools and
their runtime dependencies (bash, coreutils, git, `gh`, `jq`, curl, a python3 with scikit-learn)
and sets a default `MAROLA_INVARIANTS_BLOCK`. So two rules hold for every tool:

- **Its own files are found next to itself.** A script resolves `lib/`, `fixtures/`,
  `.github/labels.yml` and `agents/invariants.md` from its own directory, never from the caller's
  cwd, so the pinned copy is the one that runs.
- **The caller's files are found from the caller's repo root** (`git rev-parse --show-toplevel`):
  the branch, the commits, `docs/MIPs/`, `.github/PULL_REQUEST_TEMPLATE.md`.

`lib.<system>` is what a consuming flake appends: `tools` (the package plus `just`, `gh`, `jq`,
git, python and the lint lab's tools), `shellHook` (links the pinned tree at `.devkit`),
`justModule` and `invariants`. The tool list itself is in [tools](4-reference_tools.md).

## How tools find the umbrella

A code repo carries no `docs/MIPs/` since the split, but `uprd`, `issues tasks-to-issues` and
`mip-resolve` need a MIP's doc or `.tasks.md`. `scripts/lib/mip_ref.sh`'s `resolve_mip_path`
tries three places and takes the first hit:

1. **This repo.** At a git ref when one is given (`uprd` reads a branch that may not be checked
   out), else the working tree.
2. **`../docs/MIPs/`**: the checkout sits inside an umbrella clone as a submodule. A plain glob,
   so an uncommitted MIP resolves too.
3. **`gh api repos/$MAROLA_UMBRELLA/contents/docs/MIPs`**: no umbrella on disk.

`MAROLA_UMBRELLA` (default `marola-dev/marola`) is the one place the umbrella's name lives.
`issues` also takes the org from its owner: `queue` searches every repo of that org, and
`tasks-to-issues` files a MIP's parent issue in the umbrella and each row in the repo its
`delivers` cell names. The [config](4-reference_config.md) page lists every variable.

## Versioned together

A tag pins everything above at once. A consumer moves by bumping its flake input, every workflow
`@v…` and `devkit-ref`, and its marketplace `ref` together; [development](3-development.md) has
the release side.
