# Plugin

`marola-devkit@marola-devkit` is the Claude Code plugin under `plugins/marola-devkit/`, listed by
`.claude-plugin/marketplace.json`. A repo declares the marketplace with a `ref` pinned to the
same tag as its flake input and enables the plugin in `.claude/settings.json` (the README has the
snippet). Skills then load as `/marola-devkit:<skill>` and agents as `marola-devkit:<agent>`.
OpenCode does not load Claude Code plugins, but each skill is a plain `SKILL.md` it can read.

`human-only` below means `disable-model-invocation: true`: a person types the command, and the
model cannot start it on its own.

## Skills

| Skill | Use when | Notes |
|---|---|---|
| `mip` | Any non-trivial change: a new data source or integration, a scoring change, user-visible output, autonomous behaviour | The plan layer: writes `docs/MIPs/MIP-NNNN-*.md` with verified sources and open questions |
| `mip-tasks` | An accepted MIP that is more than one PR | The delivery layer: `MIP-NNNN.tasks.md` (ordered tasks, each with its test) and one stacked PR per task through `stack`. human-only |
| `mip-solve-perpetual` | Working through a `.tasks.md` unattended | One typed invocation, one Draft PR per task, a usage guard and a checkpoint contract; never merges. human-only. Mechanics on [runners](4-reference_runners.md#unattended-mip-runs) |
| `triage` | A raw idea, voice-note fragment or bug report that should become one issue | Picks the tier, drafts the body in the issue form's shape, proposes `area/*`/`layer/*`/`size/*`, checks the Definition of Ready, then stops: a person files it (MIP-0063 §5.6). human-only |
| `voice-note-ingest` | Audio a MIP or task refers to needs a transcript | Local Whisper only, pt-BR by default; writes `<audio>.txt` beside the file, never commits the audio, anonymizes names before a transcript reaches a tracked file. `scripts/transcribe.py` has a `--self-test` |
| `voice-to-feature` | Demoing voice note to Draft PR in one pass | Demo only, not the dev flow; `gh pr merge`/`close` are disallowed tools. human-only |
| `humanizer` | Prose that reads as generated | Vendored from `blader/humanizer` v3.0.0 (MIT, `LICENSE` beside it); changes wording, never a claim, number or reference |
| `ponytail` | Writing code | Vendored from `DietrichGebert/ponytail` (MIT): reuse the repo, then the stdlib, then the platform, before new code |
| `ponytail-review` | Reviewing a diff for over-engineering | Same source; lists findings only |
| `ponytail-audit` | Auditing the whole tree for over-engineering | Same source; a ranked list, no edits |
| `sharingan` | A URL to a skill, workflow or pattern elsewhere that should exist here | Runs on Opus 5.5. Fetches the unit at a pinned sha, lets the licence decide vendor, adapt or rewrite, and maps each upstream concept to what Claude Code or marola already has |
| `skill-copy` | Same as `sharingan` | An alias. human-only |
| `obsidian-vault` | Saving or resuming a session handoff, or refreshing the marola note in the maintainer's vault | Vault at `MAROLA_OBSIDIAN_VAULT`; usually invisible inside ai-jail. Has evals |

Where a vendored skill disagrees with a repo's AGENTS.md, AGENTS.md wins. The four vendored
skills are pinned in `skills.lock` beside them: `just quality` checks the copies against it, and
the weekly `skills` workflow opens a PR when an upstream moved
([skills-vendor](4-reference_tools.md#skills-vendor)).

## Agents

| Agent | Model | Does |
|---|---|---|
| `mip-reviewer` | `fable` | Reviews one PR of a MIP task stack against its `.tasks.md` row and the MIP's scoring and verification sections; reports only gaps that affect correctness or the stated requirement |
| `mip-claims-auditor` | `haiku` | Read-only: checks a MIP's §4 for unsourced external claims (a price, a count, a licence term, an endpoint) that drive a design decision. Runs only when §4 names such a claim |

## Hooks and status line

The plugin's three hooks (`format.sh`, `stop-gate.sh`, `session-start.sh`) are on
[hooks](4-reference_hooks.md). The status line is not part of the plugin: the flake ships
`.claude/statusline.sh`, which a repo wires as `.devkit/.claude/statusline.sh`.
