# Runners

Two things run marola work without a person watching each step: a self-hosted GitHub Actions
runner on the maintainer's machine, and the `mip-solve-perpetual` skill working through a task
list. Neither starts on its own: a human runs the command.

## Self-hosted Actions runner

Only one workflow may use it, marola-ml's GPU publish (`marola-sea-publish.yml`); the guard
below enforces that. The tools ([tools](4-reference_tools.md#self-hosted-runner)):

1. **`runner-preflight`** checks the machine can run the workflows: nix, python3, node, jq,
   curl, a usable docker, *no* passwordless sudo (a workflow that calls it would hang), disk, and
   with a token whether a runner carries the `dependabot` label. It queries `MAROLA_REPO`.
2. **`setup-runners N`** registers N runners, each in its own directory under
   `MAROLA_RUNNER_ROOT` named `MAROLA_RUNNER_PREFIX-<i>`, with `MAROLA_RUNNER_LABELS`, against
   `MAROLA_RUNNER_REPO`, copied from the runner release in `MAROLA_RUNNER_SOURCE`, and installs
   them as user services (`--foreground` prints the `run.sh` commands instead). One runner takes
   one job at a time, so N is how many jobs run in parallel. The registration token is fetched
   per run and needs `gh`. `--status` and `--remove` inspect and undo it.
3. **`gha-runner up|down|status|logs`** runs one registered runner from `GHA_RUNNER_DIR` in the
   background after the preflight, logging to `.tmp/gha-runner.log`. `up` refuses a second
   listener on the same registration; `down` asks the process table, not a pidfile, and stops
   every listener on the machine, the `setup-runners` clones included.
4. **`temps`** watches CPU and GPU temperature during a long training run, against each part's
   own throttle and shutdown points.

**The guard.** A public repo's self-hosted runner runs whatever a pull request asks it to.
`workflow-runners [.github/workflows]` fails when a `runs-on:` or reusable-workflow `runner:`
outside `marola-sea-publish.yml` names `self-hosted` or is an expression, or when that workflow
gains a trigger a pull request can reach (`pull_request`, `pull_request_target`,
`workflow_call`). MIP-0065 §5.2.

The variables are on [config](4-reference_config.md).

## Unattended MIP runs

`/marola-devkit:mip-solve-perpetual NNNN [NNNN…]` works through `MIP-NNNN.tasks.md` one task at a
time: one branch, one PR, the repo's gates green before each commit, trailers as usual, `just pr`
to open the PR. With no argument it picks the easiest actionable Draft MIP and says so.

- **One typed turn.** It sets a `/goal` (every row has an open PR or is logged blocked after two
  failures) and a `/loop`. The loop keeps that human-started turn going; it cannot start the
  skill. A cron or wakeup whose prompt is the slash command arrives as plain text, and the Skill
  tool refuses it (human-only). So a run lasts as long as the session and the usage guard allow,
  and the next start is a person typing the command. A cloud routine would survive a closed
  laptop, but is not assumed available.
- **No `gh` login is not a stop.** It still pushes, and appends each task's exact
  `gh pr create` command to `GH_POST_MORTEM.md` (gitignored, append-only) for a human to replay.
- **Usage guard, before every task.** From the 5-hour and weekly `used_percentage`/`resets_at`
  (via the `heavy-usage` plugin's status line; else `cost-split --estimate` against a stated
  budget), it projects end-of-window use linearly, adds the next task's estimated cost, and
  proceeds below 75%/85%, finishes the current PR and stops below 90%/95%, and stops at once
  above. `heavy-usage`'s own wind-down line wins when it is more conservative, but it is a prompt,
  not a block, and its data goes stale when no status line renders.
- **Model and effort.** A mechanical task starts at low effort, a substantive one at default;
  a failure raises effort before any model switch, and a switch to a costlier model needs the
  budget check to clear and ends with that task.
- **Checkpoint contract.** Before a usage stop, the current task is either done (gates green,
  committed, pushed, PR opened or logged) or abandoned with a clean tree and one line in the
  report. Never half-built state.
- **Stop** when every row has a PR or failed twice, when the guard trips with the checkpoint met,
  or at a decision only a human can make. It prints `GOAL_COMPLETE:` or `GOAL_FAILED:`.

**Merging stays a human act.** The skill's `disallowed-tools` drop `gh pr merge` and
`gh pr close`, but that list is documented to clear after a message, so the real boundary is the
consuming repo's `.claude/settings.json` `permissions.deny` for `gh pr merge`, `gh pr close`,
`gh stack merge`, `gh stack unstack` and `gh stack delete`. Check it is there before a run.
