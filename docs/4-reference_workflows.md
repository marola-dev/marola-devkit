# Reusable workflows

Nine `workflow_call` workflows under `.github/workflows/`. Eight took over what the umbrella's
`ci.yml`, `pr-body.yml` and `ci-short-circuit-pr-close.yml` did for one tree before the split
(MIP-0070 §5.6), and `api-docs.yml` is MIP-0074 §5.2's. A tenth, `devkit-ci.yml`, is this repo's
own CI — not reusable, nothing to call.

Every workflow pins its third-party actions and tools. Where a tool has no action (ruff,
actionlint, hadolint, shellcheck, which a repo's flake supplies locally), the default below is the
version the umbrella's flake pinned when the workflow was written. Bump the input, not the
workflow file, when a newer version is wanted.

Four of the nine (`labels-sync`, `agents-check`, `pr-body`, `api-docs`) take a required
`devkit-ref` input: they run scripts that live in *this* repo, not the caller's, so they check this
repo out a second time at that ref. Pin it to the same tag as the `uses:` line below — nothing
keeps the two in sync automatically. `ci-short-circuit`, `notify-umbrella`, `scala-ci`,
`python-ci` and `static-ci` need no such checkout.

`devkit-ref` stays required because a called workflow cannot find out its own ref: inside one,
`github.workflow_ref` and `github.workflow_sha` name the *caller's* workflow and commit, not the
called file's (checked on marola-devkit#2's first run:
`workflow_ref=marola-dev/marola-devkit/.github/workflows/pr.yml@refs/pull/2/merge`).

These four also check the devkit out with the default `GITHUB_TOKEN`, no token input of their
own — that only works because `marola-devkit` is a public repo (MIP-0070 makes it public). A
private devkit would need a cross-repo PAT input on each of them, the same shape as
`notify-umbrella`'s `token` secret.

## scala-ci

Replaces the pre-split `ci.yml`'s `build-test` job: JDK setup, sbt format/scalafix check, compile, test, with the
same sbt/coursier and zinc caches. Coverage and the site-data/badge publishing steps that job also
had were marola-app-specific and are not part of this workflow — a caller adds its own job for
those if it wants them.

