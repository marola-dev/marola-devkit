# Tools

Every tool `flake.nix` puts on `PATH`, with its script and the `devkit.just` recipe that wraps it.
Each one is also a flake app (`nix run github:marola-dev/marola-devkit/<tag>#<tool>`) and has an
offline `--self-test`. Most take `--dry-run`, which prints the mutating `git`/`gh` calls instead of
making them. A tool that calls `gh` needs a login on the host; inside ai-jail it has none, so use
`--dry-run` there or pass `GH_TOKEN`.

## Pull requests and stacks

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `pr-flow` | `pr.sh` | `just pr` | The whole PR path from a finished commit: refuses on `main` or a dirty tree, fills missing trailers (`cost-fill`), pushes (`stack pr` for a `mip-NNNN/k-*` branch), writes the body (`uprd`). `--dry-run` prints every step and the body. Not named `pr`, which would shadow coreutils' |
| `stack` | `stack.sh` | `just stack`, `just stack-link` | Stacked branches for a MIP's tasks: `start MIP-NNNN k slug` (branch off the nearest lower task branch, else `origin/main`), `pr` (push, open or update with the right base), `restack` (after the base was squash-merged; `--onto-base <sha>` when it finds no fork point), `status`, `branches`, `link` (a GitHub Stack from the open PRs) |
| `uprd` | `uprd.sh` | `just uprd` | Write the branch's PR body from its commits, in the PR template's shape, and create the PR if none exists. `uprd 84` works on that PR from any checkout; a file argument is used as the body. `BASE`, `BRANCH` and `EXTRA_FILE` override the base, the branch and an appended section |
| `uprds` | `uprds.sh` | `just uprds` | `uprd` for every PR of a MIP stack, bottom first, each with a shared "Stack" section (states, the current PR marked, the summed `Cost`) |
| `mip-stack` | `mip-stack.sh` | `just mip-stack` | Chain every open MIP draft PR into one GitHub Stack, resolving the shared `docs/MIPs/README.md` row conflict; `--resume`, `--skip <PR#>`, `status`, `clean` |
| `docs-mip-stack` | `docs-mip-stack.sh` | `just docs-mip-stack` | Chain un-merged `docs/mip-NNNN-*` branches: `auto` (push and plan the unambiguous local ones), `list`, `plan <branch>…` (conflict check with `git merge-tree`, prints the `gh pr create` lines) |
| `deps-stack` | `deps-stack.sh` | `just deps-stack` | Chain open dependency-update PRs into one GitHub Stack, auto-resolving bump-vs-bump conflicts; `--resume`, `--skip`, `--include-steward`, `status`, `clean`. Built in `.tmp/wt-deps-stack`, never the caller's checkout |
| `deps-merge` | `deps-merge.sh` | `just deps-merge` | Merge every open dependency PR that is mergeable with every check green (skipped and neutral count as green); reports the rest |
| `branches` | `branches.sh` | `just branches-clean`, `just branches-open` | `clean` deletes local branches whose PR merged (local and remote ref); `open` opens a `main`-based PR for each plain branch ahead of `main` with none |

`just stack-setup`, `just stack-view` and `just stack-sync` wrap GitHub's own `gh stack`
extension rather than a devkit tool.

## Cost and trailers

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `cost-split` | `cost-split.py` | `just cost-split` | Attribute Claude Code (and OpenCode, `--harness`) session usage to the commits it produced, per branch or `MIP-NNNN` stack. `--estimate` fills unlogged commits from a diff-size fit, labelled `est.`; `--estimate-commit <sha>` prints one commit's `Cost:` trailer with no logs |
| `cost-fill` | `cost-fill.sh` | `just cost-fill` | Add a missing `Cost:` (measured, else `est.`) and `Tested:` (`ci-only`) trailer to each commit of the branch, dates preserved, and a `Closes #N` line for a task branch whose row links an issue (`TASK_PARTIAL=1` opts out). `--base <ref>` bounds the rewrite |

