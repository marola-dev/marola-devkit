# Config

The devkit has no config file. A consumer configures it through these variables, and every one
has a default, so a repo that sets none of them gets the org's behaviour.

## `MAROLA_*` variables

| Variable | Read by | Default | What it sets |
|---|---|---|---|
| `MAROLA_UMBRELLA` | `scripts/lib/mip_ref.sh` (`uprd`, `issues`, `mip-resolve`) | `marola-dev/marola` | The umbrella's `owner/repo`: where MIPs are fetched from when no checkout has them, where `tasks-to-issues` files a MIP's parent issue, and (its owner) the org `issue-queue` searches |
| `MAROLA_INVARIANTS_BLOCK` | `agents-check` | `agents/invariants.md`; the flake's wrapper sets it to the pinned copy | The invariants block AGENTS.md is compared against |
| `MAROLA_STOP_GATE` | the Stop hook (`stop-gate.sh`) | the repo's `stop-gate` recipe when it defines one, else `just quality` | The command(s) the hook nags for, e.g. `"sbt test && just quality"`. Set it in `.claude/settings.json`'s `env`: the hook runs in Claude Code's process, not a login shell |
| `MAROLA_PUSH_REMOTE` | set by `.githooks/pre-push` for `just prepush` | — | The remote's name |
| `MAROLA_PUSH_URL` | set by `.githooks/pre-push` | — | The remote's URL |
| `MAROLA_PUSH_REFS_FILE` | set by `.githooks/pre-push` | — | A file of git's `<local ref> <local sha1> <remote ref> <remote sha1>` lines, one per pushed ref, deletions marked `(delete)` |
| `MAROLA_REPO` | `gha-runner`, `runner-preflight` | `marola-dev/marola` | The repo whose runner `status` and the preflight's checks query |
| `MAROLA_RUNNER_REPO` | `setup-runners` | `marola-dev/marola` | The repo the runners register with |
| `MAROLA_RUNNER_ROOT` | `setup-runners` | `~/.marola-runners` | Where each runner's directory is created |
| `MAROLA_RUNNER_LABELS` | `setup-runners` | `marola-sea` | The labels the runners register with |
| `MAROLA_RUNNER_PREFIX` | `setup-runners` | `marola` | The runner name prefix |
| `MAROLA_RUNNER_SOURCE` | `setup-runners` | `~/code/actions-runner`, then `~/actions-runner` | An unpacked actions-runner release to copy from |
| `MAROLA_OBSIDIAN_VAULT` | the `obsidian-vault` skill | `~/Documents/2nd-brain` | The maintainer's Obsidian vault |
| `MAROLA_BUMP_PAT` | `bump-consumers.yml` | — | Not an environment variable: the org secret, readable by marola-devkit alone, whose PAT opens the pin-bump PRs ([releases](3-development.md#releases)) |
| `MAROLA_CROSS_REPO_PAT` | a caller of `notify-umbrella` | — | Not an environment variable: the repository secret a caller passes as the workflow's `token` ([workflows](4-reference_workflows.md#notify-umbrella)) |

`MAROLA_TRACES` also appears in `scripts/`, but only as sample text in `tasks_issues.py`'s
self-test (a marola-app task row); the devkit does not read it.

## Other variables

| Variable | Read by | What it sets |
|---|---|---|
| `GHA_RUNNER_DIR` | `gha-runner` | The registered runner's directory; no default |
| `BASE`, `BRANCH`, `EXTRA_FILE` | `uprd` | The base branch, a branch other than the checked-out one, a file appended to the body |
| `TASK_PARTIAL` | `cost-fill` | `1` skips the `Closes #N` line for a task that does not finish its issue |
| `NLP_MIN_SIMILARITY` | `backfill-pr-labels` | The `--nlp` score floor (default `0.15`) |
| `GH_TOKEN` | `gh`, so every tool that calls it | A token for hosts with no `gh` login, such as ai-jail |
| `CLAUDE_PROJECT_DIR` | the plugin's hooks | The repo root, set by Claude Code; `STOP_GATE_REPO_ROOT` and `SESSION_START_REPO_ROOT` override it for one hook |
