# marola-devkit's just module (MIP-0070 §5.6). A consuming repo imports it from the pinned tree the
# devkit's shellHook links in:   import? '.devkit/devkit.just'
# Every recipe calls a tool by its PATH name, so the flake's pinned version is the one that runs.
# A repo that defines a recipe of the same name needs `set allow-duplicate-recipes`.

# Write or refresh a PR body from the branch's commits.
uprd *args:
    uprd {{ args }}

# Same for a whole MIP stack.
uprds *args:
    uprds {{ args }}

# Add a missing Cost:/Tested: trailer to a commit, measured from the session logs.
cost-fill *args:
    cost-fill {{ args }}

# Attribute session usage to commits (the `Cost:` trailer).
cost-split *args:
    cost-split {{ args }}

# The whole PR workflow: fill trailers, push, write the PR body.
pr *args:
    pr-flow {{ args }}

# Apply the deterministic label taxonomy to one PR.
pr-label *args:
    pr-label {{ args }}

# Re-label every past PR with the current taxonomy.
pr-labels-backfill *args:
    backfill-pr-labels {{ args }}

# Sync .github/labels.yml into the repo's labels.
labels-sync *args:
    issues labels sync {{ args }}

# Add `agent-ready` when the Definition of Ready passes.
issue-ready *args:
    issues ready {{ args }}

# The agent-ready queue.
issue-queue *args:
    issues queue {{ args }}

# File a MIP's .tasks.md rows as issues.
tasks-to-issues *args:
    issues tasks-to-issues {{ args }}

# Take an agent-ready issue and print its branch command.
issue-claim *args:
    issues claim {{ args }}

# A MIP's deliverable as a milestone.
milestone-new *args:
    issues milestone new {{ args }}

# Reconcile the Project board with the issues.
board-sync *args:
    issues board sync {{ args }}

# Stacked-PR mechanics for MIP task branches.
stack *args:
    stack {{ args }}

# Link a MIP's open PRs into one GitHub Stack, bottom to top.
stack-link mip="":
    stack link {{ mip }}

# Install the official `gh stack` extension GitHub's native Stacks need.
stack-setup:
    gh auth status >/dev/null 2>&1 || { echo "gh is not logged in — run: gh auth login" >&2; exit 1; }
    gh extension list | grep -q 'github/gh-stack' || gh extension install github/gh-stack

# The stack as GitHub sees it (PR numbers, states, bases).
stack-view *args:
    gh stack view {{ args }}

# Restack after a bottom PR was squash-merged.
stack-sync mip="":
    #!/usr/bin/env bash
    set -euo pipefail
    bottom="$(stack branches {{ mip }} | head -1)"
    gh stack checkout "$bottom"
    gh stack sync

# Chain independent, un-merged docs/mip-NNNN-* branches.
docs-mip-stack *args:
    docs-mip-stack {{ args }}

# Stack and merge dependency-bump PRs.
deps-stack *args:
    deps-stack {{ args }}

deps-merge *args:
    deps-merge {{ args }}

# Order a MIP's open PRs.
mip-stack *args:
    mip-stack {{ args }}

# Delete merged local branches.
branches-clean *args:
    branches clean {{ args }}

# Open a PR for every plain branch ahead of origin/main with none yet.
branches-open *args:
    branches open {{ args }}

# The invariants block in AGENTS.md against the pinned devkit's copy.
agents-check *args:
    agents-check {{ args }}

# Self-hosted Actions runner.
runner-preflight:
    runner-preflight

runner-up *args:
    gha-runner up {{ args }}

runner-down *args:
    gha-runner down {{ args }}

runner-status:
    gha-runner status

runner-logs:
    gha-runner logs

runners *args:
    setup-runners {{ args }}

temps *args:
    temps {{ args }}