## Issues and the board

`issues` is the whole issue surface (MIP-0063, MIP-0070 §5.7). Every subcommand takes
`--dry-run`; the reads behind it still happen, so a login is needed either way.

| Recipe | Command | Does |
|---|---|---|
| `just issue-queue [--milestone NAME]` | `issues queue` | The unassigned open issues across the umbrella's org, `agent-ready` ones from other repos as `owner/repo#N`, sorted size then priority, with ready, blocked and in-triage counts from labels and dependency edges |
| `just issue-ready <n>` | `issues ready` | The five-rule Definition of Ready, naming the rule that failed; adds `agent-ready` on a pass and removes it on a regression |
| `just issue-claim <n\|owner/repo#n>` | `issues claim` | Re-check readiness, assign to you, drop the label, set the board to In progress, print the `stack start` line |
| `just tasks-to-issues MIP-NNNN [--milestone NAME] [--deliverable NAME]` | `issues tasks-to-issues` | File a MIP's task table: a parent in the umbrella, one sub-issue per row in the repo its `delivers` cell names, `blocked by` edges per `depends on`, everything on Project 1. Idempotent; sets no `area/*`, `layer/*` or `size/*` |
| `just milestone-new "<name>" [--mip MIP-NNNN]` | `issues milestone new` | Create a deliverable milestone; idempotent |
| `just labels-sync [--prune] [--force]` | `issues labels sync` | Reconcile the repo's labels with the devkit's `.github/labels.yml`; orphans are deleted only with `--prune` |
| `just board-sync` | `issues board sync` | Put every open issue on the board; set Status only on a card with none or `Backlog`, and move a closed issue's card to Done |
| (none) | `issues board setup`, `issues board gates` | One-time bootstraps: the board's Status options, views and `Deliverable` field (needs `project` scope); the five phase gate issues |
| (none) | `issues sub add <parent> <child>`, `issues deps add <n> --blocked-by <m>`, `issues deps list <n>` | One sub-issue or dependency edge by hand |

