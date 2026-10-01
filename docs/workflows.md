# Reusable workflows

Eight `workflow_call` workflows under `.github/workflows/`, replacing what the monorepo's `ci.yml`,
`pr-body.yml` and `ci-short-circuit-pr-close.yml` did in one tree (MIP-0070 §5.6, task 8). A ninth,
`devkit-ci.yml`, is this repo's own CI — not reusable, nothing to call.

Every workflow here pins third-party actions/tool versions the way the monorepo does; where a tool
wasn't pinned via an action before (ruff, actionlint, hadolint, shellcheck — the monorepo gets them
from a nix flake), the version below is what that flake pinned at the time this doc was written.
Bump the input, not the workflow file, when a newer version is wanted.

Three of the eight (`labels-sync`, `agents-check`, `pr-body`) take a required `devkit-ref` input:
they run scripts that live in *this* repo, not the caller's, so they check this repo out a second
time at that ref. Pin it to the same tag as the `uses:` line below — nothing keeps the two in sync
automatically. `ci-short-circuit`, `notify-umbrella`, `scala-ci`, `python-ci` and `static-ci` need
no such checkout.

`devkit-ref` stays required (no default) rather than trying to infer "the ref this reusable
workflow is itself running from": `github.workflow_ref`/`github.workflow_sha` might give that for
free inside a called reusable workflow, which would let a caller drop the input entirely, but that
needs to be checked against a real run before relying on it (does it name *this* file, or the
top-level caller's own workflow file, when workflows call each other?) — not implemented here,
worth checking the first time one of these actually runs as a `uses:` call.

These three also check the devkit out with the default `GITHUB_TOKEN`, no token input of their
own — that only works because `marola-devkit` is a public repo (MIP-0070 makes it public). A
private devkit would need a cross-repo PAT input on each of them, the same shape as
`notify-umbrella`'s `token` secret.

## scala-ci

Replaces `ci.yml`'s `build-test` job: JDK setup, sbt format/scalafix check, compile, test, with the
same sbt/coursier and zinc caches. Coverage and the site-data/badge publishing steps that job also
had were marola-app-specific and are not part of this workflow — a caller adds its own job for
those if it wants them.

```yaml
jobs:
  build-test:
    uses: marola-dev/marola-devkit/.github/workflows/scala-ci.yml@v0.2.0
```

| Input | Default | Notes |
|---|---|---|
| `java-version` | `"25"` | Kyo's jars need 25+ (AGENTS.md) |
| `sbt-tasks` | `scalafmtCheckAll "scalafixAll --check" compile test` | one sbt session |

No secrets.

## python-ci

Ruff check + format, then a caller-supplied newline list of self-test commands (the
`scripts/*.py --self-test` / `scripts/*.sh --self-test` lines each repo used to hardcode into
`ci.yml`'s `quality-other`).

```yaml
jobs:
  python-ci:
    uses: marola-dev/marola-devkit/.github/workflows/python-ci.yml@v0.2.0
    with:
      self-test-commands: |
        python3 scripts/cost-split.py --self-test
        scripts/deps-stack.sh --self-test
```

| Input | Default | Notes |
|---|---|---|
| `python-version` | `"3.12"` | `actions/setup-python` |
| `ruff-version` | `"0.16.9"` | pip-installed, pinned |
| `self-test-commands` | `""` | newline-separated; empty runs none |

No secrets.

## static-ci

actionlint always (against the caller's own `.github/workflows/`); hadolint and shellcheck only
when the caller names files (not every repo has a Dockerfile); then a caller-supplied newline list
of extra commands (`node --check …`, `docker compose … config --quiet`, …) — the rest of
`quality-other` that wasn't Python. Tools are pinned, downloaded release binaries, not
`nix develop .#lint`, so a caller repo doesn't need a compatible flake just to lint.

`shellcheck-files` is **new, not parity**: today's monorepo `quality-other` job only prints
`shellcheck --version`, it never runs shellcheck against a file. A caller opts into a real,
stricter shellcheck gate by passing files/globs here.

`extra-commands` is also where today's `just --list >/dev/null` justfile-parse check goes, for a
caller that still has a justfile:

```yaml
jobs:
  static-ci:
    uses: marola-dev/marola-devkit/.github/workflows/static-ci.yml@v0.2.0
    with:
      hadolint-files: |
        Dockerfile
      shellcheck-files: |
        scripts/*.sh
      extra-commands: |
        just --list >/dev/null
        node --check site/static/app.js
```

| Input | Default | Notes |
|---|---|---|
| `actionlint-version` | `"1.7.12"` | |
| `hadolint-version` | `"2.15.1"` | |
| `shellcheck-version` | `"0.11.0"` | |
| `hadolint-files` | `""` | newline-separated Dockerfile paths; empty skips hadolint |
| `shellcheck-files` | `""` | globs separated by spaces or newlines, expanded in the job; a glob matching nothing fails; empty skips shellcheck |
| `shellcheck-severity` | `error` | shellcheck `--severity`; `error` matches the devkit's own `just quality` |
| `extra-commands` | `""` | newline-separated; empty runs none |

No secrets.

## notify-umbrella

Tells the umbrella a repo's docs changed via `repository_dispatch`, so it rebuilds within minutes
instead of at its next daily cron (MIP-0070 §5.5). Modelled on h0ffmann/nix-config's
`profile-ping.yml` (see the monorepo's `profile-activity.yml` for that caller style) — no checkout
on either side, ~3 lines to call.

```yaml
name: notify umbrella
on:
  push:
    branches: [main]
    paths: [README.md, docs/**]
jobs:
  notify:
    uses: marola-dev/marola-devkit/.github/workflows/notify-umbrella.yml@v0.2.0
    secrets:
      token: ${{ secrets.UMBRELLA_DISPATCH_TOKEN }}
```

| Input | Default | Notes |
|---|---|---|
| `umbrella` | `marola-dev/marola` | MAROLA_UMBRELLA, MIP-0070 §5.6 |
| `event-type` | `submodule-docs-updated` | what the umbrella's docs workflow listens for |
| `runner` | `ubuntu-latest` | resolved in the *caller's* repo |

**Secret** `token` (optional): a fine-grained PAT with Contents: read & write on the umbrella.
Unset is a notice, not a failure — the umbrella's daily cron still catches the change.

## labels-sync

Applies this repo's own `.github/labels.yml` to the caller repo via `scripts/issues.sh labels
sync`, so `agent-ready` means the same thing everywhere (MIP-0070 §5.7).

```yaml
name: labels sync
on:
  push:
    branches: [main]
    paths: [.github/labels.yml]
  workflow_dispatch:
jobs:
  sync:
    uses: marola-dev/marola-devkit/.github/workflows/labels-sync.yml@v0.2.0
    with:
      devkit-ref: v0.2.0
```

| Input | Default | Notes |
|---|---|---|
| `devkit-ref` | *(required)* | pin to the same tag as `uses:` |
| `devkit-repo` | `marola-dev/marola-devkit` | |
| `prune` | `false` | deletes repo labels absent from the manifest |
| `force` | `false` | `issues.sh`'s `--force`; only needed with `prune: true` |

`force` only matters alongside `prune: true`: `issues.sh labels sync --prune` refuses to delete
more than half the repo's labels unless `--force` is also given, on the theory that a diff that
large is more likely the wrong manifest (or the wrong repo) than an intentional cleanup. Leave it
`false` for routine syncs; set it `true` only for a deliberate one-off prune you've reviewed.

**Permission needed:** `contents: read` (for the two checkouts) and `issues: write` (labels are a
repo resource under the Issues API). Uses the default `GITHUB_TOKEN` for both checkouts and the
sync — same-repo operation, no PAT — see the public-repo note above.

Implementation note: `scripts/issues.sh` resolves which repo to act on via `gh repo view` run from
its own script directory, not from cwd or `$GH_REPO` (verified while authoring this workflow: `gh
repo view` ignores `GH_REPO` and always asks git for the enclosing repository). So this workflow
strips the `.git` out of its devkit checkout right after cloning it — `gh`/`git` then search
upward from inside that directory and land on the caller's real checkout instead of the devkit's
own remote. This is specific to `issues.sh`'s resolution style; `agents-check` and `pr-body` do not
need it (see their own notes below).

## agents-check

Compares this repo's `AGENTS.md` invariants block against the pinned devkit's `agents/invariants.md`
— never the umbrella's tree (MIP-0070 §5.6). Pure local file comparison, no `git`/`gh` call inside
`scripts/agents-check.sh`, so none of `labels-sync`'s repo-resolution workaround is needed here.

```yaml
jobs:
  agents-check:
    uses: marola-dev/marola-devkit/.github/workflows/agents-check.yml@v0.2.0
    with:
      devkit-ref: v0.2.0
```

| Input | Default | Notes |
|---|---|---|
| `devkit-ref` | *(required)* | pin to the same tag as `uses:` |
| `devkit-repo` | `marola-dev/marola-devkit` | |
| `agents-file` | `AGENTS.md` | path to this repo's copy |

No secrets. Default `GITHUB_TOKEN` for the checkout — see the public-repo note above.

## pr-body

Fills a PR's description and title from its commits (today's `pr-body.yml`), reading
`scripts/uprd.sh` and `scripts/lib/` from a pinned devkit checkout instead of the caller's own tree.
`uprd.sh`'s `git`/`gh` calls are cwd-relative (no `cd` to its own script directory the way
`issues.sh` does), so it runs correctly against the caller repo with no extra workaround — just a
second checkout alongside the first.

```yaml
name: PR body
on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize, labeled, unlabeled]
jobs:
  fill:
    uses: marola-dev/marola-devkit/.github/workflows/pr-body.yml@v0.2.0
    with:
      devkit-ref: v0.2.0
```

| Input | Default | Notes |
|---|---|---|
| `devkit-ref` | *(required)* | pin to the same tag as `uses:` |
| `devkit-repo` | `marola-dev/marola-devkit` | |
| `umbrella` | `""` | MAROLA_UMBRELLA — resolves a MIP-scoped branch's doc link when this repo carries no `docs/MIPs/` of its own |

`umbrella` defaults to empty, not `marola-dev/marola`: an empty `MAROLA_UMBRELLA` env var and an
unset one are the same thing to `scripts/lib/mip_ref.sh`'s own `${MAROLA_UMBRELLA:-marola-dev/marola}`,
so that script stays the one place the default value lives — pass `umbrella` only to override it.

Uses the default `GITHUB_TOKEN` (`pull-requests: write`, declared in the workflow) for the PR itself,
and again (see the public-repo note above) for the devkit checkout. No secrets.

## ci-short-circuit

Cancels a closed PR's in-flight runs across every workflow, by head SHA (today's
`ci-short-circuit-pr-close.yml`). No devkit checkout — only `gh api`/`gh run cancel` against the
caller's own repo.

```yaml
name: ci short-circuit on PR close
on:
  pull_request:
    types: [closed]
jobs:
  cancel:
    uses: marola-dev/marola-devkit/.github/workflows/ci-short-circuit.yml@v0.2.0
```

No inputs, no secrets. Uses the default `GITHUB_TOKEN` (`actions: write`, declared in the
workflow).

## devkit-ci (not reusable)

This repo's own CI: installs Nix, creates or verifies `flake.lock`, runs `nix flake check`, then
`nix develop --command just quality` (the same ruff/shellcheck/actionlint gate a contributor runs
locally — not reimplemented here, so it can't quietly drift from it), then (also via `nix develop`)
every script's `--self-test` (`tests/self-tests.sh`), `agents-check` against this repo's own
`AGENTS.md`, and `claude plugin validate . --strict` (a pinned, plain `npm install` — verified
needing no login or API key for a local manifest check). Nothing to call; it triggers on
`push`/`pull_request` like any normal workflow.