```yaml
jobs:
  build-test:
    uses: marola-dev/marola-devkit/.github/workflows/scala-ci.yml@v0.2.4
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
    uses: marola-dev/marola-devkit/.github/workflows/python-ci.yml@v0.2.4
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

`shellcheck-files` is **new, not parity**: the pre-split `quality-other` job only printed
`shellcheck --version`, it never ran shellcheck against a file. A caller opts into a real,
stricter shellcheck gate by passing files/globs here.

`extra-commands` is also where a `just --list >/dev/null` justfile-parse check goes, for a caller
with a justfile:

```yaml
jobs:
  static-ci:
    uses: marola-dev/marola-devkit/.github/workflows/static-ci.yml@v0.2.4
    with:
      hadolint-files: |
        Dockerfile
      shellcheck-files: |
        scripts/*.sh
      extra-commands: |
        just --list >/dev/null
        node scripts/site_check.js
```

| Input | Default | Notes |
|---|---|---|
| `actionlint-version` | `"1.7.12"` | |
| `hadolint-version` | `"2.15.1"` | |
| `shellcheck-version` | `"0.11.0"` | |
| `hadolint-files` | `""` | Dockerfile paths or globs separated by spaces or newlines, expanded in the job; a glob matching nothing fails; empty skips hadolint |
| `shellcheck-files` | `""` | globs separated by spaces or newlines, expanded in the job; a glob matching nothing fails; empty skips shellcheck |
| `shellcheck-severity` | `error` | shellcheck `--severity`; `error` matches the devkit's own `just quality` |
| `extra-commands` | `""` | newline-separated; empty runs none |

No secrets.

## notify-umbrella

Tells the umbrella a repo's docs changed via `repository_dispatch`, so it rebuilds within minutes
instead of at its next daily cron (MIP-0070 §5.5). Modelled on h0ffmann/nix-config's
`profile-ping.yml` — no checkout on either side, ~3 lines to call.

```yaml
name: notify umbrella
on:
  push:
    branches: [main]
    paths: [README.md, docs/**]
jobs:
  notify:
    uses: marola-dev/marola-devkit/.github/workflows/notify-umbrella.yml@v0.2.4
    secrets:
      token: ${{ secrets.MAROLA_CROSS_REPO_PAT }}
```

| Input | Default | Notes |
|---|---|---|
| `umbrella` | `marola-dev/marola` | MAROLA_UMBRELLA, MIP-0070 §5.6 |
| `event-type` | `submodule-docs-updated` | what the umbrella's docs workflow listens for |
| `runner` | `ubuntu-latest` | resolved in the *caller's* repo |

**Secret** `token` (optional): every repo passes the org secret `MAROLA_CROSS_REPO_PAT`, a
fine-grained PAT with Contents: read & write on the umbrella (`repository_dispatch` needs it;
`GITHUB_TOKEN` cannot reach another repo). Unset is a notice, not a failure — the umbrella's daily
cron still catches the change.

## labels-sync

Reconciles the caller repo's labels against `scripts/issues.sh labels sync`'s own default manifest
(the caller's own `.github/labels.yml` when it has one, else this devkit's bundled copy — no
`--manifest` override, the same rule `just labels-sync` gets run locally), so `agent-ready` means
the same thing everywhere (MIP-0070 §5.7) and CI can never prune against a different manifest than
a human's own run would.

```yaml
name: labels sync
on:
  push:
    branches: [main]
    paths: [.github/labels.yml]
  workflow_dispatch:
jobs:
  sync:
    uses: marola-dev/marola-devkit/.github/workflows/labels-sync.yml@v0.2.4
    with:
      devkit-ref: v0.2.4
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
cwd (`$GITHUB_WORKSPACE`, the caller's own checkout — this job never `cd`s into `.devkit-checkout`),
not `$GH_REPO` (verified while authoring this workflow: `gh repo view` ignores `GH_REPO` and always
asks git for the enclosing repository). No extra step is needed to make that resolve correctly, so
unlike an earlier version of this workflow, `.devkit-checkout`'s own `.git` is left in place.

## agents-check

Compares this repo's `AGENTS.md` invariants block against the pinned devkit's `agents/invariants.md`
— never the umbrella's tree (MIP-0070 §5.6). Pure local file comparison, no `git`/`gh` call inside
`scripts/agents-check.sh`, so it needs no `gh repo view`-style repo resolution at all.

```yaml
jobs:
  agents-check:
    uses: marola-dev/marola-devkit/.github/workflows/agents-check.yml@v0.2.4
    with:
      devkit-ref: v0.2.4
```

| Input | Default | Notes |
|---|---|---|
| `devkit-ref` | *(required)* | pin to the same tag as `uses:` |
| `devkit-repo` | `marola-dev/marola-devkit` | |
| `agents-file` | `AGENTS.md` | path to this repo's copy |

No secrets. Default `GITHUB_TOKEN` for the checkout — see the public-repo note above.

## api-docs

Runs the caller's `api-docs <output-dir>` recipe (`sbt doc`, pdoc, ...) in two jobs (MIP-0074
§5.2):

- `check`, on `pull_request`: runs the generator and commits nothing; a broken generator fails
  the PR.
- `publish`, on `push`: runs it, then `scripts/api-docs-push.sh` force-pushes the output as one
  orphan commit to the `api-docs` branch. Only the latest output is kept, and the commit message
  names the source sha.

Only `publish` has a concurrency group (`api-docs`, the latest push wins), so a PR run can never
cancel a publish. The caller wires both triggers, and each job runs only on its own event. Both
install Nix and run the recipe inside `nix develop`, so the caller's flake provides `just` and the
generator's toolchain.

A generator writes under `<output-dir>/<lang>/` (`scala/`, `python/`). The umbrella's docs build
unpacks the branch as the repo's `api-docs/`, so a page links `api-docs/<lang>/…`.

```yaml
name: api docs
on:
  pull_request:
  push:
    branches: [main]
jobs:
  api-docs:
    uses: marola-dev/marola-devkit/.github/workflows/api-docs.yml@v0.2.4
    permissions:
      contents: write
    with:
      devkit-ref: v0.2.4
```

The calling job needs `permissions: contents: write` even though only the `publish` sub-job uses
it: a reusable-workflow call's jobs can never exceed what the calling job itself was granted, so
leaving this off would make `publish`'s push fail regardless of what `api-docs.yml` declares
internally.

| Input | Default | Notes |
|---|---|---|
| `devkit-ref` | *(required)* | pin to the same tag as `uses:` |
| `devkit-repo` | `marola-dev/marola-devkit` | |
| `output-dir` | `.tmp/api-docs` | where the caller's `api-docs <output-dir>` recipe writes |

**Permissions:** `check` declares `contents: read` and checks out with `persist-credentials:
false` — it runs a PR's own `api-docs` recipe, so no token (even a read-only one) is left in
the checkout for that recipe to find. `publish` declares `contents: write` and uses the default
`GITHUB_TOKEN` for both its checkouts and the push — same-repo operation, no PAT — see the
public-repo note above. `scripts/api-docs-push.sh` itself never reads `GITHUB_TOKEN`: the push
step builds an authenticated remote URL and passes it as a plain argument, which is also why its
own `--self-test` needs no network, just a local bare repo.

## pr-body

Fills a PR's description and title from its commits (the pre-split `pr-body.yml`), reading
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
    uses: marola-dev/marola-devkit/.github/workflows/pr-body.yml@v0.2.4
    with:
      devkit-ref: v0.2.4
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

Cancels a closed PR's in-flight runs across every workflow, by head SHA (the pre-split
`ci-short-circuit-pr-close.yml`). No devkit checkout — only `gh api`/`gh run cancel` against the
caller's own repo.

```yaml
name: ci short-circuit on PR close
on:
  pull_request:
    types: [closed]
jobs:
  cancel:
    uses: marola-dev/marola-devkit/.github/workflows/ci-short-circuit.yml@v0.2.4
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
