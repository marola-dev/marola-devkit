# Changelog

Each release moves `plugin.json`'s `version`, the flake package version and every documented pin
together. A consumer adopts one by bumping its flake input, its workflow `@v…`/`devkit-ref` and its
marketplace `ref` at once.

## v0.7.0 — 2026-10-09

- `wiring`, MIP-0076 §5.1's extractor: the four tables (artifact, dispatch, pin bump, deploy),
  parsed from the umbrella's and every submodule's workflows, pin files, scripts, justfiles,
  Dockerfiles, compose files and `build.sbt`, and written between a file's `wiring:start`/`wiring:end`
  markers by `just wiring`. Each reusable-workflow call is read at its own ref, and release assets
  are parsed from `gh release upload`/`create`.
- `wiring --check` fails on a stale block, a dispatch type sent or listened for on one side only,
  and an artifact nobody reads that the umbrella's `wiring.allow` does not list with a reason.
- `notify-umbrella` sends `submodule-updated` on every push, and `submodule-docs-updated` too when
  the push touched `README.md` or `docs/**` (read from the compare API). Callers drop `paths:`;
  the `event-type` input is gone, so a caller still passing it fails (MIP-0076 §5.5).

## v0.6.0 — 2026-10-08

- `skills-vendor` pins vendored skills to an upstream commit in `skills.lock` (MIP-0080):
  `check` (offline, in `just quality`), `outdated`, `update` and `init`, as the recipes
  `skills-check`, `skills-outdated` and `skills-update`.
- The `skills-update` reusable workflow re-vendors a repo's skills weekly, 14 days behind upstream
  HEAD, as one PR labelled `skills-update` with the diffs in its body; `skills.yml` calls it here
  for the plugin's four vendored skills, pinned in `plugins/marola-devkit/skills/skills.lock`.

## v0.5.1 — 2026-10-07

- `gemini-review` retries a dropped connection to the Gemini API (`RemoteDisconnected`, a reset,
  a timeout) after 20 s and 60 s, as it already did a 503; a 429 still fails at once (#34).

## v0.5.0 — 2026-10-04

- `gemini-review` reviews fork PRs too, review only: the base branch is the workspace, the fork's
  head is read as text from `.pr-head` (`gemini_review.py review --root`), and no fix commit is
  pushed. Callers move from `pull_request` to `pull_request_target` so a fork run gets the
  secrets (#23).
- `devkit-ci` and `api-docs` drop `magic-nix-cache-action`: its post step spent 2m40s of a 6m23s
  devkit CI run uploading store paths that cache.nixos.org already serves (marola-dev/marola#657).

## v0.4.1 — 2026-10-04

- `docs-lint` skips `docs/benchmarks/**`, as it skips `docs/MIPs/**` and `SPLIT.md`: a dated benchmark
  run is a kept record, and marola-ml's would otherwise fail rule (b) once its gate is on
  (MIP-0074 task 25).

## v0.4.0 — 2026-10-04

- The `gemini-review` reusable workflow: requesting the org team `gemini` on a PR gets a Gemini
  review, then one commit with the findings it can fix safely, pushed as the `marola-gemini-bot`
  App (marola-dev/marola#641). One Gemini API call per review (`scripts/gemini_review.py`),
  since the free tier allows 20 requests a day per model. This repo calls it from `gemini.yml`.

## v0.3.1 — 2026-10-03

- Design, development and reference pages: `docs/1-design.md`, `docs/3-development.md`, and
  `docs/4-reference_{tools,config,hooks,plugin,runners}.md`, which document every tool on `PATH`,
  every `MAROLA_*` variable, both kinds of hooks, the plugin's skills and agents, and unattended
  runs (#9).
- The `mip` skill no longer cites the retired FABLE_REVIEW page and cites the system
  ARCHITECTURE's local-first integration pattern by URL.
- The README's release rule matches AGENTS.md and points at `docs/3-development.md`.

## v0.3.0 — 2026-10-03

- `docs-lint`, MIP-0074 §7's stale-content check over `README.md` and `docs/**/*.md` (#11).
- The `api-docs` reusable workflow and its `api-docs-push` tool: a PR check that runs the
  caller's generator, and on a push one orphan commit to an `api-docs` branch (#12).
- The README is the landing; `docs/index.md` is gone, and `docs/workflows.md` is now
  `docs/4-reference_workflows.md`. `notify-umbrella`'s documented secret is `MAROLA_CROSS_REPO_PAT`.
- `just quality` runs `docs-lint` on this repo.

## v0.2.4 — 2026-10-03

- `stack` bases a task branch on the nearest lower task branch of the MIP, else `main`, since a
  repo's task numbers skip; `stack pr` fails loudly on an empty base (#14).

## v0.2.3 — 2026-10-02

- `ruleset-sync`: check or apply `.github/rulesets/main-rule.json`'s branch ruleset per repo or
  across an org, with `rulesets-check`/`rulesets-apply` recipes (#5).

## v0.2.2 — 2026-10-01

- `scala-ci` passes a quoted `sbt-tasks` entry (`"scalafixAll --check"`) to sbt as one task (#4).

## v0.2.1 — 2026-10-01

- Tools resolve the caller's repo and files from its root, not their own install directory.
- The pre-push hook passes the remote, URL and pushed refs to `prepush` through
  `MAROLA_PUSH_REMOTE`, `MAROLA_PUSH_URL` and `MAROLA_PUSH_REFS_FILE`.
- The Stop hook nags for the repo's own gate (`stop-gate` recipe or `MAROLA_STOP_GATE`).
- `labels-sync` uses the same manifest rule as a local labels sync.
- The flake ships `.claude/statusline.sh`; the plugin loses its last marola-app-specific
  examples; the README documents the marketplace `ref` pin (#3).

## v0.2.0 — 2026-10-01

- Eight reusable workflows: `scala-ci`, `python-ci`, `static-ci`, `agents-check`, `labels-sync`,
  `notify-umbrella`, `pr-body`, `ci-short-circuit` (MIP-0070 task 8). This repo calls them on
  its own pull requests (#2).

## v0.1.0 — 2026-10-01

- The devkit extracted from the umbrella (MIP-0070 task 7): the tools packaged as a flake
  (packages, apps, `lib.<system>`), the `devkit.just` module, the git hooks, the org invariants
  block and `agents-check`, the issue forms and labels, and the `marola-devkit` Claude Code
  plugin with its marketplace. Its own CI runs `nix flake check`, the quality gate and
  `claude plugin validate`.
