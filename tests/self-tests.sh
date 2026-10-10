#!/usr/bin/env bash
# Every --self-test in the devkit, run from the repo root; one line per script, non-zero if any
# failed. `just quality` and the flake's `self-tests` check both call this.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

sh_tests=(
  scripts/stack.sh scripts/uprd.sh scripts/cost-fill.sh scripts/issues.sh scripts/mip-resolve.sh
  scripts/agents-check.sh scripts/mip-stack.sh scripts/docs-mip-stack.sh
  scripts/deps-stack.sh scripts/deps-merge.sh scripts/gha-runner.sh scripts/setup-runners.sh
  scripts/runner-preflight.sh scripts/temps.sh scripts/bump-consumers.sh scripts/ruleset-sync.sh scripts/api-docs-push.sh
  scripts/graph.sh
  plugins/marola-devkit/hooks/format.sh plugins/marola-devkit/hooks/stop-gate.sh
  plugins/marola-devkit/hooks/session-start.sh
)
py_tests=(
  scripts/cost-split.py scripts/gemini_review.py scripts/pr_label_nlp.py scripts/workflow_runners.py scripts/docs_lint.py
  scripts/skills_vendor.py scripts/release.py scripts/wiring.py
  scripts/lib/req_merge.py scripts/lib/uses_merge.py scripts/lib/mip_index_merge.py
  scripts/lib/tasks_issues.py plugins/marola-devkit/skills/voice-note-ingest/scripts/transcribe.py
)

failed=0
log="$(mktemp)"; trap 'rm -f "$log"' EXIT
t() {   # </dev/null: a self-test that falls through to a stdin read must fail, not hang the runner
  if "$@" --self-test </dev/null >"$log" 2>&1; then echo "ok    ${*: -1}"; else failed=$((failed + 1)); echo "FAIL  ${*: -1}"; sed 's/^/      /' "$log" | grep -E 'FAIL|Error|fatal|No such' | head -5; fi
}
for s in "${sh_tests[@]}"; do t bash "$s"; done
for p in "${py_tests[@]}"; do t python3 "$p"; done

# A fake written with an env shebang passes here and never runs in nix flake check's sandbox, which
# has no /usr/bin/env (#61, #81, #83): fakes start with #!$BASH. `en[v]` keeps this file off its own list.
env_shebangs() { grep -rnHE '#!/usr/bin/en[v]' "$@" --include='*.sh' --include='*.py' 2>/dev/null | grep -vE '^[^:]+:1:'; }
fx="$(mktemp -d)"; trap 'rm -rf "$log" "$fx"' EXIT
printf '#!/usr/bin/%s bash\ncat >fake <<EOF\n#!/usr/bin/%s bash\nEOF\n' env env >"$fx/red.sh"
printf '#!/usr/bin/%s bash\ncat >fake <<EOF\n%s\nEOF\n' env "#!\$BASH" >"$fx/green.sh"
if [ "$(env_shebangs "$fx/red.sh" | wc -l)" -eq 1 ] && [ -z "$(env_shebangs "$fx/green.sh")" ]; then
  echo "ok    env-shebang check (fixtures)"
else failed=$((failed + 1)); echo "FAIL  env-shebang check: its fixtures were judged wrong"; fi
if offenders="$(env_shebangs scripts plugins tests)"; then
  failed=$((failed + 1)); echo "FAIL  a fake with an env shebang (use #!\$BASH, or sys.executable in Python):"; sed 's/^/      /' <<<"$offenders"
else echo "ok    no env-shebang fakes"; fi
[ "$failed" -eq 0 ] || { echo "self-tests: $failed failed" >&2; exit 1; }
echo "self-tests: all ok"
