#!/usr/bin/env bash
# stop-gate — Stop hook: nudge once per session to run the repo's own gate after an edit. MIP-0011.
set -euo pipefail

REPO_ROOT="${STOP_GATE_REPO_ROOT:-${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"
MARKER_DIR="${XDG_RUNTIME_DIR:-/tmp}/marola-stop-gate"

# gate_command -> what to nag: $MAROLA_STOP_GATE when the consuming repo sets one (its gate may be
# more than one command, e.g. "just build && just test && just quality" — a repo's `quality` does
# not necessarily run tests), else "just stop-gate" when `just` itself reports that recipe (so a
# Justfile/.justfile spelling, an `@`-prefixed recipe or one brought in via `import` all count,
# unlike a plain grep of one literal filename), else the plugin's own default.
gate_command() {
  [ -z "${MAROLA_STOP_GATE:-}" ] || { printf '%s' "$MAROLA_STOP_GATE"; return 0; }
  if command -v just >/dev/null 2>&1 && (cd "$REPO_ROOT" 2>/dev/null && just --show stop-gate) >/dev/null 2>&1; then
    printf 'just stop-gate'; return 0
  fi
  printf 'just quality'
}

extract_field() {   # hook JSON on stdin -> .$1 (empty when absent or input isn't valid JSON)
  local field="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -r --arg f "$field" '.[$f] // empty' 2>/dev/null || true
  else
    python3 -c "import json,sys
try: print(json.load(sys.stdin).get(sys.argv[1], ''))
except Exception: pass" "$field" 2>/dev/null || true
  fi
}

changed_since_head() {
  # Tracked changes (`git diff`) miss a brand-new file the Write tool created but nothing
  # `git add`ed yet — `git diff` is diff-only by design and never sees untracked paths.
  git -C "$REPO_ROOT" diff --name-only HEAD 2>/dev/null | grep -q . && return 0
  git -C "$REPO_ROOT" ls-files --others --exclude-standard 2>/dev/null | grep -q .
}

# check_stop <session_id>: 0 = allow, 2 = block (and writes the marker so the next call allows).
check_stop() {
  local session_id="$1"
  [ -n "$session_id" ] || return 0   # no session id on stdin — never block by accident
  mkdir -p "$MARKER_DIR" 2>/dev/null || return 0   # can't write a marker -> allow, don't nag
  local marker="$MARKER_DIR/$session_id"
  [ -f "$marker" ] && return 0        # already nagged this session
  changed_since_head || return 0   # nothing changed — nothing to gate
  touch "$marker" 2>/dev/null || return 0   # same fail-open rule as the mkdir above
  local gate; gate="$(gate_command)"
  echo "stop-gate: files changed since HEAD and this session hasn't run \`$gate\` yet." >&2
  echo "Run \`$gate\` (the repo's own gate) before stopping, or say why this change doesn't need it. This nag only fires once per session." >&2
  return 2
}

