# Development

How to change the devkit itself. The org's general rules (how every repo tests, reviews and
releases) are at <https://docs.marola.dev/>; this page covers what is specific to this repo.

## Shell and gates

`nix develop` puts every tool on `PATH` with the lint toolchain and points `core.hooksPath` at
`.githooks` (`just install-hooks` does the same by hand). Then:

| Command | Runs |
|---|---|
| `just quality` | `ruff check`, `ruff format --check`, `shellcheck --severity=error` over the scripts, hooks and statusline, `actionlint`, `tests/self-tests.sh`, `agents-check`, `docs-lint`, `skills-vendor check` over the plugin's `skills.lock`, and `claude plugin validate .` when `claude` is on `PATH` |
| `just precommit` | `ruff check`, the same shellcheck, `agents-check`: the pre-commit hook's recipe |
| `just prepush` | `just quality`: the pre-push hook's recipe |
| `bash tests/self-tests.sh` | Every `--self-test`, one line per script |
| `claude --plugin-dir plugins/marola-devkit` | The plugin from this checkout, before any tag |

`just quality` fails when ruff, shellcheck or actionlint is missing rather than skipping it.

## Self-test first

Every tool and hook has a `--self-test` that runs offline: no network, no `gh` login, git only
in temporary repos, `gh` stubbed where a command needs it, and fixtures from `scripts/fixtures/`.
A change to a script extends its self-test first and watches it fail, then makes it pass. A new
script goes into `tests/self-tests.sh`'s `sh_tests` or `py_tests` list, which is the one list
both `just quality` and the flake's `self-tests` check read.

A self-test that creates git repos starts with
`unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR` and
`export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`. Under a git hook those variables point
at the real repo, and `git -C <tmp> config` once rewrote this repo's `.git/config` on every push.

## CI

- **`devkit-ci.yml`** (push to `main`, every pull request) is this repo's own CI: it checks
  `flake.lock` is current for `flake.nix`, runs `nix flake check` (the package build and the
  self-tests derivation), then `just quality`, `tests/self-tests.sh` and `agents-check` inside the
  dev shell, and `claude plugin validate . --strict` with a pinned Claude Code release.
- **`pr.yml`** calls five of the reusable workflows on this repo's own pull requests
  (`python-ci`, `static-ci`, `agents-check`, `pr-body`, `ci-short-circuit`), at the PR's head, so
  a change to one of them runs in a real caller before a tag. `labels.yml` calls `labels-sync` by
  hand (`workflow_dispatch`). `scala-ci`, `notify-umbrella` and `api-docs` have no caller here.

## Releases

