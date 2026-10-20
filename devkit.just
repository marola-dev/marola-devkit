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

# Add a missing Tested: trailer and a task's Closes #N line to the branch's commits.
trailer-fill *args:
    trailer-fill {{ args }}

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

# Check the repo's branch rulesets (main-rule, ci-rule) against the devkit's manifests.
rulesets-check *args:
    ruleset-sync check {{ args }}

# Apply the devkit's ruleset manifests to the repo.
rulesets-apply *args:
    ruleset-sync apply {{ args }}

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

bump-merge *args:
    bump-merge {{ args }}

# Order a MIP's open PRs.
mip-stack *args:
    mip-stack {{ args }}

# Delete merged local branches.
branches-clean *args:
    branches clean {{ args }}

# Open a PR for every plain branch ahead of origin/main with none yet.
branches-open *args:
    branches open {{ args }}

# The cross-repo wiring tables (MIP-0076), printed or rewritten between a file's wiring markers.
wiring *args:
    wiring {{ args }}

# The MIPs' decisions table, written or checked (MIP-0085 §5.6).
decisions *args:
    decisions {{ args }}

# The invariants block in AGENTS.md against the pinned devkit's copy.
agents-check *args:
    agents-check {{ args }}

# Every vendored skill matches .claude/skills/skills.lock (offline; a no-op without a lock).
skills-check *args:
    skills-vendor check {{ args }}

# What each vendored skill's upstream changed since its pin (network, advisory).
skills-outdated *args:
    skills-vendor outdated {{ args }}

# Re-vendor skills at upstream HEAD (or --sha) and rewrite the lock; never commits.
skills-update *args:
    skills-vendor update {{ args }}

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

# The code graph, offline and outside the checkout (MIP-0076 §5.2): build, query, path, explain.
graph *args:
    graph {{ args }}

# The same graph and the wiring from the umbrella's routing-bundle branch, no build (MIP-0084 §5.2).
route *args:
    route {{ args }}

# Which local Claude Code sessions routed a question, counts only, read-only (MIP-0084 §5.5).
route-usage *args:
    route-usage {{ args }}
