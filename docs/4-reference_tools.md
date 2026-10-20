# Tools

Every tool `flake.nix` puts on `PATH`, with its script and the `devkit.just` recipe that wraps it.
Each one is also a flake app (`nix run github:marola-dev/marola-devkit/<tag>#<tool>`) and has an
offline `--self-test`. Most take `--dry-run`, which prints the mutating `git`/`gh` calls instead of
making them. A tool that calls `gh` needs a login on the host; inside ai-jail it has none, so use
`--dry-run` there or pass `GH_TOKEN`.

## Pull requests and stacks

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `pr-flow` | `pr.sh` | `just pr` | The whole PR path from a finished commit: refuses on `main` or a dirty tree, fills a missing `Tested:` trailer (`trailer-fill`), pushes (`stack pr` for a `mip-NNNN/k-*` branch), writes the body (`uprd`). `--dry-run` prints every step and the body. Not named `pr`, which would shadow coreutils' |
| `stack` | `stack.sh` | `just stack`, `just stack-link` | Stacked branches for a MIP's tasks: `start MIP-NNNN k slug` (branch off the nearest lower task branch, else `origin/main`), `pr` (push, open or update with the right base), `restack` (after the base was squash-merged; `--onto-base <sha>` when it finds no fork point), `status`, `branches`, `link` (a GitHub Stack from the open PRs) |
| `uprd` | `uprd.sh` | `just uprd` | Write the branch's PR body from its commits, in the PR template's shape, and create the PR if none exists. `uprd 84` works on that PR from any checkout; a file argument is used as the body. `BASE`, `BRANCH` and `EXTRA_FILE` override the base, the branch and an appended section |
| `uprds` | `uprds.sh` | `just uprds` | `uprd` for every PR of a MIP stack, bottom first, each with a shared "Stack" section (states, the current PR marked) |
| `mip-stack` | `mip-stack.sh` | `just mip-stack` | Chain every open MIP draft PR into one GitHub Stack, resolving the shared `docs/MIPs/README.md` row conflict; `--resume`, `--skip <PR#>`, `status`, `clean` |
| `docs-mip-stack` | `docs-mip-stack.sh` | `just docs-mip-stack` | Chain un-merged `docs/mip-NNNN-*` branches: `auto` (push and plan the unambiguous local ones), `list`, `plan <branch>…` (conflict check with `git merge-tree`, prints the `gh pr create` lines) |
| `deps-stack` | `deps-stack.sh` | `just deps-stack` | Chain open dependency-update PRs into one GitHub Stack, auto-resolving bump-vs-bump conflicts; `--resume`, `--skip`, `--include-steward`, `status`, `clean`. Built in `.tmp/wt-deps-stack`, never the caller's checkout |
| `deps-merge` | `deps-merge.sh` | `just deps-merge` | Merge every open dependency PR that is mergeable with every check green (skipped and neutral count as green); reports the rest. `--repo OWNER/REPO --unattended`, for a scheduled job, also needs Dependabot as the author of the PR and every commit, no `hold` label, only Python manifests changed (`requirements*.txt`, `pyproject.toml`, a lock file), and each changed line one dependency whose versions moved by a patch or minor (a 0.x minor counts as major), read from the diff; it pins the merge to the checked head commit and writes one stderr line per refused PR. The caller checks `AUTO_MERGE` |
| `bump-merge` | `bump-merge.sh` | `just bump-merge` | Squash-merge the newest release's `chore/devkit-vX.Y.Z` PRs in every `consumers.txt` repo the umbrella first, with an optional soak and canary delay (both off by default); `--dry-run` prints each decision and command, `--now EPOCH` sets the clock. Run hourly and on `bump-ready` by `bump-merge.yml`; the rules are in [workflows](4-reference_workflows.md#bump-merge-not-reusable) |
| `branches` | `branches.sh` | `just branches-clean`, `just branches-open` | `clean` deletes local branches whose PR merged (local and remote ref); `open` opens a `main`-based PR for each plain branch ahead of `main` with none |

`just stack-setup`, `just stack-view` and `just stack-sync` wrap GitHub's own `gh stack`
extension rather than a devkit tool.

## Trailers

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `trailer-fill` | `trailer-fill.sh` | `just trailer-fill` | Add a missing `Tested:` (`ci-only`) trailer to each commit of the branch, dates preserved, and a `Closes #N` line for a task branch whose row links an issue (`TASK_PARTIAL=1` opts out). `--base <ref>` bounds the rewrite; `--dry-run` changes nothing |

## Issues and the board

`issues` is the whole issue surface (MIP-0063, MIP-0070 §5.7). Every subcommand takes
`--dry-run`; the reads behind it still happen, so a login is needed either way.