self_test() {
  # Hooks run with the harness's GIT_DIR/GIT_WORK_TREE in the environment; `git -C <tmp>` does NOT
  # override GIT_DIR, so the throwaway `git init/config/commit` below landed in the real repo
  # (core.bare=true, a test identity, a stray "init" commit) on every push — seen 2026-09-06.
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR
  local fails=0
  local tmp; tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  export XDG_RUNTIME_DIR="$tmp/runtime"
  MARKER_DIR="$XDG_RUNTIME_DIR/marola-stop-gate"

  # A throwaway git repo with an uncommitted .scala change, so changed_since_head is true.
  local repo="$tmp/repo"
  git init -q "$repo"
  git -C "$repo" config user.email test@example.com
  git -C "$repo" config user.name Test
  printf 'object A\n' > "$repo/A.scala"
  git -C "$repo" add A.scala
  git -C "$repo" commit -q -m init
  printf 'object A { val x = 1 }\n' > "$repo/A.scala"   # uncommitted change since HEAD
  REPO_ROOT="$repo"

  local session="self-test-session-$$"
  local first=0; check_stop "$session" || first=$?
  if [ "$first" -eq 2 ]; then
    echo "  ok   first call with a .scala change and no marker blocks"
  else
    echo "  FAIL first call did not block (exit $first)"
    fails=$((fails + 1))
  fi

  local second=0; check_stop "$session" || second=$?
  if [ "$second" -eq 0 ]; then
    echo "  ok   second call in the same session (marker present) allows"
  else
    echo "  FAIL second call blocked again (exit $second) — a second nag in one session"
    fails=$((fails + 1))
  fi

  # A different session id, same repo state, blocks again — the marker is per-session, not global.
  local other_session="self-test-session-other-$$"
  local third=0; check_stop "$other_session" || third=$?
  if [ "$third" -eq 2 ]; then
    echo "  ok   a different session id blocks independently of the first"
  else
    echo "  FAIL a fresh session id did not block (exit $third)"
    fails=$((fails + 1))
  fi

  # No .scala changes -> never blocks, even with no marker.
  git -C "$repo" checkout -q -- A.scala
  local clean_session="self-test-session-clean-$$"
  local fourth=0; check_stop "$clean_session" || fourth=$?
  if [ "$fourth" -eq 0 ]; then
    echo "  ok   no .scala changes since HEAD never blocks"
  else
    echo "  FAIL clean tree blocked anyway (exit $fourth)"
    fails=$((fails + 1))
  fi

  # A brand-new, never-`git add`ed .scala file (the Write-tool case) blocks too, not just an edit
  # to a tracked one — `git diff` alone would miss this.
  printf 'object Brand\n' > "$repo/Brand.scala"
  local untracked_session="self-test-session-untracked-$$"
  local untracked_result=0; check_stop "$untracked_session" || untracked_result=$?
  if [ "$untracked_result" -eq 2 ]; then
    echo "  ok   a new untracked .scala file blocks like a tracked edit does"
  else
    echo "  FAIL untracked .scala file did not block (exit $untracked_result)"
    fails=$((fails + 1))
  fi
  rm -f "$repo/Brand.scala"

  # Empty/missing session id never blocks, even with a .scala change.
  printf 'object A { val x = 2 }\n' > "$repo/A.scala"
  local empty_result=0; check_stop "" || empty_result=$?
  if [ "$empty_result" -eq 0 ]; then
    echo "  ok   missing session id never blocks by accident"
  else
    echo "  FAIL missing session id blocked (exit $empty_result)"
    fails=$((fails + 1))
  fi

  # End-to-end through the real hook JSON path (extract_field + check_stop as actually wired).
  local json_session="self-test-json-$$"
  local hook_json="{\"session_id\":\"$json_session\",\"hook_event_name\":\"Stop\"}"
  local got_session; got_session="$(printf '%s' "$hook_json" | extract_field session_id)"
  if [ "$got_session" = "$json_session" ]; then
    echo "  ok   extract_field reads .session_id from real hook JSON"
  else
    echo "  FAIL extract_field got '$got_session', expected '$json_session'"
    fails=$((fails + 1))
  fi
  if [ -z "$(printf 'not json' | extract_field session_id)" ]; then
    echo "  ok   malformed input yields an empty session id (never blocks by accident)"
  else
    echo "  FAIL malformed input did not yield an empty session id"
    fails=$((fails + 1))
  fi
  local fifth=0; check_stop "$got_session" || fifth=$?   # real path: fresh session, .scala changed again
  if [ "$fifth" -eq 2 ]; then
    echo "  ok   end-to-end JSON-derived session id blocks on a fresh session"
  else
    echo "  FAIL end-to-end JSON-derived session id did not block (exit $fifth)"
    fails=$((fails + 1))
  fi

  # An unwritable marker dir (read-only fs, XDG_RUNTIME_DIR gone) must allow, not exit 1 — the
  # hook's own "never block by accident" invariant, applied to the marker writes too (an
  # ultrareview finding, 2026-09-06: `set -euo pipefail` + an unguarded mkdir/touch used to exit
  # 1, neither the documented allow-0 nor block-2).
  printf 'object A { val x = 3 }\n' > "$repo/A.scala"
  MARKER_DIR="/proc/self/marola-stop-gate-unwritable"
  local unwritable_session="self-test-session-unwritable-$$"
  local unwritable_result=0; check_stop "$unwritable_session" || unwritable_result=$?
  MARKER_DIR="$XDG_RUNTIME_DIR/marola-stop-gate"
  if [ "$unwritable_result" -eq 0 ]; then
    echo "  ok   an unwritable marker dir allows (fails open), never exits 1"
  else
    echo "  FAIL unwritable marker dir gave exit $unwritable_result, expected 0"
    fails=$((fails + 1))
  fi

  # MAROLA_STOP_GATE: the consuming repo names its own gate (its `quality` may not run tests).
  printf 'object A { val x = 4 }\n' > "$repo/A.scala"
  local env_session="self-test-session-env-$$" env_out="" env_rc=0
  env_out="$(MAROLA_STOP_GATE='just build && just test && just quality' check_stop "$env_session" 2>&1 1>/dev/null)" || env_rc=$?
  if [ "$env_rc" -eq 2 ] && printf '%s' "$env_out" | grep -qF 'just build && just test && just quality'; then
    echo "  ok   MAROLA_STOP_GATE overrides the nagged command"
  else
    echo "  FAIL MAROLA_STOP_GATE did not override the nag (exit $env_rc): $env_out"
    fails=$((fails + 1))
  fi

  # A \`stop-gate\` recipe, detected via \`just --show\` (not a literal-filename grep, so a
  # Justfile/.justfile spelling, an @-prefixed recipe or one pulled in via \`import\` all count the
  # same way). Stubbed rather than a real justfile+just: this host may not have \`just\` installed.
  printf 'object A { val x = 5 }\n' > "$repo/A.scala"
  local jf_bin="$tmp/jf-bin"
  mkdir -p "$jf_bin"
  cat > "$jf_bin/just" <<'STUB'
#!/bin/sh
[ "$1" = "--show" ] && [ "$2" = "stop-gate" ] && exit 0
exit 1
STUB
  chmod +x "$jf_bin/just"
  local jf_session="self-test-session-justfile-$$" jf_out="" jf_rc=0
  jf_out="$(PATH="$jf_bin:$PATH" check_stop "$jf_session" 2>&1 1>/dev/null)" || jf_rc=$?
  if [ "$jf_rc" -eq 2 ] && printf '%s' "$jf_out" | grep -qF 'just stop-gate'; then
    echo "  ok   a stop-gate recipe \`just\` reports is nagged by name"
  else
    echo "  FAIL stop-gate recipe reported by just was not named in the nag (exit $jf_rc): $jf_out"
    fails=$((fails + 1))
  fi

  # \`just\` is on PATH but reports no stop-gate recipe: falls back to the default.
  printf 'object A { val x = 6 }\n' > "$repo/A.scala"
  local nojf_bin="$tmp/nojf-bin"
  mkdir -p "$nojf_bin"
  printf '#!/bin/sh\nexit 1\n' > "$nojf_bin/just"
  chmod +x "$nojf_bin/just"
  local nojf_session="self-test-session-nojf-$$" nojf_out="" nojf_rc=0
  nojf_out="$(PATH="$nojf_bin:$PATH" check_stop "$nojf_session" 2>&1 1>/dev/null)" || nojf_rc=$?
  if [ "$nojf_rc" -eq 2 ] && printf '%s' "$nojf_out" | grep -qF 'just quality'; then
    echo "  ok   just installed but no stop-gate recipe still defaults to just quality"
  else
    echo "  FAIL no-stop-gate-recipe case did not default (exit $nojf_rc): $nojf_out"
    fails=$((fails + 1))
  fi

  # Neither env var nor \`just\` reachable at all: still defaults, never crashes.
  printf 'object A { val x = 7 }\n' > "$repo/A.scala"
  local def_session="self-test-session-default-$$" def_out="" def_rc=0
  def_out="$(check_stop "$def_session" 2>&1 1>/dev/null)" || def_rc=$?
  if [ "$def_rc" -eq 2 ] && printf '%s' "$def_out" | grep -qF 'just quality'; then
    echo "  ok   with neither set, the nag still defaults to just quality"
  else
    echo "  FAIL default nag changed unexpectedly (exit $def_rc): $def_out"
    fails=$((fails + 1))
  fi

  [ "$fails" -eq 0 ] && { echo "stop-gate self-test: ok"; return 0; }
  echo "stop-gate self-test: $fails failure(s)" >&2; return 1
}

case "${1-}" in
  --self-test) self_test ;;
  *) check_stop "$(extract_field session_id)" ;;
esac