A change that alters behaviour for consumers is a release. Its version lives in
`plugins/marola-devkit/.claude-plugin/plugin.json` (Claude Code uses it to decide when an
installed copy is stale), `flake.nix`'s package version and every documented pin in `README.md`,
`docs/` and the workflow comments; `just quality` fails when they disagree
(`scripts/release.py --check`, at [scripts/release.py](https://github.com/marola-dev/marola-devkit/blob/main/scripts/release.py)).

1. On an up-to-date `main`, `just release X.Y.Z` writes the version everywhere, adds a
   `CHANGELOG.md` heading listing the commits since the last tag, and pushes
   `chore/release-vX.Y.Z`. Turn the list into prose and open the PR with `just pr`.
2. After it merges, `just release X.Y.Z` again on `main`: the versions now match, so it tags
   `vX.Y.Z` and pushes the tag. `release.yml` checks the versions against the tag and publishes
   the GitHub release.

`--dry-run` prints what either step would do. When a feature PR already moved the versions,
step 1 is skipped.

Once the release is published, `release.yml` calls `bump-consumers.yml`, which opens one PR in
each repo of `.github/consumers.txt` on `chore/devkit-vX.Y.Z`: the flake input and its
`flake.lock` node, every devkit workflow `@v…`, `devkit-ref` and docs-lint clone, and the
marketplace `ref`. A person merges each. It uses the org secret `MAROLA_BUMP_PAT`, so the PRs are
authored by that token's owner: a fine-grained PAT owned by marola-dev with access to all its
repositories and Contents, Pull requests and Workflows write, whose secret only marola-devkit may
read. It is not `MAROLA_CROSS_REPO_PAT`, which ten workflows across the org receive: with Workflows
write, any of them could push a branch whose new workflow runs with that repo's secrets. h0ffmann/ww3-gpu is outside the org token's reach and not listed: bump
the `@v…` and `devkit-ref` in its `skills.yml` by hand. Dispatch
`bump-consumers.yml` by hand to retry a version: it updates the open PRs instead of adding more.
Adding a consumer is one line in `.github/consumers.txt`.

## Updating graphify

`graph` runs whatever `graphify` the locked nixpkgs carries (0.9.66 as of v0.8.1). Nothing else pins
it, so any `nix flake update` can move it, and its CLI drifts between versions. `graph --self-test`
runs a fake graphify, so it cannot catch that. The locked version:
`nix eval --raw --inputs-from . nixpkgs#graphify.version`.

1. `nix flake update nixpkgs`. If that drags in too much (`lint` and `agentic` follow it), pin
   graphify alone instead: a second nixpkgs input locked where it carries the version you want,
   with `runtimeDeps` taking `graphify` from it.
2. Check everything [graph.sh](https://github.com/marola-dev/marola-devkit/blob/main/scripts/graph.sh)
   relies on against `nix develop -c graphify <subcommand> --help`:
   - `extract . --code-only --no-label` (0.9.66 lists `--no-label` only under `cluster-only`, but
     `extract` accepts it), and `graph.json`'s `nodes[].source_file` and `links[]`, which the
     prune reads;
   - `cluster-only . --no-label --graph <file>`: it must rewrite `graph.json`, `GRAPH_REPORT.md`
     and `graph.html` beside that file with no model call (0.9.66 labels communities with a model
     only when neither `--no-label` nor a saved `.graphify_labels.json` is there);
   - `query … --graph <file> --budget N`, and `path`/`explain` with `--graph <file>`;
   - `GRAPHIFY_OUT`, where `graph.json` must land; `GRAPHIFY_NO_AUTO_REFRESH=1` (honoured from
     0.9.72, inert before); `HOME` set to the cache dir; `OLLAMA_BASE_URL=http://127.0.0.1:9`.
3. In a fresh clone of this repo, `graph build` then `graph query "<question>"`:
   `git status --porcelain --ignored` still prints nothing, and `grep 'Token cost'` on the
   `GRAPH_REPORT.md` beside the `graph.json` that `build` names says 0 input and 0 output, and
   `jq '[.nodes[] | select((.source_file // "") == "")] | length'` on that `graph.json` says 0.
4. `bash tests/self-tests.sh`. A moved flag changes `graph.sh` and its self-test together, and the
   release entry names the new graphify version.

What proves it after the release is the umbrella's `graph` CI job on the devkit bump PR, a real
build and query (marola-dev/marola#755; it runs without `unshare -rn`, which GitHub's
`ubuntu-latest` refuses). For a minor graphify jump, also re-run the routing questions of
[MIP-0076 §7](https://docs.marola.dev/6-MIPs/MIP-0076-agent-routing-tooling/#7-verification-plan).

Never install graphify any other way, pass `--backend`, or run its installers or hooks (MIP-0076
§5.2).

## Secrets and cost

The devkit deploys nothing. Its one secret is `MAROLA_BUMP_PAT`, read by `bump-consumers.yml`
alone; its reusable workflows take what the caller passes (the `notify-umbrella` token,
`MAROLA_CROSS_REPO_PAT` in the callers) or the default `GITHUB_TOKEN`.
`setup-runners` and `gha-runner` touch a self-hosted runner only when a human runs them
([runners](4-reference_runners.md)).

## Docs

`README.md` is the landing and `docs/` holds numbered pages (MIP-0074 §5.2). `docs-lint` runs in
`just quality`; the link and recipe rules are in
[AGENTS.md](https://github.com/marola-dev/marola-devkit/blob/main/AGENTS.md).