| Recipe | Command | Does |
|---|---|---|
| `just issue-queue [--milestone NAME]` | `issues queue` | The unassigned open issues across the umbrella's org, `agent-ready` ones from other repos as `owner/repo#N`, sorted size then priority, with ready, blocked and in-triage counts from labels and dependency edges |
| `just issue-ready <n>` | `issues ready` | The five-rule Definition of Ready, naming the rule that failed; adds `agent-ready` on a pass and removes it on a regression |
| `just issue-claim <n\|owner/repo#n>` | `issues claim` | Re-check readiness, assign to you, drop the label, set the board to In progress, print the `stack start` line |
| `just tasks-to-issues MIP-NNNN [--milestone NAME] [--deliverable NAME]` | `issues tasks-to-issues` | File a MIP's task table: a parent in the umbrella, one sub-issue per row in the repo its `delivers` cell names, `blocked by` edges per `depends on`, everything on Project 1. Idempotent; sets no `area/*`, `layer/*` or `size/*` |
| `just milestone-new "<name>" [--mip MIP-NNNN]` | `issues milestone new` | Create a deliverable milestone; idempotent |
| `just labels-sync [--prune] [--force]` | `issues labels sync` | Reconcile the repo's labels with the devkit's `.github/labels.yml`; orphans are deleted only with `--prune` |
| `just board-sync` | `issues board sync` | Put every open issue on the board; set Status only on a card with none or `Backlog`, and move a closed issue's card to Done |
| (none) | `issues board setup`, `issues board gates` | One-time bootstraps: the board's Status options, views and `Deliverable` field (needs `project` scope); the five phase gate issues |
| (none) | `issues sub add <parent> <child>`, `issues deps add <n> --blocked-by <m>`, `issues deps list <n>` | One sub-issue or dependency edge by hand |

## Labels, rulesets and repo checks

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `pr-label` | `pr-label.sh` | `just pr-label` | Apply the deterministic `area/*`, `layer/*`, `kind/*` taxonomy (`scripts/lib/pr_labels.sh`) to one PR |
| `backfill-pr-labels` | `backfill-pr-labels.sh` | `just pr-labels-backfill` | The same for every merged or closed PR with no label; `--limit`, `--nlp` (compare with `pr-label-nlp`), `--nlp-apply-unscoped` (fill only an `area/unscoped` gap) |
| `pr-label-nlp` | `pr_label_nlp.py` | (none) | A local TF-IDF classifier for `area/*`, kept to compare against the deterministic one, never applied on its own. `--pr N` or `--title`/`--body` |
| `ruleset-sync` | `ruleset-sync.sh` | `just rulesets-check`, `just rulesets-apply` | `check [owner/repo…]` diffs a repo's live branch rulesets, matched by name, against each manifest in `.github/rulesets/`: `main-rule.json` (a squash PR with 1 approval; org and repo admins bypass on a PR), plus that repo's bypass actors in `.github/rulesets/bypass-extras.json` (each with a reason; the `@consumers` key applies to every repo in `.github/consumers.txt`, `--consumers FILE` to override), and `ci-rule.json` (`ci-ok` from GitHub Actions, integration 15368, non-strict; no bypass actors, and no extras); prints `<repo> <ruleset>: ok\|drifted\|missing`; `apply <owner/repo…>` creates or updates each; `--manifest FILE`, once or more, replaces the set; `--all-org ORG` covers an org |
| `merge-key-check` | `merge_key_check.py` | `just quality` | Fails when `MERGE_BOT_KEY` appears in a workflow outside the allowlist `.github/merge-key-allowlist` (workflow files, one repo-relative path per line, `#` comments), or in a job that runs third-party actions or fetch/build commands, calls a reusable workflow (except a listed local one), sets `container`/`services` or a shell other than bash or sh, or sits beside workflow-level env carrying the key (MIP-0086 §5.6). Once the allowlist exists, `secrets: inherit` is flagged repo-wide |
| `agents-check` | `agents-check.sh` | `just agents-check` | Compare AGENTS.md's invariants block byte for byte with the pinned devkit's `agents/invariants.md` (`--block` or `MAROLA_INVARIANTS_BLOCK` to use another) |
| `docs-lint` | `docs_lint.py` | (none) | MIP-0074 §7's stale-content check over `README.md` and `docs/**/*.md`: undefined or unmarked foreign recipes, another repo's paths, split-era wording, `docs/index.md`, links leaving the repo; and a MIP §11 bullet that says decided, not negated or pending ("not decided", "once that's decided"), without a `**Decided YYYY-MM-DD:**` marker (MIP-0085 §5.6) |
| `wiring` | `wiring.py` | `just wiring` | MIP-0076 §5.1's four tables (artifact, dispatch, pin bump, deploy) and MIP-0085 §5.3's contracts (a data file, `.json`, `.yaml`, `.csv`, `.sql` and the like, copied byte for byte into another repo, outside dot-directories and lock files; the first by path is canonical, a test copy never), parsed from the umbrella's and every submodule's workflows, pin files, scripts, justfiles, Dockerfiles, compose files and `build.sbt`, with each devkit workflow call's ref (each call is read at that ref: from a local devkit clone's tags, else fetched from GitHub, a tag cached under `~/.cache/marola-wiring`). `wiring [FILE]` prints the block or rewrites it between FILE's `wiring:start`/`wiring:end` markers; `--check [FILE]` fails on a stale block, an orphan or unreadable dispatch, or an artifact nobody reads that the umbrella's `wiring.allow` (`<artifact>: <reason>` per line) does not list; a `contract <canonical> <copy>…: <reason>` line there names the canonical file, or links copies that differ on purpose, and fails the check when a path in it is gone; run it in an umbrella checkout with submodules. `--lint-devkit [DIR]` fails on the devkit's own workflows: a dispatch it cannot read, or a type they listen for and none sends (devkit CI runs it) |
| `decisions` | `decisions.py` | `just decisions` | MIP-0085 §5.6: one row per decision in the MIPs (`MIP-NNNN-*.md` under `--mips`, `docs/MIPs` by default): §1's first sentence, each top-level "Alternatives considered" bullet's bold lead and first clause, shown as `<lead> (rejected): <clause>` (a bullet with several bold-led sentences gives a row each), and every `**Decided YYYY-MM-DD:** …` sentence outside code, each with the MIP's section and the first words of its Status row. `decisions` writes `DECISIONS.md` in that directory, or `--md FILE`; `--json FILE` writes the rows as `{"rows": [{mip, file, section, status, kind, text}]}`, the bundle's `decisions.json`. `--check [FILE]` fails when the file differs from what the MIPs give |
| `workflow-runners` | `workflow_runners.py` | (none) | Fail when any workflow can reach a self-hosted runner ([runners](4-reference_runners.md)) |
| `skills-vendor` | `skills_vendor.py` | `just skills-check`, `just skills-outdated`, `just skills-update` | Pin vendored skills to an upstream commit in `skills.lock` (below) |

