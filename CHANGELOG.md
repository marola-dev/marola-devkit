# Changelog

Each release moves `plugin.json`'s `version`, the flake package version and every documented pin
together. A consumer adopts one by bumping its flake input, its workflow `@v…`/`devkit-ref` and its
marketplace `ref` at once.

## Unreleased

- `labels.yml` gains `hold`, the label pointer-sync-merge and bump-merge leave a PR alone for, so
  `labels-sync` creates it in every repo and a pruning sync no longer deletes it from marola (#130).

## v0.12.0 — 2026-10-15

- `docs-lint` rule `f` checks the ADR shape the umbrella's docs build requires: `docs/adr/NNNN-<slug>.md`,
  an `# ADR-NNNN: <decision>` heading with the file's number, a `| **Status** |` row, and no hand-written
  `index.md` or `README.md`. A consumer bump can newly fail its gate (#124).
- `docs-lint` rule `g` fails an image link (`![…](…)`, an HTML `src=`, or a target ending in an image suffix)
  that stays in the repo and resolves outside `docs/`, as the umbrella's docs build does (#126).

## v0.11.1 — 2026-10-15

- `bot-token` sets the bot's git identity with `GIT_AUTHOR_*`/`GIT_COMMITTER_*` variables instead of
  `GIT_CONFIG_GLOBAL`, which captured `actions/checkout`'s temporary global credential include and made
  a later submodule fetch send `Authorization` twice (400 Duplicate header) (#121).

## v0.11.0 — 2026-10-14

- `bump-consumers` runs as `prepare` (nix, no key) and `publish` (marola-merge-bot, checked-in scripts
  only), so the bump PRs and their commits come from `marola-merge-bot[bot]`. `release.yml` passes
  `MERGE_BOT_KEY` and `MAROLA_BUMP_PAT` is no longer read: marola-devkit must hold `MERGE_BOT_APP_ID`
  and `MERGE_BOT_KEY`. `gemini.yml` passes its secrets explicitly (#80).

## v0.10.1 — 2026-10-14

- `skills-update` takes an optional secret `pr_bot_key` and opens its PR as `marola-pr-bot`; callers
  pass `pr_bot_key: ${{ secrets.PR_BOT_KEY }}` so the PR starts CI (`ci-rule` needs `ci-ok`) (#116).

## v0.10.0 — 2026-10-14

- `ruleset-sync` manages every manifest in `.github/rulesets/`, matched by name, and adds
  `ci-rule`: `ci-ok` from GitHub Actions (integration 15368) required, non-strict, no bypass actors;
  `main-rule` now needs 1 approval, and `bypass-extras.json` applies to it alone (#112). `just
  rulesets-apply` after this release creates `ci-rule` with no bypass and raises `main-rule` to 1
  approval in each repo, and moves marola-devkit's own admins from `always` to `pull_request`.
  Apply `ci-rule` only to a repo whose CI already posts a `ci-ok` check from GitHub Actions on every
  PR (no path filter on the workflow that posts it): it has no bypass actors, so it would block
  every merge elsewhere. `check` and `apply` now fail on a live response without `bypass_actors`.

## v0.9.0 — 2026-10-11

- The `Cost:` commit trailer is out of scope for marola, and the tooling around it is gone (#103):
  `cost-split` and `cost-fill` (tools and recipes), the PR body's **Cost** row, `uprds`'s summed
  stack cost, the `Cost:` line in every automation commit (`bump-consumers`, `release.py`,
  `skills-update.yml`, `gemini-review.yml`) and the MIP skills' "Cost so far" field and column.
- `trailer-fill` (`just trailer-fill`) replaces `cost-fill` for what it still has to do: a missing
  `Tested:` placeholder and a task branch's `Closes #N` line. `pr-flow` and `stack pr` call it.
- The invariant is now two trailers, `Tested:` and `Co-Authored-By: Claude
  <noreply@anthropic.com>`, and the AGENTS.md section is "Attribution (hard rule)". A consumer
  copies the new block from `agents/invariants.md` and drops `Cost:` from its AGENTS.md when it
  bumps, or `agents-check` fails.
- `gemini-review.yml` has no `cost-trailer` input any more; a caller that passes it must remove it.
- `gemini-review.yml` falls back to `gemini-3.6-flash`; Google deprecated `gemini-3.5-flash` and
  redirects it there (#106). An org or repo `GEMINI_MODEL` variable still wins.
- `ruleset-sync` reads an `@consumers` key in `bypass-extras.json` and applies it to every repo in
  `.github/consumers.txt` (`--consumers FILE` overrides), before that repo's own extras:
  `marola-merge-bot` (5257042) becomes a pull-request-only bypass actor on every consumer (MIP-0086
  §5.7, #100). `just rulesets-apply` over the consumers after this release also moves org and repo
  admins from `always` to `pull_request` where v0.8.2's rule was never applied.
- `.github/actions/bot-token` mints a `marola-pr-bot` or `marola-merge-bot` token for named repos
  (MIP-0086 §5.4); `release.py --consumer` moves its pin. `merge-key-check` runs in `just quality`
  (§5.6). `notify-umbrella` takes `pr_bot_key` (the `token` PAT is still read as fallback);
  `gemini-review` reads `PR_BOT_*` with `GEMINI_*` as fallback, and `GEMINI_APP_PRIVATE_KEY` is no
  longer required (#79).

## v0.8.3 — 2026-10-10

- `graph build` is code-only and pruned: no `update .` Markdown pass, no node without a source
  file, no self-loop; the report and HTML are rebuilt from the pruned graph with no model call, and
  communities are named by their hub symbol. `graph update`/`extract` refuse, since either would
  overwrite the pruned graph. The umbrella's graph went from 6,441 nodes, 53% noise in answers, to
  about 1,600 and 0% (#82, #85).
- The `routing` skill asks `graph query` with identifiers (class, file or function names) and sends
  a concept or behaviour to `git grep` first: the graph matches symbol names, not meanings (#82).
- `bump-consumers` regenerates the umbrella's REPOS.md wiring block in its bump commit, so the
  umbrella's bump PR passes `wiring --check` with no hand edit (#78, #81).
- `tests/self-tests.sh` refuses a fake script with an env shebang, which the Nix sandbox lacks
  (#83, #84).

## v0.8.2 — 2026-10-10

- `ruleset-sync` merges each repo's bypass extras from `.github/rulesets/bypass-extras.json`
  (`owner/repo` → actors, each with a reason) into `main-rule`'s bypass list, so `apply` keeps the
  umbrella's `marola-pointer-sync` App instead of dropping it (#68).
- `main-rule`'s two admin bypasses are `pull_request`, not `always`: no admin, and no admin's PAT,
  pushes straight to `main`. After this release, `just rulesets-apply` per repo; no workflow
  pushes to a default branch, so only a person's direct push to `main` stops working.
- `docs/3-development.md` has an "Updating graphify" guide: what `graph.sh` depends on, and what
  proves a new version works (#72, #73).

## v0.8.1 — 2026-10-10

- `wiring` counts dispatch types a `run:` builds in a bash array (`events=(…)`, `events+=(…)`)
  as sent: v0.7.0's own `notify-umbrella` does, so the umbrella's block had lost every sender of
  `submodule-updated` and `submodule-docs-updated`, and `wiring --check` called both orphaned (#69).

## v0.8.0 — 2026-10-10

- `graph`, MIP-0076 §5.2's code graph: graphify 0.9.66 pinned from the flake's nixpkgs, run offline
  and keyless under `env -i`, and `unshare -rn` where allowed. `graph build` writes to
  `~/.cache/marola-graph/<repo>`, never the checkout; `query` defaults to a 400-token budget, and
  `query`, `path` and `explain` print a staleness line once HEAD or a submodule has moved.
- The plugin's `routing` skill (MIP-0076 §5.3): the wiring block for what produces, consumes, pins
  or triggers X, `just graph query` for an unfamiliar keyword or repo, `git grep` for a known one.
- `bump-consumers` reads its own org secret, `MAROLA_BUMP_PAT`, which only marola-devkit may read,
  instead of the shared `MAROLA_CROSS_REPO_PAT` (#63, #64).
- `release.py --consumer` finds a marketplace `ref` spread over several lines in
  `.claude/settings.json`; v0.7.0's bump had left marola's and marola-app's at v0.4.1 (#65).

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
