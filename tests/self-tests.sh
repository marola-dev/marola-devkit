#!/usr/bin/env bash
# Every --self-test in the devkit, run from the repo root; one line per script, non-zero if any
# failed. `just quality` and the flake's `self-tests` check both call this.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

sh_tests=(
  scripts/stack.sh scripts/uprd.sh scripts/cost-fill.sh scripts/issues.sh scripts/mip-resolve.sh
  scripts/agents-check.sh scripts/mip-stack.sh scripts/docs-mip-stack.sh
  scripts/deps-stack.sh scripts/deps-merge.sh scripts/gha-runner.sh scripts/setup-runners.sh
  scripts/runner-preflight.sh scripts/temps.sh scripts/ruleset-sync.sh scripts/api-docs-push.sh
  plugins/marola-devkit/hooks/format.sh plugins/marola-devkit/hooks/stop-gate.sh
  plugins/marola-devkit/hooks/session-start.sh
)
py_tests=(
  scripts/cost-split.py scripts/gemini_review.py scripts/pr_label_nlp.py scripts/workflow_runners.py scripts/docs_lint.py
  scripts/skills_vendor.py
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
[ "$failed" -eq 0 ] || { echo "self-tests: $failed failed" >&2; exit 1; }
echo "self-tests: all ok"
