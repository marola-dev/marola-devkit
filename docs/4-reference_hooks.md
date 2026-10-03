# Hooks

Two kinds: git hooks a repo opts into, and the Claude Code hooks the plugin brings.

## Git hooks (`.githooks/`)

A consuming repo opts in with `git config core.hooksPath .devkit/.githooks`; this repo's dev
shell sets `.githooks` itself. Each hook runs one recipe from the repo's own justfile, through
`nix develop --command just` when nix is there and the shell is not already a nix shell.

| Hook | Runs | Contract |
|---|---|---|
| `pre-commit` | `just precommit`, when something is staged | Fast checks on what is staged |
| `pre-push` | `just prepush`, unless every pushed ref is a deletion | The gates CI would fail the push on |

A repo with no such recipe gets one line and the commit or push goes through. With no `just` to
run, the hook fails. `--no-verify` bypasses either.

`prepush` takes no arguments, so a parameterless recipe keeps working. git's pre-push data
reaches it as `MAROLA_PUSH_REMOTE`, `MAROLA_PUSH_URL` and `MAROLA_PUSH_REFS_FILE`
([config](4-reference_config.md)), so a recipe can check the real push range instead of HEAD's.

## Plugin hooks (`plugins/marola-devkit/hooks/`)

Declared in `hooks.json`; each script has a `--self-test` in `tests/self-tests.sh`. A repo that
enables the plugin drops its own `.claude/hooks/` copies of these.

| Script | Event | Does |
|---|---|---|
| `format.sh` | `PostToolUse` on `Edit`/`Write` (30 s) | Formats the one file just written, in place: `*.scala` with the scalafmt version from `.scalafmt.conf` (via `cs`), `*.py` with `ruff format`. Best effort: a missing tool or config is a no-op |
| `stop-gate.sh` | `Stop` (10 s) | Once per session, when files changed since HEAD (untracked included), blocks the stop with a nudge to run the repo's gate: `MAROLA_STOP_GATE`, else the repo's `stop-gate` recipe if it defines one, else `just quality`. A marker under `$XDG_RUNTIME_DIR/marola-stop-gate/` keeps it to one nag; anything it cannot read or write lets the stop through |
| `session-start.sh` | `SessionStart` (10 s) | Prints the branch, the `gh` login and the uncommitted count, plus two ai-jail caveats: `.env.example` and `.ai-jail` read empty in the jail (stage by name), and `gh` has no login there |