## skills-vendor

A skill copied from another repo is pinned the way a dependency is (MIP-0080): `skills.lock`
beside the skill directories names its upstream, the commit it was taken at and the git blob sha
of every vendored file. The lock is `<repo>/.claude/skills/skills.lock` unless `--lock` says
otherwise, and a skill's files live in `<lock dir>/<name>/`;
[this repo's](https://github.com/marola-dev/marola-devkit/blob/main/plugins/marola-devkit/skills/skills.lock)
shows the shape.

| Field | Meaning |
|---|---|
| `upstream`, `path` | The GitHub repo and the skill's directory in it (`""` for the root) |
| `commit` | The upstream commit the files were taken at; `null` when `init` found none |
| `files` | Each vendored file with its git blob sha. Only these files are vendored |
| `paths` | Optional: a vendored file that sits elsewhere upstream (`"LICENSE": "LICENSE"`) |
| `license` | The SPDX id from `SKILL.md`'s `license:` or the `LICENSE` file, or `null` |
| `local_edits` | Optional: a patch relative to the lock directory, re-applied after every update; `files` then holds the edited shas and `upstream_files` the pristine ones |
| `hold` | A reason to leave the skill where it is; `outdated` and `update` skip it |

| Command | Does |
|---|---|
| `check` | Offline: every file in each skill directory has the sha in `files`. No lock file: exit 0 |
| `outdated [--min-age DAYS] [--json] [--strict]` | The newest upstream commit touching `path` at least `--min-age` days old, with a diff of what it changes |
| `update <name>... \| --all [--sha SHA] [--min-age DAYS] [--report FILE]` | Fetch at that commit, re-apply `local_edits` (a patch that fails stops it), rewrite the lock; never commits |
| `init <name> --upstream o/r --path DIR [--sha SHA] [--paths FILE=UPSTREAM]` | A lock entry from the copy already in the tree; without `--sha`, the newest of 200 upstream commits whose tree matches |

Upstreams are read through a blobless `git clone` of `https://github.com/<upstream>`, so the tool
works wherever that does, Actions included.
| `mip-resolve` | `mip-resolve.sh` | (none) | Print a MIP's doc or `.tasks.md` (`MIP-NNNN [tasks]`), or with `--path` where it was found, by the lookup in [design](1-design.md#how-tools-find-the-umbrella) |
| `api-docs-push` | `api-docs-push.sh` | (none) | `api-docs-push <dir> <remote> <sha> [--branch api-docs]`: force-push a directory as one orphan commit, the `api-docs` workflow's push step |

## Code graph

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `graph` | `graph.sh` | `just graph` | MIP-0076 §5.2's pinned graphify. `build` runs `extract . --code-only --no-label`, prunes every node without a source file (library and package nodes such as `String`), the edges touching them and self-loops, then `cluster-only . --no-label --graph <graph.json>` twice: the first assigns communities, and the second, given `.graphify_labels.json` naming each after its highest-degree member (a file node only when nothing else is there), writes the report and HTML with those names. Last, it tags entry points `entry: true` (a node whose line is `def main(`, `@main` or `object X extends …App`, and the file node of a file with a `__main__` guard), which `route query` boosts (MIP-0085 §5.2). All of it lands in `${XDG_CACHE_HOME:-~/.cache}/marola-graph/<repo>`, with no `update` (no Markdown nodes), recording `HEAD` and `git submodule status` in `build.json`; an uninitialised submodule fails it. `query` (`--budget 400` by default), `path` and `explain` read that graph and warn when it is stale. Every call runs under `env -i` with five variables, and `unshare -rn` where allowed; `--backend` is refused, and so are `extract` and `update`, which would overwrite the pruned graph. Bumping graphify: [Updating graphify](3-development.md#updating-graphify) |
| `route` | `skills/routing/scripts/route.py` | `just route` | MIP-0084 §5.2: the code graph and the wiring with no Nix and no graphify, Python stdlib only. `query "<identifiers>" [--budget 400]`, `explain <node>`, `path <a> <b>` and `wiring <term>` read a local `graph build` whose `build.json` matches `HEAD` and the submodules, else `refs/marola/routing-bundle`, else `origin/routing-bundle`, and say which on their first line. `query` splits identifiers on case and separators (`OpenMeteoClient` is open, meteo, client), drops stop words such as where, the, entry and point, and boosts a node tagged as an entry point when the question says entry, start or main (MIP-0085 §5.2). `impact <node|file> [--depth 2]` walks `calls`, `imports` and `indirect_call` edges backwards from a node, or from every node of a file, and lists the files reached, nearest first; then, for a file the wiring knows, a contract's copies with the readers and pins of its repo's artifacts in the copies' repos, or an artifact's readers, pins, publishers and bumps when the file pins or publishes it. It always ends with `(lower bound: name-resolved calls only)`: tree-sitter resolves calls by name and misses Scala implicits, extension methods and Kyo's effect plumbing (MIP-0085 §5.5). `fetch` brings the umbrella's `routing-bundle` into `refs/marola/routing-bundle` (`MAROLA_ROUTING_REMOTE` overrides the URL), `status [--line]` prints its commit and age, `why "<words>"` ranks the bundle's `decisions.json` rows by BM25 and prints the top five as `MIP-NNNN §<section>  <text> [<status>]` (MIP-0085 §5.6). `bench [file]` scores right-first per question (MIP-0076's eight by default; a `via: why` question is right when an expected MIP is in its first three answer lines); a `via: impact` question asks `impact` and counts its first three indented rows. With no bundle it prints one line and exits 0; a bundle file over 20 MB is refused. A repo runs it from its vendored `.claude/skills/routing/scripts/` |
| `route-usage` | `route_usage.py` | `just route-usage` | MIP-0084 §5.5: one line per Claude Code session under `${CLAUDE_CONFIG_DIR:-~/.claude}/projects` (or the directories given) that ran `route`, `graph query/explain/path/build`, or read the umbrella's wiring block, with the counts, `prompted` when a person's message before the first call named route, routing, graph or wiring, and `hook` when the session-start `▸ routing:` line was in its context; then a total. A subagent's transcript counts toward its parent. Local and read-only; it prints counts, never transcript text |

## Self-hosted runner

| Tool | Script | Recipe | Does |
|---|---|---|---|
| `runner-preflight` | `runner-preflight.sh` | `just runner-preflight` | Check this machine can run the workflows (nix, python3, node, jq, curl, docker, no passwordless sudo, disk) |
| `gha-runner` | `gha-runner.sh` | `just runner-up`, `runner-down`, `runner-status`, `runner-logs` | Run a registered runner in the background from `GHA_RUNNER_DIR` |
| `setup-runners` | `setup-runners.sh` | `just runners` | Register N runners on this machine as user services; `--status`, `--remove` |
| `temps` | `temps.sh` | `just temps` | CPU and GPU temperature against the parts' own throttle and shutdown points; `--watch`, `--json` |

Details and variables are on the [runners](4-reference_runners.md) and
[config](4-reference_config.md) pages.