## Labels, rulesets and repo checks

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `pr-label` | `pr-label.sh` | `just pr-label` | Apply the deterministic `area/*`, `layer/*`, `kind/*` taxonomy (`scripts/lib/pr_labels.sh`) to one PR |
| `backfill-pr-labels` | `backfill-pr-labels.sh` | `just pr-labels-backfill` | The same for every merged or closed PR with no label; `--limit`, `--nlp` (compare with `pr-label-nlp`), `--nlp-apply-unscoped` (fill only an `area/unscoped` gap) |
| `pr-label-nlp` | `pr_label_nlp.py` | (none) | A local TF-IDF classifier for `area/*`, kept to compare against the deterministic one, never applied on its own. `--pr N` or `--title`/`--body` |
| `ruleset-sync` | `ruleset-sync.sh` | `just rulesets-check`, `just rulesets-apply` | `check [owner/repo…]` diffs a repo's live branch ruleset against `.github/rulesets/main-rule.json`; `apply <owner/repo…>` creates or updates it; `--all-org ORG` covers an org |
| `agents-check` | `agents-check.sh` | `just agents-check` | Compare AGENTS.md's invariants block byte for byte with the pinned devkit's `agents/invariants.md` (`--block` or `MAROLA_INVARIANTS_BLOCK` to use another) |
| `docs-lint` | `docs_lint.py` | (none) | MIP-0074 §7's stale-content check over `README.md` and `docs/**/*.md`: undefined or unmarked foreign recipes, another repo's paths, split-era wording, `docs/index.md`, links leaving the repo |
| `wiring` | `wiring.py` | `just wiring` | MIP-0076 §5.1's four tables (artifact, dispatch, pin bump, deploy), parsed from the umbrella's and every submodule's workflows, pin files, scripts, justfiles, Dockerfiles, compose files and `build.sbt`, with each devkit workflow call's ref (each call is read at that ref: from a local devkit clone's tags, else fetched from GitHub, a tag cached under `~/.cache/marola-wiring`). `wiring [FILE]` prints the block or rewrites it between FILE's `wiring:start`/`wiring:end` markers; run it in an umbrella checkout with submodules |
| `workflow-runners` | `workflow_runners.py` | (none) | Fail when any workflow but the GPU publish one can reach a self-hosted runner ([runners](4-reference_runners.md)) |
| `skills-vendor` | `skills_vendor.py` | `just skills-check`, `just skills-outdated`, `just skills-update` | Pin vendored skills to an upstream commit in `skills.lock` (below) |

## skills-vendor

A skill copied from another repo is pinned the way a dependency is (MIP-0080): `skills.lock`
beside the skill directories names its upstream, the commit it was taken at and the git blob sha
of every vendored file. The lock is `<repo>/.claude/skills/skills.lock` unless `--lock` says
otherwise, and a skill's files live in `<lock dir>/<name>/`;
[this repo's](https://github.com/marola-dev/marola-devkit/blob/main/plugins/marola-devkit/skills/skills.lock)
shows the shape.

| Field | Meaning |
|---|---|
| `upstream`, `path` | The GitHub repo and the skill's directory in it (`""` for the root) |
| `commit` | The upstream commit the files were taken at; `null` when `init` found none |
| `files` | Each vendored file with its git blob sha. Only these files are vendored |
| `paths` | Optional: a vendored file that sits elsewhere upstream (`"LICENSE": "LICENSE"`) |
| `license` | The SPDX id from `SKILL.md`'s `license:` or the `LICENSE` file, or `null` |
| `local_edits` | Optional: a patch relative to the lock directory, re-applied after every update; `files` then holds the edited shas and `upstream_files` the pristine ones |
| `hold` | A reason to leave the skill where it is; `outdated` and `update` skip it |

| Command | Does |
|---|---|
| `check` | Offline: every file in each skill directory has the sha in `files`. No lock file: exit 0 |
| `outdated [--min-age DAYS] [--json] [--strict]` | The newest upstream commit touching `path` at least `--min-age` days old, with a diff of what it changes |
| `update <name>... \| --all [--sha SHA] [--min-age DAYS] [--report FILE]` | Fetch at that commit, re-apply `local_edits` (a patch that fails stops it), rewrite the lock; never commits |
| `init <name> --upstream o/r --path DIR [--sha SHA] [--paths FILE=UPSTREAM]` | A lock entry from the copy already in the tree; without `--sha`, the newest of 200 upstream commits whose tree matches |

Upstreams are read through a blobless `git clone` of `https://github.com/<upstream>`, so the tool
works wherever that does, Actions included.
| `mip-resolve` | `mip-resolve.sh` | (none) | Print a MIP's doc or `.tasks.md` (`MIP-NNNN [tasks]`), or with `--path` where it was found, by the lookup in [design](1-design.md#how-tools-find-the-umbrella) |
| `api-docs-push` | `api-docs-push.sh` | (none) | `api-docs-push <dir> <remote> <sha> [--branch api-docs]`: force-push a directory as one orphan commit, the `api-docs` workflow's push step |

## Self-hosted runner

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `runner-preflight` | `runner-preflight.sh` | `just runner-preflight` | Check this machine can run the workflows (nix, python3, node, jq, curl, docker, no passwordless sudo, disk) |
| `gha-runner` | `gha-runner.sh` | `just runner-up`, `runner-down`, `runner-status`, `runner-logs` | Run a registered runner in the background from `GHA_RUNNER_DIR` |
| `setup-runners` | `setup-runners.sh` | `just runners` | Register N runners on this machine as user services; `--status`, `--remove` |
| `temps` | `temps.sh` | `just temps` | CPU and GPU temperature against the parts' own throttle and shutdown points; `--watch`, `--json` |

Details and variables are on the [runners](4-reference_runners.md) and
[config](4-reference_config.md) pages.
