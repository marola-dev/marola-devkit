---
name: easy-issue-solver
description: Sweep open marola-dev issues for quick wins and solve each one as its own PR — one subagent per repo, each fix tested and gated per that repo's AGENTS.md. Works for members (branches upstream) and outside contributors (fork, PR from the fork). Use when someone types /easy-issue-solver, optionally with a repo, "org", or repo#N list.
disable-model-invocation: true
---

# Solving the easy issues, one PR each

Typed by a person, never started by the model: it opens PRs on repos other people maintain.

**Never**, whoever runs it: merge, close or label an issue, approve a workflow run, push to
`main`, `git commit --no-verify`, or touch a secret, a paid resource or a repo setting. Those stay
with maintainers.

## Step 1 — who is running it

```bash
gh auth status                                    # no login → stop; the person runs `gh auth login`
gh api repos/marola-dev/<repo> --jq .permissions.push
```

- `true` → **member**: branches go to `marola-dev/<repo>`.
- `false` → **contributor**: `gh repo fork marola-dev/<repo> --clone=false` once, push branches to
  the fork, open PRs with `--repo marola-dev/<repo> --head <login>:<branch>`. Fork PRs wait for a
  maintainer to approve their CI run; say so in the report rather than treating it as a failure.

## Step 2 — the queue

Scope from the arguments: none → the repo of the current checkout; `org` → every repo;
`repo#N …` → exactly those.

```bash
gh search issues --owner marola-dev --state open --limit 200 \
  --json repository,number,title,labels,assignees
```

**Only `agent-ready` issues**, per every repo's AGENTS.md: that label is a maintainer saying what
done means. A member may widen this to unlabelled issues when they explicitly ask for it, and each
such PR says so in one line. A contributor may not; list the tempting unlabelled ones in the
report as "ask a maintainer to mark ready".

Keep an issue only if all hold:

- `size/S` or `size/XS` (or unsized and plainly under ~100 changed lines once read).
- Not `mip`, `phase/*`, `question`, `R&D`, `area/outreach`, a MIP task (`NNNN-T…`, "MIP-NNNN task
  N"), or `duplicate`; not assigned to someone else.
- No open PR already fixes it: `gh pr list -R marola-dev/<repo> --search "<N> in:body" --state open`.
- After `gh issue view N --comments`: the fix is clear from the issue, needs no human decision,
  secret, external account or design choice, and stays inside that repo. Destructive cleanups
  (deleting branches, data) are out even when small.

Show the shortlist, one line per issue, before Step 3.

## Step 3 — one subagent per repo, in parallel

Each gets its repo's shortlist and these rules:

- Clone to a **short** path (`/tmp/eis-<repo>`): sbt's boot socket fails on long paths
  (marola-app#46).
- Read the repo's `AGENTS.md`, `CLAUDE.md` and `.claude/rules/` first and follow them, the comment
  rules especially. Run tools through `nix develop -c …` where a flake exists.
- Per issue: a branch from fresh `origin/main` (`fix/<N>-<slug>`); for a bug, the failing test
  first; the smallest fix; that repo's full gate (`just build && just test && just quality`, or what
  its AGENTS.md names). A gate that cannot run here (Docker, a GPU) is named in the PR, not skipped
  silently.
- Commit: a body with `Fixes #N`, then the trailers the repo's AGENTS.md lists (`Tested:`, and
  `Co-Authored-By: Claude <noreply@anthropic.com>`). Open the PR with `just pr` where the recipe
  exists, else `gh pr create` filling `.github/PULL_REQUEST_TEMPLATE.md`.
- Anything that grows past quick-win size mid-way: stop, leave no PR, report it skipped with why.

Traps seen on the first run: a coursier `checksum format error` is a corrupt local cache, fixed by
pointing `COURSIER_CACHE` at a fresh directory; a pre-commit hook that runs sbt fails on the long
path above, not on the change.

## Step 4 — report

One table across repos: issue, PR link or "skipped: reason", and any gate that could not run.
Contributors also get the fork-CI note and the "ask a maintainer to mark ready" list.
